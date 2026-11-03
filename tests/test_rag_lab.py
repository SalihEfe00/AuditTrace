# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path

from mufettis.config import DATA_DIR, EMBEDDING_MODEL, DB_PATH, SOURCE_CACHE_DIR
from mufettis.legal.parser import _chunk_is_retrievable
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex

RAG_LAB_DIR = DATA_DIR / "rag_lab"
RAG_LAB_FIXTURES = RAG_LAB_DIR / "fixtures"
RAG_LAB_CHROMA = RAG_LAB_DIR / "chroma"
RAG_LAB_SOURCE_CACHE = RAG_LAB_DIR / "source_cache"
RAG_LAB_INDEX_FILE = RAG_LAB_DIR / "ingest_index.json"
RAG_LAB_BASELINE_FILE = RAG_LAB_DIR / "retrieval_baseline.json"


@unittest.skipUnless(RAG_LAB_CHROMA.exists() and RAG_LAB_FIXTURES.exists(),
                     "RAG laboratuvarı korpusu yok (data/rag_lab; depoda bulunmaz)")
class TestRagLabCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rag = LocalRAG(
            db_path=RAG_LAB_CHROMA,
            model_name=EMBEDDING_MODEL,
            allow_download=False,
        )
        cls.index = IngestIndex(path=RAG_LAB_INDEX_FILE)

    def test_01_corpus_inventory_and_chunk_count(self):
        """Verifies 5 documents and all chunks ingested properly."""
        pdf_files = list(RAG_LAB_FIXTURES.glob("*.pdf"))
        self.assertEqual(len(pdf_files), 5)
        self.assertEqual(self.rag.count(), 317)
        self.assertEqual(len(self.index.data.get("indexed_items", {})), 5)

    def test_02_production_isolation_and_zero_mutation(self):
        """Verifies production paths remain completely distinct and unmutated."""
        self.assertNotEqual(Path(self.rag.db_path).resolve(), DB_PATH.resolve())
        self.assertEqual(Path(self.rag.db_path).resolve(), RAG_LAB_CHROMA.resolve())
        self.assertTrue(Path(self.rag.db_path).as_posix().endswith("rag_lab/chroma"))
        self.assertNotEqual(Path(RAG_LAB_SOURCE_CACHE).resolve(), SOURCE_CACHE_DIR.resolve())
        # Üretimin etkin parça deposu (SQLite ya da taşınmamış Chroma) yerinde olmalı.
        from mufettis.rag.store import active_store_file

        self.assertTrue(active_store_file(DB_PATH).exists())

    def test_03_metadata_environment_tagging(self):
        """Verifies that all chunks carry environment='rag_lab'."""
        all_lab = self.rag.collection.get(
            where={"environment": "rag_lab"},
            include=["metadatas"],
        )
        self.assertEqual(len(all_lab.get("ids", [])), 317)

    def test_04_document_retrieval(self):
        """Smoke test: document title retrieval."""
        res = self.rag.query("FİNANSAL PİYASALARDA MANİPÜLASYON VE YANILTICI İŞLEMLER", k=3)
        self.assertGreater(len(res), 0)
        found = any(
            "MANİPÜLASYON" in (r.get("document", "") + " " + r.get("metadata", {}).get("title", "")).upper()
            for r in res
        )
        self.assertTrue(found)

    def test_05_accounting_retrieval(self):
        """Smoke test: accounting account code retrieval."""
        res = self.rag.query("010 KASA", k=3)
        self.assertGreater(len(res), 0)
        found = any(
            "010" in r.get("document", "") or r.get("metadata", {}).get("account_code") == "010"
            for r in res
        )
        self.assertTrue(found)

    def test_06_exact_retrieval(self):
        """Smoke test: exact law reference retrieval."""
        res = self.rag.query("5411 sayılı Kanun", k=5)
        self.assertGreater(len(res), 0)
        found = any(r.get("metadata", {}).get("law_number") == "5411" for r in res)
        self.assertTrue(found)

    def test_07_semantic_retrieval(self):
        """Smoke test: semantic natural language retrieval."""
        res = self.rag.query("bankalarda kredi açma yetkisi ve kredi komitesi çalışma esasları", k=5)
        self.assertGreater(len(res), 0)
        found = any("kredi" in r.get("document", "").lower() for r in res)
        self.assertTrue(found)

    def test_08_metadata_filtering(self):
        """Smoke test: metadata filtering by structure type."""
        acc_plan = self.rag.collection.get(
            where={"structure_type": "accounting_plan"},
            include=["metadatas"],
        )
        self.assertGreater(len(acc_plan.get("ids", [])), 0)

    def test_09_retrievability_100_percent(self):
        """Smoke test: 100% of chunks pass _chunk_is_retrievable."""
        all_chunks = self.rag.collection.get(include=["documents", "metadatas"])
        docs = all_chunks.get("documents", [])
        metas = all_chunks.get("metadatas", [])
        retrievable = [i for i, (d, m) in enumerate(zip(docs, metas)) if _chunk_is_retrievable(m, d)]
        self.assertEqual(len(retrievable), len(docs))

    def test_10_retrieval_baseline_file_exists_and_valid(self):
        """Verifies the generated retrieval baseline file for FAZ 11B."""
        self.assertTrue(RAG_LAB_BASELINE_FILE.exists())
        data = json.loads(RAG_LAB_BASELINE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data.get("environment"), "rag_lab")
        self.assertEqual(data.get("total_documents"), 5)
        self.assertEqual(data.get("total_chunks"), 317)
        self.assertGreaterEqual(len(data.get("benchmarks", [])), 10)


if __name__ == "__main__":
    unittest.main()
