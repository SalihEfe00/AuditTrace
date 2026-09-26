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

import math
import sys
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

BM25_K1 = 1.2
BM25_B = 0.75
# Parçaların yarısından fazlasında geçen anahtarlar aday seçiminde ayırt edici değildir.
MAX_DOC_RATIO = 0.5


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
            terms = _document_terms(text or "")
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
        scores: Dict[int, float] = defaultdict(float)
        for term in dict.fromkeys(terms):
            posting = self.postings.get(term)
            if not posting or len(posting) > MAX_DOC_RATIO * n:
                continue
            idf = math.log(1.0 + (n - len(posting) + 0.5) / (len(posting) + 0.5))
            for pos, tf in posting:
                norm = 1.0 - BM25_B + BM25_B * (self.lengths[pos] / self.avg_length if self.avg_length else 1.0)
                scores[pos] += idf * tf * (BM25_K1 + 1.0) / (tf + BM25_K1 * norm)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        out: List[Tuple[str, float]] = []
        for pos, score in ranked:
            if admit is not None and not admit(self.metadatas[pos]):
                continue
            out.append((self.ids[pos], score))
            if len(out) >= limit:
                break
        return out


__all__ = ["LexicalIndex", "eligibility_view", "query_terms"]
