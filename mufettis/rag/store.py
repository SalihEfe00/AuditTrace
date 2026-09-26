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
- `store.sqlite3`: `chunks` (kimlik, belge anahtarı, metin, metadata JSONB, vektör satırı).
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

import json
import os
import sqlite3
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

STORE_FILE = "store.sqlite3"
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
_ID_IN = "id IN (SELECT value FROM json_each(?))"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    file_hash TEXT,
    document TEXT,
    metadata BLOB NOT NULL,
    vec INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_file_hash ON chunks(file_hash);
CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


# ── filtre (Chroma `where` dili → SQL) ──────────────────────────────────────
def _path(key: str) -> str:
    return '$."' + str(key).replace('"', '\\"') + '"'


def _typed_eq(key: str, value: Any, params: List[Any]) -> str:
    """Tipe duyarlı eşitlik (Chroma gibi: 1, "1" ve True birbirine eşit değildir). Anahtarı olmayan
    kayıtta NULL değil yanlış döner; böylece `$ne`/`$nin` bu kayıtları da kapsar."""
    return f"IFNULL({_typed_eq_raw(key, value, params)}, 0)"


def _typed_eq_raw(key: str, value: Any, params: List[Any]) -> str:
    path = _path(key)
    if isinstance(value, bool):
        params.append(path)
        return f"json_type(metadata, ?) = '{'true' if value else 'false'}'"
    if key == "file_hash" and isinstance(value, str):
        params.append(value)
        return "file_hash = ?"
    if isinstance(value, int):
        kind = "'integer'"
    elif isinstance(value, float):
        kind = "'real'"
    elif isinstance(value, str):
        kind = "'text'"
    else:
        raise ValueError(f"Desteklenmeyen filtre değeri: {key}={value!r}")
    params.extend([path, path, value])
    return f"(json_type(metadata, ?) = {kind} AND json_extract(metadata, ?) = ?)"


def _compare(key: str, op: str, value: Any, params: List[Any]) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{op} yalnızca sayılarla kullanılır: {key}")
    path = _path(key)
    params.extend([path, path, value])
    sign = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}[op]
    return f"(json_type(metadata, ?) IN ('integer', 'real') AND json_extract(metadata, ?) {sign} ?)"


def where_sql(where: Dict[str, Any], params: List[Any]) -> str:
    if not isinstance(where, dict) or len(where) != 1:
        raise ValueError(f"Filtre tek anahtarlı sözlük olmalı: {where!r}")
    (key, cond), = where.items()
    if key in ("$and", "$or"):
        if not isinstance(cond, list) or not cond:
            raise ValueError(f"{key} boş olmayan liste ister")
        joiner = " AND " if key == "$and" else " OR "
        return "(" + joiner.join(where_sql(c, params) for c in cond) + ")"
    if not isinstance(cond, dict):
        return _typed_eq(key, cond, params)
    if len(cond) != 1:
        raise ValueError(f"Koşul tek işleçli olmalı: {key}={cond!r}")
    (op, value), = cond.items()
    if op == "$eq":
        return _typed_eq(key, value, params)
    if op == "$ne":  # anahtarı olmayan kayıtlar da eşleşir (Chroma gibi)
        return f"NOT {_typed_eq(key, value, params)}"
    if op in ("$in", "$nin"):
        if not isinstance(value, list) or not value:
            raise ValueError(f"{op} boş olmayan liste ister: {key}")
        any_eq = "(" + " OR ".join(_typed_eq(key, v, params) for v in value) + ")"
        return any_eq if op == "$in" else f"NOT {any_eq}"
    if op in ("$gt", "$gte", "$lt", "$lte"):
        return _compare(key, op, value, params)
    raise ValueError(f"Desteklenmeyen işleç: {op}")


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
            con.executescript(_SCHEMA)
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

    def _read_rows(self, sql: str, params: Sequence[Any]) -> Tuple[List[Tuple[str, Optional[str], Dict[str, Any], int]], int]:
        """(satırlar, vektör kümesi) aynı anlık görüntüden: satırların vektör numaraları o kümeye aittir."""
        with self._txn() as con:
            rows = con.execute(sql, params).fetchall()
            fileset = self._state(con)[1]
        return [(r[0], r[1], json.loads(r[2]), int(r[3])) for r in rows], fileset

    def get(self, ids: Optional[Sequence[str]] = None, where: Optional[Dict[str, Any]] = None,
            limit: Optional[int] = None, offset: Optional[int] = None,
            include: Sequence[str] = GET_DEFAULT_INCLUDE, where_document: Any = None) -> Dict[str, Any]:
        if where_document:
            raise ValueError("where_document desteklenmiyor (tam metin indeksi yok)")
        if ids is not None and len(ids) == 0:
            raise ValueError("Kimlik listesi boş olamaz")
        clauses, params = [], []
        if where:
            clauses.append(where_sql(where, params))
        if ids is not None:
            clauses.append(_ID_IN)
            params.append(json.dumps([str(i) for i in ids], ensure_ascii=False))
        sql = "SELECT id, document, json(metadata), vec FROM chunks"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq"  # Chroma gibi: ekleme sırası (istenen kimlik sırası değil)
        if limit is not None or offset:
            sql += " LIMIT ? OFFSET ?"
            params.extend([-1 if limit is None else int(limit), int(offset or 0)])
        rows, fileset = self._read_rows(sql, params)
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
                params: List[Any] = []
                sql = "SELECT vec FROM chunks" + (f" WHERE {where_sql(where, params)}" if where else "")
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
                "SELECT id, document, json(metadata), vec FROM chunks WHERE vec IN (SELECT value FROM json_each(?))",
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
            existing = {r[0]: (r[1], json.loads(r[2]), int(r[3])) for r in con.execute(
                f"SELECT id, document, json(metadata), vec FROM chunks WHERE {_ID_IN}", [json.dumps(ids, ensure_ascii=False)])}
            if create and embeddings is None and any(i not in existing for i in ids):
                raise ValueError("Yeni parça gömme vektörü olmadan eklenemez")
            first_vec = self._append_vectors(con, embeddings) if embeddings is not None else None
            rows = []
            for n, rid in enumerate(ids):
                old = existing.get(rid)
                if old is None and not create:
                    continue  # Chroma: olmayan kimliği güncellemek sessizce atlanır
                if old is not None and not update:
                    continue
                doc = documents[n] if documents is not None else (old[0] if old else None)
                meta = _merge_meta(old[1] if old else {}, metadatas[n] if metadatas is not None else None)
                vec = first_vec + n if first_vec is not None else old[2]
                file_hash = meta.get("file_hash") if isinstance(meta.get("file_hash"), str) else None
                rows.append((rid, file_hash, doc, json.dumps(meta, ensure_ascii=False), vec))
            if rows:
                con.executemany(
                    "INSERT INTO chunks (id, file_hash, document, metadata, vec) VALUES (?, ?, ?, jsonb(?), ?) "
                    "ON CONFLICT(id) DO UPDATE SET file_hash = excluded.file_hash, document = excluded.document, "
                    "metadata = excluded.metadata, vec = excluded.vec",
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
        clauses, params = [], []
        if where:
            clauses.append(where_sql(where, params))
        if ids is not None:
            clauses.append(_ID_IN)
            params.append(json.dumps([str(i) for i in ids]))
        if not clauses:
            raise ValueError("Silme için kimlik veya filtre gerekir")
        with self._lock, self._txn("IMMEDIATE") as con:
            deleted = con.execute("DELETE FROM chunks WHERE " + " AND ".join(clauses), params).rowcount
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

    def compact(self) -> Dict[str, int]:
        """Kullanılmayan vektör satırlarını atar. Canlı satırlar yeni bir dosya kümesine sırayla
        kopyalanır; kümeye geçiş, parçaların yeni satır numaralarıyla birlikte tek işlemde yapılır ve
        yarıda kesilirse eski küme geçerli kalır. Önceki küme, o sırada okuyan sorgular için bir sonraki
        sıkıştırmaya kadar durur; daha eskileri silinir. Sıkıştırma boyunca diğer yazmalar bekler."""
        with self._lock, self._txn("IMMEDIATE") as con:
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

    def compact_if_needed(self) -> Optional[Dict[str, int]]:
        with self._txn() as con:
            fileset = self._state(con)[1]
            live = int(con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        garbage = self._vector_rows(fileset) - live
        if garbage >= max(COMPACT_MIN_GARBAGE, COMPACT_GARBAGE_RATIO * live):
            return self.compact()
        return None

    def _remove_filesets(self, keep: set) -> None:
        for f in self.path.glob("vec_*.bin"):
            parts = f.name.split(".")
            number = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else 0
            if number not in keep:
                try:
                    f.unlink()
                except OSError:  # Windows: okuyan bir süreç dosyayı hâlâ eşlemiş olabilir; sonra silinir
                    pass


class SqliteStoreClient:
    """`chromadb.PersistentClient` yerine: koleksiyonlar `path/<ad>/` dizinleridir."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def _dir(self, name: str) -> Path:
        if not name or any(ch in name for ch in '/\:') or name in (".", ".."):
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


__all__ = ["SqliteStoreClient", "SqliteVectorCollection", "where_sql"]
