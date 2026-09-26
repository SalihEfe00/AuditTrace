# -*- coding: utf-8 -*-
"""Aşama C1: Türkçe metin normalizasyonu ve terim eşleştirme."""
from __future__ import annotations

import unittest

from mufettis.analysis.turkish_text import (
    contains_phrase,
    content_terms,
    distinct_stems,
    is_generic_term,
    matched_terms,
    terms_match,
    tokenize,
    turkish_lower,
)


class TestTurkishLower(unittest.TestCase):
    def test_dotted_and_dotless_i(self):
        self.assertEqual(turkish_lower("İŞLEM İade"), "işlem iade")
        self.assertEqual(turkish_lower("IRMAK ISPARTA"), "ırmak ısparta")
        self.assertNotIn("̇", turkish_lower("İstanbul İzmir"))

    def test_python_lower_is_broken_for_turkish(self):
        """Bu modülün varlık sebebi: str.lower() 'İ' harfini iki karaktere böler."""
        self.assertNotEqual("İşlem".lower(), "işlem")
        self.assertEqual(tokenize("İşlem İade"), ["işlem", "iade"])

    def test_empty(self):
        self.assertEqual(turkish_lower(""), "")
        self.assertEqual(turkish_lower(None), "")


class TestTermMatching(unittest.TestCase):
    def test_suffix_and_consonant_softening(self):
        self.assertTrue(terms_match("hesabımdan", "hesap"))
        self.assertTrue(terms_match("kart", "kartımdan"))
        self.assertTrue(terms_match("ücret", "ücretin"))
        self.assertTrue(terms_match("raporları", "raporlarının"))

    def test_distinct_words_do_not_match(self):
        self.assertFalse(terms_match("kasa", "kart"))
        self.assertFalse(terms_match("işleyişi", "işletim"))
        self.assertFalse(terms_match("fon", "fonksiyon"))  # 3 harfli terimler yalnızca birebir

    def test_generic_terms_excluded(self):
        for word in ("bankası", "müşterinin", "parası", "işlemi", "hesabı", "şubesi"):
            self.assertTrue(is_generic_term(word), word)
        self.assertFalse(is_generic_term("usulsüz"))
        terms = content_terms("Kasa hesabı, bankanın elindeki Türk parası nakit mevcudunu izler.")
        self.assertIn("kasa", terms)
        self.assertNotIn("hesabı", terms)
        self.assertNotIn("parası", terms)
        self.assertNotIn("bankanın", terms)

    def test_stop_words_and_numbers_excluded(self):
        terms = content_terms("Konu: 15.000.000 TL tutarındaki talep ve şikayetçiyim")
        self.assertNotIn("konu", terms)
        self.assertNotIn("000", terms)
        self.assertNotIn("şikayetçiyim", terms)

    def test_matched_terms_and_distinct_stems(self):
        matched = matched_terms({"ücretin", "işletim", "kasa"}, {"ücret", "işletim", "mevduat"})
        self.assertEqual(matched, {"ücretin", "işletim"})
        self.assertEqual(len(distinct_stems({"raporları", "raporlarının", "rapor"})), 1)
        self.assertEqual(len(distinct_stems({"kredi", "kredisi", "kontrol"})), 2)


class TestContainsPhrase(unittest.TestCase):
    def test_turkish_suffixes(self):
        self.assertTrue(contains_phrase("Konut kredisi borcum", "kredi"))
        self.assertTrue(contains_phrase("ERKEN ÖDEMESİ yapıldı", "erken ödeme"))
        self.assertTrue(contains_phrase("Şüpheli İşlem Bildirimi", "şüpheli işlem"))

    def test_word_start_required(self):
        self.assertFalse(contains_phrase("akredite edildi", "kredi"))
        self.assertFalse(contains_phrase("", "kredi"))


if __name__ == "__main__":
    unittest.main()
