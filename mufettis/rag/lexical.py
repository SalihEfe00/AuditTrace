# -*- coding: utf-8 -*-
"""Sözcüksel (BM25) aday üretimi.

Vektör araması tek başına bazı ilgili hükümleri aday havuzuna getiremiyor: gömme modeli şikâyet
cümlesini tanım maddelerine ("36 aylık vade …") ilgili hükümden ("sözleşmede yer almayan …
masraf … talep edilemez") daha yakın buluyor. Bu modül yalnızca ADAY üretir; adaylar vektör
adaylarıyla birleştirilir, gerçek embedding uzaklıkları hesaplanır ve aynı alaka sıralamasından
(_legal_rerank) geçer. Uygunluk ve erişilebilirlik kararları taze meta veriyle çağıranda verilir.

Terimler Türkçe kök anahtarlarıyla eşleşir (analysis.term_stats.term_key ile aynı kural:
yoksunluk ekli kelimeler kökten ayrıdır, 4 harfli kökler önek eşleşir).
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

BM25_K1 = 1.2
BM25_B = 0.75
# Parçaların yarısından fazlasında geçen anahtarlar aday seçiminde ayırt edici değildir.
MAX_DOC_RATIO = 0.5


def indexed_text(text: Optional[str], meta: Optional[Dict[str, Any]]) -> str:
    """Parçanın aranan metni: maddenin başlığı ("Zamanaşımı") + parça metni. Başlık gömülen metne
    eklenmez (bkz. parser.split_legal_chunks); konu sözcüğü olarak yalnızca burada aranır."""
    heading = str((meta or {}).get("article_heading") or "").strip()
    return f"{heading}\n{text or ''}" if heading else (text or "")


def _document_terms(text: str) -> List[str]:
    from mufettis.analysis.term_stats import term_key
    from mufettis.analysis.turkish_text import MIN_PREFIX_MATCH, STOP_WORDS, stem, tokenize

    out: List[str] = []
    for token in tokenize(text):
        if token in STOP_WORDS or token.isdigit():
            continue
        key = term_key(token)
        out.append(key)
        s = stem(token)
        prefix = f"{s[:MIN_PREFIX_MATCH]}*"
        # Yoksunluk ekli kelime ("yetkisiz") kökün önek ailesine ("yetk*") katılmaz.
        if len(s) > MIN_PREFIX_MATCH and prefix != key and key == s[:len(key)]:
            out.append(prefix)
    return out


def eligibility_view(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Uygunluk kararı için yeterli küçük meta veri: güncel şemadaki kanonik alanlar. Kayıt eski
    şemadaysa alanlar bir kez türetilir (canonical_view ile aynı sonuç)."""
    from mufettis.corpus import CANONICAL_SCHEMA, canonical_view
    from mufettis.corpus.model import CANONICAL_FIELDS

    view = canonical_view(meta)
    out = {field: getattr(view, field) for field in CANONICAL_FIELDS}
    out["canonical_schema"] = CANONICAL_SCHEMA
    return {k: sys.intern(v) if isinstance(v, str) else v for k, v in out.items()}


def query_terms(text: str) -> List[str]:
    """Sorgu anahtarları. Belge tarafıyla aynı kural: 4 harfli kökün çekimleri ("kartım") hem kendi
    anahtarıyla hem kök önekiyle ("kart*") eşleşir; böylece "kartları" geçen hüküm de bulunur."""
    from mufettis.analysis.term_stats import term_key
    from mufettis.analysis.turkish_text import MIN_PREFIX_MATCH, STOP_WORDS, is_generic_term, stem, tokenize

    keys: List[str] = []
    for token in tokenize(text):
        if token in STOP_WORDS or token.isdigit() or is_generic_term(token):
            continue
        key = term_key(token)
        candidates = [key]
        if not key.endswith("*") and len(stem(token)) > MIN_PREFIX_MATCH and key == stem(token)[:len(key)]:
            candidates.append(f"{stem(token)[:MIN_PREFIX_MATCH]}*")
        for candidate in candidates:
            if candidate not in keys:
                keys.append(candidate)
    return keys


class LexicalIndex:
    """Parça kimliği -> terim frekansları üzerine kurulan bellek içi BM25 indeksi.

    Parça başına tam meta veri tutulmaz: uygunluk ön kararı yalnızca kanonik alanlara bakar
    (`eligibility_view`). Aynı belgenin parçaları aynı değerleri paylaşır (sys.intern)."""

    def __init__(self, ids: Sequence[str] = (), documents: Sequence[str] = (), metadatas: Optional[Sequence[Dict[str, Any]]] = None):
        self.ids: List[str] = []
        self.metadatas: List[Dict[str, Any]] = []
        self.lengths: List[int] = []
        self.postings: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        self.add(ids, documents, metadatas)

    @classmethod
    def from_pages(cls, pages: Iterable[Tuple[Sequence[str], Sequence[str], Sequence[Dict[str, Any]]]]) -> "LexicalIndex":
        """Sayfa sayfa kurulum: koleksiyonun tamamı hiçbir anda bellekte olmaz."""
        index = cls()
        for ids, documents, metadatas in pages:
            index.add(ids, documents, metadatas)
        return index

    def add(self, ids: Sequence[str], documents: Sequence[str], metadatas: Optional[Sequence[Dict[str, Any]]] = None) -> None:
        metadatas = list(metadatas) if metadatas is not None else [{} for _ in ids]
        for cid, text, meta in zip(ids, documents, metadatas):
            pos = len(self.ids)
            self.ids.append(cid)
            self.metadatas.append(eligibility_view(meta or {}))
            terms = _document_terms(indexed_text(text, meta))
            self.lengths.append(len(terms))
            for term, tf in Counter(terms).items():
                self.postings[term].append((pos, tf))
        self.avg_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0

    def __len__(self) -> int:
        return len(self.ids)

    def search(
        self,
        terms: Iterable[str],
        limit: int,
        admit: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> List[Tuple[str, float]]:
        """admit: uygun olmayan parçalar sıralamadan ÖNCE elenir (aksi halde ilk sıraları uygun
        olmayan binlerce hesap planı satırı doldurabilir)."""
        n = len(self.ids)
        if not n or limit <= 0:
            return []

        def postings(term: str) -> Optional[List[Tuple[int, int, int]]]:
            posting = self.postings.get(term)
            return [(pos, tf, self.lengths[pos]) for pos, tf in posting] if posting else None

        ranked = bm25_rank(terms, n, self.avg_length, postings, lambda term: len(self.postings.get(term, ())))
        out: List[Tuple[str, float]] = []
        for pos, score in ranked:
            if admit is not None and not admit(self.metadatas[pos]):
                continue
            out.append((self.ids[pos], score))
            if len(out) >= limit:
                break
        return out


def bm25_rank(
    terms: Iterable[str],
    n: int,
    avg_length: float,
    postings: Callable[[str], Optional[List[Tuple[int, int, int]]]],
    doc_freq: Callable[[str], int],
) -> List[Tuple[int, float]]:
    """Bellek içi ve disk indeksinin ortak BM25 puanlaması: (konum, puan), puana göre azalan,
    eşitlikte konuma göre. postings(term) -> [(konum, tf, parça uzunluğu)]."""
    scores: Dict[int, float] = defaultdict(float)
    for term in dict.fromkeys(terms):
        df = doc_freq(term)
        # Parçaların yarısından fazlasında geçen anahtar okunmaz bile (ayırt edici değil).
        if not df or df > MAX_DOC_RATIO * n:
            continue
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for pos, tf, length in postings(term) or ():
            norm = 1.0 - BM25_B + BM25_B * (length / avg_length if avg_length else 1.0)
            scores[pos] += idf * tf * (BM25_K1 + 1.0) / (tf + BM25_K1 * norm)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


# 2: madde başlığı (article_heading) da dizinlenir.
LEXICAL_INDEX_VERSION = "2"
ADMIT_FETCH_BATCH = 200


class DiskLexicalIndex:
    """Diskte (SQLite) tutulan BM25 indeksi. Bellekte yalnızca sorgudaki terimlerin kayıtları ve
    sıralanan adayların uygunluk alanları bulunur; mevzuatın tamamı yüklendiğinde de 8 GB RAM'li
    bir laptopta çalışır. Puanlama bellek içi indeksle birebir aynıdır (bm25_rank).

    Dosya, kurulduğu koleksiyonun imzasını saklar; imza değişmişse `open` None döner ve çağıran
    yeniden kurar. Kurulum geçici dosyaya yapılır ve tamamlanınca atomik olarak yerine konur:
    yarım kalan kurulum eski indeksi bozmaz."""

    def __init__(self, path: Path, conn: sqlite3.Connection):
        self.path = path
        self._conn = conn
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        self.signature = meta.get("signature", "")
        self._n = int(meta.get("n", 0))
        self._avg_length = float(meta.get("avg_length", 0.0))

    @classmethod
    def open(cls, path: Path, signature: str) -> Optional["DiskLexicalIndex"]:
        if not Path(path).exists():
            return None
        try:
            conn = sqlite3.connect(str(path), check_same_thread=False)
            index = cls(Path(path), conn)
        except sqlite3.Error:
            return None
        if index.signature != signature:
            conn.close()
            return None
        return index

    @classmethod
    def build(cls, path: Path, pages: Iterable[Tuple[Sequence[str], Sequence[str], Sequence[Dict[str, Any]]]],
              signature: str) -> "DiskLexicalIndex":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.building")
        if tmp.exists():
            tmp.unlink()
        conn = sqlite3.connect(str(tmp))
        try:
            conn.executescript(
                "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;"
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                "CREATE TABLE docs (pos INTEGER PRIMARY KEY, id TEXT NOT NULL, length INTEGER NOT NULL, elig TEXT NOT NULL);"
                "CREATE TABLE postings (term TEXT NOT NULL, pos INTEGER NOT NULL, tf INTEGER NOT NULL);"
            )
            pos = 0
            total_length = 0
            for ids, documents, metadatas in pages:
                docs, rows = [], []
                for cid, text, meta in zip(ids, documents, metadatas or [{}] * len(ids)):
                    terms = _document_terms(indexed_text(text, meta))
                    docs.append((pos, cid, len(terms), json.dumps(eligibility_view(meta or {}), ensure_ascii=False)))
                    rows.extend((term, pos, tf) for term, tf in Counter(terms).items())
                    total_length += len(terms)
                    pos += 1
                conn.executemany("INSERT INTO docs VALUES (?, ?, ?, ?)", docs)
                conn.executemany("INSERT INTO postings VALUES (?, ?, ?)", rows)
            conn.executescript(
                "CREATE INDEX postings_term ON postings(term);"
                "CREATE TABLE terms AS SELECT term, COUNT(*) AS df FROM postings GROUP BY term;"
                "CREATE UNIQUE INDEX terms_term ON terms(term);"
            )
            conn.executemany("INSERT INTO meta VALUES (?, ?)", [
                ("version", LEXICAL_INDEX_VERSION), ("signature", signature), ("n", str(pos)),
                ("avg_length", repr(total_length / pos if pos else 0.0)),
            ])
            conn.commit()
        except BaseException:
            conn.close()
            tmp.unlink(missing_ok=True)
            raise
        conn.close()
        try:
            os.replace(tmp, path)
        except PermissionError:
            # Aynı imzalı indeksi başka bir süreç kurup açmış (Windows açık dosyayı değiştirmez).
            tmp.unlink(missing_ok=True)
        index = cls.open(path, signature)
        assert index is not None
        return index

    def close(self) -> None:
        self._conn.close()

    def __len__(self) -> int:
        return self._n

    def search(
        self,
        terms: Iterable[str],
        limit: int,
        admit: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> List[Tuple[str, float]]:
        if not self._n or limit <= 0:
            return []
        conn = self._conn

        def doc_freq(term: str) -> int:
            row = conn.execute("SELECT df FROM terms WHERE term = ?", (term,)).fetchone()
            return int(row[0]) if row else 0

        def postings(term: str) -> List[Tuple[int, int, int]]:
            return conn.execute(
                "SELECT p.pos, p.tf, d.length FROM postings p JOIN docs d ON d.pos = p.pos WHERE p.term = ?",
                (term,),
            ).fetchall()

        ranked = bm25_rank(terms, self._n, self._avg_length, postings, doc_freq)
        out: List[Tuple[str, float]] = []
        # Uygunluk alanları sıralama sırasıyla, küçük gruplar hâlinde okunur.
        for start in range(0, len(ranked), ADMIT_FETCH_BATCH):
            batch = ranked[start:start + ADMIT_FETCH_BATCH]
            placeholders = ",".join("?" * len(batch))
            rows = {pos: (cid, elig) for pos, cid, elig in conn.execute(
                f"SELECT pos, id, elig FROM docs WHERE pos IN ({placeholders})", [pos for pos, _ in batch])}
            for pos, score in batch:
                cid, elig = rows[pos]
                if admit is not None and not admit(json.loads(elig)):
                    continue
                out.append((cid, score))
                if len(out) >= limit:
                    return out
        return out


__all__ = ["DiskLexicalIndex", "LexicalIndex", "bm25_rank", "eligibility_view", "query_terms"]
