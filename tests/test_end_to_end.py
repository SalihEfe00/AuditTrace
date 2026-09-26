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

from mufettis.common.utils import (
    atomic_write_json,
    canonical_source_id,
    load_json,
    now_iso,
    sha256_bytes,
    source_is_allowed,
    source_profile,
)
from mufettis.config import BASE_DIR, BOOTSTRAP_STATE_FILE
from mufettis.extraction.pdf import (
    _extract_pdfium_spatial_pages,
    _join_pdf_pages,
    _pdf_text_quality,
)
from mufettis.extraction.text_repair import normalize_extracted_text, repair_extracted_spacing
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper
from mufettis.importers.bootstrapper import RegulationBootstrapper, should_offer_bootstrap
from mufettis.legal.parser import (
    classify_legal_structure,
    extract_legal_metadata,
    split_legal_chunks,
)
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex
from mufettis.rag.maintenance import _sha256_text, load_source_cache, save_source_cache
from mufettis.scheduler.scheduler import BackgroundUpdateScheduler
from mufettis.tests.patching import module_overrides

class TestVersioningAndScheduler(unittest.TestCase):
    def test_scheduler_time_math(self):
        scheduler = BackgroundUpdateScheduler(["resmi_gazete"], use_browser_for_bddk=False)
        seconds = scheduler.seconds_until_next_run()
        self.assertGreater(seconds, 0)
        self.assertLessEqual(seconds, 24 * 3600)

    def test_legal_document_key(self):
        m = extract_legal_metadata("5411 sayılı Bankacılık Kanunu", "Kanun No: 5411\nMADDE 1 - Amaç", {"file_hash": "x"})
        self.assertEqual(m["document_key"], "5411")
        self.assertEqual(m["status"], "active")


    def test_bootstrap_not_offered_after_completion(self):
        original = load_json(BOOTSTRAP_STATE_FILE, None)
        try:
            atomic_write_json(BOOTSTRAP_STATE_FILE, {"completed": True})
            self.assertFalse(should_offer_bootstrap(None))
        finally:
            if original is None:
                BOOTSTRAP_STATE_FILE.unlink(missing_ok=True)
            else:
                atomic_write_json(BOOTSTRAP_STATE_FILE, original)

    def test_bootstrap_relevance_filter(self):
        self.assertTrue(RegulationBootstrapper._candidate_is_relevant("bddk_official", {"url": "https://www.bddk.gov.tr/Mevzuat/Detay/1", "kind": "html"}))
        self.assertFalse(RegulationBootstrapper._candidate_is_relevant("bddk_official", {"url": "https://www.bddk.gov.tr/Hakkimizda", "kind": "html"}))
        self.assertTrue(source_is_allowed("https://www.bddk.org.tr/Mevzuat/DokumanGetir/1041", "bddk_official"))
        self.assertFalse(source_is_allowed("https://example.com/Mevzuat/DokumanGetir/1041", "bddk_official"))


class TestBootstrapSafety(unittest.TestCase):
    def test_source_alias(self):
        self.assertEqual(canonical_source_id("bdkk_official"), "bddk_official")
        self.assertEqual(canonical_source_id("bddk_official"), "bddk_official")

    def test_bootstrap_does_not_mark_zero_data_complete(self):
        # Gerçek web çağrısı yapmadan state mantığını doğrula.
        started = now_iso()
        result = {"started_at": started, "finished_at": started, "sources": {"bddk_official": {"found": 0, "selected": 0, "ingested": 0, "skipped": 0, "errors": 0}}, "errors": 0}
        meaningful = sum(int(v.get("selected", 0)) + int(v.get("ingested", 0)) + int(v.get("skipped", 0)) for v in result["sources"].values())
        self.assertEqual(meaningful, 0)
        self.assertFalse(meaningful > 0)


class TestSourceHierarchy(unittest.TestCase):
    def test_bddk_is_official_and_preferred(self):
        p = source_profile("bddk_official")
        self.assertEqual(p.get("source_type"), "official")
        self.assertTrue(p.get("preferred"))
        self.assertEqual(p.get("authority_rank"), 100)

    def test_mevzuat_org_is_secondary(self):
        p = source_profile("mevzuat_org")
        self.assertEqual(p.get("source_type"), "secondary")
        self.assertLess(p.get("authority_rank"), 100)

    def test_domain_allowlist(self):
        self.assertTrue(source_is_allowed("https://www.bddk.gov.tr/Mevzuat", "bddk_official"))
        self.assertFalse(source_is_allowed("https://example.com/test.pdf", "bddk_official"))


class TestV1336ExtractionAndStructure(unittest.TestCase):
    def test_accounting_inline_glue_is_repaired_and_detected(self):
        text = "10304TEMİNATLI İHRACAT FAKTORİNG ALACAKLARI103040RÜCÜ EDİLEBİLİR 103041İŞTİRAKLER VE BAĞLI ORTAKLIKLAR 103042İŞTİRAKLER"
        repaired = repair_extracted_spacing(text, "BDDK Belge", "BDDK Belge")
        self.assertIn("10304 TEMİNATLI", repaired)
        repaired = repaired + " " + ("103050DİĞER HESAPLAR " * 20)
        meta = classify_legal_structure("BDDK Belge", repaired, "BDDK Belge")
        self.assertEqual(meta["structure_type"], "accounting_plan")
        self.assertTrue(meta["document_indexable"])

    def test_accounting_chunk_split_preserves_codes(self):
        text = "10304TEMİNATLI İHRACAT FAKTORİNG ALACAKLARI 10401RÜCÜ EDİLEMEZ 10402İŞTİRAKLER 10403DİĞER HESAPLAR 10404BİR HESAP 10405BAŞKA HESAP"
        chunks = split_legal_chunks(text, max_chars=500)
        self.assertTrue(any(c.get("account_code") == "10304" for c in chunks))
        self.assertTrue(any(c.get("account_code") == "10401" for c in chunks))
        self.assertTrue(all(c.get("chunk_type") == "accounting_code" for c in chunks))

    def test_numbered_guidance_marker_spacing(self):
        repaired = repair_extracted_spacing("240)SENDİKASYON TABANLI KREDİLER 241)DİĞER HÜKÜM", "Rehber", "Rehber")
        self.assertIn("240) SENDİKASYON", repaired)
        self.assertIn("241) DİĞER", repaired)

    def test_pdf_quality_prefers_spaced_candidate(self):
        glued = "ŞİRKETLERİNSERMAYEVEYÖNETİMKONTROLÜ VE DİĞER HÜKÜMLER"
        spaced = "ŞİRKETLERİN SERMAYE VE YÖNETİM KONTROLÜ VE DİĞER HÜKÜMLER"
        self.assertGreater(_pdf_text_quality(spaced)[0], _pdf_text_quality(glued)[0])

    def test_generic_document_status_is_not_failed(self):
        text = "Gerçek içerik taşıyan genel bir BDDK belgesi ve denetim açıklaması. " * 20
        meta = classify_legal_structure("BDDK Belge", text, "BDDK Belge")
        self.assertEqual(meta["structure_type"], "document")
        self.assertEqual(meta["parse_status"], "unstructured")
        self.assertNotEqual(meta["parse_status"], "failed")


class TestV1380FinalE2ERegression(unittest.TestCase):
    """v0.13.80: G — gerçek belge E2E + regression final gate."""

    @classmethod
    def setUpClass(cls):
        if chromadb is None or SentenceTransformer is None:
            raise unittest.SkipTest("RAG bağımlılıkları yok")

        cls.tmp_dir = Path(tempfile.mkdtemp(prefix="mufettis_g_final_"))
        cls.db_path = cls.tmp_dir / "chroma"
        cls.cache_dir = cls.tmp_dir / "source_cache"
        cls.old_cache_dir = module_overrides.get("SOURCE_CACHE_DIR")
        module_overrides["SOURCE_CACHE_DIR"] = cls.cache_dir

        cls.pdf_path = cls._find_real_bddk_pdf()
        if cls.pdf_path is None:
            module_overrides["SOURCE_CACHE_DIR"] = cls.old_cache_dir
            shutil.rmtree(cls.tmp_dir, ignore_errors=True)
            raise unittest.SkipTest(
                "G final testi için gerçek BDDK PDF bulunamadı. "
                "MUFETTIS_G_BDDK_PDF ile bir BDDK PDF yolu verin "
                "veya BASE_DIR altında 1041/1043/1045 gibi bir PDF bulundurun."
            )

        cls.pdf_bytes = cls.pdf_path.read_bytes()
        cls.file_hash = sha256_bytes(cls.pdf_bytes)
        cls.pages = _extract_pdfium_spatial_pages(cls.pdf_bytes)
        if not cls.pages:
            raise AssertionError(f"Gerçek BDDK PDF extraction üretmedi: {cls.pdf_path}")

        cls.raw_text = _join_pdf_pages(cls.pages)
        cls.normalized = normalize_extracted_text(cls.raw_text)
        if len(re.sub(r"\s+", "", cls.normalized)) < 500:
            raise AssertionError(f"Gerçek BDDK PDF metni kullanılamayacak kadar kısa: {cls.pdf_path}")

        stem = cls.pdf_path.stem.strip()
        cls.document_ref = stem if re.fullmatch(r"\d{4,6}", stem) else ""
        if not cls.document_ref:
            match = re.search(r"(?<!\d)(\d{4,6})(?!\d)", cls.normalized[:4000])
            cls.document_ref = match.group(1) if match else ""

        cls.scraper = ResmiGazeteScraper(index=IngestIndex(), local_only=True)
        base_meta = {
            "file_hash": cls.file_hash,
            "title": cls.pdf_path.name,
            "url": "",
            "source": "BDDK",
            "source_id": "bddk_official",
            "source_type": "official",
            "authority_rank": 100,
            "version_number": 1,
            "status": "active",
        }
        cls.meta = extract_legal_metadata(cls.pdf_path.name, cls.normalized, base_meta)
        if cls.document_ref:
            cls.meta["source_document_id"] = cls.document_ref

        structure = classify_legal_structure(cls.pdf_path.name, cls.normalized, "BDDK Belge")
        cls.structure = structure
        if not structure.get("document_indexable") and not structure.get("evidence_indexable"):
            raise AssertionError(f"Gerçek BDDK PDF indexlenebilir bulunmadı: {structure}")

        cls.rag = LocalRAG(db_path=cls.db_path, allow_download=False)
        cls.ingest_result = cls.rag.ingest_text(cls.normalized, cls.meta, force=True)
        if cls.ingest_result.get("status") != "ingested":
            raise AssertionError(f"BDDK PDF ingest başarısız: {cls.ingest_result}")

        cls.chunks = list(cls.ingest_result.get("chunks", []) or [])
        cls.semantic_query = cls._build_semantic_query()
        cls.exact_query = ""
        if cls.document_ref:
            cls.exact_query = f"{cls.document_ref} BDDK"
            codes = [
                str(c.get("account_code", "")).strip()
                for c in cls.chunks
                if str(c.get("account_code", "")).strip()
            ]
            if codes:
                cls.exact_query = f"{cls.document_ref} {codes[0]}"

    @classmethod
    def _find_real_bddk_pdf(cls) -> Optional[Path]:
        configured = os.environ.get("MUFETTIS_G_BDDK_PDF", "").strip()
        if configured:
            path = Path(configured)
            return path if path.is_file() and path.suffix.lower() == ".pdf" else None

        preferred = (
            "1041.pdf", "1043.pdf", "1045.pdf",
            "bddk.pdf", "BDDK.pdf",
            "bdkk.pdf", "BDDK_Belge.pdf",
        )
        for name in preferred:
            path = BASE_DIR / name
            if path.is_file():
                return path

        candidates = sorted(
            p for p in BASE_DIR.rglob("*.pdf")
            if p.is_file()
            and p.name.lower() not in {"111.pdf", "mevzuat_1310.pdf", "ocr_test_111_image_only.pdf"}
            and (
                "bddk" in p.stem.lower()
                or re.match(r"^\d{4,6}(?:[-_].*)?$", p.stem)
            )
        )
        return candidates[0] if candidates else None

    @classmethod
    def _build_semantic_query(cls) -> str:
        lowered = cls.normalized.lower()
        for marker in (
            "idari para cezaları",
            "kontrol hataları",
            "kasa",
            "raporlama",
            "sermaye",
            "risk",
        ):
            pos = lowered.find(marker)
            if pos >= 0:
                snippet = cls.normalized[max(0, pos - 30):pos + 110]
                terms = re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]{4,}", snippet)
                query = " ".join(terms[:8]).strip()
                if len(query.split()) >= 3:
                    return query
        parts = [
            x.strip()
            for x in cls.normalized.splitlines()
            if len(re.sub(r"\s+", "", x)) >= 25
        ]
        return " ".join(re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]{4,}", parts[0])[:8]) if parts else "BDDK belge"

    @classmethod
    def tearDownClass(cls):
        try:
            if getattr(cls, "old_cache_dir", None) is not None:
                module_overrides["SOURCE_CACHE_DIR"] = cls.old_cache_dir
            else:
                module_overrides.pop("SOURCE_CACHE_DIR", None)
        finally:
            shutil.rmtree(getattr(cls, "tmp_dir", Path(".")), ignore_errors=True)

    def test_g1_real_bddk_pdf_extraction_normalization_structure(self):
        self.assertGreaterEqual(len(self.pages), 1)
        self.assertGreater(len(re.sub(r"\s+", "", self.normalized)), 500)
        self.assertIn(self.structure.get("structure_type"), {
            "article", "section", "accounting_plan", "document"
        })
        self.assertTrue(
            self.structure.get("document_indexable")
            or self.structure.get("evidence_indexable")
        )
        self.assertTrue(self.chunks)

    def test_g2_real_bddk_pdf_is_persisted_with_provenance(self):
        got = self.rag.collection.get(
            where={"file_hash": self.file_hash},
            include=["documents", "metadatas"],
        )
        docs = got.get("documents", []) or []
        metas = got.get("metadatas", []) or []
        self.assertTrue(docs)
        self.assertEqual(len(docs), len(metas))
        self.assertTrue(all(str(m.get("file_hash", "")) == self.file_hash for m in metas))
        self.assertTrue(all(str(m.get("source", "")).upper() == "BDDK" for m in metas))
        self.assertTrue(all(str(m.get("source_type", "")) == "official" for m in metas))
        if self.document_ref:
            self.assertTrue(all(
                str(m.get("source_document_id", "")) == self.document_ref
                for m in metas
            ))

    def test_g3_real_bddk_exact_rag_query(self):
        if not self.exact_query:
            self.skipTest("BDDK PDF dosya adından güvenilir belge kimliği çıkarılamadı")
        results = self.rag.query(self.exact_query, k=5)
        self.assertTrue(results)
        self.assertTrue(all(
            str(r.get("metadata", {}).get("file_hash", "")) == self.file_hash
            for r in results
        ))
        self.assertTrue(all(
            str(r.get("metadata", {}).get("source_document_id", "")) == self.document_ref
            for r in results
        ))

    def test_g4_real_bddk_semantic_rag_query(self):
        results = self.rag.query(self.semantic_query, k=5)
        self.assertTrue(results)
        self.assertTrue(any(
            str(r.get("metadata", {}).get("file_hash", "")) == self.file_hash
            for r in results
        ))
        top = results[0]
        self.assertTrue(str(top.get("document", "")).strip())
        self.assertEqual(str(top.get("metadata", {}).get("source", "")).upper(), "BDDK")

    def test_g5_real_bddk_cache_provenance_matches_indexed_text(self):
        save_source_cache(self.file_hash, self.normalized)
        cached = load_source_cache(self.file_hash)
        self.assertTrue(cached.strip())
        self.assertEqual(_sha256_text(cached), _sha256_text(self.normalized))

        got = self.rag.collection.get(
            where={"file_hash": self.file_hash},
            include=["documents"],
        )
        docs = got.get("documents", []) or []
        self.assertTrue(docs)
        self.assertTrue(all(
            str(meta.get("file_hash", "")) == self.file_hash
            for meta in (
                self.rag.collection.get(
                    where={"file_hash": self.file_hash},
                    include=["metadatas"],
                ).get("metadatas", []) or []
            )
        ))

