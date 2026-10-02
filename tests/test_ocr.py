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


class TestPageStreaming(unittest.TestCase):
    def test_pages_are_rendered_one_at_a_time(self):
        # 764 sayfalık RG sayısı tümü birden çizilseydi ~9 GB olurdu: görüntüler istendikçe çizilmeli.
        import pypdfium2 as pdfium
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "three.pdf"
            first, *rest = [Image.new("RGB", (200, 200), "white") for _ in range(3)]
            first.save(path, "PDF", save_all=True, append_images=rest)
            data = path.read_bytes()
        rendered = []
        original = pdfium.PdfPage.render

        def counting(page, *args, **kwargs):
            rendered.append(1)
            return original(page, *args, **kwargs)

        with mock.patch.object(pdfium.PdfPage, "render", counting):
            pages = ocr.iter_page_images(data, dpi=36)
            next(pages)
            self.assertEqual(len(rendered), 1)
            self.assertEqual(len(list(pages)), 2)
        self.assertEqual(len(rendered), 3)


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
class TestMixedPdfPages(unittest.TestCase):
    """K2a.1: metin katmanı olan PDF'te görüntü sayfaları tek tek OCR'lanır (KGF kararı: 6 sayfanın 5'i görüntü)."""

    def _extract(self, fast_pages, ocr_texts):
        from mufettis.harvesting import resmi_gazete

        calls = []

        def fake_ocr(pdf_bytes, max_pages=None, lang=ocr.OCR_LANG, only=None):
            calls.append(list(only) if only is not None else None)
            return ocr_texts[:len(only or [])]

        scraper = resmi_gazete.ResmiGazeteScraper(local_only=True)
        with mock.patch.object(resmi_gazete, "_extract_pdfium_pages", return_value=fast_pages),                 mock.patch.object(resmi_gazete, "_pdf_quality_gate", return_value={"status": "HIGH_CONFIDENCE"}),                 mock.patch.object(ocr, "ocr_pdf_pages", side_effect=fake_ocr):
            text, pages, used = scraper._extract_pdf_text(b"%PDF")
        return text, used, calls

    def test_image_pages_are_filled_by_ocr(self):
        body = "Kredi Garanti Kurumlarına Sağlanan Hazine Desteğine İlişkin Karar MADDE 1 Amaç ve kapsam"
        text, used, calls = self._extract(["Karar Sayısı : 2016/9538 imza sayfası metni burada", "", " "], [body, "MADDE 2"])
        self.assertEqual(calls, [[1, 2]])
        self.assertTrue(used)
        self.assertIn("Hazine Desteğine", text)

    def test_label_only_pages_are_all_ocrd(self):
        # K2b.3: mikrofilm taraması; her sayfanın metin katmanı yalnızca "Yargıtay Film 1"
        text, used, calls = self._extract(["Yargıtay\nFilm\n1", "Yargıtay\nFilm\n2"],
                                          ["İÇTİHADI BİRLEŞTİRME KARARI Esas No: 2017/4", "Karar No: 2018/5"])
        self.assertEqual(calls, [[0, 1]])
        self.assertTrue(used)
        self.assertIn("2018/5", text)

    def test_glyph_code_pages_are_ocrd(self):
        # K5.3a: Unicode eşlemesi olmayan yazı tipi: pdfplumber harf yerine "(cid:N)" yazar, sayfa okunamaz
        glyphs = "(cid:4)(cid:12)(cid:7)(cid:6)(cid:10)(cid:11)(cid:3)(cid:5)(cid:9) " * 6
        page = "Bu sayfada yeterince uzun bir metin katmanı vardır; OCR gerekmez. " * 2
        text, used, calls = self._extract([page, glyphs], ["Kredi Garanti Kurumu kararı MADDE 1"])
        self.assertEqual(calls, [[1]])
        self.assertTrue(used)
        self.assertNotIn("(cid:", text)
        self.assertIn("MADDE 1", text)

    def test_text_pages_do_not_go_to_ocr(self):
        page = "Bu sayfada yeterince uzun bir metin katmanı vardır; OCR gerekmez. " * 2
        text, used, calls = self._extract([page, page], ["OCR METNİ"])
        self.assertEqual(calls, [])
        self.assertFalse(used)


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
