# -*- coding: utf-8 -*-
"""Hibrit (vektör + sözcüksel BM25) aday üretimi.

Sentetik koleksiyon: vektör araması ilgili hükmü getirmiyor (gömme uzaklığı yüksek), ama
hüküm sorgu terimlerini taşıyor. Sözcüksel aday havuza girmeli, gerçek embedding uzaklığıyla
aynı alaka sıralamasından geçmeli ve uygunluk politikası sıralamadan önce uygulanmalı.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

import numpy as np

from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.corpus import EligibilityPolicy, NormativeRole
from mufettis.rag.lexical import LexicalIndex, query_terms

QUERY_VEC = [1.0, 0.0, 0.0]


def _meta(**over: Any) -> Dict[str, Any]:
    base = {"chunk_schema_version": CHUNK_SCHEMA_VERSION, "status": "active", "document_indexable": "True",
            "source_id": "local_file", "title": "ÖRNEK KANUN", "law_number": "99001", "document_type": "Kanun",
            "structure_type": "article", "file_hash": "a" * 64}
    base.update(over)
    return base


class FakeCollection:
    """Chroma arayüzünün kullanılan kısmı; vektör sorgusu `vector_hits` sırasını döndürür."""

    def __init__(self, rows: List[Dict[str, Any]], vector_hits: List[str]):
        self.rows = {r["id"]: r for r in rows}
        self.vector_hits = vector_hits
        self.full_scans = 0

    def count(self) -> int:
        return len(self.rows)

    def query(self, query_embeddings, n_results, where=None, **kw):
        hit = [self.rows[i] for i in self.vector_hits][:n_results]
        q = np.asarray(query_embeddings[0])
        return {"ids": [[r["id"] for r in hit]], "documents": [[r["document"] for r in hit]],
                "metadatas": [[r["metadata"] for r in hit]],
                "distances": [[float(np.sum((np.asarray(r["embedding"]) - q) ** 2)) for r in hit]]}

    def get(self, where=None, include=None, ids=None, **kw):
        if ids is None:
            self.full_scans += 1
        rows = [self.rows[i] for i in ids] if ids is not None else list(self.rows.values())
        out = {"ids": [r["id"] for r in rows], "documents": [r["document"] for r in rows],
               "metadatas": [r["metadata"] for r in rows]}
        if include and "embeddings" in include:
            out["embeddings"] = np.asarray([r["embedding"] for r in rows])
        return out


def _rag(rows, vector_hits):
    from mufettis.rag.engine import LocalRAG

    rag = LocalRAG.__new__(LocalRAG)
    rag.collection = FakeCollection(rows, vector_hits)
    rag.embed = lambda texts, **kw: [QUERY_VEC for _ in texts]
    return rag


ROWS = [
    {"id": "def-1", "document": "MADDE 3 – d) Vade: otuz altı aylık vade için hesaplanan tutar.",
     "metadata": _meta(article_no="3", chunk_no="1"), "embedding": [0.8, 0.6, 0.0]},
    {"id": "fee-24", "document": "MADDE 24 – Sözleşmede yer almayan aidat, komisyon veya masraf adı altında "
                                 "kart hamilinden ücret talep edilemez.",
     "metadata": _meta(article_no="24", chunk_no="2"), "embedding": [0.2, 0.0, 0.98]},
    {"id": "chart-1", "document": "39090 KART AİDAT ÜCRETLERİ ÜCRET KOMİSYON AİDAT",
     "metadata": _meta(structure_type="accounting_plan", file_hash="c" * 64, title="Hesap Planı",
                       law_number="", document_type="Tebliğ", chunk_no="1"), "embedding": [0.1, 0.0, 0.99]},
]


class TestLexicalIndex(unittest.TestCase):
    def test_turkish_inflections_and_privative_keys(self):
        idx = LexicalIndex(["a", "b"], ["kartlarının aidatı talep edilemez", "yetkisiz işlem yapılmıştır"])
        self.assertEqual([i for i, _ in idx.search(query_terms("kartım için aidat"), 5)], ["a"])
        # "yetki" sorgusu "yetkisiz" (yoksunluk eki) ile eşleşmez.
        self.assertEqual(idx.search(query_terms("yetki devri"), 5), [])

    def test_admit_is_applied_before_truncation(self):
        idx = LexicalIndex(["x1", "x2", "ok", "o1", "o2", "o3"],
                           ["aidat aidat aidat", "aidat aidat", "aidat", "faiz", "masraf", "komisyon"],
                           [{"structure_type": "accounting_plan"}] * 2 + [{}] * 4)
        hits = idx.search(query_terms("aidat"), 1, admit=lambda m: m["normative_role"] != "classification")
        self.assertEqual([i for i, _ in hits], ["ok"])

    def test_index_keeps_only_canonical_fields_and_builds_from_pages(self):
        meta = {"structure_type": "accounting_plan", "title": "Hesap Planı", "long_field": "x" * 500}
        idx = LexicalIndex.from_pages([(["a"], ["aidat"], [meta]), (["b"], ["aidat faiz"], [{}])])
        self.assertEqual(idx.ids, ["a", "b"])
        self.assertNotIn("long_field", idx.metadatas[0])
        self.assertEqual(idx.metadatas[0]["normative_role"], "classification")
        # Kanonik görünüm küçük kayıttan aynı sonucu verir.
        from mufettis.corpus import canonical_view
        self.assertEqual(canonical_view(idx.metadatas[0]), canonical_view(meta))


class TestHybridQuery(unittest.TestCase):
    QUERY = "kredi kartım için sözleşmede olmayan aidat ücreti talep edildi"

    def test_lexical_candidate_enters_and_competes_with_true_distance(self):
        rag = _rag(ROWS, vector_hits=["def-1"])
        out = rag.query(self.QUERY, k=2)
        ids = [r["metadata"]["article_no"] for r in out]
        self.assertIn("24", ids)
        fee = next(r for r in out if r["metadata"]["article_no"] == "24")
        self.assertAlmostEqual(fee["distance"], float(np.sum((np.asarray([0.2, 0.0, 0.98]) - QUERY_VEC) ** 2)), 5)

    def test_eligibility_excludes_non_rule_lexical_candidates(self):
        rag = _rag(ROWS, vector_hits=["def-1"])
        policy = EligibilityPolicy(exclude_roles=frozenset({NormativeRole.CLASSIFICATION}))
        out = rag.query(self.QUERY, k=3, eligibility=policy)
        self.assertNotIn("accounting_plan", {r["metadata"]["structure_type"] for r in out})
        self.assertIn("24", {r["metadata"]["article_no"] for r in out})

    def test_index_is_cached_and_rebuilt_after_ingest(self):
        rag = _rag(ROWS, vector_hits=["def-1"])
        rag.query(self.QUERY, k=2)
        rag.query(self.QUERY, k=2)
        self.assertEqual(rag.collection.full_scans, 1)
        rag._mutations = getattr(rag, "_mutations", 0) + 1  # ingest_text / delete_hash bunu artırır
        rag.query(self.QUERY, k=2)
        self.assertEqual(rag.collection.full_scans, 2)


if __name__ == "__main__":
    unittest.main()
