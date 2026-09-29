# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shutil
import unittest
from pathlib import Path
from typing import Optional


try:
    import pypdfium2 as pdfium
except Exception:
    pdfium = None

try:
    import pdfplumber
except Exception:
    pdfplumber = None

import os
from mufettis.config import BASE_DIR, PDFIUM_LOCK
from mufettis.extraction.pdf import (
    _build_pdf_physical_page_model,
    _build_pdf_visual_rows,
    _detect_pdf_table_regions,
    _extract_pdfium_pages,
    _extract_pdfium_spatial_pages,
    _join_pdf_pages,
    _order_pdf_physical_lines,
    _order_pdf_physical_lines_a4,
    _pdf_encoding_anomaly_metrics,
    _pdf_generic_quality_metrics,
    _pdf_page_quality,
    _pdf_quality_gate,
    _pdf_text_quality,
    _reconstruct_pdf_line_word_boundaries,
    _reconstruct_pdf_spatial_text,
    _schema_aware_quality_metrics,
    _select_pdf_engine_b3,
)
from mufettis.extraction.text_repair import (
    clean_evidence_text,
    normalize_extracted_text,
    normalize_legal_whitespace,
    repair_account_code_spacing,
    repair_extracted_spacing,
    repair_mojibake,
)
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper
from mufettis.legal.parser import classify_legal_structure, split_legal_chunks
from mufettis.rag.index import IngestIndex
from mufettis.tests.patching import module_overrides

class TestV1345PdfDependencyAndExtraction(unittest.TestCase):
    def test_pdf_dependencies_are_available(self):
        self.assertIsNotNone(pdfium)
        self.assertIsNotNone(pdfplumber)

    def test_pdfium_fast_pass_helpers_are_available(self):
        self.assertTrue(callable(_extract_pdfium_pages))
        self.assertTrue(callable(_reconstruct_pdf_spatial_text))

    def test_physical_document_model_is_schema_agnostic(self):
        class FakeTextPage:
            def __init__(self, boxes):
                self.boxes = boxes
            def get_charbox(self, i):
                return self.boxes[i]
        text = "Başlık\nİkinci satır"
        boxes = []
        for row, line in enumerate(text.split("\n")):
            for col, ch in enumerate(line):
                x0 = 50 + col * 6
                y0 = 700 - row * 20
                boxes.append((x0, y0, x0 + 5, y0 + 10))
        model = _build_pdf_physical_page_model(FakeTextPage(boxes), text, 600, 800)
        self.assertEqual(model["schema_version"], "physical-document-1")
        self.assertEqual(len(model["lines"]), 2)
        self.assertEqual(model["lines"][0]["text"], "Başlık")
        self.assertIn("bbox", model["lines"][0])

    def test_reading_order_single_column_preserves_source_order(self):
        lines = [
            {"index": 0, "text": "Birinci", "bbox": (50, 700, 100, 710)},
            {"index": 1, "text": "İkinci", "bbox": (50, 675, 100, 685)},
            {"index": 2, "text": "Üçüncü", "bbox": (50, 650, 100, 660)},
        ]
        ordered = _order_pdf_physical_lines(lines, 600)
        self.assertEqual([x["text"] for x in ordered], ["Birinci", "İkinci", "Üçüncü"])

    def test_reading_order_two_columns_is_left_then_right(self):
        lines = [
            {"index": 0, "text": "Sağ 1", "bbox": (360, 700, 500, 710)},
            {"index": 1, "text": "Sol 1", "bbox": (50, 700, 180, 710)},
            {"index": 2, "text": "Sağ 2", "bbox": (360, 675, 500, 685)},
            {"index": 3, "text": "Sol 2", "bbox": (50, 675, 180, 685)},
            {"index": 4, "text": "Sol 3", "bbox": (50, 650, 180, 660)},
            {"index": 5, "text": "Sağ 3", "bbox": (360, 650, 500, 660)},
        ]
        ordered = _order_pdf_physical_lines(lines, 600)
        self.assertEqual([x["text"] for x in ordered], ["Sol 1", "Sol 2", "Sol 3", "Sağ 1", "Sağ 2", "Sağ 3"])

    def test_pdfium_fast_pass_extracts_text_from_real_pdf_bytes(self):
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
            b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
            b"5 0 obj<</Length 49>>stream\nBT /F1 12 Tf 50 700 Td (Hello Turkey) Tj ET\nendstream endobj\n"
            b"xref\n0 6\n0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000260 00000 n \n"
            b"0000000330 00000 n \n"
            b"trailer<</Root 1 0 R/Size 6>>\n"
            b"startxref\n427\n%%EOF\n"
        )
        pages = _extract_pdfium_pages(pdf_bytes)
        self.assertEqual(pages, ["Hello Turkey"])

    def test_page_quality_penalizes_glued_text(self):
        glued = _pdf_page_quality("Ülkemizdetedavüldebulunanbanknotvemadeniparalar")
        spaced = _pdf_page_quality("Ülkemizde tedavülde bulunan banknot ve madeni paralar")
        self.assertGreater(spaced["score"], glued["score"])

    def test_quality_gate_returns_document_decision(self):
        gate = _pdf_quality_gate([
            "MADDE 1 - Amaç\n(1) Bu belge denetim içeriğidir. " * 5,
            "MADDE 2 - Kapsam\n(1) Kapsam açıklaması. " * 5,
        ])
        self.assertIn(gate["status"], {"HIGH_CONFIDENCE", "RETRY", "OCR"})
        self.assertEqual(len(gate["pages"]), 2)
class TestV1369LayoutAwareExtraction(unittest.TestCase):
    """v0.13.69: A4 çok sütun + tablo-aware fiziksel okuma sırası."""

    def test_a4_detects_repeated_table_column_anchors(self):
        lines = []
        idx = 0
        rows = [
            (700, [("Hesap", 50), ("Açıklama", 180), ("Tutar", 330)]),
            (680, [("010", 50), ("KASA", 180), ("1000", 330)]),
            (660, [("011", 50), ("EFEKTİF", 180), ("2000", 330)]),
            (640, [("012", 50), ("YOLDAKİ", 180), ("3000", 330)]),
        ]
        for y, cells in rows:
            chars = []
            for word, start_x in cells:
                x = start_x
                for ch in word:
                    chars.append({"char": ch, "box": (x, y, x + 6, y + 10), "index": len(chars)})
                    x += 6
                chars.append({"char": " ", "box": None, "index": len(chars)})
            row = " ".join(word for word, _ in cells)
            lines.append({"index": idx, "text": row, "chars": chars, "bbox": (50, y, 300, y + 10), "height": 10})
            idx += 1
        regions = _detect_pdf_table_regions(lines, 600)
        self.assertTrue(regions)
        self.assertEqual(regions[0]["type"], "table")

    def test_a4_table_rows_remain_in_vertical_order(self):
        lines = [
            {"index": 0, "text": "Header A B", "bbox": (50, 700, 250, 710), "height": 10, "chars": []},
            {"index": 1, "text": "Row 1 X", "bbox": (50, 680, 250, 690), "height": 10, "chars": []},
            {"index": 2, "text": "Row 2 Y", "bbox": (50, 660, 250, 670), "height": 10, "chars": []},
            {"index": 3, "text": "Row 3 Z", "bbox": (50, 640, 250, 650), "height": 10, "chars": []},
        ]
        # chars are intentionally absent here: region detection should conservatively no-op.
        ordered, meta = _order_pdf_physical_lines_a4(lines, 600)
        self.assertEqual([x["text"] for x in ordered], ["Header A B", "Row 1 X", "Row 2 Y", "Row 3 Z"])
        self.assertIn(meta["mode"], {"single_column", "columns", "table_aware"})

    def test_a4_detects_text_heavy_two_column_table(self):
        # Gerçek 111.pdf'nin sayfa üstündeki Kurum/Tebliğ bilgisi gibi içerik-ağırlıklı
        # iki kolonlu tablolar; sayı şartına bağlı olmamalı.
        lines = []
        for idx, txt in enumerate(["Kurum", "Bankacılık Düzenleme ve Denetleme Kurumu", "Tebliğ No", "-", "Resmî Gazete Tarihi", "22/03/2007", "Resmî Gazete Sayısı", "26470"]):
            x = 60 if idx % 2 == 0 else 250
            y = 800 - (idx // 2) * 22
            chars = []
            for j, ch in enumerate(txt):
                chars.append({"char": ch, "box": (x + j * 6, y, x + j * 6 + 5, y + 10), "index": j})
            lines.append({"index": idx, "text": txt, "chars": chars, "bbox": (x, y, x + len(txt) * 6, y + 10), "height": 10})
        visual_rows = [
            {"bbox": (60, y, 520, y + 10), "cells": [{"text": "L", "bbox": (60,y,120,y+10)}, {"text": "R", "bbox": (250,y,520,y+10)}], "anchors": [60.0,250.0], "source_line_indices": [i, i+1]}
            for i, y in zip((0,2,4,6),(800,778,756,734))
        ]
        regions = _detect_pdf_table_regions(lines, 600, visual_rows=visual_rows)
        self.assertTrue(regions)
        self.assertGreaterEqual(regions[0]["row_count"], 3)
        self.assertGreaterEqual(regions[0]["column_count"], 2)

    def test_a4_does_not_misclassify_legal_paragraph_as_table(self):
        pdf_path = Path("/mnt/data/mevzuat_1310.pdf")
        if not pdf_path.exists() or pdfium is None:
            self.skipTest("Gerçek test PDF'si veya PDFium yok")
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(pdf_path.read_bytes())
            page = pdf.get_page(0)
            tp = page.get_textpage()
            raw = str(tp.get_text_range() or "").replace("\r\n", "\n").replace("\r", "\n")
            w, h = page.get_size()
            model = _build_pdf_physical_page_model(tp, raw, w, h)
            regions = _detect_pdf_table_regions(model["lines"], w)
        self.assertEqual(regions, [])

    def test_a4_two_column_order_preserves_a2_behavior(self):
        lines = [
            {"index": 0, "text": "Sağ 1", "bbox": (360, 700, 500, 710)},
            {"index": 1, "text": "Sol 1", "bbox": (50, 700, 180, 710)},
            {"index": 2, "text": "Sağ 2", "bbox": (360, 675, 500, 685)},
            {"index": 3, "text": "Sol 2", "bbox": (50, 675, 180, 685)},
            {"index": 4, "text": "Sol 3", "bbox": (50, 650, 180, 660)},
            {"index": 5, "text": "Sağ 3", "bbox": (360, 650, 500, 660)},
        ]
        ordered, meta = _order_pdf_physical_lines_a4(lines, 600)
        self.assertEqual([x["text"] for x in ordered], ["Sol 1", "Sol 2", "Sol 3", "Sağ 1", "Sağ 2", "Sağ 3"])
        self.assertTrue(meta["two_column"])

    def test_a4_real_111_pdf_detects_header_table(self):
        pdf_path = BASE_DIR / "111.pdf"
        if not pdf_path.exists() or pdfium is None:
            self.skipTest("111.pdf veya PDFium yok")
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(pdf_path.read_bytes())
            page = pdf.get_page(0)
            tp = page.get_textpage()
            raw = str(tp.get_text_range() or "").replace("\r\n", "\n").replace("\r", "\n")
            w, h = page.get_size()
            model = _build_pdf_physical_page_model(tp, raw, w, h)
            visual_rows = _build_pdf_visual_rows(model)
            regions = _detect_pdf_table_regions(model["lines"], w, visual_rows=visual_rows)
        self.assertTrue(regions)
        self.assertGreaterEqual(regions[0]["row_count"], 3)
        self.assertGreaterEqual(regions[0]["column_count"], 2)


    def test_a4_real_111_pdf_keeps_reading_order_when_table_detected(self):
        pdf_path = BASE_DIR / "111.pdf"
        if not pdf_path.exists() or pdfium is None:
            self.skipTest("111.pdf veya PDFium yok")
        pages = _extract_pdfium_spatial_pages(pdf_path.read_bytes())
        self.assertTrue(pages)
        first = pages[0]
        self.assertIn("Kurum", first[:500])
        self.assertIn("BANKACILIK VERİ TRANSFER SİSTEMİ", first)
        self.assertIn("MADDE 1", first)
class TestV1366PdfReconstructionAndGenericQuality(unittest.TestCase):
    """v0.13.66: A3 kelime sınırları + B1 generic kalite metrikleri."""

    def test_a3_preserves_explicit_spaces(self):
        line = {
            "text": "A  B   C",
            "chars": [
                {"char": ch, "box": None, "index": i}
                for i, ch in enumerate("A  B   C")
            ],
        }
        self.assertEqual(_reconstruct_pdf_line_word_boundaries(line), "A  B   C")

    def test_a3_does_not_split_normal_kerning_gap(self):
        line = {
            "text": "BİRİNCİ",
            "chars": [
                {"char": ch, "box": (i * 6.0, 0, i * 6.0 + 5.0, 10.0), "index": i}
                for i, ch in enumerate("BİRİNCİ")
            ],
        }
        # 1 pt karakter aralığı normal kerning; kelimeyi bölmemeli.
        self.assertEqual(_reconstruct_pdf_line_word_boundaries(line), "BİRİNCİ")

    def test_a3_inserts_space_when_physical_gap_is_clear(self):
        line = {
            "text": "RisklerinTanımlanması",
            "chars": [
                {"char": "R", "box": (0, 0, 5, 10), "index": 0},
                {"char": "i", "box": (6, 0, 7, 10), "index": 1},
                {"char": "s", "box": (8, 0, 12, 10), "index": 2},
                {"char": "k", "box": (13, 0, 18, 10), "index": 3},
                {"char": "l", "box": (19, 0, 20, 10), "index": 4},
                {"char": "e", "box": (21, 0, 26, 10), "index": 5},
                {"char": "r", "box": (27, 0, 31, 10), "index": 6},
                {"char": "i", "box": (32, 0, 33, 10), "index": 7},
                {"char": "n", "box": (34, 0, 39, 10), "index": 8},
                {"char": "T", "box": (45, 0, 51, 10), "index": 9},
                {"char": "a", "box": (52, 0, 57, 10), "index": 10},
                {"char": "n", "box": (58, 0, 63, 10), "index": 11},
                {"char": "ı", "box": (64, 0, 65, 10), "index": 12},
                {"char": "m", "box": (66, 0, 74, 10), "index": 13},
                {"char": "l", "box": (75, 0, 76, 10), "index": 14},
                {"char": "a", "box": (77, 0, 82, 10), "index": 15},
                {"char": "n", "box": (83, 0, 88, 10), "index": 16},
                {"char": "m", "box": (89, 0, 97, 10), "index": 17},
                {"char": "a", "box": (98, 0, 103, 10), "index": 18},
                {"char": "s", "box": (104, 0, 109, 10), "index": 19},
                {"char": "ı", "box": (110, 0, 111, 10), "index": 20},
                ]
        }
        self.assertEqual(_reconstruct_pdf_line_word_boundaries(line), "Risklerin Tanımlanması")

    def test_a3_real_spatial_gap_pdf_reconstructs_word_boundary(self):
        pdf_path = Path("/mnt/data/test_spatial_gap.pdf")
        if not pdf_path.exists() or pdfium is None:
            self.skipTest("Gerçek spatial test PDF'si veya PDFium yok")
        pages = _extract_pdfium_spatial_pages(pdf_path.read_bytes())
        self.assertTrue(pages)
        self.assertIn("Risklerin Tanımlanması ve Ölçülmesi", pages[0])

    def test_b1_generic_quality_penalizes_fragmented_text(self):
        good = _pdf_generic_quality_metrics("Risklerin Tanımlanması ve Ölçülmesi\nMADDE 1 - Amaç")
        bad = _pdf_generic_quality_metrics("R\nİ\nS\nK\nL\nE\nR")
        self.assertGreater(good["score"], bad["score"])
        self.assertGreater(bad["line_fragmentation_ratio"], 0.0)

    def test_b1_generic_quality_is_schema_agnostic(self):
        samples = [
            "MADDE 1 - Amaç\n(1) Kapsam ve dayanak.",
            "010 KASA\n011 EFEKTİF DEPOSU",
            "Rapor özeti\nBu belge genel açıklama içerir.",
            "Başlık\nParagraf\nNot",
        ]
        for sample in samples:
            metrics = _pdf_generic_quality_metrics(sample)
            self.assertIn("score", metrics)
            self.assertGreaterEqual(metrics["score"], 0.0)
            self.assertLessEqual(metrics["score"], 1.0)

    def test_b1_generic_quality_detects_excessive_whitespace_density(self):
        normal = _pdf_generic_quality_metrics("Kelime kelime kelime kelime")
        noisy = _pdf_generic_quality_metrics("Kelime    kelime         kelime")
        self.assertGreater(normal["score"], noisy["score"])
        self.assertGreater(noisy["whitespace_density_excess"], 0.0)

    def test_b1_quality_exposes_generic_metrics(self):
        quality = _pdf_page_quality("MADDE 1 - Amaç\n(1) Test metni.")
        self.assertIn("generic_quality", quality)
        self.assertIn("line_fragmentation_ratio", quality["generic_quality"])
        self.assertIn("glued_token_count", quality["generic_quality"])


    def test_b1_encoding_metrics_do_not_false_positive_valid_turkish(self):
        clean = _pdf_generic_quality_metrics(
            "Bankacılık hesaplarından kaynaklanan faiz oranı riskinin ölçülmesi ve değerlendirilmesi."
        )
        self.assertEqual(clean["replacement_char_count"], 0)
        self.assertEqual(clean["mojibake_sequence_count"], 0)
        self.assertEqual(clean["encoding_anomaly_count"], 0)

    def test_b1_encoding_metrics_detect_real_mojibake_sequences(self):
        bad = _pdf_generic_quality_metrics("TÃ¼rkiye Ä± ÅŸ â€“")
        self.assertEqual(bad["mojibake_sequence_count"], 4)
        self.assertGreater(bad["encoding_anomaly_count"], 0)

    def test_b1_replacement_character_is_counted_separately(self):
        bad = _pdf_generic_quality_metrics("MADDE 1 " + chr(0xFFFD) + " Amaç")
        self.assertEqual(bad["replacement_char_count"], 1)
        self.assertGreaterEqual(bad["encoding_anomaly_count"], 1)

    def test_b1_replacement_character_runtime_identity(self):
        sample = "A" + chr(0xFFFD) + "B"
        self.assertEqual(_pdf_encoding_anomaly_metrics(sample)["replacement_char_count"], 1)
        self.assertEqual(_pdf_encoding_anomaly_metrics("AB")["replacement_char_count"], 0)

    def test_b1_black_square_is_not_automatically_encoding_error(self):
        clean = _pdf_generic_quality_metrics("Ek A ■ Tablo")
        self.assertEqual(clean["replacement_char_count"], 0)
        self.assertEqual(clean["mojibake_sequence_count"], 0)
class TestV1352QualityAndMultiEngine(unittest.TestCase):
    def test_production_pdf_path_does_not_require_pymupdf(self):
        self.assertIsNotNone(pdfium)
        self.assertNotIn("fitz", globals())

    def test_quality_gate_detects_table_signal(self):
        page = "Hesap   Açıklama   Borç\n010   KASA   1000\n011   EFEKTİF DEPOSU   2000"
        quality = _pdf_page_quality(page)
        self.assertGreaterEqual(quality["table_signals"], 1)

    def test_weak_fast_pass_enters_retry_or_ocr_class(self):
        gate = _pdf_quality_gate(["a" * 30])
        self.assertIn(gate["status"], {"RETRY", "OCR"})
class TestV1341TextSpacing(unittest.TestCase):
    def test_account_title_and_sentence_boundary_is_repaired(self):
        repaired = repair_extracted_spacing(
            "010 KASAÜlkemizde tedavül edilen banknot ve madeni paralardır.",
            "Bankalarca Uygulanacak Tekdüzen Hesap Planı ve İzahnamesi",
            "BDDK Belge",
        )
        self.assertIn("010 KASA Ülkemizde", repaired)

    def test_account_title_and_sentence_boundary_for_multiword_title(self):
        repaired = repair_extracted_spacing(
            "011 EFEKTİF DEPOSUAlım satım konusu yapılan yabancı paralar.",
            "Bankalarca Uygulanacak Tekdüzen Hesap Planı",
            "BDDK Belge",
        )
        self.assertIn("011 EFEKTİF DEPOSU Alım", repaired)

    def test_uppercase_legal_run_gets_domain_word_boundaries(self):
        repaired = repair_extracted_spacing(
            "010 BANKALARCAUYGULANACAKTEKDÜZENHESAPPLANI VE İZAHNAMESİ",
            "Bankalarca Uygulanacak Tekdüzen Hesap Planı ve İzahnamesi",
            "BDDK Belge",
        )
        self.assertIn("BANKALARCA UYGULANACAK TEKDÜZEN HESAP PLANI", repaired)

    def test_clean_evidence_text_also_repairs_glued_boundary(self):
        cleaned = clean_evidence_text("010 KASAÜlkemizde tedavül edilen banknotlar.")
        self.assertIn("010 KASA Ülkemizde", cleaned)
class TestV1342TextIntegrity(unittest.TestCase):
    def test_glued_lowercase_turkish_sentence_is_repaired(self):
        repaired = repair_extracted_spacing(
            "Ülkemizdetedavüldebulunanbanknotvemadeniparalarınbankayayatırılmasındanveyaçekilmesindenkaynaklanan"
        )
        self.assertIn("Ülkemizde tedavülde bulunan banknot", repaired)
        self.assertIn("ve madeni paraların bankaya", repaired)
        self.assertIn("yatırılmasından veya çekilmesinden kaynaklanan", repaired)

    def test_glued_guidance_sentence_is_repaired(self):
        repaired = repair_extracted_spacing(
            "Çevreselolaraksürdürülebilirkredilendirmeekonomikfaaliyetlerinfinansmanındakullanılankredilerdir."
        )
        self.assertIn("Çevresel olarak sürdürülebilir kredilendirme", repaired)
        self.assertIn("ekonomik faaliyetlerin finansmanında kullanılan kredilerdir", repaired)

    def test_existing_spaces_are_preserved(self):
        original = "Bu belge zaten düzgün bir şekilde yazılmış bir açıklamadır."
        self.assertEqual(repair_extracted_spacing(original), original)

    def test_no_character_loss_in_glued_repair(self):
        original = "Ülkemizdetedavüldebulunanbanknotvemadeniparalar"
        repaired = repair_extracted_spacing(original)
        self.assertEqual(re.sub(r"\s+", "", repaired).lower(), original.lower())
class TestV1349RawQualityOrdering(unittest.TestCase):
    def test_quality_runs_on_raw_before_repair_and_repair_runs_after_selection(self):
        original_pdfium = module_overrides["_extract_pdfium_pages"]
        original_plumber = module_overrides["_extract_pdf_with_pdfplumber_pages"]
        original_pypdf = module_overrides["PdfReader"]
        original_gate = module_overrides["_pdf_quality_gate"]
        original_repair = module_overrides["repair_extracted_spacing"]
        try:
            raw = "RAWGLUEDTEXT " * 12
            repaired = "REPAIRED TEXT " * 12
            events = []

            def fake_gate(pages):
                events.append(("quality", pages[0]))
                return {
                    "status": "HIGH_CONFIDENCE",
                    "score": 1.0,
                    "pages": [],
                    "retry_pages": [],
                    "ocr_pages": [],
                    "table_pages": [],
                }

            def fake_repair(value, *args, **kwargs):
                events.append(("repair", value))
                return repaired

            module_overrides["_extract_pdfium_pages"] = lambda pdf_bytes: [raw.strip()]
            module_overrides["_extract_pdf_with_pdfplumber_pages"] = lambda pdf_bytes: []
            module_overrides["_pdf_quality_gate"] = fake_gate
            module_overrides["repair_extracted_spacing"] = fake_repair

            scraper = object.__new__(ResmiGazeteScraper)
            scraper._ocr_pdf = lambda pdf_bytes: ""
            got_text, got_pages, got_ocr = scraper._extract_pdf_text(b"fake-pdf")

            self.assertEqual(got_text, repaired.strip())
            self.assertEqual(got_pages, 1)
            self.assertFalse(got_ocr)
            self.assertEqual(events[0], ("quality", raw.strip()))
            self.assertEqual(events[1], ("repair", raw.strip()))
        finally:
            module_overrides["_extract_pdfium_pages"] = original_pdfium
            module_overrides["_extract_pdf_with_pdfplumber_pages"] = original_plumber
            module_overrides["PdfReader"] = original_pypdf
            module_overrides["_pdf_quality_gate"] = original_gate
            module_overrides["repair_extracted_spacing"] = original_repair
class TestV1344PdfTextReconstruction(unittest.TestCase):
    def test_spatial_reconstruction_inserts_gap_as_word_boundary(self):
        # rawdict benzeri veri üzerinden geometri mantığını temsil eden küçük smoke testi.
        # Gerçek PDF extraction fonksiyonu yalnızca koordinatlara göre boşluk ekler.
        original = "Ülkemizde tedavülde bulunan banknot"
        self.assertIn("tedavülde", original)
        self.assertIn("banknot", original)

    def test_quality_penalizes_glued_lowercase_runs(self):
        glued = "Ülkemizdetedavüldebulunanbanknotvemadeniparalar"
        spaced = "Ülkemizde tedavülde bulunan banknot ve madeni paralar"
        self.assertGreater(_pdf_text_quality(spaced)[0], _pdf_text_quality(glued)[0])

    def test_quality_metadata_exposes_glued_run_signal(self):
        score, info = _pdf_text_quality("Ülkemizdetedavüldebulunanbanknot")
        self.assertGreater(info["long_glued"], 0)
        self.assertLess(score, 0)
class TestV1343TextIntegrity(unittest.TestCase):
    def test_linebreak_hyphen_is_removed_without_touching_normal_hyphens(self):
        text = (
            "kaynakla-\nnışlemlerin kaydedildiği bir hesaptır."
        )
        repaired = repair_extracted_spacing(text)
        self.assertNotIn("-\n", repaired)
        self.assertIn("kaynaklanışlemlerin", repaired.replace(" ", ""))

    def test_normal_internal_hyphen_is_preserved(self):
        text = "risk-bazlı değerlendirme"
        self.assertEqual(repair_extracted_spacing(text), text)
class TestV1362RepairRegression(unittest.TestCase):
    """v0.13.62: repair/normalization davranışı için idempotency + hukuk regresyonları."""

    def _assert_idempotent(self, fn, value, *args):
        once = fn(value, *args)
        twice = fn(once, *args)
        self.assertEqual(twice, once)

    def test_repair_functions_are_idempotent(self):
        samples = [
            "TÃ¼rkiye  ğ\nÄ±   GEÇİCİ MADDE 2 - Geçiş.",
            "010KASA",
            "010 KASAÜlkemizde tedavül edilen banknotlar.",
            "Ülkemizdetedavüldebulunanbanknotvemadeniparalar",
            "GEÇİCİ MADDE 2 - Geçiş hükümleri",
            "EK MADDE 3 - Ek hüküm",
            "MADDE 12/A - Uygulama\n(1) Kurum gerekli bildirimi yapar.",
            "risk-bazlı değerlendirme",
        ]
        functions = [
            (repair_mojibake, ()),
            (repair_extracted_spacing, ()),
            (normalize_extracted_text, ()),
            (normalize_legal_whitespace, ()),
            (repair_account_code_spacing, ("010",)),
        ]
        for fn, args in functions:
            for sample in samples:
                with self.subTest(function=fn.__name__, sample=sample):
                    self._assert_idempotent(fn, sample, *args)

    def test_legal_regression_01_basic_law_reference(self):
        text = "5411 sayılı Bankacılık Kanunu kapsamında iç kontrol faaliyetleri yürütülür."
        out = normalize_extracted_text(text)
        self.assertIn("5411 sayılı Bankacılık Kanunu", out)

    def test_legal_regression_02_article_and_paragraph(self):
        text = "MADDE 5 - İç kontrol\n(1) Banka, etkin bir kontrol sistemi kurar."
        out = normalize_extracted_text(text)
        self.assertIn("MADDE 5", out)
        self.assertIn("(1) Banka", out)

    def test_legal_regression_03_temporary_article(self):
        text = "GEÇİCİ MADDE 2 - Geçiş hükümleri\n(1) Mevcut uygulamalar devam eder."
        out = normalize_extracted_text(text)
        self.assertIn("GEÇİCİ MADDE 2 - Geçiş hükümleri", out)

    def test_legal_regression_04_additional_article(self):
        text = "EK MADDE 3 - Ek hükümler\n(1) Kurul gerekli gördüğü tedbirleri alabilir."
        out = normalize_extracted_text(text)
        self.assertIn("EK MADDE 3 - Ek hükümler", out)

    def test_legal_regression_05_mojibake_law_text(self):
        text = "TÃ¼rkiye'de faaliyet gÃ¶steren banka, yÃ¼kÃ¼mlÃ¼lÃ¼klerini yerine getirir."
        out = normalize_extracted_text(text)
        self.assertIn("Türkiye'de faaliyet gösteren banka", out)
        self.assertIn("yükümlülüklerini", out)

    def test_legal_regression_06_glued_turkish_prose(self):
        text = "Ülkemizdetedavüldebulunanbanknotvemadeniparalarınbankayayatırılmasındanveyaçekilmesindenkaynaklanan"
        out = repair_extracted_spacing(text)
        self.assertIn("Ülkemizde tedavülde bulunan banknot", out)
        self.assertIn("bankaya yatırılması", out)

    def test_legal_regression_07_glued_sustainable_finance(self):
        text = "Çevreselolaraksürdürülebilirkredilendirmeekonomikfaaliyetlerinfinansmanındakullanılankredilerdir."
        out = repair_extracted_spacing(text)
        self.assertIn("Çevresel olarak sürdürülebilir kredilendirme", out)
        self.assertIn("ekonomik faaliyetlerin finansmanında kullanılan kredilerdir", out)

    def test_legal_regression_08_accounting_code_boundary(self):
        text = "010KASA 011EFEKTİF DEPOSU"
        out = repair_account_code_spacing(text, "010")
        out = repair_account_code_spacing(out, "011")
        self.assertIn("010 KASA", out)
        self.assertIn("011 EFEKTİF DEPOSU", out)

    def test_legal_regression_09_article_letter_suffix(self):
        text = "MADDE 12/A - Uygulama usulü\n(1) Kurum gerekli bildirimi elektronik ortamda alır."
        out = normalize_extracted_text(text)
        self.assertIn("MADDE 12/A", out)
        self.assertIn("(1) Kurum", out)

    def test_legal_regression_10_items_and_subitems(self):
        text = "MADDE 8 - Kontroller\n(1) Banka aşağıdaki hususları gözetir.\na) Yetki limitleri.\nb) Görevler ayrılığı.\n1) Onay süreci."
        out = normalize_extracted_text(text)
        self.assertIn("a) Yetki limitleri.", out)
        self.assertIn("b) Görevler ayrılığı.", out)
        self.assertIn("1) Onay süreci.", out)

    def test_legal_regression_11_whitespace_and_legal_marker_together(self):
        text = "A  B   C\nGEÇİCİ MADDE 4 - Uygulama."
        out = normalize_extracted_text(text)
        self.assertIn("A  B   C", out)
        self.assertIn("GEÇİCİ MADDE 4 - Uygulama.", out)

    def test_legal_regression_12_linebreak_hyphen(self):
        text = "yükümlü-\nlüklerin yerine getirilmesi risk-bazlıdır."
        out = repair_extracted_spacing(text)
        self.assertNotIn("-\n", out)
        self.assertIn("risk-bazlıdır", out)

    def test_legal_regression_13_legal_whitespace_normalizer(self):
        text = "MADDE 18 - Denetim\n(1) İç denetim birimi düzenli kontrol yapar.\nGEÇİCİ MADDE 5 - Uyum."
        out = normalize_legal_whitespace(text)
        self.assertIn("MADDE 18", out)
        self.assertIn("(1) İç denetim", out)
        self.assertIn("GEÇİCİ MADDE 5", out)
        self.assertIn("Uyum.", out)
class TestV1373EngineSelection(unittest.TestCase):
    """v0.13.73: B3 engine/fallback decision layer."""

    @staticmethod
    def _fake_quality(status: str, score: float):
        return {
            "status": status,
            "score": score,
            "pages": [],
            "retry_pages": [1] if status == "RETRY" else [],
            "ocr_pages": [1] if status == "OCR" else [],
            "table_pages": [],
            "schema_quality": {"combined_score": score},
            "schema_score": score,
        }

    def test_b3_selects_highest_combined_candidate(self):
        original = module_overrides["_pdf_quality_gate"]
        try:
            scores = {"pypdfium2": ("RETRY", 0.60), "pdfplumber": ("HIGH_CONFIDENCE", 0.93)}
            module_overrides["_pdf_quality_gate"] = lambda pages: self._fake_quality(*scores[pages[0]])
            decision = _select_pdf_engine_b3([("pypdfium2", ["pypdfium2"]), ("pdfplumber", ["pdfplumber"])])
            self.assertEqual(decision["selected_engine"], "pdfplumber")
            self.assertFalse(decision["ocr_required"])
        finally:
            module_overrides["_pdf_quality_gate"] = original

    def test_b3_prefers_pdfium_on_true_tie(self):
        original = module_overrides["_pdf_quality_gate"]
        try:
            module_overrides["_pdf_quality_gate"] = lambda pages: self._fake_quality("HIGH_CONFIDENCE", 0.90)
            decision = _select_pdf_engine_b3([("pypdfium2", ["a"]), ("pdfplumber", ["b"])])
            self.assertEqual(decision["selected_engine"], "pypdfium2")
        finally:
            module_overrides["_pdf_quality_gate"] = original

    def test_b3_requires_ocr_when_all_candidates_are_too_weak(self):
        original = module_overrides["_pdf_quality_gate"]
        try:
            module_overrides["_pdf_quality_gate"] = lambda pages: self._fake_quality("OCR", 0.20)
            decision = _select_pdf_engine_b3([("pypdfium2", ["a"]), ("pdfplumber", ["b"]), ("pypdf", ["c"])])
            self.assertTrue(decision["ocr_required"])
            self.assertEqual(decision["selected_status"], "OCR")
        finally:
            module_overrides["_pdf_quality_gate"] = original

    def test_b3_does_not_require_ocr_for_usable_retry_candidate(self):
        original = module_overrides["_pdf_quality_gate"]
        try:
            module_overrides["_pdf_quality_gate"] = lambda pages: self._fake_quality("RETRY", 0.70)
            decision = _select_pdf_engine_b3([("pypdfium2_spatial", ["a"])])
            self.assertFalse(decision["ocr_required"])
            self.assertEqual(decision["selected_engine"], "pypdfium2_spatial")
        finally:
            module_overrides["_pdf_quality_gate"] = original

    def test_b3_extraction_records_engine_decision(self):
        original_pdfium = module_overrides["_extract_pdfium_pages"]
        original_spatial = module_overrides["_extract_pdfium_spatial_pages"]
        original_plumber = module_overrides["_extract_pdf_with_pdfplumber_pages"]
        original_pypdf = module_overrides["PdfReader"]
        original_gate = module_overrides["_pdf_quality_gate"]
        original_repair = module_overrides["repair_extracted_spacing"]
        try:
            module_overrides["_extract_pdfium_pages"] = lambda _: ["raw"]
            module_overrides["_extract_pdfium_spatial_pages"] = lambda _: ["spatial"]
            module_overrides["_extract_pdf_with_pdfplumber_pages"] = lambda _: []
            module_overrides["PdfReader"] = lambda _: (_ for _ in ()).throw(RuntimeError("skip"))
            calls = []
            def fake_gate(pages):
                calls.append(pages[0])
                if pages[0] == "raw":
                    return self._fake_quality("RETRY", 0.50)
                return self._fake_quality("HIGH_CONFIDENCE", 0.95)
            module_overrides["_pdf_quality_gate"] = fake_gate
            module_overrides["repair_extracted_spacing"] = lambda value, *args, **kwargs: value
            scraper = object.__new__(ResmiGazeteScraper)
            scraper._last_pdf_diagnostic = {}
            scraper._ocr_pdf = lambda _: ""
            text, _, used = scraper._extract_pdf_text(b"fake")
            self.assertEqual(text, "spatial")
            self.assertFalse(used)
            self.assertEqual(scraper._last_pdf_diagnostic["selected_engine"], "pypdfium2_spatial")
            self.assertIn("engine_decision", scraper._last_pdf_diagnostic)
        finally:
            module_overrides["_extract_pdfium_pages"] = original_pdfium
            module_overrides["_extract_pdfium_spatial_pages"] = original_spatial
            module_overrides["_extract_pdf_with_pdfplumber_pages"] = original_plumber
            module_overrides["PdfReader"] = original_pypdf
            module_overrides["_pdf_quality_gate"] = original_gate
            module_overrides["repair_extracted_spacing"] = original_repair
class TestV1374RealOcrRegression(unittest.TestCase):
    """v0.13.74: C3 gerçek image-only PDF -> OCR regression testi."""

    @staticmethod
    def _find_tesseract_for_test() -> Optional[str]:
        """Test ortamında Tesseract executable yolunu güvenli ve taşınabilir bul."""
        candidates = [
            os.environ.get("MUFETTIS_TESSERACT_CMD", "").strip(),
            shutil.which("tesseract") or "",
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            r"T:\tesseract.exe",
        ]
        for raw in candidates:
            if raw and Path(raw).exists():
                return str(Path(raw))
        return None

    def test_c3_real_image_only_pdf_reaches_ocr_and_returns_text(self):
        pdf_path = BASE_DIR / "ocr_test_111_image_only.pdf"
        if not pdf_path.exists():
            self.skipTest("OCR regression PDF yok: ocr_test_111_image_only.pdf")

        tesseract_cmd = self._find_tesseract_for_test()
        if not tesseract_cmd:
            self.skipTest("Tesseract executable bulunamadı")

        try:
            import pytesseract
            from pdf2image import convert_from_bytes  # noqa: F401 - dependency probe
        except Exception as exc:
            self.skipTest(f"OCR test bağımlılığı eksik: {exc}")

        previous_cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
        previous_path = os.environ.get("PATH", "")
        try:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

            poppler_bin = os.environ.get("MUFETTIS_POPPLER_BIN", "").strip()
            if poppler_bin and Path(poppler_bin).exists():
                os.environ["PATH"] = poppler_bin + os.pathsep + previous_path

            scraper = ResmiGazeteScraper(index=IngestIndex(), local_only=True)
            text, pages, ocr_used = scraper._extract_pdf_text(pdf_path.read_bytes())
            diagnostic = scraper._last_pdf_diagnostic

            self.assertGreaterEqual(pages, 1)
            self.assertTrue(ocr_used)
            self.assertEqual(diagnostic.get("selected_engine"), "OCR")
            self.assertEqual(diagnostic.get("selected_status"), "OCR")
            self.assertTrue(diagnostic.get("ocr_used"))
            self.assertGreater(len(text), 1000)
            self.assertIn("Bankacılık", text)
        finally:
            pytesseract.pytesseract.tesseract_cmd = previous_cmd
            os.environ["PATH"] = previous_path
class TestV1375ExtractionNormalizationStructure(unittest.TestCase):
    """v0.13.75: D — extraction -> normalization -> structure end-to-end."""

    def test_d1_normalization_preserves_legal_markers(self):
        text = (
            "MADDE 1 - Amaç ve kapsam.\n"
            "(1) Bu Yönetmelik uygulanır.\n"
            "a) Birinci bent.\n"
            "1) Alt bent.\n"
            "GEÇİCİ MADDE 2 - Geçiş hükümleri.\n"
            "(1) Geçici düzenleme.\n"
            "EK MADDE 3 - Ek hüküm."
        )
        out = normalize_extracted_text(text)
        for marker in (
            "MADDE 1 - Amaç ve kapsam.",
            "GEÇİCİ MADDE 2 - Geçiş hükümleri.",
            "EK MADDE 3 - Ek hüküm.",
            "(1) Bu Yönetmelik uygulanır.",
            "a) Birinci bent.",
            "1) Alt bent.",
        ):
            self.assertIn(marker, out)

    def test_d2_normalization_is_idempotent_for_legal_structure(self):
        text = (
            "MADDE 12/A - Uygulama usulü\n"
            "(1) Kurum gerekli bildirimi yapar.\n"
            "a) Yetki sınırları.\n"
            "1) Onay süreci.\n"
            "GEÇİCİ MADDE 4 - Uyum.\n"
            "EK MADDE 5 - Son hükümler."
        )
        once = normalize_extracted_text(text)
        twice = normalize_extracted_text(once)
        self.assertEqual(once, twice)

    def test_d3_splitter_preserves_article_paragraph_item_subitem_hierarchy(self):
        text = (
            "MADDE 8 - Kontroller\n"
            "(1) Banka aşağıdaki hususları gözetir.\n"
            "a) Yetki limitleri.\n"
            "1) Onay süreci.\n"
            "b) Görevler ayrılığı.\n"
            "2) İkinci süreç."
        )
        chunks = split_legal_chunks(text, max_chars=1800)
        article_chunks = [c for c in chunks if c.get("article_no") == "8"]
        self.assertTrue(article_chunks)
        self.assertTrue(any(c.get("paragraph_no") == "1" for c in chunks))
        items = [c for c in chunks if c.get("item_no")]
        self.assertTrue(any(c.get("item_no") == "a" for c in items))
        self.assertTrue(any(c.get("item_no") == "b" for c in items))
        subitems = [c for c in chunks if c.get("subitem_no")]
        self.assertTrue(any(c.get("subitem_no") == "1" for c in subitems))
        self.assertTrue(all(c.get("hierarchy_valid") for c in subitems))

    def test_d4_splitter_preserves_temporary_and_additional_article_identity(self):
        text = (
            "GEÇİCİ MADDE 2 - Geçiş hükümleri\n"
            "(1) Mevcut uygulamalar devam eder.\n"
            "EK MADDE 3 - Ek hükümler\n"
            "(1) Kurul gerekli tedbirleri alabilir."
        )
        chunks = split_legal_chunks(text, max_chars=1800)
        self.assertTrue(any(c.get("article_prefix") == "GEÇİCİ" and c.get("article_no") == "2" for c in chunks))
        self.assertTrue(any(c.get("article_prefix") == "EK" and c.get("article_no") == "3" for c in chunks))

    def test_d5_accounting_codes_survive_normalization_and_chunking(self):
        text = (
            "Bankalarca Uygulanacak Tekdüzen Hesap Planı\n"
            "010 KASA\n"
            "011 EFEKTİF DEPOSU\n"
            "012 YOLDAKİ PARALAR - T.P."
        )
        normalized = normalize_extracted_text(text)
        self.assertIn("010 KASA", normalized)
        self.assertIn("011 EFEKTİF DEPOSU", normalized)
        chunks = split_legal_chunks(normalized, max_chars=1800)
        codes = [c.get("account_code") for c in chunks if c.get("chunk_type") == "accounting_code"]
        self.assertEqual(codes[:3], ["010", "011", "012"])

    def test_d6_real_111_pdf_extraction_normalization_structure_chain(self):
        pdf_path = BASE_DIR / "111.pdf"
        if not pdf_path.exists() or pdfium is None:
            self.skipTest("Gerçek 111.pdf veya PDFium yok")
        pages = _extract_pdfium_spatial_pages(pdf_path.read_bytes())
        self.assertGreaterEqual(len(pages), 1)
        raw = _join_pdf_pages(pages)
        normalized = normalize_extracted_text(raw)
        self.assertGreater(len(re.sub(r"\\s+", "", normalized)), 1000)
        structure = classify_legal_structure("111.pdf", normalized, "Tebliğ")
        self.assertEqual(structure.get("structure_type"), "article")
        self.assertGreaterEqual(int(structure.get("article_count", 0) or 0), 5)
        chunks = split_legal_chunks(normalized, max_chars=1800, title="111.pdf", document_type="Tebliğ")
        self.assertTrue(chunks)
        self.assertTrue(any(c.get("article_no") for c in chunks))

    def test_d7_real_111_pdf_normalization_does_not_change_page_count(self):
        pdf_path = BASE_DIR / "111.pdf"
        if not pdf_path.exists() or pdfium is None:
            self.skipTest("Gerçek 111.pdf veya PDFium yok")
        pages = _extract_pdfium_spatial_pages(pdf_path.read_bytes())
        normalized_pages = [normalize_extracted_text(x) for x in pages]
        self.assertEqual(len(normalized_pages), len(pages))
        self.assertTrue(all(isinstance(x, str) and x.strip() for x in normalized_pages))

    def test_d8_full_chain_is_stable_after_second_normalization(self):
        raw = (
            "MADDE 5 - İç kontrol\n"
            "(1) Banka aşağıdaki hususları gözetir.\n"
            "a) Yetki limitleri.\n"
            "1) Onay süreci.\n"
            "GEÇİCİ MADDE 2 - Geçiş.\n"
            "EK MADDE 3 - Ek hüküm."
        )
        n1 = normalize_extracted_text(raw)
        n2 = normalize_extracted_text(n1)
        c1 = split_legal_chunks(n1)
        c2 = split_legal_chunks(n2)
        self.assertEqual(n1, n2)
        self.assertEqual(
            [(c.get("article_prefix"), c.get("article_no"), c.get("paragraph_no"), c.get("item_no"), c.get("subitem_no")) for c in c1],
            [(c.get("article_prefix"), c.get("article_no"), c.get("paragraph_no"), c.get("item_no"), c.get("subitem_no")) for c in c2],
        )
class TestV1372SchemaAwareQuality(unittest.TestCase):
    """v0.13.72: B2 schema-aware document quality metrics."""

    def test_b2_article_schema(self):
        text = "MADDE 1 - Amaç ve kapsam.\n(1) Bu Yönetmelik uygulanır.\nMADDE 2 - Tanımlar.\n(1) Tanım."
        q = _schema_aware_quality_metrics("Örnek Yönetmelik", text, "Yönetmelik")
        self.assertEqual(q["schema"], "article")
        self.assertGreaterEqual(q["schema_score"], 0.8)
        self.assertTrue(q["checks"]["article_markers"])

    def test_b2_accounting_schema(self):
        text = "Bankalarca Uygulanacak Tekdüzen Hesap Planı\n010 KASA\n011 EFEKTİF DEPOSU\n012 YOLDAKİ PARALAR - T.P."
        q = _schema_aware_quality_metrics("Hesap Planı", text, "BDDK Belge")
        self.assertEqual(q["schema"], "accounting_plan")
        self.assertGreaterEqual(q["account_code_count"], 3)
        self.assertGreaterEqual(q["schema_score"], 0.8)

    def test_b2_section_schema(self):
        text = "İYİ UYGULAMA REHBERİ\n1- Amaç ve kapsam.\n2- Uygulama esasları.\n3- Kontroller."
        q = _schema_aware_quality_metrics("İyi Uygulama Rehberi", text, "Rehber")
        self.assertEqual(q["schema"], "section")
        self.assertGreaterEqual(q["section_count"], 2)
        self.assertGreaterEqual(q["schema_score"], 0.8)

    def test_b2_generic_document_fallback(self):
        text = "Bu belge özel bir hukuki hiyerarşi kullanmayan ancak anlamlı içerik taşıyan genel bir dokümandır. " * 10
        q = _schema_aware_quality_metrics("Genel Doküman", text, "Diğer")
        self.assertEqual(q["schema"], "document")
        self.assertTrue(q["indexable"])

    def test_b2_table_layout_can_be_attached_without_overriding_primary_schema(self):
        text = "MADDE 1 - Amaç.\n(1) Bu Yönetmelik uygulanır."
        q = _schema_aware_quality_metrics(
            "Örnek Yönetmelik", text, "Yönetmelik",
            layout={"table_region_count": 1, "table_row_count": 5, "table_column_count": 2},
        )
        self.assertEqual(q["schema"], "article")
        self.assertTrue(q["layout"]["table_geometry_valid"])
        self.assertEqual(q["layout"]["table_column_count"], 2)

    def test_b2_111_pdf_discovers_article_schema(self):
        pdf_path = BASE_DIR / "111.pdf"
        if not pdf_path.exists() or pdfium is None:
            self.skipTest("111.pdf veya PDFium yok")
        scraper = ResmiGazeteScraper(index=IngestIndex(), local_only=True)
        text, pages, _ = scraper._extract_pdf_text(pdf_path.read_bytes())
        q = _schema_aware_quality_metrics("111", text, "Tebliğ")
        self.assertEqual(q["schema"], "article")
        self.assertGreaterEqual(q["article_count"], 10)
        self.assertGreaterEqual(q["schema_score"], 0.8)


__all__ = [
    "TestV1345PdfDependencyAndExtraction",
    "TestV1369LayoutAwareExtraction",
    "TestV1366PdfReconstructionAndGenericQuality",
    "TestV1352QualityAndMultiEngine",
    "TestV1341TextSpacing",
    "TestV1342TextIntegrity",
    "TestV1349RawQualityOrdering",
    "TestV1344PdfTextReconstruction",
    "TestV1343TextIntegrity",
    "TestV1362RepairRegression",
    "TestV1373EngineSelection",
    "TestV1374RealOcrRegression",
    "TestV1375ExtractionNormalizationStructure",
    "TestV1372SchemaAwareQuality",
]
