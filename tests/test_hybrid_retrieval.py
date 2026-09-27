# -*- coding: utf-8 -*-
"""Hibrit (vektör + sözcüksel BM25) aday üretimi.

Sentetik koleksiyon: vektör araması ilgili hükmü getirmiyor (gömme uzaklığı yüksek), ama
hüküm sorgu terimlerini taşıyor. Sözcüksel aday havuza girmeli, gerçek embedding uzaklığıyla
aynı alaka sıralamasından geçmeli ve uygunluk politikası sıralamadan önce uygulanmalı.
"""
from __future__ import annotations

import unittest
from pathlib import Path
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


class TestDiskLexicalIndex(unittest.TestCase):
    DOCS = ["kartlarının aidatı talep edilemez", "yetkisiz işlem yapılmıştır", "kredi kartı aidat ücreti",
            "aidat aidat masraf", "faiz oranı", "hesap planı aidat"]
    METAS = [{}, {}, {}, {}, {}, {"structure_type": "accounting_plan"}]

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "lex.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def _pages(self):
        ids = [f"c{i}" for i in range(len(self.DOCS))]
        return [(ids[:4], self.DOCS[:4], self.METAS[:4]), (ids[4:], self.DOCS[4:], self.METAS[4:])]

    def test_same_ranking_as_in_memory_index(self):
        from mufettis.rag.lexical import DiskLexicalIndex
        memory = LexicalIndex.from_pages(self._pages())
        disk = DiskLexicalIndex.build(self.path, self._pages(), "sig-1")
        try:
            admit = lambda m: m["normative_role"] != "classification"
            for query in ("kartım için aidat", "aidat masraf", "yetki devri", "faiz"):
                for kw in ({}, {"admit": admit}):
                    self.assertEqual(disk.search(query_terms(query), 3, **kw), memory.search(query_terms(query), 3, **kw))
            self.assertEqual(len(disk), len(memory))
        finally:
            disk.close()

    def test_signature_mismatch_requires_rebuild_and_failed_build_keeps_old_file(self):
        from mufettis.rag.lexical import DiskLexicalIndex
        DiskLexicalIndex.build(self.path, self._pages(), "sig-1").close()
        self.assertIsNone(DiskLexicalIndex.open(self.path, "sig-2"))

        def broken_pages():
            yield from self._pages()[:1]
            raise RuntimeError("okuma kesildi")

        with self.assertRaises(RuntimeError):
            DiskLexicalIndex.build(self.path, broken_pages(), "sig-2")
        old = DiskLexicalIndex.open(self.path, "sig-1")
        self.assertIsNotNone(old)
        self.assertEqual(len(old), len(self.DOCS))
        old.close()


class TestPersistentLexicalIndexOnChroma(unittest.TestCase):
    """Gerçek Chroma: indeks diskte kalır, yeni süreç yeniden kurmaz, yazma sonrası yenilenir."""

    def test_reuse_and_refresh(self):
        import tempfile
        from unittest.mock import patch
        from mufettis.config import CHUNK_SCHEMA_VERSION
        from mufettis.rag import engine as engine_module
        from mufettis.rag.lexical import DiskLexicalIndex

        # Chroma, dosyalarını süreç bitene kadar açık tutar (Windows'ta silinemez).
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "mevzuat_db"
            rag = engine_module.LocalRAG(db_path=db)
            meta = {"chunk_schema_version": CHUNK_SCHEMA_VERSION, "title": "Örnek"}
            rag.collection.add(ids=["a", "b"], documents=["kart aidatı talep edilemez", "faiz oranı"],
                               metadatas=[meta, meta], embeddings=[[1.0, 0.0], [0.0, 1.0]])
            first = rag._lexical_index()
            self.assertIsInstance(first, DiskLexicalIndex)
            self.assertEqual([i for i, _ in first.search(query_terms("aidat"), 5)], ["a"])
            self.assertEqual(len(list(Path(tmp).glob("mevzuat_db_lexical_*.sqlite3"))), 1)

            again = engine_module.LocalRAG(db_path=db)
            with patch.object(DiskLexicalIndex, "build", side_effect=AssertionError("yeniden kuruldu")):
                self.assertEqual(len(again._lexical_index()), 2)

            rag.collection.add(ids=["c"], documents=["aidat iadesi"], metadatas=[meta], embeddings=[[0.5, 0.5]])
            refreshed = again._lexical_index()
            self.assertEqual([i for i, _ in refreshed.search(query_terms("iade"), 5)], ["c"])

            # Yalnızca meta veri güncellemesi (parça sayısı aynı) de indeksi yeniler.
            before = again._collection_signature()
            rag.collection.update(ids=["c"], metadatas=[{**meta, "structure_type": "accounting_plan"}])
            self.assertNotEqual(again._collection_signature(), before)
            role = again._lexical_index().search(query_terms("iade"), 5, admit=lambda m: m["normative_role"] != "classification")
            self.assertEqual(role, [])
            for index in (first, refreshed):
                index.close()
            again._lexical_index().close()


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


class TestClaimCandidates(unittest.TestCase):
    """LocalRAG.claim_candidates: BM25 ve vektör sıraları RRF ile birleşir, hukuki yeniden sıralama yok."""
    QUERY = "kredi kartım için sözleşmede olmayan aidat ücreti talep edildi"

    def test_lexical_hit_outranks_vector_only_hit_and_policy_applies(self):
        rag = _rag(ROWS, vector_hits=["def-1", "chart-1"])
        out = rag.claim_candidates(self.QUERY, 5)
        self.assertEqual(out[0]["id"], "fee-24")
        self.assertIn("def-1", [r["id"] for r in out])
        self.assertTrue(all(r["distance"] is not None and r["fusion_score"] > 0 for r in out))
        policy = EligibilityPolicy(exclude_roles=frozenset({NormativeRole.CLASSIFICATION}))
        self.assertNotIn("chart-1", [r["id"] for r in rag.claim_candidates(self.QUERY, 5, eligibility=policy)])

    def test_limit_and_empty_query(self):
        rag = _rag(ROWS, vector_hits=["def-1"])
        self.assertEqual(len(rag.claim_candidates(self.QUERY, 1)), 1)
        self.assertEqual(rag.claim_candidates("  ", 5), [])
