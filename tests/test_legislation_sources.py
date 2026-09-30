# -*- coding: utf-8 -*-
"""Mevzuat listesi (M6.2): kayıtlar geçerli, resmî kaynaklı, tekrarsız; öncelikli set Bölüm 3'ü kapsıyor."""
from __future__ import annotations

import dataclasses
import unittest

from mufettis.corpus.legislation_sources import (
    LegislationSource,
    load_sources,
    select_sources,
    validate_sources,
)


class TestLegislationSources(unittest.TestCase):
    def setUp(self):
        self.sources = load_sources()

    def test_list_is_valid(self):
        self.assertEqual(validate_sources(self.sources), [])

    def test_priority_set_covers_section_3(self):
        priority = {s.number for s in self.sources if s.priority == 1 and s.instrument == "kanun"}
        # 5549, 6415 (MASAK); 6098, 4721 (kefalet, aile konutu); 6502 (tüketici)
        self.assertTrue({"5549", "6415", "6098", "4721", "6502"} <= priority)
        titles = " ".join(s.title for s in self.sources if s.priority == 1)
        for phrase in ("TEDBİRLER HAKKINDA", "UYUM PROGRAMI", "İÇ SİSTEMLERİ", "KREDİLERİN SINIFLANDIRILMASI",
                       "BİLGİ SİSTEMLERİ", "KURUMSAL YÖNETİM", "DESTEK HİZMETİ", "TÜKETİCİ KREDİSİ",
                       "KONUT FİNANSMANI", "ALINACAK ÜCRETLER", "MALİ SUÇLARI ARAŞTIRMA KURULU GENEL TEBLİĞİ"):
            self.assertIn(phrase, titles)

    def test_download_urls_are_official(self):
        from urllib.parse import urlparse

        from mufettis.corpus.legislation_sources import OFFICIAL_HOSTS

        for s in self.sources:
            url = urlparse(s.download_url)
            self.assertEqual(url.scheme, "https", s.key)
            self.assertIn(url.hostname, OFFICIAL_HOSTS, s.key)
            if s.code:
                self.assertTrue(s.download_url.startswith("https://www.mevzuat.gov.tr/MevzuatMetin/"), s.key)

    def test_invalid_entries_are_reported(self):
        base = self.sources[0]
        bad = [dataclasses.replace(base, code="", url="https://www.mevzuat.org/x.pdf"),
               dataclasses.replace(base, instrument="kanun", number=""),
               dataclasses.replace(base, priority=3),
               base, base]
        problems = [p for _, p in validate_sources(bad)]
        self.assertIn("resmî olmayan alan adı: https://www.mevzuat.org/x.pdf", problems)
        self.assertIn("kanun numarası yok", problems)
        self.assertIn("geçersiz öncelik: 3", problems)
        self.assertIn("tekrarlanan kayıt", problems)

    def test_selection_skips_documents_already_in_corpus(self):
        chosen = select_sources(self.sources, max_priority=1)
        self.assertTrue(chosen)
        self.assertTrue(all(s.priority == 1 and not s.in_corpus for s in chosen))
        self.assertIn("5411", {s.number for s in select_sources(self.sources, 1, include_in_corpus=True)})
        self.assertIsInstance(chosen[0], LegislationSource)


if __name__ == "__main__":
    unittest.main()
