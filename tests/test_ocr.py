# -*- coding: utf-8 -*-
"""Taranmış PDF OCR'ı (M6.15): Tesseract bulma, OCR yoksa boş dönme, indirici doğrulamasında OCR'a düşme."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mufettis.extraction import ocr
from mufettis.harvesting import official_legislation


class TestTesseractLookup(unittest.TestCase):
    def tearDown(self):
        ocr.tesseract_cmd.cache_clear()

    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "tesseract.exe"
            exe.write_bytes(b"")
            ocr.tesseract_cmd.cache_clear()
            with mock.patch.dict(os.environ, {"MUFETTIS_TESSERACT_CMD": str(exe)}):
                self.assertEqual(ocr.tesseract_cmd(), str(exe))

    def test_missing_tesseract_gives_no_text(self):
        ocr.tesseract_cmd.cache_clear()
        with mock.patch.object(ocr, "_KNOWN_PATHS", ()), mock.patch.dict(os.environ, {"MUFETTIS_TESSERACT_CMD": ""}), \
                mock.patch("shutil.which", return_value=None):
            self.assertIsNone(ocr.tesseract_cmd())
            self.assertFalse(ocr.available())
            self.assertEqual(ocr.ocr_pdf_pages(b"%PDF-1.7"), [])


class TestFetcherOcrFallback(unittest.TestCase):
    def test_image_only_pdf_is_verified_with_ocr_text(self):
        with mock.patch("mufettis.extraction.pdf._extract_pdfium_pages", return_value=["", ""]), \
                mock.patch("mufettis.extraction.ocr.ocr_pdf_pages", return_value=["HAYAT SİGORTASI GENEL ŞARTLARI"]) as run:
            pages = official_legislation._default_pages(b"%PDF-1.7")
        self.assertEqual(pages, ["HAYAT SİGORTASI GENEL ŞARTLARI", ""])
        run.assert_called_once_with(b"%PDF-1.7", max_pages=official_legislation.VERIFY_PAGES)

    def test_text_pdf_does_not_run_ocr(self):
        text = ["MADDE 1 – Bu Yönetmeliğin amacı " * 10]
        with mock.patch("mufettis.extraction.pdf._extract_pdfium_pages", return_value=text), \
                mock.patch("mufettis.extraction.ocr.ocr_pdf_pages") as run:
            self.assertEqual(official_legislation._default_pages(b"%PDF-1.7"), text)
        run.assert_not_called()

    def test_short_text_header_on_scanned_pdf_triggers_ocr(self):
        # Resmî Gazete görüntü PDF'i: üstte yalnızca kısa bir metin başlığı var.
        with mock.patch("mufettis.extraction.pdf._extract_pdfium_pages", return_value=["RESMÎ GAZETE 28 Temmuz 2026"]), \
                mock.patch("mufettis.extraction.ocr.ocr_pdf_pages", return_value=["KURUL KARARI " * 30]):
            self.assertEqual(official_legislation._default_pages(b"%PDF-1.7"), ["KURUL KARARI " * 30])


@unittest.skipUnless(ocr.available(), "Tesseract kurulu değil")
class TestRealOcr(unittest.TestCase):
    def test_rendered_text_page_is_read(self):
        import pypdfium2 as pdfium
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("RGB", (1200, 300), "white")
        try:
            font = ImageFont.truetype("arial.ttf", 60)
        except OSError:
            font = ImageFont.load_default()
        ImageDraw.Draw(image).text((40, 100), "GENEL ŞARTLAR MADDE 1", fill="black", font=font)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.pdf"
            image.save(path, "PDF", resolution=150)
            pdfium.PdfDocument(path.read_bytes()).close()  # geçerli PDF
            text = " ".join(ocr.ocr_pdf_pages(path.read_bytes()))
        self.assertIn("MADDE", text.upper())


if __name__ == "__main__":
    unittest.main()
