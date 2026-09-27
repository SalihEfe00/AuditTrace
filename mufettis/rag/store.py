# -*- coding: utf-8 -*-
"""Parça deposu: SQLite (metin + metadata) ve disk üzerinde kesin vektör taraması.

Chroma'nın yerine geçebilecek, koleksiyon arayüzünün projede kullanılan alt kümesini
(`get`, `query`, `upsert`, `update`, `delete`, `count`) aynı sonuç biçimi ve aynı anlamla sunar;
çağıran kod değişmeden depo değiştirilebilir. Anlam Chroma 1.x ile karşılaştırmalı testlerle
sabitlenir (tests/test_chunk_store.py).

Neden: Chroma parça başına ~13 KB disk (metadata'yı anahtar başına satır olarak indeksler, tam
metin trigram indeksi tutar) ve silinen öğeleri geri kazanmayan bellek içi bir HNSW grafiği kullanır;
tam mevzuat ölçeğinde (~1M parça) 8 GB RAM'li bir laptopta taşınamaz.

Depolama (dizin `path`):
- `store.sqlite3`:
  - `chunks`: kimlik, belge anahtarı, metin, parçaya özgü metadata (JSONB), taban, vektör satırı.
  - `bases`: belge düzeyindeki ortak metadata (içerik adresli; aynı içerik tek satır). Bir parçanın
    metadata'sı = tabanı ∪ kendi alanları; bir anahtar bu ikisinden yalnızca birinde bulunur. Parça
    başına ~3 KB olan metadata böylece belge başına bir kez saklanır ve filtreler binlerce taban
    satırında değerlendirilip parçalara indeksle inilir (tüm parçaların JSON'u taranmaz).
  - `delta_keys`: herhangi bir parçanın kendi alanlarında görülmüş anahtarlar (filtre bu anahtarlarda
    parça alanlarına da bakar; diğerlerinde yalnızca tabana).
- `vec_i8[.<küme>].bin` (satır başına `dim` bayt int8), `vec_f16…` (float16), `vec_aux…`
  (satır başına float32 ölçek ve ‖v‖²). Yalnızca sona eklenir; bir parçanın vektörü değişince yeni
  satır yazılır, eskisi çöp kalır (`garbage_rows`, `compact`). Ekleme SQLite yazma kilidi altında
  yapılır (süreçler sırayla yazar); yarıda kalan bir yazma yalnızca kullanılmayan satır bırakır.
  Sıkıştırma canlı satırları yeni dosya kümesine kopyalar ve kümeye tek işlemle geçer.

Arama kesindir (yaklaşık indeks yok): tüm aday satırlar int8 ile taranır, en iyi adaylar float16 ile
yeniden puanlanır. 1M parçada bu laptopta ~0.5 sn/sorgu; float32 kesin sonuçla örtüşme ilk 10'da
%99.7, ilk 50'de %99.95 (55 bin gerçek vektörle ölçüldü). Mesafe Chroma varsayılanı gibi L2'nin
karesidir.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

import numpy as np

from mufettis.config import CHROMA_COLLECTION, CORPUS_STORE_PATH, DB_PATH, VECTOR_STORE

STORE_FILE = "store.sqlite3"
# Chroma'nın kalıcı istemcisinin veritabanı dizininde oluşturduğu dosya (eski depo işareti).
CHROMA_DB_FILE = "chroma.sqlite3"
STORE_SCHEMA = "2"  # 2: belge düzeyindeki metadata tabanlarda
SCAN_BLOCK_ROWS = 65536
RESCORE_FACTOR = 4          # int8 taramasından float16 yeniden puanlamaya giden aday çarpanı
RESCORE_MIN = 200
COMPACT_MIN_GARBAGE = 10_000  # kendiliğinden sıkıştırma: en az bu kadar ve canlı satırların
COMPACT_GARBAGE_RATIO = 0.3    # bu oranı kadar çöp satır (toplam maliyet dosya boyutuyla orantılı kalır)
FILTER_CACHE_SIZE = 16      # sorgu filtresi → aday satırlar önbelleği (filtre başına ≤ 8 bayt/satır)
BUSY_TIMEOUT_S = 30.0

GET_DEFAULT_INCLUDE = ("metadatas", "documents")
QUERY_DEFAULT_INCLUDE = ("metadatas", "documents", "distances")

# Kimlik listeleri tek JSON parametresiyle bağlanır (SQLite değişken sayısı sınırına takılmaz).
_ID_IN = "c.id IN (SELECT value FROM json_each(?))"
_ROW_COLUMNS = "c.id, c.document, c.base, json(c.metadata), c.vec"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bases (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    metadata BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    file_hash TEXT,
    document TEXT,
    base INTEGER REFERENCES bases(id),
    metadata BLOB NOT NULL,
    vec INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_file_hash ON chunks(file_hash);
CREATE INDEX IF NOT EXISTS chunks_vec ON chunks(vec);
CREATE INDEX IF NOT EXISTS chunks_base_vec ON chunks(base, vec);
CREATE TABLE IF NOT EXISTS delta_keys (key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


# ── metadata değerleri ───────────────────────────────────────────────────────
def _typed(value: Any) -> Tuple[str, Any]:
    """Tipe duyarlı karşılaştırma anahtarı (Python'da True == 1 ve 1 == 1.0; Chroma'da değil)."""
    return type(value).__name__, value


def _clean_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = {}
    for key, value in (meta or {}).items():
        if value is None:
            continue
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"Metadata değeri str/int/float/bool olmalı: {key}={type(value).__name__}")
        out[str(key)] = value
    return out


def _merge_meta(old: Dict[str, Any], patch: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Chroma `update`/`upsert` anlamı: verilen anahtarlar yazılır, None anahtarı siler."""
    merged = dict(old)
    for key, value in (patch or {}).items():
        if value is None:
            merged.pop(str(key), None)
        else:
            merged.update(_clean_meta({key: value}))
    return merged


def _contains(meta: Dict[str, Any], part: Dict[str, Any]) -> bool:
    return all(k in meta and _typed(meta[k]) == _typed(v) for k, v in part.items())


def _common(metas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Tüm kayıtlarda aynı (tipiyle) değeri taşıyan alanlar."""
    first = metas[0]
    return {k: v for k, v in first.items() if all(k in m and _typed(m[k]) == _typed(v) for m in metas[1:])}


# ── filtre (Chroma `where` dili → SQL) ──────────────────────────────────────
def _path(key: str) -> str:
    return '$."' + str(key).replace('"', '\\"') + '"'


def _eq(col: str, key: str, value: Any, params: List[Any]) -> str:
    """Tipe duyarlı eşitlik; anahtarı olmayan kayıtta NULL değil yanlış (0) döner."""
    path = _path(key)
    if isinstance(value, bool):
        params.append(path)
        return f"IFNULL(json_type({col}, ?) = '{'true' if value else 'false'}', 0)"
    if isinstance(value, int):
        kind = "'integer'"
    elif isinstance(value, float):
        kind = "'real'"
    elif isinstance(value, str):
        kind = "'text'"
    else:
        raise ValueError(f"Desteklenmeyen filtre değeri: {key}={value!r}")
    params.extend([path, path, value])
    return f"IFNULL(json_type({col}, ?) = {kind} AND json_extract({col}, ?) = ?, 0)"


def _compare(col: str, key: str, op: str, value: Any, params: List[Any]) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{op} yalnızca sayılarla kullanılır: {key}")
    path = _path(key)
    params.extend([path, path, value])
    sign = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}[op]
    return f"IFNULL(json_type({col}, ?) IN ('integer', 'real') AND json_extract({col}, ?) {sign} ?, 0)"


class _Where:
    """Chroma `where` → SQL (`chunks c` üzerinde).

    Bir anahtar bir kayıtta ya tabanda ya parçanın kendi alanlarında bulunur. Eksik anahtarda yanlış
    olan her temel koşul için birleşik kayıtta: parça alanında doğru VEYA tabanında doğru. Taban
    tarafı `bases` üzerinde tek alt sorgudur (belge başına bir satır); parça tarafı yalnızca anahtar
    herhangi bir parçanın kendi alanlarında görülmüşse eklenir. `$ne`/`$nin` bu koşulun değilidir,
    dolayısıyla anahtarı olmayan kayıtları da kapsar (Chroma gibi)."""

    def __init__(self, delta_keys: Set[str]):
        self.delta_keys = delta_keys
        self.params: List[Any] = []

    def _atom(self, key: str, build: Callable[[str, List[Any]], str]) -> str:
        parts = []
        if key in self.delta_keys:
            parts.append(build("c.metadata", self.params))
        parts.append(f"IFNULL(c.base IN (SELECT id FROM bases WHERE {build('metadata', self.params)}), 0)")
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"

    def _any_eq(self, key: str, values: List[Any]) -> str:
        if key == "file_hash" and all(isinstance(v, str) for v in values):
            self.params.extend(values)  # sütun: NULL'a karşı güvenli ve indeksli
            return "(" + " OR ".join("c.file_hash IS ?" for _ in values) + ")"
        return self._atom(key, lambda col, p: "(" + " OR ".join(_eq(col, key, v, p) for v in values) + ")")

    def sql(self, where: Dict[str, Any]) -> str:
        if not isinstance(where, dict) or len(where) != 1:
            raise ValueError(f"Filtre tek anahtarlı sözlük olmalı: {where!r}")
        (key, cond), = where.items()
        if key in ("$and", "$or"):
            if not isinstance(cond, list) or not cond:
                raise ValueError(f"{key} boş olmayan liste ister")
            joiner = " AND " if key == "$and" else " OR "
            return "(" + joiner.join(self.sql(c) for c in cond) + ")"
        if not isinstance(cond, dict):
            return self._any_eq(key, [cond])
        if len(cond) != 1:
            raise ValueError(f"Koşul tek işleçli olmalı: {key}={cond!r}")
        (op, value), = cond.items()
        if op == "$eq":
            return self._any_eq(key, [value])
        if op == "$ne":
            return f"NOT {self._any_eq(key, [value])}"
        if op in ("$in", "$nin"):
            if not isinstance(value, list) or not value:
                raise ValueError(f"{op} boş olmayan liste ister: {key}")
            any_eq = self._any_eq(key, value)
            return any_eq if op == "$in" else f"NOT {any_eq}"
        if op in ("$gt", "$gte", "$lt", "$lte"):
            return self._atom(key, lambda col, p: _compare(col, key, op, value, p))
        raise ValueError(f"Desteklenmeyen işleç: {op}")


def _result(include: Sequence[str], rows: List[Tuple[str, Optional[str], Dict[str, Any]]],
            embeddings: Optional[np.ndarray]) -> Dict[str, Any]:
    return {
        "ids": [r[0] for r in rows],
        "documents": [r[1] for r in rows] if "documents" in include else None,
        "metadatas": [r[2] or None for r in rows] if "metadatas" in include else None,
        "embeddings": embeddings if "embeddings" in include else None,
        "uris": None,
        "data": None,
        "included": list(include),
    }


Row = Tuple[str, Optional[str], Dict[str, Any], int]


class SqliteVectorCollection:
    """Chroma koleksiyonu yerine geçen, SQLite + disk vektörlü parça deposu."""

    def __init__(self, path: Path, name: str = "mevzuat"):
        self.path = Path(path)
        self.name = name
        self.path.mkdir(parents=True, exist_ok=True)
        self.db_file = self.path / STORE_FILE
        self._lock = threading.RLock()
        self._filter_cache: "OrderedDict[Tuple[str, int], np.ndarray]" = OrderedDict()
        with self._connect() as con:
            columns = {r[1] for r in con.execute("PRAGMA table_info(chunks)")}
            if columns and "base" not in columns:
                raise RuntimeError(f"Parça deposu eski biçimde: {self.path}. "
                                   "scripts/migrate_chroma_to_sqlite.py --replace ile yeniden kurun.")
            con.executescript(_SCHEMA)
            con.execute("INSERT OR IGNORE INTO store_meta (key, value) VALUES ('schema', ?)", (STORE_SCHEMA,))
            row = con.execute("SELECT value FROM store_meta WHERE key = 'dim'").fetchone()
        self.dim: Optional[int] = int(row[0]) if row else None

    # ── altyapı ──────────────────────────────────────────────────────────────
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(str(self.db_file), timeout=BUSY_TIMEOUT_S)
        try:
            with con:
                yield con
        finally:
            con.close()

    @contextmanager
    def _txn(self, mode: str = "DEFERRED") -> Iterator[sqlite3.Connection]:
        """Açık işlem. DEFERRED: tek anlık görüntüden tutarlı okuma. IMMEDIATE: süreçler arası yazma
        kilidi; vektör dosyasına ekleme de bu kilit altında yapılır (iki süreç aynı satırları almaz)."""
        con = sqlite3.connect(str(self.db_file), timeout=BUSY_TIMEOUT_S, isolation_level=None)
        try:
            con.execute(f"BEGIN {mode}")
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        finally:
            con.close()

    def _vf(self, kind: str, fileset: int) -> Path:
        """Vektör dosyası: küme 0 `vec_<tür>.bin`, sonrakiler `vec_<tür>.<küme>.bin`."""
        return self.path / (f"vec_{kind}.bin" if fileset == 0 else f"vec_{kind}.{fileset}.bin")

    @staticmethod
    def _state(con: sqlite3.Connection) -> Tuple[int, int]:
        """(yazma sayacı, geçerli vektör dosya kümesi)."""
        values = dict(con.execute("SELECT key, value FROM store_meta WHERE key IN ('generation', 'fileset')"))
        return int(values.get("generation", 0)), int(values.get("fileset", 0))

    @staticmethod
    def _where(con: sqlite3.Connection, where: Optional[Dict[str, Any]]) -> Tuple[str, List[Any]]:
        if not where:
            return "", []
        compiler = _Where({r[0] for r in con.execute("SELECT key FROM delta_keys")})
        return compiler.sql(where), compiler.params

    def _vector_rows(self, fileset: int) -> int:
        aux = self._vf("aux", fileset)
        return aux.stat().st_size // 8 if aux.exists() else 0

    def vector_rows(self) -> int:
        with self._connect() as con:
            return self._vector_rows(self._state(con)[1])

    @staticmethod
    def _bump(con: sqlite3.Connection) -> None:
        con.execute("INSERT INTO store_meta (key, value) VALUES ('generation', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")

    def _append_vectors(self, con: sqlite3.Connection, vectors: np.ndarray) -> int:
        """Vektörleri geçerli kümenin sonuna ekler, ilk satır numarasını döndürür. Yazma kilidi
        (IMMEDIATE işlem) altında çağrılır."""
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim != 2:
            raise ValueError("Gömme listesi iki boyutlu olmalı")
        if self.dim is None:
            row = con.execute("SELECT value FROM store_meta WHERE key = 'dim'").fetchone()
            self.dim = int(row[0]) if row else int(vectors.shape[1])
            con.execute("INSERT OR IGNORE INTO store_meta (key, value) VALUES ('dim', ?)", (str(self.dim),))
        if vectors.shape[1] != self.dim:
            raise ValueError(f"Gömme boyutu {vectors.shape[1]}, depo boyutu {self.dim}")
        peak = np.abs(vectors).max(axis=1)
        scale = np.where(peak > 0, peak / 127.0, 1.0).astype(np.float32)
        q8 = np.round(vectors / scale[:, None]).astype(np.int8)
        aux = np.stack([scale, np.einsum("ij,ij->i", vectors, vectors)], axis=1).astype(np.float32)
        fileset = self._state(con)[1]
        first = self._vector_rows(fileset)
        # Yarım kalmış bir eklemeden sonra dosyalar aynı satırda hizalanır (aux en son yazılır).
        for kind, width in (("i8", self.dim), ("f16", 2 * self.dim)):
            f = self._vf(kind, fileset)
            if f.exists() and f.stat().st_size != first * width:
                with open(f, "r+b") as h:
                    h.truncate(first * width)
        for kind, data in (("i8", q8), ("f16", vectors.astype(np.float16)), ("aux", aux)):
            with open(self._vf(kind, fileset), "ab") as h:
                h.write(np.ascontiguousarray(data).tobytes())
                h.flush()
                os.fsync(h.fileno())
        return first

    # ── okuma ────────────────────────────────────────────────────────────────
    def generation(self) -> int:
        """Kalıcı yazma sayacı: her ekleme, güncelleme, silme ve sıkıştırmada artar (türetilmiş
        indekslerin tazelik imzası; başka süreçlerin yazmalarını da görür)."""
        with self._connect() as con:
            return self._state(con)[0]

    def count(self) -> int:
        with self._connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    @staticmethod
    def _bases(con: sqlite3.Connection, ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
        wanted = sorted({int(i) for i in ids if i is not None})
        if not wanted:
            return {}
        return {int(bid): json.loads(meta) for bid, meta in con.execute(
            "SELECT id, json(metadata) FROM bases WHERE id IN (SELECT value FROM json_each(?))", [json.dumps(wanted)])}

    def _fetch(self, con: sqlite3.Connection, sql: str, params: Sequence[Any]) -> List[Tuple[str, Optional[str], Optional[int], Dict[str, Any], int]]:
        """(kimlik, metin, taban, parça alanları, vektör satırı); `sql` `_ROW_COLUMNS` seçer."""
        return [(r[0], r[1], r[2], json.loads(r[3]), int(r[4])) for r in con.execute(sql, params)]

    def _merged(self, con: sqlite3.Connection, raw) -> List[Row]:
        bases = self._bases(con, (r[2] for r in raw))
        return [(rid, doc, {**bases.get(base, {}), **delta}, vec) for rid, doc, base, delta, vec in raw]

    def _read_rows(self, sql: str, params: Sequence[Any]) -> Tuple[List[Row], int]:
        """(satırlar, vektör kümesi) aynı anlık görüntüden: satırların vektör numaraları o kümeye aittir."""
        with self._txn() as con:
            rows = self._merged(con, self._fetch(con, sql, params))
            fileset = self._state(con)[1]
        return rows, fileset

    def get(self, ids: Optional[Sequence[str]] = None, where: Optional[Dict[str, Any]] = None,
            limit: Optional[int] = None, offset: Optional[int] = None,
            include: Sequence[str] = GET_DEFAULT_INCLUDE, where_document: Any = None) -> Dict[str, Any]:
        if where_document:
            raise ValueError("where_document desteklenmiyor (tam metin indeksi yok)")
        if ids is not None and len(ids) == 0:
            raise ValueError("Kimlik listesi boş olamaz")
        with self._txn() as con:
            clause, params = self._where(con, where)
            clauses = [clause] if clause else []
            if ids is not None:
                clauses.append(_ID_IN)
                params.append(json.dumps([str(i) for i in ids], ensure_ascii=False))
            sql = f"SELECT {_ROW_COLUMNS} FROM chunks c"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY c.seq"  # Chroma gibi: ekleme sırası (istenen kimlik sırası değil)
            if limit is not None or offset:
                sql += " LIMIT ? OFFSET ?"
                params.extend([-1 if limit is None else int(limit), int(offset or 0)])
            rows = self._merged(con, self._fetch(con, sql, params))
            fileset = self._state(con)[1]
        embeddings = self._embeddings([r[3] for r in rows], fileset) if "embeddings" in include else None
        return _result(include, [(r[0], r[1], r[2]) for r in rows], embeddings)

    def _embeddings(self, vec_rows: List[int], fileset: int) -> np.ndarray:
        if not vec_rows:
            return np.empty((0, self.dim or 0), dtype=np.float32)
        f16 = np.memmap(self._vf("f16", fileset), dtype=np.float16, mode="r").reshape(-1, self.dim)
        try:
            return np.asarray(f16[np.asarray(vec_rows)], dtype=np.float32)
        finally:
            del f16

    # ── arama ────────────────────────────────────────────────────────────────
    def _candidates(self, where: Optional[Dict[str, Any]]) -> Tuple[np.ndarray, int]:
        """(filtreye uyan parçaların vektör satırları (sıralı), vektör kümesi). Yazma olmadıkça
        önbellekten gelir."""
        key = json.dumps(where, sort_keys=True, ensure_ascii=False) if where else ""
        with self._txn() as con:
            generation, fileset = self._state(con)
            cached = self._filter_cache.get((key, generation))
            if cached is None:
                clause, params = self._where(con, where)
                sql = "SELECT c.vec FROM chunks c" + (f" WHERE {clause}" if clause else "")
                cached = np.fromiter((r[0] for r in con.execute(sql, params)), dtype=np.int64)
                cached.sort()
        with self._lock:
            self._filter_cache[(key, generation)] = cached
            self._filter_cache.move_to_end((key, generation))
            while len(self._filter_cache) > FILTER_CACHE_SIZE:
                self._filter_cache.popitem(last=False)
        return cached, fileset

    def _nearest(self, query: np.ndarray, rows: np.ndarray, k: int, fileset: int) -> Tuple[np.ndarray, np.ndarray]:
        """(vektör satırları, L2² mesafeleri), en yakından uzağa."""
        if rows.size == 0 or k <= 0:
            return np.empty(0, np.int64), np.empty(0, np.float32)
        total = self._vector_rows(fileset)
        i8 = np.memmap(self._vf("i8", fileset), dtype=np.int8, mode="r", shape=(total, self.dim))
        f16 = np.memmap(self._vf("f16", fileset), dtype=np.float16, mode="r", shape=(total, self.dim))
        aux = np.fromfile(self._vf("aux", fileset), dtype=np.float32, count=2 * total).reshape(-1, 2)
        try:
            keep = max(RESCORE_MIN, RESCORE_FACTOR * k)
            best_rows, best_scores = [], []
            dense = rows.size > total // 4
            for start in range(0, total, SCAN_BLOCK_ROWS):
                stop = min(total, start + SCAN_BLOCK_ROWS)
                lo, hi = np.searchsorted(rows, [start, stop])
                if lo == hi:
                    continue
                block_rows = rows[lo:hi]
                block = i8[start:stop][block_rows - start] if dense else i8[block_rows]
                # L2 sıralaması: ‖v‖² − 2 q·v (‖q‖² tüm adaylar için sabit)
                scores = 2.0 * (block.astype(np.float32) @ query) * aux[block_rows, 0] - aux[block_rows, 1]
                take = min(keep, scores.size)
                top = np.argpartition(-scores, take - 1)[:take]
                best_rows.append(block_rows[top])
                best_scores.append(scores[top])
            cand = np.concatenate(best_rows)
            scores = np.concatenate(best_scores)
            if cand.size > keep:
                cand = cand[np.argpartition(-scores, keep - 1)[:keep]]
            cand.sort()
            exact = np.asarray(f16[cand], dtype=np.float32)
            dist = float(query @ query) + np.einsum("ij,ij->i", exact, exact) - 2.0 * (exact @ query)
            order = np.lexsort((cand, dist))[:k]
            return cand[order], np.maximum(dist[order], 0.0).astype(np.float32)
        finally:
            del i8, f16

    def query(self, query_embeddings: Sequence[Sequence[float]], n_results: int = 10,
              where: Optional[Dict[str, Any]] = None, include: Sequence[str] = QUERY_DEFAULT_INCLUDE,
              where_document: Any = None, query_texts: Any = None) -> Dict[str, Any]:
        if query_texts is not None or where_document:
            raise ValueError("Yalnızca query_embeddings ve metadata filtresi desteklenir")
        for _attempt in range(3):
            result = self._query_once(query_embeddings, int(n_results), where, include)
            if result is not None:
                return result
        raise RuntimeError("Arama sırasında vektör dosyaları tekrar tekrar sıkıştırıldı")

    def _query_once(self, query_embeddings, n_results, where, include) -> Optional[Dict[str, Any]]:
        """Arama sırasında sıkıştırma olduysa (vektör numaraları değişti) None döner."""
        out: Dict[str, List[Any]] = {"ids": [], "documents": [], "metadatas": [], "distances": [], "embeddings": []}
        if self.dim is None:
            with self._connect() as con:
                row = con.execute("SELECT value FROM store_meta WHERE key = 'dim'").fetchone()
            self.dim = int(row[0]) if row else None
        candidates, fileset = self._candidates(where) if self.dim else (np.empty(0, np.int64), 0)
        for q in query_embeddings:
            vec_rows, dist = self._nearest(np.asarray(q, dtype=np.float32), candidates, n_results, fileset)
            rows, now = self._read_rows(
                f"SELECT {_ROW_COLUMNS} FROM chunks c WHERE c.vec IN (SELECT value FROM json_each(?))",
                [json.dumps([int(v) for v in vec_rows])])
            if now != fileset:
                return None
            by_vec = {r[3]: r for r in rows}
            # Arada silinen ya da vektörü değişen parça sonuçtan düşer.
            hits = [(by_vec[int(v)], float(d)) for v, d in zip(vec_rows, dist) if int(v) in by_vec]
            out["ids"].append([h[0][0] for h in hits])
            out["documents"].append([h[0][1] for h in hits])
            out["metadatas"].append([h[0][2] or None for h in hits])
            out["distances"].append([h[1] for h in hits])
            out["embeddings"].append(self._embeddings([h[0][3] for h in hits], fileset) if "embeddings" in include else None)
        return {
            "ids": out["ids"],
            **{key: (out[key] if key in include else None) for key in ("documents", "metadatas", "distances", "embeddings")},
            "uris": None,
            "data": None,
            "included": list(include),
        }

    # ── yazma ────────────────────────────────────────────────────────────────
    @staticmethod
    def _intern_base(con: sqlite3.Connection, base: Dict[str, Any], cache: Dict[str, int]) -> Optional[int]:
        if not base:
            return None
        text = json.dumps(base, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest not in cache:
            con.execute("INSERT OR IGNORE INTO bases (hash, metadata) VALUES (?, jsonb(?))", (digest, text))
            cache[digest] = int(con.execute("SELECT id FROM bases WHERE hash = ?", (digest,)).fetchone()[0])
        return cache[digest]

    def _split(self, con: sqlite3.Connection, targets: List[Tuple[Dict[str, Any], Optional[str], Optional[int]]],
               bases: Dict[int, Dict[str, Any]]) -> List[Tuple[Optional[int], Dict[str, Any]]]:
        """Her kayıt için (taban, parça alanları). targets: (birleşik metadata, belge anahtarı, eski taban).

        - Tabanı olan kayıt tabanını korur; taban alanlarından değişen değerler tabana yazılır, silinenler
          tabandan düşer (aynı belgenin tüm parçaları aynı güncellemeyi alınca aynı yeni tabanda buluşur).
          Yeni alanlar parçanın kendi alanlarına gider.
        - Tabanı olmayan kayıtlar belge anahtarına göre gruplanır: gruptaki ortak alanlar taban olur; tek
          kayıtlık grupta aynı belgenin mevcut tabanlarından kayda uyan en genişi, yoksa kaydın tamamı.
        - Belge anahtarı olmayan kayıtta tabanın kendisi kayıttır."""
        cache: Dict[str, int] = {}
        out: List[Optional[Tuple[Optional[int], Dict[str, Any]]]] = [None] * len(targets)
        groups: Dict[str, List[int]] = defaultdict(list)
        for n, (meta, file_hash, old_base) in enumerate(targets):
            if old_base is not None:
                base = {k: meta[k] for k in bases.get(old_base, {}) if k in meta}
                base_id = old_base if base == bases.get(old_base) and all(
                    _typed(base[k]) == _typed(bases[old_base][k]) for k in base) else self._intern_base(con, base, cache)
                out[n] = (base_id, {k: v for k, v in meta.items() if k not in base})
            elif file_hash:
                groups[file_hash].append(n)
            else:
                out[n] = (self._intern_base(con, targets[n][0], cache), {})
        for file_hash, members in groups.items():
            metas = [targets[n][0] for n in members]
            if len(members) > 1:
                base = _common(metas)
            else:
                known = [r[0] for r in con.execute(
                    "SELECT DISTINCT base FROM chunks WHERE file_hash IS ? AND base IS NOT NULL", (file_hash,))]
                bases.update(self._bases(con, (b for b in known if b not in bases)))
                fitting = [bases[b] for b in known if b in bases and _contains(metas[0], bases[b])]
                # Uyan taban yoksa kaydın tamamı taban olur: parça alanları yalnızca belge içinde gerçekten
                # değişen anahtarları taşır ve `delta_keys` küçük kalır (filtreler tabanda değerlendirilir).
                base = max(fitting, key=len) if fitting else dict(metas[0])
            base_id = self._intern_base(con, base, cache)
            for n in members:
                out[n] = (base_id, {k: v for k, v in targets[n][0].items() if k not in base})
        return out  # type: ignore[return-value]

    def _write(self, ids: Sequence[str], embeddings: Any, documents: Any, metadatas: Any,
               create: bool, update: bool, op: str) -> None:
        ids = [str(i) for i in ids]
        if not ids:
            return
        if len(set(ids)) != len(ids):
            raise ValueError(f"Aynı kimlik bir yazmada birden fazla kez verildi ({op})")
        for name, values in (("embeddings", embeddings), ("documents", documents), ("metadatas", metadatas)):
            if values is not None and len(values) != len(ids):
                raise ValueError(f"{name} uzunluğu kimlik sayısıyla aynı olmalı")
        if documents is not None and embeddings is None:
            # Chroma bu durumda vektörü kendi varsayılan modeliyle yeniden hesaplar; burada eski
            # vektörün yeni metinle sessizce eşleşmesi yerine açık hata verilir.
            raise ValueError("Metin değişirken gömme vektörü de verilmeli")
        with self._lock, self._txn("IMMEDIATE") as con:
            raw = self._fetch(con, f"SELECT {_ROW_COLUMNS} FROM chunks c WHERE {_ID_IN}", [json.dumps(ids, ensure_ascii=False)])
            bases = self._bases(con, (r[2] for r in raw))
            existing = {rid: (doc, base, {**bases.get(base, {}), **delta}, vec) for rid, doc, base, delta, vec in raw}
            if create and embeddings is None and any(i not in existing for i in ids):
                raise ValueError("Yeni parça gömme vektörü olmadan eklenemez")
            first_vec = self._append_vectors(con, embeddings) if embeddings is not None else None
            chosen, targets = [], []
            for n, rid in enumerate(ids):
                old = existing.get(rid)
                if old is None and not create:
                    continue  # Chroma: olmayan kimliği güncellemek sessizce atlanır
                if old is not None and not update:
                    continue
                meta = _merge_meta(old[2] if old else {}, metadatas[n] if metadatas is not None else None)
                file_hash = meta.get("file_hash") if isinstance(meta.get("file_hash"), str) else None
                chosen.append((n, rid, old, file_hash))
                targets.append((meta, file_hash, old[1] if old else None))
            if not chosen:
                return
            rows, delta_keys = [], set()
            for (n, rid, old, file_hash), (base_id, delta) in zip(chosen, self._split(con, targets, bases)):
                doc = documents[n] if documents is not None else (old[0] if old else None)
                vec = first_vec + n if first_vec is not None else old[3]
                delta_keys.update(delta)
                rows.append((rid, file_hash, doc, base_id, json.dumps(delta, ensure_ascii=False), vec))
            con.executemany("INSERT OR IGNORE INTO delta_keys (key) VALUES (?)", ((k,) for k in sorted(delta_keys)))
            con.executemany(
                "INSERT INTO chunks (id, file_hash, document, base, metadata, vec) VALUES (?, ?, ?, ?, jsonb(?), ?) "
                "ON CONFLICT(id) DO UPDATE SET file_hash = excluded.file_hash, document = excluded.document, "
                "base = excluded.base, metadata = excluded.metadata, vec = excluded.vec",
                rows,
            )
            self._bump(con)

    def upsert(self, ids: Sequence[str], embeddings: Any = None, documents: Any = None,
               metadatas: Any = None, **_: Any) -> None:
        self._write(ids, embeddings, documents, metadatas, create=True, update=True, op="upsert")

    def add(self, ids: Sequence[str], embeddings: Any = None, documents: Any = None,
            metadatas: Any = None, **_: Any) -> None:
        self._write(ids, embeddings, documents, metadatas, create=True, update=False, op="add")

    def update(self, ids: Sequence[str], embeddings: Any = None, documents: Any = None,
               metadatas: Any = None, **_: Any) -> None:
        self._write(ids, embeddings, documents, metadatas, create=False, update=True, op="update")

    def delete(self, ids: Optional[Sequence[str]] = None, where: Optional[Dict[str, Any]] = None,
               **_: Any) -> Dict[str, int]:
        if not where and ids is None:
            raise ValueError("Silme için kimlik veya filtre gerekir")
        with self._lock, self._txn("IMMEDIATE") as con:
            clause, params = self._where(con, where)
            clauses = [clause] if clause else []
            if ids is not None:
                clauses.append(_ID_IN)
                params.append(json.dumps([str(i) for i in ids]))
            deleted = con.execute("DELETE FROM chunks WHERE seq IN (SELECT c.seq FROM chunks c WHERE "
                                  + " AND ".join(clauses) + ")", params).rowcount
            if deleted:
                self._bump(con)
        return {"deleted": int(deleted)}

    # ── bakım ────────────────────────────────────────────────────────────────
    def garbage_rows(self) -> int:
        """Hiçbir parçanın kullanmadığı vektör satırları (sıkıştırmayla geri kazanılır)."""
        with self._txn() as con:
            fileset = self._state(con)[1]
            live = int(con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return self._vector_rows(fileset) - live

    def compact_if_needed(self) -> Optional[Dict[str, int]]:
        garbage = self.garbage_rows()
        if garbage >= max(COMPACT_MIN_GARBAGE, COMPACT_GARBAGE_RATIO * self.count()):
            return self.compact()
        return None

    def compact(self) -> Dict[str, int]:
        """Kullanılmayan vektör satırlarını ve tabanları atar. Canlı satırlar yeni bir dosya kümesine
        sırayla kopyalanır; kümeye geçiş, parçaların yeni satır numaralarıyla birlikte tek işlemde
        yapılır ve yarıda kesilirse eski küme geçerli kalır. Önceki küme, o sırada okuyan sorgular için
        bir sonraki sıkıştırmaya kadar durur; daha eskileri silinir. Sıkıştırma boyunca yazmalar bekler."""
        with self._lock, self._txn("IMMEDIATE") as con:
            con.execute("DELETE FROM bases WHERE id NOT IN (SELECT base FROM chunks WHERE base IS NOT NULL)")
            # delta_keys yalnızca büyür; burada gerçekte kullanılan anahtarlara indirilir.
            con.execute("DELETE FROM delta_keys")
            con.execute("INSERT INTO delta_keys (key) SELECT DISTINCT j.key FROM chunks, json_each(chunks.metadata) AS j")
            _, fileset = self._state(con)
            total = self._vector_rows(fileset)
            live = con.execute("SELECT seq, vec FROM chunks ORDER BY vec").fetchall()
            if len(live) == total:
                return {"removed": 0, "rows": total, "fileset": fileset}
            new = fileset + 1
            old_rows = np.fromiter((vec for _, vec in live), dtype=np.int64, count=len(live))
            for kind, width, dtype in (("i8", self.dim, np.int8), ("f16", self.dim, np.float16), ("aux", 2, np.float32)):
                src = np.memmap(self._vf(kind, fileset), dtype=dtype, mode="r", shape=(total, width))
                try:
                    with open(self._vf(kind, new), "wb") as h:
                        for start in range(0, old_rows.size, SCAN_BLOCK_ROWS):
                            h.write(np.ascontiguousarray(src[old_rows[start:start + SCAN_BLOCK_ROWS]]).tobytes())
                        h.flush()
                        os.fsync(h.fileno())
                finally:
                    del src
            con.executemany("UPDATE chunks SET vec = ? WHERE seq = ?", ((i, seq) for i, (seq, _) in enumerate(live)))
            con.execute("INSERT INTO store_meta (key, value) VALUES ('fileset', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(new),))
            self._bump(con)
        self._remove_filesets(keep={fileset, new})
        return {"removed": total - len(live), "rows": len(live), "fileset": new}

    def backup(self, target: Path) -> Path:
        """Tutarlı yedek: yazma kilidi tutulurken SQLite yedekleme arayüzüyle veritabanı ve geçerli
        vektör dosya kümesi (aynı adlarla) kopyalanır. Yedek dizini doğrudan depo olarak açılabilir."""
        import shutil

        target = Path(target)
        target.mkdir(parents=True, exist_ok=False)
        with self._lock, self._txn("IMMEDIATE") as con:
            fileset = self._state(con)[1]
            dest = sqlite3.connect(str(target / STORE_FILE))
            source = sqlite3.connect(str(self.db_file), timeout=BUSY_TIMEOUT_S)
            try:
                source.backup(dest)
            finally:
                source.close()
                dest.close()
            for kind in ("i8", "f16", "aux"):
                if self._vf(kind, fileset).exists():
                    shutil.copy2(self._vf(kind, fileset), target / self._vf(kind, fileset).name)
        return target

    def _remove_filesets(self, keep: set) -> None:
        for f in self.path.glob("vec_*.bin"):
            parts = f.name.split(".")
            number = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else 0
            if number not in keep:
                try:
                    f.unlink()
                except OSError:  # Windows: okuyan bir süreç dosyayı hâlâ eşlemiş olabilir; sonra silinir
                    pass


def store_root_for(db_path: Path) -> Path:
    """Bir veritabanı yolunun SQLite parça deposu kökü: varsayılan yol için `CORPUS_STORE_PATH`,
    başka yollar (ör. test laboratuvarı) için `<yol>/corpus_store`."""
    db_path = Path(db_path)
    return CORPUS_STORE_PATH if db_path.resolve() == Path(DB_PATH).resolve() else db_path / "corpus_store"


def store_file_for(db_path: Path, name: str = CHROMA_COLLECTION) -> Path:
    return store_root_for(db_path) / name / STORE_FILE


def resolve_store_kind(requested: Optional[str], db_path: Path) -> str:
    """"sqlite" veya "chroma". "auto": SQLite deposu varsa o; yalnızca taşınmamış bir Chroma
    veritabanı varsa Chroma; hiçbiri yoksa (yeni kurulum) SQLite. Böylece yeni kurulumlar chromadb
    gerektirmez; Chroma yalnızca eski veritabanını okumak ve taşımak içindir."""
    kind = (requested or VECTOR_STORE or "auto").strip().lower()
    if kind == "auto":
        if store_file_for(db_path).is_file():
            return "sqlite"
        return "chroma" if (Path(db_path) / CHROMA_DB_FILE).is_file() else "sqlite"
    if kind not in ("sqlite", "chroma"):
        raise ValueError(f"Bilinmeyen parça deposu: {kind}")
    return kind


def backup_corpus_store(target: Path, db_path: Path = DB_PATH) -> Optional[Path]:
    """Veritabanı yolunun SQLite parça deposunu (varsa) `target/corpus_store` altına yedekler.
    Veri onaran betikler Chroma dizinini yedeklerken bunu da çağırır: hangi depo etkin olursa olsun
    değiştirilen veri yedekte bulunur."""
    root = store_root_for(db_path)
    collections = [d for d in root.iterdir() if (d / STORE_FILE).is_file()] if root.is_dir() else []
    if not collections:
        return None
    out = Path(target) / "corpus_store"
    for directory in collections:
        SqliteVectorCollection(directory, name=directory.name).backup(out / directory.name)
    return out


def backup_vector_store(target: Path, db_path: Path = DB_PATH) -> Path:
    """Veri onaran betiklerin tek yedek noktası: yalnızca ETKİN parça deposu yedeklenir (auto kuralı,
    resolve_store_kind). SQLite etkinken eski Chroma dizini (yüzlerce MB, artık yazılmıyor) her
    yedeğe kopyalanmaz. Yedeğin kök dizinini döndürür."""
    import shutil

    target = Path(target)
    if resolve_store_kind(None, db_path) == "sqlite":
        out = backup_corpus_store(target, db_path)
        if out is None:
            raise RuntimeError(f"Etkin SQLite parça deposu yedeklenemedi: {store_root_for(db_path)}")
        return out
    out = target / Path(db_path).name
    shutil.copytree(db_path, out)  # Chroma açılmadan önce çağrılmalı
    return out


class SqliteStoreClient:
    """`chromadb.PersistentClient` yerine: koleksiyonlar `path/<ad>/` dizinleridir."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def _dir(self, name: str) -> Path:
        if not name or any(ch in name for ch in r'/\:') or name in (".", ".."):
            raise ValueError(f"Geçersiz koleksiyon adı: {name!r}")
        return self.path / name

    def get_or_create_collection(self, name: str, embedding_function: Any = None, **_: Any) -> SqliteVectorCollection:
        return SqliteVectorCollection(self._dir(name), name=name)

    def get_collection(self, name: str, **_: Any) -> SqliteVectorCollection:
        if not (self._dir(name) / STORE_FILE).is_file():
            raise ValueError(f"Koleksiyon yok: {name}")
        return SqliteVectorCollection(self._dir(name), name=name)

    def delete_collection(self, name: str) -> None:
        target = self._dir(name)
        if not target.is_dir():
            raise ValueError(f"Koleksiyon yok: {name}")
        import shutil

        shutil.rmtree(target)


__all__ = ["SqliteStoreClient", "SqliteVectorCollection", "backup_corpus_store", "backup_vector_store", "resolve_store_kind", "store_file_for", "store_root_for"]
