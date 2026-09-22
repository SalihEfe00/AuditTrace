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

