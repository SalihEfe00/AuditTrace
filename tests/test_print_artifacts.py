# -*- coding: utf-8 -*-
"""Tarayıcıdan yazdırılmış PDF'lerin sayfa üst/alt bilgisi belge metnine karışmaz."""
from __future__ import annotations

import unittest

from mufettis.extraction.text_repair import normalize_extracted_text, strip_print_artifacts

FOOTER = "https://www.mevzuat.gov.tr/mevzuat? Mevzuat No=5411&Mevzuat Tur=1&Mevzuat Tertip=5 {page}/45"
HEADER = "4.09.2026 18:31 Mevzuat Bilgi Sistemi"


class TestStripPrintArtifacts(unittest.TestCase):
    def test_page_break_inside_sentence_rejoins(self):
        text = (f"{HEADER}\nMadde 60 — Kredi kuruluşları dışında hiçbir kişi mevduat veya katılım fonu\n"
                f"{FOOTER.format(page=1)}\n{HEADER}\nkabul edemez.\n{FOOTER.format(page=2)}")
        out = strip_print_artifacts(text)
        self.assertEqual(out, "Madde 60 — Kredi kuruluşları dışında hiçbir kişi mevduat veya katılım fonu\nkabul edemez.\n")
        self.assertNotIn("Mevzuat Bilgi Sistemi", normalize_extracted_text(text))

    def test_single_dated_line_is_content(self):
        text = "Toplantı notu\n01.03.2024 10:00 tarihinde kredi komitesi toplandı.\nKarar alındı."
        self.assertEqual(strip_print_artifacts(text), text)

    def test_repeated_header_without_footer_is_removed(self):
        text = "27.09.2026 17:00 Rehber\nBirinci sayfa metni.\n27.09.2026 17:00 Rehber\nİkinci sayfa metni."
        self.assertEqual(strip_print_artifacts(text), "Birinci sayfa metni.\nİkinci sayfa metni.")

    def test_url_in_text_without_page_counter_is_kept(self):
        text = "Başvurular https://www.bddk.org.tr adresinden yapılır.\nhttps://www.bddk.org.tr/duyuru\nSon."
        self.assertEqual(strip_print_artifacts(text), text)


if __name__ == "__main__":
    unittest.main()
