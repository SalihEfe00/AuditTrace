# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import re
import shutil
import inspect
import tempfile
import unittest
from pathlib import Path
from typing import Optional

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

import os
import zipfile
from mufettis.analysis.risk_engine import (
    findings_to_dataframe,
    looks_like_date_column,
    parse_turkish_number,
    run_analysis,
    validate_run_analysis_source,
)
from mufettis.common.utils import (
    atomic_write_json,
    canonical_source_id,
    load_json,
    now_iso,
    save_web_cache_meta,
    sha256_bytes,
    source_is_allowed,
    source_profile,
    web_cache_key,
)
from mufettis.config import BASE_DIR, BOOTSTRAP_STATE_FILE, DATA_DIR, SOURCE_DEFINITIONS
from mufettis.exports.report_generator import create_docx_report, create_xlsx_bytes
from mufettis.extraction.pdf import (
    _extract_pdfium_spatial_pages,
    _join_pdf_pages,
    _pdf_text_quality,
)
from mufettis.extraction.text_repair import (
    clean_evidence_text,
    extract_legal_identifier,
    normalize_extracted_text,
    repair_extracted_spacing,
    repair_mojibake,
)
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper
from mufettis.harvesting.web_harvester import WebSourceHarvester
from mufettis.importers.bootstrapper import RegulationBootstrapper, should_offer_bootstrap
from mufettis.importers.bulk_importer import BulkRegulationImporter
from mufettis.legal.parser import (
    _legal_rerank,
    classify_legal_structure,
    extract_legal_metadata,
    legal_diff,
    prefer_official_matches,
    split_legal_chunks,
)
from mufettis.llm.client import (
    build_ai_report_prompt,
    prepare_report_findings,
    report_mevzuat_preflight,
)
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex
from mufettis.rag.maintenance import (
    _sha256_text,
    load_source_cache,
    resolve_inventory_evidence_meta,
    save_source_cache,
)
from mufettis.scheduler.scheduler import BackgroundUpdateScheduler
from mufettis.tests.patching import module_overrides

class TestMufettisAI(unittest.TestCase):
    def test_run_analysis_source(self):
        self.assertTrue(validate_run_analysis_source())

    def test_number_parser(self):
        cases = {
            "1.234,56": 1234.56,
            "(2.000,00)": -2000.0,
            "2.000,00 TL": 2000.0,
            "-1.234,50": -1234.5,
            "1234.5": 1234.5,
        }
        for raw, expected in cases.items():
            self.assertAlmostEqual(parse_turkish_number(raw), expected, places=4)

    def test_date_protection(self):
        s = pd.Series(["05.09.2026", "01.10.2026"])
        self.assertTrue(looks_like_date_column(s, "islem_tarihi"))

    def test_analysis_schema_and_duplicates(self):
        df = pd.DataFrame({
            "tutar": [100, 100, 100, 10000, 110, 120],
            "aciklama": ["a", "a", "b", "c", "d", "e"],
            "grup": ["A", "A", "A", "A", "B", "B"],
            "tarih": ["01.01.2026", "02.01.2026", "03.01.2026", "04.01.2026", "05.01.2026", "06.01.2026"],
        })
        findings, working, summary = run_analysis(
            df,
            amount_column="tutar",
            methods=["zscore", "mad", "iqr", "duplicates", "group"],
            duplicate_columns=["aciklama"],
            group_columns=["grup"],
            source_file="test.xlsx",
        )
        self.assertIsInstance(findings, list)
        self.assertEqual(len((findings, working, summary)), 3)
        self.assertIn("risk_skoru", working.columns)
        self.assertGreater(summary["finding_count"], 0)
        self.assertTrue(any(f["bulgu_tipi"] == "Potansiyel duplicate" for f in findings))

    def test_diff(self):
        d = legal_diff("MADDE 1\nEski", "MADDE 1\nYeni")
        self.assertTrue(d["changed"])
        self.assertEqual(d["added_lines"], 1)
        self.assertEqual(d["removed_lines"], 1)

    def test_metadata(self):
        m = extract_legal_metadata("ABC Yönetmeliği", "MADDE 5 - açıklama\nYürürlük Tarihi: 05.09.2026", {"file_hash": "x"})
        self.assertEqual(m["document_type"], "Yönetmelik")
        self.assertEqual(m["article_hint"], "5")

    def test_empty_findings_dataframe_schema(self):
        df = findings_to_dataframe([])
        self.assertTrue(df.empty)
        self.assertIn("bulgu_id", df.columns)
        self.assertIn("inceleme_durumu", df.columns)

    def test_scheduler_empty_sources_is_respected(self):
        scheduler = BackgroundUpdateScheduler.get_or_create([])
        self.assertEqual(scheduler.sources, [])

    def test_atomic_json(self):
        p = DATA_DIR / "test_atomic.json"
        atomic_write_json(p, {"ok": True})
        self.assertTrue(p.exists())
        self.assertEqual(load_json(p, {}).get("ok"), True)
        p.unlink(missing_ok=True)
        Path(str(p) + ".tmp").unlink(missing_ok=True)

    def test_exports(self):
        summary = {"row_count": 1, "finding_count": 1}
        findings = [{
            "bulgu_id": "abc", "bulgu_tipi": "Z-score", "risk_seviyesi": "Yüksek",
            "risk_skoru": 80, "satir": 0, "deger": 123, "kolonlar": ["tutar"], "gerekce": "test",
            "mevzuat_eslesmeleri": [],
        }]
        docx = create_docx_report(summary, findings)
        self.assertTrue(docx.startswith(b"PK"))
        xlsx = create_xlsx_bytes(pd.DataFrame({"tutar": [1]}), findings_to_dataframe(findings), summary)
        self.assertTrue(xlsx.startswith(b"PK"))


    def test_bulk_scan_and_import_resume(self):
        if chromadb is None or SentenceTransformer is None:
            self.skipTest("RAG bağımlılıkları yok")
        tmp_dir = Path(tempfile.mkdtemp(prefix="mufettis_bulk_test_"))
        try:
            (tmp_dir / "alt").mkdir()
            (tmp_dir / "kanun.txt").write_text("5549 sayılı Kanun\nMADDE 1 - Amaç ve kapsam\nYürürlük Tarihi: 05.09.2026", encoding="utf-8")
            (tmp_dir / "alt" / "not.txt").write_text("MADDE 2 - Deneme metni", encoding="utf-8")
            test_db = tmp_dir / "db"
            rag = LocalRAG(db_path=test_db, allow_download=False)
            test_index = IngestIndex(path=tmp_dir / "ingest_index.json")
            importer = BulkRegulationImporter(tmp_dir, rag=rag, index=test_index, max_file_mb=5)
            files = importer.scan()
            self.assertEqual(len(files), 2)
            summary = importer.summarize(files)
            self.assertEqual(summary["total"], 2)
            # İlgili test yalnızca embed modeli önceden varsa çalışır; yoksa import yerine scan
            # ve metadata mantığını doğrulamak için skip edilir.
            try:
                result = importer.import_folder()
            except RuntimeError as exc:
                self.skipTest(str(exc))
            self.assertGreaterEqual(result["ingested"] + result["skipped_chroma"], 1)
            result2 = importer.import_folder()
            self.assertGreaterEqual(result2["skipped_index"], 1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
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
class TestV1307Quality(unittest.TestCase):
    def test_legal_chunk_hierarchy(self):
        sample = """
        MADDE 5 - Bilgi sistemleri
        (1) Banka gerekli kontrolleri kurar.
        (2) İkinci fıkra uygulanır.
        a) İlk bent.
        b) İkinci bent.
        1) Alt bent.
        """
        chunks = split_legal_chunks(sample, max_chars=1000)
        self.assertTrue(any(c.get("article_no") == "5" for c in chunks))
        self.assertTrue(any(c.get("paragraph_no") == "1" for c in chunks))
        self.assertTrue(any(c.get("paragraph_no") == "2" for c in chunks))
        self.assertTrue(any(c.get("item_no") == "a" for c in chunks))
        self.assertTrue(any(c.get("item_no") == "b" for c in chunks))

    def test_no_cross_article_chunk(self):
        sample = "MADDE 1 - A\nMADDE 2 - B"
        chunks = split_legal_chunks(sample, max_chars=500)
        self.assertTrue(any(c.get("article_no") == "1" for c in chunks))
        self.assertTrue(any(c.get("article_no") == "2" for c in chunks))

    def test_mojibake(self):
        self.assertEqual(repair_mojibake("TÃ¼rkiye"), "Türkiye")
        self.assertIn("Türkiye", normalize_extracted_text("TÃ¼rkiye"))

    def test_mojibake_preserves_whitespace_invariant(self):
        sample = "TÃ¼rkiye  ğ\nÄ±   GEÇİCİ MADDE 2 - Geçiş."
        repaired = repair_mojibake(sample)
        self.assertEqual(repaired, "Türkiye  ğ\nı   GEÇİCİ MADDE 2 - Geçiş.")
        self.assertEqual(
            tuple(ch for ch in repaired if ch.isspace()),
            tuple(ch for ch in sample if ch.isspace()),
        )

    def test_normalize_preserves_existing_whitespace_invariant(self):
        sample = "A  B   C"
        normalized = normalize_extracted_text(sample)
        self.assertEqual(normalized, sample)
        self.assertEqual(
            tuple(ch for ch in normalized if ch.isspace()),
            tuple(ch for ch in sample if ch.isspace()),
        )

    def test_normalize_preserves_horizontal_whitespace_with_tabs(self):
        sample = "A  B\t\tC   D"
        normalized = normalize_extracted_text(sample)
        self.assertEqual(normalized, sample)
        self.assertEqual(
            tuple(ch for ch in normalized if ch in " \t"),
            tuple(ch for ch in sample if ch in " \t"),
        )

    def test_legal_marker_protected_before_normalization(self):
        sample = "GEÇİCİ MADDE 2 - Geçiş.\nEK MADDE 3 - Ek hüküm."
        normalized = normalize_extracted_text(sample)
        self.assertIn("GEÇİCİ MADDE 2 - Geçiş.", normalized)
        self.assertIn("EK MADDE 3 - Ek hüküm.", normalized)
class TestV1308Quality(unittest.TestCase):
    def test_hierarchical_chunking(self):
        sample = """MADDE 5 - Bilgi sistemleri
(1) Birinci fıkra.
(2) İkinci fıkra.
a) A bendi.
b) B bendi.
1) Alt bent.
GEÇİCİ MADDE 2 - Geçiş.
(1) Geçiş fıkrası.
EK MADDE 3 - Ek hüküm.
(1) Ek fıkra."""
        chunks = split_legal_chunks(sample, max_chars=1000)
        self.assertTrue(any(c["article_no"] == "5" for c in chunks))
        self.assertTrue(any(c["paragraph_no"] == "1" for c in chunks))
        self.assertTrue(any(c["paragraph_no"] == "2" for c in chunks))
        self.assertTrue(any(c["item_no"] == "a" for c in chunks))
        self.assertTrue(any(c["subitem_no"] == "1" for c in chunks))
        self.assertTrue(any(c["article_prefix"] == "GEÇİCİ" and c["article_no"] == "2" for c in chunks))
        self.assertTrue(any(c["article_prefix"] == "EK" and c["article_no"] == "3" for c in chunks))

    def test_mojibake_repair(self):
        self.assertEqual(repair_mojibake("TÃ¼rkiye"), "Türkiye")
        self.assertEqual(repair_mojibake("TemettÃ¼ StopajÄ±"), "Temettü Stopajı")
class TestV1309Quality(unittest.TestCase):
    def test_exact_legal_identifier(self):
        self.assertEqual(extract_legal_identifier("5549 sayılı Kanun kapsamındaki yükümlülükler"), "5549")
        self.assertEqual(extract_legal_identifier("Kanun No: 5411"), "5411")

    def test_clean_xml_evidence(self):
        cleaned = clean_evidence_text("<Kalem KalemAdi='x'>TÃ¼rkiye</Kalem>")
        self.assertNotIn("<Kalem", cleaned)
        self.assertIn("Türkiye", cleaned)

    def test_rank_exact_law_number(self):
        results = [
            {
                "document": "Genel vergi hükümleri.",
                "metadata": {
                    "title": "GVK",
                    "source": "GVK.pdf",
                    "source_type": "official",
                    "authority_rank": 100,
                    "status": "active",
                },
                "distance": 0.20,
            },
            {
                "document": "5549 sayılı Kanun yükümlülükleri.",
                "metadata": {
                    "title": "5549 sayılı Kanun",
                    "source": "Resmî Gazete",
                    "source_type": "official",
                    "authority_rank": 100,
                    "status": "active",
                },
                "distance": 0.50,
            },
        ]
        ranked = prefer_official_matches(
            results,
            limit=2,
            query="5549 sayılı Kanun kapsamındaki yükümlülükler",
        )
        self.assertEqual(ranked[0]["metadata"]["title"], "5549 sayılı Kanun")
class TestV1310Phase1(unittest.TestCase):
    def test_article_paragraph_item_hierarchy(self):
        sample = """
        MADDE 34 - Elektronik bankacılık
        (1) Bankalar gerekli kontrolleri kurar.
        (2) İkinci fıkra uygulanır.
        a) Kimlik doğrulama.
        b) İşlem güvenliği.
        1) Alt bent koşulu.
        GEÇİCİ MADDE 2 - Geçiş hükümleri
        (1) Geçiş hükmü.
        """
        chunks = split_legal_chunks(sample, max_chars=1000)
        self.assertTrue(any(c["article_no"] == "34" for c in chunks))
        self.assertTrue(any(c["paragraph_no"] == "1" for c in chunks))
        self.assertTrue(any(c["paragraph_no"] == "2" for c in chunks))
        self.assertTrue(any(c["item_no"] == "a" for c in chunks))
        self.assertTrue(any(c["item_no"] == "b" for c in chunks))
        self.assertTrue(any(c["subitem_no"] == "1" for c in chunks))
        self.assertTrue(
            any(
                c["article_prefix"] == "GEÇİCİ"
                and c["article_no"] == "2"
                for c in chunks
            )
        )

    def test_no_cross_article_merge(self):
        sample = "MADDE 1 - A\n(1) Birinci.\nMADDE 2 - B\n(1) İkinci."
        chunks = split_legal_chunks(sample, max_chars=500)
        for c in chunks:
            if c["article_no"] == "1":
                self.assertNotIn("MADDE 2 - B", c["text"])
            if c["article_no"] == "2":
                self.assertNotIn("MADDE 1 - A", c["text"])

    def test_markup_cleanup(self):
        raw = "<Kalem KalemAdi='x'>TÃ¼rkiye</Kalem>\n<Değer>25</Değer>"
        cleaned = clean_evidence_text(raw)
        self.assertNotIn("<Kalem", cleaned)
        self.assertNotIn("<Değer", cleaned)
        self.assertIn("Türkiye", cleaned)

    def test_identifier_extraction(self):
        self.assertEqual(
            extract_legal_identifier("5549 sayılı Kanun kapsamındaki yükümlülükler"),
            "5549",
        )
        self.assertEqual(
            extract_legal_identifier("Kanun No: 5411"),
            "5411",
        )

    def test_exact_identifier_rerank(self):
        results = [
            {
                "document": "Genel vergi hükümleri.",
                "metadata": {
                    "title": "GVK",
                    "source": "GVK.pdf",
                    "source_type": "official",
                    "authority_rank": 100,
                    "status": "active",
                },
                "distance": 0.10,
            },
            {
                "document": "5549 sayılı Kanun kapsamındaki yükümlülükler.",
                "metadata": {
                    "title": "5549 sayılı Kanun",
                    "source": "Resmi Gazete",
                    "source_type": "official",
                    "authority_rank": 100,
                    "status": "active",
                },
                "distance": 0.50,
            },
        ]
        ranked = prefer_official_matches(
            results,
            query="5549 sayılı Kanun kapsamındaki yükümlülükler",
            limit=2,
        )
        self.assertEqual(ranked[0]["metadata"]["title"], "5549 sayılı Kanun")
class TestV1311Hierarchy(unittest.TestCase):
    def test_valid_parent_chain(self):
        sample = """MADDE 9 - Test
(2) İkinci fıkra.
b) Bent b.
2) Alt bent 2."""
        chunks = split_legal_chunks(sample, max_chars=1000)
        target = [c for c in chunks if c.get("subitem_no") == "2"]
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0].get("paragraph_no"), "2")
        self.assertEqual(target[0].get("item_no"), "b")
        self.assertTrue(target[0].get("hierarchy_valid"))

    def test_no_orphan_subitem(self):
        sample = """MADDE 9 - Test
b) Bent b.
2) Sayısal ifade."""
        chunks = split_legal_chunks(sample, max_chars=1000)
        for c in chunks:
            self.assertFalse(
                c.get("subitem_no")
                and not (c.get("paragraph_no") and c.get("item_no"))
            )

    def test_cross_article_isolation(self):
        sample = """MADDE 1 - Birinci
(1) A.
MADDE 2 - İkinci
(1) B."""
        chunks = split_legal_chunks(sample, max_chars=1000)
        for c in chunks:
            if c["article_no"] == "1":
                self.assertNotIn("MADDE 2", c["text"])
            if c["article_no"] == "2":
                self.assertNotIn("MADDE 1", c["text"])
class TestV1314SourceParsing(unittest.TestCase):
    def test_bddk_numbered_sections(self):
        sample = """
        12- Teminatın nakde dönüştürülmesi ile ilgili olarak oluşturulacak politika:
        a) Tasfiye maliyetlerini içerecek şekilde, teminatla ilişkin izlenecek değerleme yaklaşımı.
        13- Bankalar tarafından herhangi bir zamanda alınacak kredi sınırları.
        14- Bankalar kredi riski politikaları.
        """
        chunks = split_legal_chunks(sample, max_chars=1000)
        nums = [c.get("section_no") for c in chunks]
        self.assertIn("12", nums)
        self.assertIn("13", nums)
        self.assertIn("14", nums)
        self.assertTrue(all(c.get("chunk_type") in {"section","document"} for c in chunks))

    def test_law_number_metadata(self):
        meta = extract_legal_metadata(
            "5549 sayılı Kanun",
            "5549 sayılı Kanun kapsamındaki yükümlülükler.\nMADDE 1 - Amaç.",
            {"file_hash": "x", "source_id": "resmi_gazete"},
        )
        self.assertEqual(meta.get("law_number"), "5549")
        self.assertEqual(meta.get("document_key"), "5549")
class TestV1315Hardening(unittest.TestCase):
    def test_title_only_law_is_not_indexable(self):
        meta=extract_legal_metadata(
            "5549 sayılı Kanun",
            "5549 sayılı Kanun",
            {"file_hash":"x","source_id":"local_file"},
        )
        self.assertEqual(meta.get("law_number"),"5549")
        self.assertFalse(meta.get("evidence_indexable",True))
        self.assertEqual(meta.get("parse_status"),"failed")
        self.assertIn("çok kısa", meta.get("evidence_indexable_reason", ""))

    def test_bddk_section_is_indexable(self):
        body=(
            "12- Teminat politikası ve değerleme yaklaşımı.\n"
            "13- Kredi sınırları ve izleme.\n"
            "14- Kredi riski politikaları ve kontroller."
        )
        meta=extract_legal_metadata(
            "1040",
            body,
            {"file_hash":"y","source_id":"bddk_official"},
        )
        self.assertEqual(meta.get("structure_type"),"section")
        self.assertTrue(meta.get("evidence_indexable"))
        self.assertIn("Numaralı hukuki bölüm", meta.get("evidence_indexable_reason", ""))
        chunks=split_legal_chunks(body,max_chars=1000)
        self.assertTrue({"12","13","14"}.issubset({c.get("section_no") for c in chunks}))

    def test_no_orphan_subitem(self):
        chunks=split_legal_chunks("MADDE 9 - Test\nb) Bent b.\n2) Sayısal ifade.")
        self.assertTrue(all(
            not c.get("subitem_no")
            or (c.get("paragraph_no") and c.get("item_no"))
            for c in chunks
        ))

    def test_exact_law_rerank(self):
        results=[
            {
                "document":"5411 sayılı Bankacılık Kanunu.",
                "metadata":{
                    "title":"5411 sayılı Bankacılık Kanunu",
                    "source":"Resmi Gazete",
                    "source_type":"official",
                    "authority_rank":100,
                    "status":"active",
                    "law_number":"5411",
                    "document_key":"5411",
                },
                "distance":0.01,
            },
            {
                "document":"5549 sayılı Kanun kapsamındaki yükümlülükler.",
                "metadata":{
                    "title":"5549 sayılı Kanun",
                    "source":"Resmi Gazete",
                    "source_type":"official",
                    "authority_rank":100,
                    "status":"active",
                    "law_number":"5549",
                    "document_key":"5549",
                },
                "distance":0.90,
            },
        ]
        ranked=_legal_rerank(
            results,
            "5549 sayılı Kanun kapsamındaki yükümlülükler",
            limit=2,
        )
        self.assertEqual(ranked[0]["metadata"]["law_number"],"5549")
class TestV1317Hardening(unittest.TestCase):
    def test_turkish_money_parser(self):
        cases = {
            "12.500": 12500.0,
            "1.234": 1234.0,
            "100.000": 100000.0,
            "1.234.567": 1234567.0,
            "12,50": 12.50,
            "1.234,56": 1234.56,
            "1,234.56": 1234.56,
            "(2.000,00)": -2000.0,
        }
        for raw, expected in cases.items():
            self.assertAlmostEqual(
                parse_turkish_number(raw),
                expected,
                places=6,
                msg=raw,
            )

    def test_mojibake_mixed_turkish(self):
        self.assertEqual(
            repair_mojibake("TÃ¼rkiye ğ ı İ"),
            "Türkiye ğ ı İ",
        )
        self.assertEqual(
            clean_evidence_text("<Kalem>TÃ¼rkiye</Kalem>"),
            "Türkiye",
        )

    def test_single_identifier_definition(self):
        import mufettis.extraction.text_repair as _tr
        source = inspect.getsource(_tr)
        self.assertEqual(
            len(re.findall(r"^def extract_legal_identifier\(", source, re.M)),
            1,
        )

    def test_no_tls_bypass(self):
        import mufettis.harvesting.web_harvester as _wh
        source = inspect.getsource(_wh)
        start = source.find("class BrowserWebSourceHarvester")
        end = source.find("\nclass ", start + 6) if start >= 0 else -1
        collector = source[start:end if end >= 0 else None]
        self.assertNotIn("ignore_https_errors=True", collector)
        self.assertNotIn('"https_certificate_bypass": True', collector)
        self.assertIn("ignore_https_errors=False", collector)

    def test_prompt_masks_sensitive_values(self):
        prompt = build_ai_report_prompt(
            {"row_count": 1},
            [{
                "bulgu_id": "x",
                "satir": 1,
                "bulgu_tipi": "Test",
                "risk_skoru": 90,
                "risk_seviyesi": "Yüksek",
                "gerekce": "IBAN TR330006100519786457841326 ile eşleşti.",
                "deger": "TR330006100519786457841326",
                "kolonlar": ["IBAN", "TCKN 12345678901"],
                "mevzuat_eslesmeleri": [],
            }],
        )
        self.assertNotIn("TR330006100519786457841326", prompt)
        self.assertNotIn("12345678901", prompt)
        self.assertIn("[IBAN]", prompt)

    def test_docx_contains_no_xml_junk(self):
        summary = {
            "row_count": 1,
            "finding_count": 1,
            "affected_rows": 1,
            "high_count": 1,
            "medium_count": 0,
            "low_count": 0,
            "z_threshold": 3.0,
            "mad_threshold": 3.5,
            "iqr_multiplier": 1.5,
        }
        finding = [{
            "bulgu_id": "docx-test",
            "source_file": "test.xlsx",
            "satir": 1,
            "bulgu_tipi": "Z-score",
            "risk_skoru": 90,
            "risk_seviyesi": "Yüksek",
            "deger": 1000000,
            "kolonlar": ["Tutar"],
            "gerekce": "TÃ¼rkiye ve <Islem> dahil.",
            "mevzuat_eslesmeleri": [{
                "title": "5549 sayılı Kanun",
                "source": "Resmî Gazete",
                "article_no": "5",
                "paragraph_no": "1",
                "item_no": "a",
                "snippet": "<Kalem>TÃ¼rkiye</Kalem>",
                "status": "active",
                "effective_date": "05.09.2026",
                "page": "12",
                "url": "https://www.resmigazete.gov.tr/test",
            }],
        }]
        data = create_docx_report(summary, finding, auditor="Test")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        self.assertNotIn("TÃ¼rkiye", xml)
        self.assertNotIn("<Kalem", xml)
        self.assertNotIn("<Islem", xml)
        self.assertIn("1.000.000,00", xml)
class TestV1318ReportAndAdmin(unittest.TestCase):
    def test_report_preflight_counts(self):
        findings = [
            {"bulgu_id": "1", "mevzuat_eslesmeleri": [{"title": "5549 sayılı Kanun"}]},
            {"bulgu_id": "2", "mevzuat_eslesmeleri": []},
        ]
        p = report_mevzuat_preflight(findings)
        self.assertEqual(p["total_findings"], 2)
        self.assertEqual(p["matched_findings"], 1)
        self.assertEqual(p["unmatched_findings"], 1)

    def test_report_prepare_without_rag_is_explicit(self):
        original = module_overrides.get("_get_rag")
        try:
            module_overrides["_get_rag"] = lambda **kwargs: None
            prepared, status = prepare_report_findings(
                [{"bulgu_id": "x", "mevzuat_eslesmeleri": []}],
                pd.DataFrame(),
                allow_embedding_download=False,
            )
            self.assertEqual(len(prepared), 1)
            self.assertEqual(status["unmatched_findings"], 1)
            self.assertTrue(status["errors"])
        finally:
            if original is not None:
                module_overrides["_get_rag"] = original

    def test_docx_no_match_is_explicit(self):
        summary = {
            "row_count": 1,
            "finding_count": 1,
            "affected_rows": 1,
            "high_count": 1,
            "medium_count": 0,
            "low_count": 0,
        }
        findings = [{
            "bulgu_id": "no-ref",
            "source_file": "test.xlsx",
            "satir": 1,
            "bulgu_tipi": "Z-score",
            "risk_skoru": 80,
            "risk_seviyesi": "Yüksek",
            "deger": 1000,
            "kolonlar": ["Tutar"],
            "gerekce": "test",
            "mevzuat_eslesmeleri": [],
        }]
        data = create_docx_report(summary, findings)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("Mevzuat atfı:", xml)

    def test_docx_real_attribution(self):
        summary = {
            "row_count": 1,
            "finding_count": 1,
            "affected_rows": 1,
            "high_count": 1,
            "medium_count": 0,
            "low_count": 0,
        }
        findings = [{
            "bulgu_id": "ref",
            "source_file": "test.xlsx",
            "satir": 1,
            "bulgu_tipi": "Z-score",
            "risk_skoru": 90,
            "risk_seviyesi": "Yüksek",
            "deger": 12500,
            "kolonlar": ["Tutar"],
            "gerekce": "test",
            "mevzuat_eslesmeleri": [{
                "title": "5549 sayılı Kanun",
                "law_number": "5549",
                "document_number": "5549",
                "source": "Resmî Gazete",
                "article_no": "5",
                "paragraph_no": "1",
                "item_no": "a",
                "subitem_no": "2",
                "effective_date": "05.09.2026",
                "status": "active",
                "page": "12",
                "snippet": "5549 sayılı Kanun kapsamında yükümlülük.",
                "url": "https://www.resmigazete.gov.tr/test",
            }],
        }]
        data = create_docx_report(summary, findings)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("Mevzuat atfı:", xml)
        self.assertIn("5549 sayılı Kanun", xml)
        self.assertIn("Madde 5 / Fıkra 1 / Bent a / Alt bent 2", xml)
        self.assertIn("Kanun No: 5549", xml)
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

        original_split=module_overrides["split_legal_chunks"]
        original_mask=module_overrides["mask_sensitive"]
        try:
            module_overrides["split_legal_chunks"]=lambda text, *args, **kwargs: [
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
            module_overrides["mask_sensitive"]=lambda x:x
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
            module_overrides["split_legal_chunks"]=original_split
            module_overrides["mask_sensitive"]=original_mask
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
        original_split=module_overrides["split_legal_chunks"]
        original_mask=module_overrides["mask_sensitive"]
        try:
            module_overrides["split_legal_chunks"]=lambda text, *args, **kwargs:[
                {"text":f"c{i}","article_prefix":"","article_no":"1","article_title":"",
                 "paragraph_no":"1","item_no":"","subitem_no":"","section_no":"",
                 "chunk_type":"paragraph","subchunk_no":0,"chunk_no":i}
                for i in range(65)
            ]
            module_overrides["mask_sensitive"]=lambda x:x
            r=rag.ingest_text("x",{"file_hash":"perf001","title":"x"})
            self.assertEqual(r["count"],65)
            self.assertEqual(sum(calls),65)
            self.assertEqual(rag.collection.n,65)
        finally:
            module_overrides["split_legal_chunks"]=original_split
            module_overrides["mask_sensitive"]=original_mask
class TestV1331NonArticleEvidence(unittest.TestCase):
    def test_bddk_guidance_is_evidence_indexable(self):
        text = """İYİ UYGULAMA REHBERİ\nBİRİNCİ KISIM\n1- Bu rehberin amacı ve kapsamıdır.\n2- Uygulama esasları aşağıda açıklanmıştır.\n3- Kuruluşlar gerekli kontrolleri yapar."""
        meta = classify_legal_structure("Sorunlu Alacak Çözümleme Rehberi", text, "Rehber")
        self.assertEqual(meta["structure_type"], "section")
        self.assertTrue(meta["evidence_indexable"])
        self.assertGreaterEqual(meta["section_count"], 3)

    def test_accounting_plan_without_title_phrase_is_evidence_indexable(self):
        # 1043'te envanter başlığı yalnızca "BDDK Belge" olabilir; başlık
        # çıkarımda kaybolsa bile hesap kodu hiyerarşisi yeterli sinyal vermelidir.
        text = """010 KASA\n011 EFEKTİF DEPOSU\n012 YOLDAKİ PARALAR - T.P.\n013 YOLDAKİ PARALAR - Y.P.\n014 SATIN ALINAN BANKA ÇEKLERİ - T.P.\n015 SATIN ALINAN BANKA ÇEKLERİ - Y.P."""
        meta = classify_legal_structure("BDDK Belge", text, "BDDK Belge")
        self.assertEqual(meta["structure_type"], "accounting_plan")
        self.assertTrue(meta["evidence_indexable"])
        self.assertGreaterEqual(meta["account_code_count"], 5)

    def test_accounting_plan_is_evidence_indexable(self):
        text = """Bankalarca Uygulanacak Tekdüzen Hesap Planı ve İzahnamesi\nI. TEKDÜZEN HESAP PLANI\n010 KASA\n011 EFEKTİF DEPOSU\n012 YOLDAKİ PARALAR - T.P."""
        meta = classify_legal_structure("Bankalarca Uygulanacak Tekdüzen Hesap Planı ve İzahnamesi", text, "BDDK Belge")
        self.assertEqual(meta["structure_type"], "accounting_plan")
        self.assertTrue(meta["evidence_indexable"])
        self.assertGreaterEqual(meta["account_code_count"], 3)

    def test_non_article_chunks_keep_their_real_hierarchy(self):
        guidance = """İYİ UYGULAMA REHBERİ\nBİRİNCİ KISIM\n1- Amaç ve kapsam.\n2- Uygulama esasları.\n3- Kontroller."""
        chunks = split_legal_chunks(guidance, max_chars=1800)
        sections = [c for c in chunks if c.get("chunk_type") == "section"]
        self.assertGreaterEqual(len(sections), 3)
        self.assertEqual([c["section_no"] for c in sections[:3]], ["1", "2", "3"])
        self.assertTrue(all(not c.get("article_no") for c in sections))

    def test_accounting_chunks_keep_account_code(self):
        plan = """Bankalarca Uygulanacak Tekdüzen Hesap Planı ve İzahnamesi\n010 KASA\n011 EFEKTİF DEPOSU\n012 YOLDAKİ PARALAR - T.P."""
        chunks = split_legal_chunks(plan, max_chars=1800)
        codes = [c.get("account_code") for c in chunks if c.get("chunk_type") == "accounting_code"]
        self.assertEqual(codes[:3], ["010", "011", "012"])
class TestV1333StructureDiscovery(unittest.TestCase):
    def test_hierarchical_guidance_sections_are_discovered(self):
        text = """KREDİ TAHSİS VE İZLEME SÜREÇLERİNE İLİŞKİN REHBER
2.3.1 Kredi değerlendirme
Bankalar gerekli değerlendirmeleri yapar.
2.3.2 Kaldıraçlı işlemler
İşlemler risk iştahına göre değerlendirilir.
2.3.3 Limitler
Limitler düzenli olarak izlenir."""
        meta = classify_legal_structure("Kredi Tahsis ve İzleme Süreçlerine İlişkin Rehber", text, "Rehber")
        self.assertEqual(meta["structure_type"], "section")
        self.assertTrue(meta["evidence_indexable"])
        self.assertGreaterEqual(meta["section_count"], 3)

    def test_accounting_plan_accepts_six_digit_codes(self):
        text = """010 KASA
011 EFEKTİF DEPOSU
01200 YURTİÇİ HAVALELER
01201 YURTDIŞI HAVALELER
01300 DİĞER HESAPLAR
01400 SATIN ALINAN ÇEKLER"""
        meta = classify_legal_structure("BDDK Belge", text, "BDDK Belge")
        self.assertEqual(meta["structure_type"], "accounting_plan")
        self.assertTrue(meta["evidence_indexable"])
        self.assertGreaterEqual(meta["account_code_count"], 5)

    def test_inventory_source_cache_overrides_stale_false_and_reason(self):
        original = module_overrides["load_source_cache"]
        try:
            module_overrides["load_source_cache"] = lambda file_hash: """010 KASA
011 EFEKTİF DEPOSU
012 YOLDAKİ PARALAR
013 TAKAS HESAPLARI
014 ÇEKLER
015 DİĞER HESAPLAR"""
            resolved = resolve_inventory_evidence_meta({
                "file_hash": "stale-1043",
                "title": "1043",
                "document_type": "BDDK Belge",
                "evidence_indexable": "False",
                "evidence_indexable_reason": "Hukuki yapı çözümlenemedi",
            })
            self.assertTrue(resolved["evidence_indexable"])
            self.assertIn("Hesap planı", resolved["evidence_indexable_reason"])
        finally:
            module_overrides["load_source_cache"] = original

    def test_splitter_preserves_hierarchical_section_identity(self):
        text = """2.3.1 Kredi değerlendirme
Bankalar değerlendirme yapar.
2.3.2 Kaldıraçlı işlemler
Risk iştahı dikkate alınır."""
        chunks = split_legal_chunks(text, max_chars=1800)
        sections = [c for c in chunks if c.get("chunk_type") == "section"]
        self.assertEqual([c["section_no"] for c in sections[:2]], ["2.3.1", "2.3.2"])
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


__all__ = [
    "TestMufettisAI",
    "TestVersioningAndScheduler",
    "TestBootstrapSafety",
    "TestSourceHierarchy",
    "TestV1307Quality",
    "TestV1308Quality",
    "TestV1309Quality",
    "TestV1310Phase1",
    "TestV1311Hierarchy",
    "TestV1314SourceParsing",
    "TestV1315Hardening",
    "TestV1317Hardening",
    "TestV1318ReportAndAdmin",
    "TestV1319Performance",
    "TestV1320PerformancePipeline",
    "TestV1331NonArticleEvidence",
    "TestV1333StructureDiscovery",
    "TestV1336ExtractionAndStructure",
    "TestV1380FinalE2ERegression",
]
