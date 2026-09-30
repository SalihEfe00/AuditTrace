# -*- coding: utf-8 -*-
"""Word 97-2003 (.doc) metni (M6.15): resmî .doc belgeler (TBB tebliğleri, TMSF yönetmelikleri) okunur."""
from __future__ import annotations

import unittest
from pathlib import Path

from mufettis.extraction.files import extract_file_text
from mufettis.extraction.word97 import _clean, is_word97, word97_text
from mufettis.harvesting.official_legislation import content_suffix, non_pdf_text

FIXTURE = Path(__file__).parent / "fixtures" / "tbb_teblig_780.doc"  # TBB Tebliğ No: 780 (1983), kamuya açık


class TestWord97(unittest.TestCase):
    def test_real_document_text(self):
        data = FIXTURE.read_bytes()
        self.assertTrue(is_word97(data))
        text = word97_text(data)
        self.assertIn("TÜRKİYE BANKALAR BİRLİĞİ", text)
        self.assertIn("Vadeli mevduat faizlerinin hesaplanmasında", text)
        self.assertIn("Valör tarihi", text)
        self.assertEqual(extract_file_text("teblig.doc", data).text, text)
        self.assertEqual(non_pdf_text(data, ".doc"), text)

    def test_field_codes_and_control_marks_are_removed(self):
        raw = "Başlık\r\x13 HYPERLINK \"x\" \x14bağlantı\x15 metin\x07hücre\x07\r\r\r\rson"
        self.assertEqual(_clean(raw), "Başlık\nbağlantı metin\thücre\n\nson")

    def test_not_a_word_file(self):
        with self.assertRaises(ValueError):
            word97_text(b"%PDF-1.7")

    def test_content_suffix_recognises_word(self):
        self.assertEqual(content_suffix(FIXTURE.read_bytes()), ".doc")
        self.assertEqual(content_suffix(b"PK\x03\x04" + b"\x00" * 26 + b"word/document.xml"), ".docx")
        self.assertEqual(content_suffix(b"PK\x03\x04" + b"\x00" * 26 + b"xl/workbook.xml"), ".pdf")


if __name__ == "__main__":
    unittest.main()
