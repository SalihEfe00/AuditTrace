# -*- coding: utf-8 -*-
"""PDF çıkarımının kelime içine koyduğu sahte boşlukların onarımı."""
from __future__ import annotations

import unittest

from mufettis.extraction.text_repair import normalize_extracted_text, normalize_legal_whitespace, repair_split_words


class TestRepairSplitWords(unittest.TestCase):
    def test_no_turkish_word_starts_with_soft_g(self):
        self.assertEqual(repair_split_words("kredi riskinin de ğerlendirilmesi ve ba ğlantılı"),
                         "kredi riskinin değerlendirilmesi ve bağlantılı")
        self.assertEqual(repair_split_words("KAR ŞILI ĞI"), "KAR ŞILIĞI")
        # Tek başına "ğ" kelime parçası değil, bozuk karakterdir.
        self.assertEqual(repair_split_words("Türkiye ğ"), "Türkiye ğ")

    def test_fragment_joined_when_the_text_itself_has_the_word(self):
        text = "5411 sayılı Kanun ve 5464 s ayılı Kanun. RİSK yönetimi, R İSK ölçümü, risk."
        self.assertEqual(repair_split_words(text),
                         "5411 sayılı Kanun ve 5464 sayılı Kanun. RİSK yönetimi, RİSK ölçümü, risk.")

    def test_two_real_words_are_never_joined(self):
        # Birleşik biçim metinde geçse de iki parça da tek başına kelime: "yurt dışında", "için de".
        text = ("yurtdışında yurtdışında yurt dışında; yurt içi; dışında kalan. "
                "içinde içinde için de; bunun için; de facto")
        self.assertEqual(repair_split_words(text), text)

    def test_single_glued_occurrence_is_not_evidence(self):
        text = "ihracat rejimi kapsamında; rejimikapsamında (yapışık satır); kapsamında kalan"
        self.assertEqual(repair_split_words(text), text)

    def test_line_breaks_and_double_spaces_are_left_alone(self):
        text = "sayılı sayılı s\nayılı s  ayılı"
        self.assertEqual(repair_split_words(text), text)

    def test_idempotent_and_used_by_normalization(self):
        text = "ka psamlı kap samlı kapsamlı kapsamlı de ğer"
        once = repair_split_words(text)
        self.assertEqual(repair_split_words(once), once)
        self.assertEqual(once, "kapsamlı kapsamlı kapsamlı kapsamlı değer")
        self.assertIn("değerlendirilmesine ilişkin", normalize_extracted_text("de ğerlendirilmesine ili şkin; ilişkin, ilişkin"))
        self.assertIn("değerlendirilmesine", normalize_legal_whitespace("MADDE 1 - de ğerlendirilmesine"))


if __name__ == "__main__":
    unittest.main()
