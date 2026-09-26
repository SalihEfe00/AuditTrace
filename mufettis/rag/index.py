# -*- coding: utf-8 -*-
"""Ingest index: indekslenmiş her belgenin kaydı (başlık, kaynak, kimlik, durum, sürüm bağları).

Depolama SQLite'tır (`ingest_index.sqlite3`): her belge bir satırdır, bir belgeyi güncellemek yalnızca
o satırı yazar ve yalnızca istenen kayıt okunur. Önceki tek JSON dosyası her güncellemede baştan
yazılıyor ve her açılışta bütünüyle belleğe alınıyordu; tam mevzuat ölçeğinde (~25 bin belge,
~70 MB) güncelleme başına ~1.5 sn ve açılış başına ~270 MB tepe bellek demekti.

Arayüz korunur: `get`, `upsert` (mevcut kayıtla birleştirir), `remove`, `find_by_title_and_source`
ve `index.data["indexed_items"]` sözlük görünümü (okuma sayfalıdır; dönen kayıtlar kopyadır,
yerinde değiştirmek kaydetmez — `upsert` kullanılır).

Eski JSON: yolu verilen (veya varsayılan) `.json` dosyası varsa ve son içe aktarımdan beri
değiştiyse (ör. yedekten geri konduysa) içeriği indeksin yerine içe aktarılır; dosya yerinde kalır.
Her işlem kendi kısa bağlantısını açar: dosya kilidi tutulmaz ve aynı indeksin iki nesnesi
birbirinin yazdığını ezmez.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from mufettis.common.utils import normalize_title, now_iso, safe_json
from mufettis.config import INGEST_INDEX_FILE

logger = logging.getLogger(__name__)

INDEX_VERSION = "2.0"
STORE_SUFFIX = ".sqlite3"
PAGE_SIZE = 500
BUSY_TIMEOUT_S = 30.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL DEFAULT '',
    title_normalized TEXT NOT NULL DEFAULT '',
    record TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_title ON items(source_id, title_normalized);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def store_path(path: Path) -> Path:
    """Verilen yolun SQLite deposu: `.sqlite3` ise kendisi, değilse yanındaki `<ad>.sqlite3`."""
    path = Path(path)
    return path if path.suffix == STORE_SUFFIX else path.with_suffix(STORE_SUFFIX)


def _legacy_json(path: Path) -> Path:
    path = Path(path)
    return path if path.suffix == ".json" else path.with_suffix(".json")


def _legacy_items(payload: Any) -> Optional[Dict[str, Any]]:
    """Eski JSON biçimleri: şema 2.0 ("indexed_items"), daha eski "documents" ve sürümsüz düz
    {hash: kayıt} sözlüğü. Tanınmayan biçim None döner (boş indeks sayılmaz)."""
    if not isinstance(payload, dict):
        return None
    for key in ("indexed_items", "documents"):
        if isinstance(payload.get(key), dict):
            return payload[key]
    if "version" not in payload and all(isinstance(v, dict) for v in payload.values()):
        return payload
    return None


def _columns(record: Dict[str, Any]) -> Tuple[str, str]:
    return str(record.get("source_id") or ""), str(record.get("title_normalized") or "")


class _View:
    """Sayfalı, yeniden gezilebilir görünüm (`items()`, `values()`, `keys()`); `len` destekler."""

    def __init__(self, rows: Callable[[], Iterator[Any]], size: Callable[[], int]):
        self._rows = rows
        self._size = size

    def __iter__(self) -> Iterator[Any]:
        return self._rows()

    def __len__(self) -> int:
        return self._size()


class IndexedItems(MutableMapping):
    """`index.data["indexed_items"]`: SQLite deposu üzerinde sözlük görünümü (ekleme sırasıyla)."""

    def __init__(self, index: "IngestIndex"):
        self._index = index

    def __getitem__(self, file_hash: str) -> Dict[str, Any]:
        record = self._index.get(file_hash)
        if record is None:
            raise KeyError(file_hash)
        return record

    def __setitem__(self, file_hash: str, record: Dict[str, Any]) -> None:
        self._index.replace(file_hash, record)

    def __delitem__(self, file_hash: str) -> None:
        if not self._index.remove(file_hash):
            raise KeyError(file_hash)

    def __contains__(self, file_hash: object) -> bool:
        return isinstance(file_hash, str) and self._index.contains(file_hash)

    def __iter__(self) -> Iterator[str]:
        return (h for h, _ in self._index.iter_records())

    def __len__(self) -> int:
        return self._index.count()

    def keys(self):  # type: ignore[override]
        return _View(lambda: iter(self), self.__len__)

    def values(self):  # type: ignore[override]
        return _View(lambda: (r for _, r in self._index.iter_records()), self.__len__)

    def items(self):  # type: ignore[override]
        return _View(self._index.iter_records, self.__len__)

    def __repr__(self) -> str:
        return f"IndexedItems({self._index.path}, {len(self)} kayıt)"


class IngestIndex:
    @staticmethod
    def default_path() -> Path:
        """Varsayılan deponun dosyası (değişiklik zamanı önbellek anahtarı olarak kullanılabilir)."""
        return store_path(INGEST_INDEX_FILE)

    def __init__(self, path: Path = INGEST_INDEX_FILE):
        self.path = store_path(path)
        self.legacy_path = _legacy_json(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)
        self._import_legacy_if_changed()
        self.data: Dict[str, Any] = {"version": INDEX_VERSION, "indexed_items": IndexedItems(self)}

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT_S)
        try:
            with con:  # işlem: başarıda commit, hatada rollback
                yield con
        finally:
            con.close()

    # ── eski JSON'dan içe aktarma ────────────────────────────────────────────
    def _legacy_signature(self) -> str:
        stat = self.legacy_path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    def _import_legacy_if_changed(self) -> None:
        if not self.legacy_path.is_file():
            return
        signature = self._legacy_signature()
        with self._connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key = 'legacy_json_signature'").fetchone()
        if row and row[0] == signature:
            return
        # Bozuk dosya "boş indeks" sayılmaz: aksi halde tüm kayıtlar sessizce kaybolurdu.
        payload = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        items = _legacy_items(payload)
        if items is None:
            raise ValueError(f"Ingest index JSON biçimi tanınmadı: {self.legacy_path}")
        with self._connect() as con:
            con.execute("DELETE FROM items")
            con.executemany(
                "INSERT INTO items (file_hash, source_id, title_normalized, record) VALUES (?, ?, ?, ?)",
                ((str(h), *_columns(rec or {}), json.dumps(safe_json(rec or {}), ensure_ascii=False))
                 for h, rec in items.items()),
            )
            con.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", [
                ("legacy_json_signature", signature),
                ("legacy_json_imported_at", now_iso()),
                ("legacy_json_path", str(self.legacy_path)),
            ])
        logger.info("Ingest index JSON'dan içe aktarıldı: %s kayıt (%s)", len(items), self.legacy_path)

    # ── okuma ────────────────────────────────────────────────────────────────
    def get(self, file_hash: str) -> Optional[Dict[str, Any]]:
        with self._connect() as con:
            row = con.execute("SELECT record FROM items WHERE file_hash = ?", (str(file_hash),)).fetchone()
        return json.loads(row[0]) if row else None

    def contains(self, file_hash: str) -> bool:
        with self._connect() as con:
            return con.execute("SELECT 1 FROM items WHERE file_hash = ?", (str(file_hash),)).fetchone() is not None

    def count(self) -> int:
        with self._connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM items").fetchone()[0])

    def iter_records(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        """(file_hash, kayıt) çiftleri, ekleme sırasıyla. Sayfalar ayrı bağlantılarla okunur: gezinirken
        yazmak güvenlidir ve bellekte bir sayfadan fazlası tutulmaz."""
        last = 0
        while True:
            with self._connect() as con:
                rows = con.execute("SELECT seq, file_hash, record FROM items WHERE seq > ? ORDER BY seq LIMIT ?",
                                   (last, PAGE_SIZE)).fetchall()
            if not rows:
                return
            for seq, file_hash, record in rows:
                yield file_hash, json.loads(record)
            last = rows[-1][0]

    def find_by_title_and_source(self, title: str, source_id: str) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT record FROM items WHERE source_id = ? AND title_normalized = ? ORDER BY seq",
                               (str(source_id or ""), normalize_title(title))).fetchall()
        return [json.loads(r[0]) for r in rows]

    # ── yazma ────────────────────────────────────────────────────────────────
    def upsert(self, file_hash: str, meta: Dict[str, Any]) -> None:
        """Kayıtla birleştirir: verilmeyen alanlar korunur. Alan temizlemek için boş değer yazılır."""
        with self._connect() as con:
            row = con.execute("SELECT record FROM items WHERE file_hash = ?", (str(file_hash),)).fetchone()
            merged = {**(json.loads(row[0]) if row else {}), **safe_json(meta)}
            self._write(con, file_hash, merged)

    def replace(self, file_hash: str, record: Dict[str, Any]) -> None:
        """Kaydı olduğu gibi yazar (birleştirmez)."""
        with self._connect() as con:
            self._write(con, file_hash, safe_json(record))

    @staticmethod
    def _write(con: sqlite3.Connection, file_hash: str, record: Dict[str, Any]) -> None:
        con.execute(
            "INSERT INTO items (file_hash, source_id, title_normalized, record) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(file_hash) DO UPDATE SET source_id = excluded.source_id, "
            "title_normalized = excluded.title_normalized, record = excluded.record",
            (str(file_hash), *_columns(record), json.dumps(record, ensure_ascii=False)),
        )

    def remove(self, file_hash: str) -> bool:
        with self._connect() as con:
            return con.execute("DELETE FROM items WHERE file_hash = ?", (str(file_hash),)).rowcount > 0

    def save(self) -> None:
        """Uyumluluk: her yazma zaten kalıcıdır."""

    # ── yedek ────────────────────────────────────────────────────────────────
    def backup(self, target: Path) -> Path:
        """Tutarlı kopya (SQLite yedekleme arayüzü; yazma sürerken de güvenli)."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT_S)
        dest = sqlite3.connect(str(target))
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        return target

    def export_json(self, target: Path) -> Path:
        """Eski biçimde JSON dışa aktarımı (inceleme veya geri dönüş için)."""
        from mufettis.common.utils import atomic_write_json

        atomic_write_json(Path(target), {"version": INDEX_VERSION, "indexed_items": dict(self.iter_records())})
        return Path(target)


__all__ = [
    "INDEX_VERSION",
    "IndexedItems",
    "IngestIndex",
    "store_path",
]
