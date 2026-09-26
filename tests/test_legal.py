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

from mufettis.legal.parser import (
    classify_legal_structure,
    extract_legal_metadata,
    split_legal_chunks,
)
from mufettis.rag.maintenance import resolve_inventory_evidence_meta
from mufettis.tests.patching import module_overrides

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


class TestV1312ChunkIntegrity(unittest.TestCase):
    def test_article_header_attaches_to_first_paragraph(self):
        sample = "MADDE 9 - Denetim\n(1) Birinci fıkra."
        chunks = split_legal_chunks(sample, max_chars=1000)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["article_no"], "9")
        self.assertEqual(chunks[0]["paragraph_no"], "1")
        self.assertIn("MADDE 9", chunks[0]["text"])

    def test_long_paragraph_keeps_sentence_boundaries(self):
        sample = "MADDE 9 - Denetim\n(1) " + ("Bu hukuki cümledir. " * 120)
        chunks = split_legal_chunks(sample, max_chars=800)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c["paragraph_no"] == "1" for c in chunks))
        self.assertTrue(all(c["overlap_chars"] == 0 for c in chunks))
        # Normal cümle parçaları nokta ile bitmeli; ilk/son sınırları da aynı fıkrada kalır.
        for c in chunks:
            self.assertTrue(
                c["text"].rstrip().endswith(".")
                or len(c["text"]) < 800
            )

    def test_no_orphan_subitem(self):
        sample = "MADDE 9 - Denetim\nb) Bent b.\n2) Sayısal ifade."
        chunks = split_legal_chunks(sample, max_chars=1000)
        self.assertTrue(
            all(
                not c.get("subitem_no")
                or (c.get("paragraph_no") and c.get("item_no"))
                for c in chunks
            )
        )


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

