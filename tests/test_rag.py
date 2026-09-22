# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:
    import chromadb
except Exception:
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

try:
    import streamlit as st
except Exception:
    st = None

try:
    import pdfplumber
except Exception:
    pdfplumber = None

from mufettis.config import *
from mufettis.common.utils import *
from mufettis.audit.service import *
from mufettis.extraction.text_repair import *
from mufettis.extraction.pdf import *
from mufettis.legal.parser import *
from mufettis.harvesting.web_harvester import *
from mufettis.harvesting.resmi_gazete import *
from mufettis.rag.index import *
from mufettis.rag.engine import *
from mufettis.rag.maintenance import *
from mufettis.analysis.risk_engine import *
from mufettis.importers.bulk_importer import *
from mufettis.importers.bootstrapper import *
from mufettis.scheduler.scheduler import *
from mufettis.llm.client import *
from mufettis.exports.report_generator import *
from mufettis.ui.admin import *
from mufettis.ui.app import *

class TestRAGReranking(unittest.TestCase):
    def test_query_aware_reranking_prefers_relevant_official_result(self):
        query = "Bankaların bilgi sistemleri ve elektronik bankacılık hizmetleri"
        results = [
            {
                "document": "Bankaların bilgi sistemleri, elektronik bankacılık hizmetleri.",
                "metadata": {
                    "title": "Bankaların Bilgi Sistemleri",
                    "source": "BDDK",
                    "source_id": "bddk_official",
                    "source_type": "official",
                    "authority_rank": 100,
                    "status": "active",
                },
                "distance": 0.52,
            },
            {
                "document": "Gelir vergisi mükelleflerine ilişkin genel hükümler.",
                "metadata": {
                    "title": "Gelir Vergisi Kanunu",
                    "source": "GVK.pdf",
                    "source_id": "mevzuat_org",
                    "source_type": "secondary",
                    "authority_rank": 40,
                    "status": "active",
                },
                "distance": 0.44,
            },
        ]
        ranked = prefer_official_matches(results, query=query, limit=2)
        self.assertEqual(ranked[0]["metadata"]["source_id"], "bddk_official")
        self.assertGreater(
            ranked[0]["_rerank_score"],
            ranked[1]["_rerank_score"],
        )

    def test_reranking_exposes_scores(self):
        results = [{
            "document": "5549 sayılı Kanun yükümlülükleri.",
            "metadata": {
                "title": "5549 sayılı Kanun",
                "source": "Resmi Gazete",
                "source_id": "resmi_gazete",
                "source_type": "official",
                "authority_rank": 100,
                "status": "active",
            },
            "distance": 0.30,
        }]
        ranked = prefer_official_matches(
            results,
            query="5549 yükümlülük",
            limit=1,
        )
        self.assertIn("_rerank_score", ranked[0])
        self.assertIn("_lexical_score", ranked[0])
        self.assertIn("_authority_score", ranked[0])


class TestV1313RAGIsolation(unittest.TestCase):
    def test_query_source_has_current_schema_filter(self):
        source = inspect.getsource(LocalRAG.query)
        self.assertIn("chunk_schema_version", source)
        self.assertIn("CHUNK_SCHEMA_VERSION", source)
        self.assertIn('where={"chunk_schema_version":CHUNK_SCHEMA_VERSION}', source)


class TestV1316ExactLawRecovery(unittest.TestCase):
    def test_inline_articles_are_detected(self):
        body=(
            "5549 sayılı Kanun MADDE 1 - Amaç ve kapsam. "
            "(1) Bu Kanun uygulanır. MADDE 2 - Tanımlar. (1) Tanım."
        )
        meta=extract_legal_metadata(
            "5549 sayılı Kanun",
            body,
            {"file_hash":"x","source_id":"local_file"},
        )
        self.assertEqual(meta.get("law_number"),"5549")
        self.assertEqual(meta.get("structure_type"),"article")
        self.assertTrue(meta.get("evidence_indexable"))

        chunks=split_legal_chunks(body,max_chars=800)
        self.assertTrue(any(c.get("article_no")=="1" for c in chunks))
        self.assertTrue(any(c.get("article_no")=="2" for c in chunks))

    def test_exact_law_metadata_or_text_scan(self):
        class FakeCollection:
            def get(self, **kwargs):
                return {
                    "documents":[
                        "Genel vergi metni.",
                        "5549 sayılı Kanun MADDE 1 - Amaç.",
                    ],
                    "metadatas":[
                        {
                            "chunk_schema_version":CHUNK_SCHEMA_VERSION,
                            "evidence_indexable":"True",
                            "status":"active",
                            "title":"GVK",
                            "law_number":"193",
                            "document_key":"193",
                        },
                        {
                            "chunk_schema_version":CHUNK_SCHEMA_VERSION,
                            "evidence_indexable":"True",
                            "status":"active",
                            "title":"5549",
                            "law_number":"",
                            "document_key":"5549",
                        },
                    ],
                }
        class FakeRAG:
            pass


        rag=object.__new__(LocalRAG)
        rag.collection=FakeCollection()
        got=rag._exact_law_candidates("5549")
        self.assertEqual(len(got),1)
        self.assertIn("5549 sayılı Kanun",got[0]["document"])


class TestV1324RAGInventory(unittest.TestCase):
    def test_inventory_groups_current_schema_chunks_by_document(self):
        class FakeCollection:
            def get(self, **kwargs):
                self.kwargs = kwargs
                return {
                    "metadatas": [
                        {
                            "file_hash": "a",
                            "document_key": "5549",
                            "title": "5549 sayılı Kanun",
                            "source_id": "resmi_gazete",
                            "source": "Resmi Gazete",
                            "law_number": "5549",
                            "status": "active",
                            "evidence_indexable": "True",
                            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                        },
                        {
                            "file_hash": "a",
                            "document_key": "5549",
                            "title": "5549 sayılı Kanun",
                            "source_id": "resmi_gazete",
                            "source": "Resmi Gazete",
                            "law_number": "5549",
                            "status": "active",
                            "evidence_indexable": "True",
                            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                        },
                        {
                            "file_hash": "b",
                            "document_key": "1041",
                            "title": "BDDK Belgesi",
                            "source_id": "bddk_official",
                            "source": "BDDK",
                            "status": "active",
                            "evidence_indexable": "True",
                            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                        },
                    ]
                }
        rag = object.__new__(LocalRAG)
        rag.collection = FakeCollection()
        inventory = build_rag_inventory(rag)
        self.assertTrue(inventory["available"])
        self.assertEqual(inventory["total_chunks"], 3)
        self.assertEqual(inventory["total_documents"], 2)
        self.assertEqual(inventory["active_documents"], 2)
        self.assertEqual(inventory["searchable_documents"], 2)
        law_doc = next(d for d in inventory["documents"] if d["law_number"] == "5549")
        self.assertEqual(law_doc["chunk_count"], 2)
        self.assertEqual(len(inventory["sources"]), 2)

    def test_inventory_is_read_only_and_uses_current_schema(self):
        class FakeCollection:
            def get(self, **kwargs):
                self.kwargs = kwargs
                return {"metadatas": []}
        collection = FakeCollection()
        rag = object.__new__(LocalRAG)
        rag.collection = collection
        inventory = build_rag_inventory(rag)
        self.assertTrue(inventory["available"])
        self.assertEqual(
            collection.kwargs.get("where"),
            {"chunk_schema_version": CHUNK_SCHEMA_VERSION},
        )
        self.assertEqual(inventory["total_documents"], 0)


class TestV1326EvidenceInventoryReasonRecovery(unittest.TestCase):
    def test_inventory_recovers_reason_for_legacy_metadata(self):
        class FakeCollection:
            def get(self, **kwargs):
                return {"metadatas": [{
                    "file_hash": "missing-cache-hash",
                    "document_key": "5549",
                    "title": "5549 sayılı Kanun",
                    "source_id": "resmi_gazete",
                    "source": "Resmi Gazete",
                    "law_number": "5549",
                    "status": "active",
                    "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                }]}
        rag = object.__new__(LocalRAG)
        rag.collection = FakeCollection()
        inventory = build_rag_inventory(rag)
        doc = inventory["documents"][0]
        self.assertTrue(doc["evidence_indexable"])
        self.assertTrue(doc["evidence_indexable_reason"])
        self.assertIn("eski metadata", doc["evidence_indexable_reason"])

    def test_explicit_reason_is_preserved(self):
        meta = {
            "evidence_indexable": False,
            "evidence_indexable_reason": "Test gerekçesi",
        }
        resolved = resolve_inventory_evidence_meta(meta, None)
        self.assertFalse(resolved["evidence_indexable"])
        self.assertEqual(resolved["evidence_indexable_reason"], "Test gerekçesi")


class TestV1334IndexabilityBoundary(unittest.TestCase):
    def test_generic_real_document_is_indexable_without_legal_structure(self):
        text = ("Operasyon prosedürü ve kontrol açıklamaları. " * 30).strip()
        meta = classify_legal_structure("İç Denetim Prosedürü", text, "Diğer")
        self.assertEqual(meta["structure_type"], "document")
        self.assertEqual(meta["parse_status"], "unstructured")
        self.assertFalse(meta["legal_structure_resolved"])
        self.assertTrue(meta["document_indexable"])
        self.assertTrue(meta["evidence_indexable"])

    def test_title_only_document_is_not_indexable(self):
        text = "İç Denetim Prosedürü"
        meta = classify_legal_structure("İç Denetim Prosedürü", text, "Diğer")
        self.assertFalse(meta["document_indexable"])
        self.assertFalse(meta["evidence_indexable"])
        self.assertEqual(meta["structure_type"], "title_only")

    def test_generic_document_gets_document_chunks(self):
        text = ("Bu belge genel belge şemasıyla aranabilir ve kaynak metin korunur. " * 45).strip()
        chunks = split_legal_chunks(text, max_chars=700)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(c.get("chunk_type") == "document" for c in chunks))
        self.assertTrue(all(not c.get("article_no") for c in chunks))

    def test_current_chroma_chunk_overrides_stale_false_without_cache(self):
        original = globals()["load_source_cache"]
        try:
            globals()["load_source_cache"] = lambda file_hash: ""
            resolved = resolve_inventory_evidence_meta({
                "file_hash": "already-indexed",
                "title": "Yapılandırılmamış BDDK Belgesi",
                "document_type": "BDDK Belge",
                "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                "evidence_indexable": "False",
                "evidence_indexable_reason": "Hukuki yapı çözümlenemedi",
            })
            self.assertTrue(resolved["evidence_indexable"])
            self.assertTrue(resolved["document_indexable"])
            self.assertIn("RAG chunk", resolved["evidence_indexable_reason"])
        finally:
            globals()["load_source_cache"] = original

    def test_legal_structure_failure_does_not_equal_index_failure(self):
        text = ("Bu metin özel bir hukuki bölüm işareti taşımıyor ancak gerçek içerik içeriyor. " * 25).strip()
        meta = classify_legal_structure("Genel BDDK Belgesi", text, "BDDK Belge")
        self.assertFalse(meta["legal_structure_resolved"])
        self.assertTrue(meta["document_indexable"])
        self.assertNotEqual(meta["parse_status"], "failed")


class TestV1335RetrievalBoundary(unittest.TestCase):
    def test_string_false_metadata_does_not_become_truthy(self):
        self.assertFalse(_metadata_bool("False"))
        self.assertTrue(_metadata_bool("True"))

    def test_legacy_real_chunk_is_retrievable_without_evidence_flag(self):
        meta = {
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "document_indexable": "False",
            "evidence_indexable": "False",
            "status": "active",
        }
        text = "Gerçek mevzuat belgesi içeriği ve denetim açıklaması. " * 20
        self.assertTrue(_chunk_is_retrievable(meta, text))

    def test_title_only_chunk_is_not_retrievable(self):
        meta = {
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "document_indexable": "False",
            "evidence_indexable": "False",
            "status": "active",
        }
        self.assertFalse(_chunk_is_retrievable(meta, "1045 BDDK Belge"))

    def test_rerank_does_not_collapse_all_document_chunks_to_one(self):
        results = []
        for i in range(3):
            results.append({
                "document": f"Belge içeriği {i} " * 30,
                "metadata": {
                    "source_id": "bddk_official",
                    "file_hash": "same-file",
                    "title": "Örnek belge",
                    "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                    "document_indexable": "True",
                    "status": "active",
                    "chunk_type": "document",
                    "chunk_no": str(i),
                },
                "distance": 0.2 + i * 0.01,
            })
        ranked = prefer_official_matches(results, limit=3, query="Belge içeriği")
        self.assertEqual(len(ranked), 3)
        self.assertEqual(
            [x["metadata"]["chunk_no"] for x in ranked],
            ["0", "1", "2"],
        )


class TestV1337IndexPipeline(unittest.TestCase):
    def test_inventory_cache_returns_full_structure(self):
        text = """Bankalarca Uygulanacak Tekdüzen Hesap Planı\n010 KASA\n011 EFEKTİF DEPOSU\n012 YOLDAKİ PARALAR - T.P.\n013 YOLDAKİ PARALAR - Y.P.\n014 SATIN ALINAN BANKA ÇEKLERİ - T.P."""
        meta = classify_legal_structure("BDDK Belge", text, "BDDK Belge")
        self.assertEqual(meta["structure_type"], "accounting_plan")
        self.assertGreaterEqual(meta["account_code_count"], 5)

    def test_accounting_split_preserves_account_codes(self):
        text = "010 KASA 011 EFEKTİF DEPOSU 012 YOLDAKİ PARALAR - T.P. 013 DİĞER HESAPLAR 014 ÇEKLER 015 MEVDUATLAR"
        chunks = split_legal_chunks(text, title="BDDK Belge", document_type="BDDK Belge")
        codes = [c.get("account_code") for c in chunks if c.get("account_code")]
        self.assertGreaterEqual(len(set(codes)), 5)
        self.assertTrue(all(c.get("chunk_type") == "accounting_code" for c in chunks if c.get("account_code")))

    def test_document_fallback_remains_available(self):
        text = "Bu belge özel hukuki hiyerarşi içermeyen ancak gerçek metin barındıran genel bir dokümandır. " * 8
        chunks = split_legal_chunks(text, title="Genel Doküman", document_type="Diğer")
        self.assertTrue(chunks)
        self.assertTrue(all(c.get("chunk_type") == "document" for c in chunks))


class TestV1337FinalPipeline(unittest.TestCase):
    def test_glued_uppercase_financial_words_are_repaired(self):
        text = "10304TEMİNATLILHİRAÇATFAKTORİNGALACAKLARI 103040RÜCÜEDİLEBİLİR 103041İŞTİRAKLER"
        repaired = repair_extracted_spacing(text, "BDDK Belge", "BDDK Belge")
        self.assertIn("10304 TEMİNATLI İHRACAT FAKTORİNG ALACAKLARI", repaired)
        self.assertIn("103040 RÜCÜ EDİLEBİLİR", repaired)

    def test_accounting_pipeline_creates_real_account_chunks(self):
        text = "10304TEMİNATLILHİRAÇATFAKTORİNGALACAKLARI 10401RÜCÜEDİLEMEZ 10402İŞTİRAKLER 10403DİĞERHESAPLAR 10404BİRHESAP 10405BAŞKAHESAP"
        chunks = split_legal_chunks(text)
        codes = {c.get("account_code") for c in chunks}
        self.assertIn("10304", codes)
        self.assertIn("10401", codes)
        self.assertIn("10405", codes)
        self.assertTrue(all(c.get("chunk_type") == "accounting_code" for c in chunks))


class TestV1338RetrievalIntegrity(unittest.TestCase):
    def test_query_document_reference(self):
        self.assertEqual(extract_query_document_reference("1041 kaldıraçlı işlemler"), "1041")
        self.assertIsNone(extract_query_document_reference("5549 sayılı Kanun yükümlülükleri"))

    def test_query_account_code_excludes_document_reference(self):
        self.assertEqual(extract_query_account_code("1045 280 hesabı"), "280")
        self.assertEqual(extract_query_account_code("1043 010 KASA"), "010")

    def test_rerank_exact_document_and_account(self):
        results = [
            {"document": "280 DİĞER ALACAKLAR", "distance": 0.6, "metadata": {
                "source_document_id": "1045", "account_code": "280", "title": "BDDK Belge",
                "authority_rank": 100, "source_type": "official", "status": "active"}},
            {"document": "280 DİĞER ALACAKLAR", "distance": 0.1, "metadata": {
                "source_document_id": "1043", "account_code": "280", "title": "BDDK Belge",
                "authority_rank": 100, "source_type": "official", "status": "active"}},
        ]
        ranked = _legal_rerank(results, "1045 280 hesabı", limit=2)
        self.assertEqual(ranked[0]["metadata"]["source_document_id"], "1045")
        self.assertTrue(ranked[0]["_exact_document_match"])
        self.assertTrue(ranked[0]["_exact_account_match"])

    def test_display_spacing_repairs_account_code_heading(self):
        self.assertEqual(
            repair_extracted_spacing("010KASA", "BDDK Belge", "BDDK Belge"),
            "010 KASA",
        )


class TestV1339RetrievalFallback(unittest.TestCase):
    def test_account_code_spacing_is_deterministic(self):
        self.assertEqual(repair_account_code_spacing("010KASA", "010"), "010 KASA")
        self.assertEqual(repair_account_code_spacing("011EFEKTİF DEPOSU", "011"), "011 EFEKTİF DEPOSU")
        self.assertEqual(repair_account_code_spacing("normal metin", "280"), "normal metin")

    def test_account_query_requires_document_boundary(self):
        query = "1045 280 hesabı"
        self.assertEqual(extract_query_document_reference(query), "1045")
        self.assertEqual(extract_query_account_code(query), "280")


class TestV1340DeterministicIdentityRetrieval(unittest.TestCase):
    def test_document_ref_can_match_title_when_source_document_id_missing(self):
        self.assertTrue(_chunk_matches_document_ref({"title":"1043","source":"BDDK"}, "010 KASA", "1043"))

    def test_document_ref_does_not_cross_documents(self):
        self.assertFalse(_chunk_matches_document_ref({"title":"1045","source":"BDDK","source_document_id":"1045"}, "280 DİĞER", "1043"))

    def test_account_ref_matches_text_when_metadata_stale(self):
        self.assertTrue(_chunk_matches_account_ref({"account_code":"780"}, "280 DİĞER ALACAKLAR", "280"))

    def test_account_ref_rejects_partial_numeric_match(self):
        self.assertFalse(_chunk_matches_account_ref({"account_code":"999"}, "1280 DİĞER", "280"))

    def test_account_display_repairs_inferred_code_when_metadata_is_stale(self):
        text="280DİĞER"
        inferred=re.search(r"(?<!\d)(\d{3,6})(?=\s*[A-ZÇĞİÖŞÜa-zçğıöşü])", text)
        self.assertEqual(inferred.group(1), "280")
        self.assertEqual(repair_account_code_spacing(text, inferred.group(1)), "280 DİĞER")


class TestV1347RAGDiagnosticHelpers(unittest.TestCase):
    def test_diagnostic_focus_preserves_spaces(self):
        value = "280 BORÇLU GEÇİCİ HESAPLAR-T.P."
        focused = _diagnostic_focus_text(value, "280")
        self.assertIn("280 BORÇLU GEÇİCİ", focused)

    def test_diagnostic_quality_flags_glued_text(self):
        glued = _diagnostic_quality("Ülkemizdetedavüldebulunanbanknot")
        spaced = _diagnostic_quality("Ülkemizde tedavülde bulunan banknot")
        self.assertGreater(glued["long_glued"], 0)
        self.assertLess(spaced["long_glued"], glued["long_glued"])


class TestV1348Provenance(unittest.TestCase):
    def test_sha256_text_differs_for_different_cache_content(self):
        self.assertNotEqual(_sha256_text("1043 metin"), _sha256_text("1045 metin"))
        self.assertEqual(_sha256_text("a b", compact=True), _sha256_text("ab", compact=True))

    def test_diagnostic_cache_excerpt_does_not_mutate_text(self):
        original = "280 BORÇLU GEÇİCİ HESAPLAR"
        self.assertEqual(_diagnostic_cache_excerpt(original, "280"), original)


class TestV1377QueryIdentityResolution(unittest.TestCase):
    """v0.13.77: 3 haneli belge kimliği ile hesap kodu çakışmasını veriyle çözer."""

    def test_v1377_three_digit_leading_document_ref_does_not_change_generic_parser(self):
        self.assertIsNone(extract_query_document_reference("111 Bankacılık Veri Transfer Sistemi"))
        self.assertEqual(extract_query_account_code("111 Bankacılık Veri Transfer Sistemi"), "111")

    def test_v1377_three_digit_account_query_remains_account_without_document_match(self):
        class _Collection:
            def get(self, **kwargs):
                return {"documents": ["010 KASA"], "metadatas": [{"source_document_id": "999", "chunk_schema_version": CHUNK_SCHEMA_VERSION}]}

        class _Rag:
            collection = _Collection()

        doc_ref, account_ref = _resolve_query_document_reference_from_collection(
            _Rag(), "010 KASA", None, "010"
        )
        self.assertIsNone(doc_ref)
        self.assertEqual(account_ref, "010")


class TestV1379SourceCacheRechunkReindex(unittest.TestCase):
    """v0.13.79: F — source_cache yenileme + rechunk/reindex bütünlüğü."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="mufettis_f_cache_"))
        self.cache_dir = self.tmp_dir / "source_cache"
        self.db_path = self.tmp_dir / "chroma"
        self.index_path = self.tmp_dir / "ingest_index.json"
        self.old_cache_dir = globals().get("SOURCE_CACHE_DIR")
        globals()["SOURCE_CACHE_DIR"] = self.cache_dir
        self.file_hash = "f" * 64
        self.title = "F Test Belgesi"

    def tearDown(self):
        globals()["SOURCE_CACHE_DIR"] = self.old_cache_dir
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _meta(self):
        return {
            "file_hash": self.file_hash,
            "title": self.title,
            "source": "Resmi Gazete",
            "source_id": "resmi_gazete",
            "source_type": "official",
            "authority_rank": 100,
            "status": "active",
            "source_document_id": "F-001",
            "structure_type": "article",
        }

    def test_f1_same_hash_cache_overwrites_old_extraction(self):
        old = "MADDE 1 - BOZUK ESKI EXTRACTION\nÜlkemizdetedavüldebulunanbanknot"
        new = "MADDE 1 - GÜNCEL EXTRACTION\nÜlkemizde tedavülde bulunan banknot"
        save_source_cache(self.file_hash, old)
        self.assertEqual(load_source_cache(self.file_hash), old)
        save_source_cache(self.file_hash, new)
        self.assertEqual(load_source_cache(self.file_hash), new)

    def test_f2_rechunk_uses_current_cache_not_stale_cache(self):
        old = "MADDE 1 - BOZUK METIN\nEski extraction kaydi"
        new = "MADDE 1 - GUNCEL METIN\nYeni extraction kaydi"
        save_source_cache(self.file_hash, new)
        index = IngestIndex(self.index_path)
        index.upsert(self.file_hash, self._meta())

        class FakeRag:
            def __init__(self):
                self.calls = []

            def ingest_text(self, text, meta, force=False):
                self.calls.append({"text": text, "meta": dict(meta), "force": force})
                return {"status": "ingested", "count": 1}

        rag = FakeRag()
        result = rechunk_cached_document(rag, index, self.file_hash)
        self.assertEqual(result["status"], "reindexed")
        self.assertEqual(rag.calls[0]["text"], new)
        self.assertTrue(rag.calls[0]["force"])

    def test_f3_rechunk_refreshes_index_chunk_count_from_current_cache(self):
        new = "MADDE 1 - GUNCEL METIN\nYeni extraction kaydi"
        save_source_cache(self.file_hash, new)
        index = IngestIndex(self.index_path)
        meta = self._meta()
        meta["chunk_count"] = 999
        index.upsert(self.file_hash, meta)

        class FakeRag:
            def ingest_text(self, text, meta, force=False):
                return {"status": "ingested", "count": 1, "chunks": [{"text": text, "chunk_type": "article", "chunk_no": 0}]}

        result = rechunk_cached_document(FakeRag(), index, self.file_hash)
        self.assertEqual(result["chunks"], 1)
        self.assertEqual(index.get(self.file_hash)["chunk_count"], 1)
        self.assertEqual(index.get(self.file_hash)["chunk_schema_version"], CHUNK_SCHEMA_VERSION)

    def test_f4_reindex_cached_documents_forces_current_cache_into_chroma_path(self):
        new = "MADDE 1 - GUNCEL METIN\nYeni extraction kaydi"
        save_source_cache(self.file_hash, new)
        index = IngestIndex(self.index_path)
        index.upsert(self.file_hash, self._meta())

        class FakeRag:
            def __init__(self):
                self.seen = []

            def ingest_text(self, text, meta, force=False):
                self.seen.append((text, force))
                return {"status": "ingested", "count": 2}

        rag = FakeRag()
        result = reindex_cached_documents(rag, index, force=True)
        self.assertEqual(result["reindexed"], 1)
        self.assertEqual(rag.seen[0], (new, True))

    def test_f5_real_chroma_old_text_is_removed_after_same_hash_reindex(self):
        if chromadb is None:
            self.skipTest("chromadb kurulu değil")
        old = "MADDE 1 - BOZUK EXTRACTION\nBOZUK_METIN_F_TEST_UNIQUE"
        new = "MADDE 1 - GUNCEL EXTRACTION\nGUNCEL_METIN_F_TEST_UNIQUE"
        save_source_cache(self.file_hash, old)
        index = IngestIndex(self.index_path)
        index.upsert(self.file_hash, self._meta())

        rag = LocalRAG(db_path=self.db_path, allow_download=False)
        rag.embed = lambda docs, batch_size=None: [[0.1, 0.2, 0.3] for _ in docs]
        first = rechunk_cached_document(rag, index, self.file_hash)
        self.assertEqual(first["status"], "reindexed")
        got_old = rag.collection.get(where={"file_hash": self.file_hash}, include=["documents"])
        self.assertTrue(any(old.split("\n", 1)[1] in str(doc) for doc in (got_old.get("documents", []) or [])))

        save_source_cache(self.file_hash, new)
        second = rechunk_cached_document(rag, index, self.file_hash)
        self.assertEqual(second["status"], "reindexed")
        got_new = rag.collection.get(where={"file_hash": self.file_hash}, include=["documents"])
        docs = got_new.get("documents", []) or []
        self.assertTrue(docs)
        self.assertTrue(any("GUNCEL_METIN_F_TEST_UNIQUE" in str(doc) for doc in docs))
        self.assertFalse(any("BOZUK_METIN_F_TEST_UNIQUE" in str(doc) for doc in docs))


class TestV1381ProductionRepairScanner(unittest.TestCase):
    """v0.13.81: production source_cache + Chroma kalite tarama ve güvenli repair planı."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="mufettis_g_scanner_"))
        self.cache_dir = self.tmp_dir / "source_cache"
        self.index_path = self.tmp_dir / "ingest_index.json"
        self.old_cache_dir = globals().get("SOURCE_CACHE_DIR")
        globals()["SOURCE_CACHE_DIR"] = self.cache_dir
        self.bad_hash = "a" * 64
        self.clean_hash = "b" * 64
        self.index = IngestIndex(self.index_path)

        self.index.upsert(self.bad_hash, {
            "file_hash": self.bad_hash,
            "title": "1030 Bozuk Belge",
            "source_id": "bddk_official",
            "source": "BDDK",
            "status": "active",
        })
        self.index.upsert(self.clean_hash, {
            "file_hash": self.clean_hash,
            "title": "1031 Temiz Belge",
            "source_id": "bddk_official",
            "source": "BDDK",
            "status": "active",
        })
        save_source_cache(self.bad_hash, "MADDE 1 - Ülkemizdetedavüldebulunanbanknot ve madeniparalar")
        save_source_cache(self.clean_hash, "MADDE 1 - Ülkemizde tedavülde bulunan banknot ve madeni paralar.")

    def tearDown(self):
        globals()["SOURCE_CACHE_DIR"] = self.old_cache_dir
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_f6_scanner_flags_glued_cache_without_mutation(self):
        before = load_source_cache(self.bad_hash)
        result = scan_production_rag_quality(self.index)
        self.assertEqual(result["repair_candidates"], 1)
        self.assertIn("cache_glued_text", result["candidates"][0]["reasons"])
        self.assertEqual(load_source_cache(self.bad_hash), before)

    def test_f7_scanner_keeps_clean_document_out(self):
        result = scan_production_rag_quality(self.index)
        candidate_hashes = {row["file_hash"] for row in result["candidates"]}
        self.assertNotIn(self.clean_hash, candidate_hashes)

    def test_f7_scanner_flags_glued_chroma_chunk(self):
        class FakeCollection:
            def get(self, **kwargs):
                target = str((kwargs.get("where") or {}).get("file_hash", ""))
                if target == self_bad_hash:
                    return {"documents": ["Ülkemizdetedavüldebulunanbanknot"]}
                return {"documents": ["Ülkemizde tedavülde bulunan banknot"]}

        class FakeRag:
            collection = FakeCollection()

        self_bad_hash = self.bad_hash
        result = scan_production_rag_quality(self.index, rag=FakeRag())
        bad = next(row for row in result["documents"] if row["file_hash"] == self.bad_hash)
        self.assertIn("chroma_glued_text", bad["reasons"])
        self.assertTrue(bad["repair_candidate"])

    def test_f8_repair_plan_requires_matching_index_hash_and_dry_run_changes_nothing(self):
        pdf_path = self.tmp_dir / "1030.pdf"
        pdf_path.write_bytes(b"%PDF-test-f81")
        old = load_source_cache(self.bad_hash)

        def fake_extract(pdf_bytes):
            self.assertEqual(pdf_bytes, b"%PDF-test-f81")
            return ("MADDE 1 - Ülkemizde tedavülde bulunan banknot ve madeni paralar.", 1, False)

        # PDF bytes hash'i index'teki bad_hash ile aynı olmadığı için güvenli biçimde reddedilir.
        result = repair_production_document_from_pdf(
            rag=object(),
            index=self.index,
            pdf_path=pdf_path,
            dry_run=True,
            extractor=fake_extract,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing_index")
        self.assertEqual(load_source_cache(self.bad_hash), old)

    def test_f9_real_repair_path_updates_cache_and_forces_reindex(self):
        pdf_path = self.tmp_dir / "realish.pdf"
        pdf_bytes = b"F81-REALISH-PDF"
        pdf_path.write_bytes(pdf_bytes)
        real_hash = sha256_bytes(pdf_bytes)
        self.index.upsert(real_hash, {
            "file_hash": real_hash,
            "title": "F81 Repair Belgesi",
            "source_id": "bddk_official",
            "source": "BDDK",
            "status": "active",
        })
        save_source_cache(real_hash, "MADDE 1 - BOZUK EskiMetin")

        class FakeRag:
            def __init__(self):
                self.calls = []
            def ingest_text(self, text, meta, force=False):
                self.calls.append((text, dict(meta), force))
                return {"status": "ingested", "count": 2}

        rag = FakeRag()

        def fake_extract(pdf_bytes):
            return ("MADDE 1 - Ülkemizde tedavülde bulunan banknot ve madeni paralar.", 1, False)

        result = repair_production_document_from_pdf(
            rag=rag,
            index=self.index,
            pdf_path=pdf_path,
            dry_run=False,
            extractor=fake_extract,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "reindexed")
        self.assertEqual(result["reindexed_chunks"], 2)
        self.assertEqual(load_source_cache(real_hash), "MADDE 1 - Ülkemizde tedavülde bulunan banknot ve madeni paralar.")
        self.assertTrue(rag.calls[0][2])
        self.assertEqual(self.index.get(real_hash)["chunk_count"], 2)


class TestV1376RealChromaRetrieval(unittest.TestCase):
    """v0.13.76: E — gerçek 111.pdf -> chunk -> metadata/provenance -> Chroma -> retrieval."""

    @classmethod
    def setUpClass(cls):
        if chromadb is None or SentenceTransformer is None:
            raise unittest.SkipTest("RAG bağımlılıkları yok")
        cls.tmp_dir = Path(tempfile.mkdtemp(prefix="mufettis_e_rag_"))
        cls.db_path = cls.tmp_dir / "chroma"
        cls.cache_dir = cls.tmp_dir / "source_cache"
        cls._old_cache_dir = globals().get("SOURCE_CACHE_DIR")
        globals()["SOURCE_CACHE_DIR"] = cls.cache_dir
        cls.pdf_path = BASE_DIR / "111.pdf"
        if not cls.pdf_path.exists() or pdfium is None:
            globals()["SOURCE_CACHE_DIR"] = cls._old_cache_dir
            shutil.rmtree(cls.tmp_dir, ignore_errors=True)
            raise unittest.SkipTest("Gerçek 111.pdf veya PDFium yok")
        pdf_bytes = cls.pdf_path.read_bytes()
        cls.file_hash = sha256_bytes(pdf_bytes)
        pages = _extract_pdfium_spatial_pages(pdf_bytes)
        cls.raw_text = _join_pdf_pages(pages)
        cls.normalized = normalize_extracted_text(cls.raw_text)
        save_source_cache(cls.file_hash, cls.normalized)
        base_meta = {
            "file_hash": cls.file_hash,
            "title": "111.pdf",
            "url": "",
            "source": "Resmi Gazete",
            "source_id": "resmi_gazete",
            "source_type": "official",
            "authority_rank": 100,
            "version_number": 1,
            "status": "active",
        }
        cls.meta = extract_legal_metadata("111.pdf", cls.normalized, base_meta)
        # Test provenance kimliği gerçek belge kimliğini temsil eder; ağ çağrısı gerektirmez.
        cls.meta["source_document_id"] = "111"
        cls.rag = LocalRAG(db_path=cls.db_path, allow_download=False)
        cls.ingest_result = cls.rag.ingest_text(cls.normalized, cls.meta, force=True)
        cls.expected_chunks = list(cls.ingest_result.get("chunks", []) or [])

    @classmethod
    def tearDownClass(cls):
        try:
            if getattr(cls, "_old_cache_dir", None) is not None:
                globals()["SOURCE_CACHE_DIR"] = cls._old_cache_dir
            else:
                globals().pop("SOURCE_CACHE_DIR", None)
        finally:
            shutil.rmtree(getattr(cls, "tmp_dir", Path(".")), ignore_errors=True)

    def test_e1_real_pdf_is_persisted_as_chunks_in_chroma(self):
        self.assertEqual(self.ingest_result.get("status"), "ingested")
        self.assertGreater(self.ingest_result.get("count", 0), 0)
        self.assertEqual(self.rag.count(), self.ingest_result.get("count"))

    def test_e2_real_chunks_have_identity_metadata_and_provenance(self):
        got = self.rag.collection.get(
            where={"file_hash": self.file_hash},
            include=["documents", "metadatas"],
        )
        ids = got.get("ids", []) or []
        metas = got.get("metadatas", []) or []
        docs = got.get("documents", []) or []
        self.assertEqual(len(ids), len(metas))
        self.assertEqual(len(ids), len(docs))
        self.assertTrue(ids)
        for rid, meta, doc in zip(ids, metas, docs):
            self.assertTrue(str(rid).startswith(self.file_hash[:24]))
            self.assertEqual(str(meta.get("file_hash", "")), self.file_hash)
            self.assertEqual(str(meta.get("chunk_schema_version", "")), CHUNK_SCHEMA_VERSION)
            self.assertEqual(str(meta.get("source", "")), "Resmi Gazete")
            self.assertEqual(str(meta.get("source_type", "")), "official")
            self.assertEqual(str(meta.get("authority_rank", "")), "100")
            self.assertEqual(str(meta.get("source_document_id", "")), "111")
            self.assertTrue(str(meta.get("chunk_type", "")))
            self.assertTrue(str(meta.get("chunk_no", "")) != "")
            self.assertTrue(str(doc).strip())

    def test_e3_source_cache_hash_matches_chroma_provenance(self):
        cache_text = load_source_cache(self.file_hash)
        self.assertTrue(cache_text.strip())
        self.assertEqual(_sha256_text(cache_text), _sha256_text(self.normalized))
        got = self.rag.collection.get(
            where={"file_hash": self.file_hash},
            include=["metadatas"],
        )
        metas = got.get("metadatas", []) or []
        self.assertTrue(metas)
        self.assertTrue(all(str(m.get("file_hash", "")) == self.file_hash for m in metas))

    def test_e4_exact_document_retrieval_returns_111_chunks(self):
        results = self.rag.query("111 Bankacılık Veri Transfer Sistemi", k=5)
        self.assertTrue(results)
        self.assertTrue(all(str(r.get("metadata", {}).get("source_document_id", "")) == "111" for r in results))
        # E4 exact-document testi kimliği doğrular; içerik/semantik uygunluğu E5 testi ayrıca doğrular.
        self.assertTrue(any(str(r.get("document", "")).strip() for r in results))

    def test_e5_semantic_retrieval_returns_relevant_real_chunk(self):
        query = "raporlamalarda uygulanacak idari para cezaları ve kontrol hataları"
        results = self.rag.query(query, k=5)
        self.assertTrue(results)
        top = results[0]
        text = str(top.get("document", ""))
        self.assertTrue(
            any(term in text.lower() for term in (
                "idari para cezaları", "kontrol hataları", "raporlamalarda",
            ))
        )
        self.assertEqual(str(top.get("metadata", {}).get("file_hash", "")), self.file_hash)

    def test_e6_rag_diagnostic_sees_exact_chroma_and_source_cache_provenance(self):
        diag = run_rag_diagnostic(self.rag, "111")
        self.assertTrue(diag.get("ok"), diag)
        self.assertGreaterEqual(int(diag.get("match_count", 0) or 0), 1)
        primary = diag.get("primary", {}) or {}
        meta = primary.get("metadata", {}) or {}
        self.assertEqual(str(meta.get("file_hash", "")), self.file_hash)
        self.assertEqual(str(meta.get("source_document_id", "")), "111")
        self.assertTrue((diag.get("source", {}) or {}).get("cache_exists"))


class TestV1319Performance(unittest.TestCase):
    def test_embed_passes_batch_size(self):
        rag=object.__new__(LocalRAG)
        calls=[]
        class FakeModel:
            device="cpu"
            def encode(self, texts, **kwargs):
                calls.append((len(texts),kwargs.get("batch_size")))
                return np.zeros((len(texts),4),dtype=np.float32)
        rag._model=FakeModel()
        result=rag.embed(["a","b","c"],batch_size=7)
        self.assertEqual(len(result),3)
        self.assertEqual(calls[0][1],7)

    def test_ingest_batches_embedding_and_upsert(self):
        rag=object.__new__(LocalRAG)
        rag._model=type("M",(),{"device":"cpu"})()

        class FakeCollection:
            def __init__(self):
                self.upserts=[]
            def get(self, **kwargs):
                return {"ids":[]}
            def upsert(self, **kwargs):
                self.upserts.append(kwargs)

        rag.collection=FakeCollection()
        rag.embed=lambda texts,batch_size=None: np.zeros(
            (len(texts),4),dtype=np.float32
        ).tolist()

        original_split=globals()["split_legal_chunks"]
        original_mask=globals()["mask_sensitive"]
        try:
            globals()["split_legal_chunks"]=lambda text: [
                {
                    "text":f"chunk-{i}",
                    "article_prefix":"",
                    "article_no":"1",
                    "article_title":"",
                    "paragraph_no":"1",
                    "item_no":"",
                    "subitem_no":"",
                    "section_no":"",
                    "chunk_type":"paragraph",
                    "subchunk_no":0,
                    "chunk_no":i,
                }
                for i in range(70)
            ]
            globals()["mask_sensitive"]=lambda x:x
            result=rag.ingest_text(
                "sample",
                {"file_hash":"abcdef0123456789","title":"Test"},
                force=False,
            )
            self.assertEqual(result["count"],70)
            self.assertGreaterEqual(len(rag.collection.upserts),1)
            total_upserted=sum(len(x["ids"]) for x in rag.collection.upserts)
            self.assertEqual(total_upserted,70)
        finally:
            globals()["split_legal_chunks"]=original_split
            globals()["mask_sensitive"]=original_mask


class TestV1320PerformancePipeline(unittest.TestCase):
    def test_web_cache_key_is_stable(self):
        self.assertEqual(web_cache_key("https://example.com/a"), web_cache_key("https://example.com/a"))
        self.assertNotEqual(web_cache_key("https://example.com/a"), web_cache_key("https://example.com/b"))

    def test_fast_http_fetch_uses_conditional_headers(self):
        harvester=object.__new__(WebSourceHarvester)
        harvester.source_id="bddk_official"
        harvester.profile=SOURCE_DEFINITIONS["bddk_official"]
        harvester.local_only=False
        harvester.user_agent="MufettisAI/test"
        harvester.index=type("I",(),{"get":lambda self,h: {"file_hash":h}})()
        calls=[]

        class Resp:
            status_code=304
            headers={}
            content=b""
        class Sess:
            def get(self,url,headers,timeout):
                calls.append(headers)
                return Resp()
            def close(self): pass

        harvester._build_session=lambda: Sess()
        url="https://www.bddk.gov.tr/test.pdf"
        cached_hash="abc123"
        save_web_cache_meta(url,{"file_hash":cached_hash,"meta":{"file_hash":cached_hash},"etag":"abc","last_modified":"y"})
        harvester.is_robots_allowed=lambda u: True
        payload,error=harvester.fetch_candidate_http_fast({"url":url,"title":"test"})
        self.assertIsNone(error)
        self.assertEqual(payload["status"],"not_modified")
        self.assertEqual(calls[0]["If-None-Match"],"abc")
        self.assertEqual(calls[0]["If-Modified-Since"],"y")

    def test_fast_embedding_path_keeps_batch_semantics(self):
        rag=object.__new__(LocalRAG)
        rag._model=type("M",(),{"device":"cpu"})()
        calls=[]
        rag.embed=lambda texts,batch_size=None: (
            calls.append(len(texts)) or np.zeros((len(texts),4),dtype=np.float32).tolist()
        )
        class Coll:
            def __init__(self): self.n=0
            def get(self,**kwargs): return {"ids":[]}
            def upsert(self,**kwargs): self.n+=len(kwargs["ids"])
        rag.collection=Coll()
        original_split=globals()["split_legal_chunks"]
        original_mask=globals()["mask_sensitive"]
        try:
            globals()["split_legal_chunks"]=lambda text:[
                {"text":f"c{i}","article_prefix":"","article_no":"1","article_title":"",
                 "paragraph_no":"1","item_no":"","subitem_no":"","section_no":"",
                 "chunk_type":"paragraph","subchunk_no":0,"chunk_no":i}
                for i in range(65)
            ]
            globals()["mask_sensitive"]=lambda x:x
            r=rag.ingest_text("x",{"file_hash":"perf001","title":"x"})
            self.assertEqual(r["count"],65)
            self.assertEqual(sum(calls),65)
            self.assertEqual(rag.collection.n,65)
        finally:
            globals()["split_legal_chunks"]=original_split
            globals()["mask_sensitive"]=original_mask

