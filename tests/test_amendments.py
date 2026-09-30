# -*- coding: utf-8 -*-
"""Madde düzeyinde değişiklik tarihleri (M7.2): kanun ve yönetmelik not biçimleri, madde düzeyinde en geç tarih."""
from __future__ import annotations

import unittest

from mufettis.legal.amendments import Amendment, amendment_metadata, amendment_notes
from mufettis.legal.parser import split_legal_chunks


class TestNotes(unittest.TestCase):
    def test_law_and_gazette_forms(self):
        text = ("MADDE 9 – (Değişik üçüncü fıkra: 24/6/2009-5915/1 md.) Kart çıkaran kuruluşlar (Ek cümle: \n"
                "13/6/2012-6327/43 md.) ... MADDE 21 – (1) (Değişik:RG-25/9/2020-31255) Kart verilemez. "
                "(Mülga birinci fıkra:RG-1/12/2021-31676)")
        self.assertEqual(amendment_notes(text), [
            Amendment("değişik", "2009-06-24", "kanun 5915"), Amendment("ek", "2012-06-13", "kanun 6327"),
            Amendment("değişik", "2020-09-25", "RG 31255"), Amendment("mülga", "2021-12-01", "RG 31676")])

    def test_other_bases(self):
        notes = amendment_notes("(Değişik: Kurul Kararı-20/8/2026-11559) a (Değişik: 7/7/2011 - KHK - 646/10 md.) b "
                                "(Değişik: 9/5/2014-2014/6381 K.)")
        self.assertEqual([(n.date, n.basis) for n in notes], [("2026-08-20", "Kurul Kararı 11559"),
                                                              ("2011-07-07", "KHK 646/10 md."),
                                                              ("2014-05-09", "2014/6381 K.")])

    def test_ordinary_parentheses_are_not_notes(self):
        self.assertEqual(amendment_notes("(1) Bu Kanunun 12 nci maddesi (24/6/2009 tarihli karar) uyarınca"), [])


class TestChunkMetadata(unittest.TestCase):
    def test_article_level_latest_date(self):
        text = ("MADDE 1 – (1) Amaç hükmü.\n"
                "MADDE 2 – (1) (Değişik:RG-25/9/2020-31255) Birinci fıkra.\n"
                "(2) (Ek:RG-6/6/2022-31858) İkinci fıkra.\n"
                "MADDE 3 – (1) Değişmemiş hüküm.\n")
        chunks = split_legal_chunks(text, max_chars=60)
        meta = amendment_metadata(chunks)
        by_article = {}
        for ch, m in zip(chunks, meta):
            by_article.setdefault(ch["article_no"], set()).add(m["article_amended_on"])
        self.assertEqual(by_article["1"], {""})
        self.assertEqual(by_article["2"], {"2022-06-06"})
        self.assertEqual(by_article["3"], {""})
        self.assertIn("değişik 2020-09-25 RG 31255", " ".join(m["amendments"] for m in meta))


if __name__ == "__main__":
    unittest.main()
