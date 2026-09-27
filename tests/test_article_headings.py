# -*- coding: utf-8 -*-
"""Madde başlığı ile metin içi madde atfının ayrımı (mufettis.legal.parser.ARTICLE_NUMBER).

Kesme işaretiyle ek alan numara ("Geçici Madde 2'ye", "Madde 5'te") atıftır: yeni madde başlatmamalı
ve rehberin numaralı bölüm yapısını kapatmamalıdır.
"""
from __future__ import annotations

import unittest

from mufettis.legal.parser import classify_legal_structure, split_legal_chunks

GUIDANCE = (
    "LİKİDİTE RİSKİNİN YÖNETİMİNE İLİŞKİN REHBER\n"
    "1. Giriş\nBankalar likidite riskini yönetir.\n"
    "Geçici Madde 2’ye paralel olarak, sistemlerini 1.1.2017’ye kadar tamamlamaları gerekmektedir.\n"
    "2. Kapsam\nBu rehber tüm bankaları kapsar."
)


class TestArticleReference(unittest.TestCase):
    def test_reference_in_guidance_keeps_section_structure(self):
        blocks = split_legal_chunks(GUIDANCE)
        self.assertFalse(any(b["article_no"] for b in blocks))
        self.assertEqual([b["section_no"] for b in blocks if b["section_no"]], ["1", "2"])
        self.assertIn("Geçici Madde 2’ye paralel", next(b["text"] for b in blocks if b["section_no"] == "1"))

    def test_reference_inside_law_does_not_start_article(self):
        blocks = split_legal_chunks("MADDE 5 - Amaç\n(1) Metin.\nMADDE 6 – Madde 5'te belirtilen usul uygulanır.")
        self.assertEqual([b["article_no"] for b in blocks if "Madde 5'te" in b["text"]], ["6"])

    def test_real_headings_still_recognised(self):
        blocks = split_legal_chunks("GEÇİCİ MADDE 2 – Bu Kanun yürürlüğe girer.\nEK MADDE 3- Ek hüküm.\nMADDE 5 - Amaç")
        self.assertEqual([(b["article_prefix"], b["article_no"]) for b in blocks],
                         [("GEÇİCİ", "2"), ("EK", "3"), ("", "5")])

    LAW = ("Örnek bağış sınırı\nMadde 59 — Bankalar bağış miktarını aşamaz.\nBu hükmün usulü Kurulca belirlenir.\n"
           "BEŞİNCİ KISIM\nÖrnek Toplamaya İlişkin Hükümler\nÖrnek fon kabulü\n"
           "Madde 60 — Yetkililer dışında kimse fon kabul edemez. Evrak düzenlenemez ve\nverilemez.\n"
           "Zamanaşımı\nMadde 61 — Fon on yıl aranmazsa zamanaşımına uğrar.")

    def test_heading_above_article_belongs_to_that_article(self):
        blocks = split_legal_chunks(self.LAW)
        by_article = {b["article_no"]: b for b in blocks}
        self.assertEqual(by_article["60"]["article_heading"],
                         "BEŞİNCİ KISIM — Örnek Toplamaya İlişkin Hükümler — Örnek fon kabulü")
        self.assertEqual(by_article["61"]["article_heading"], "Zamanaşımı")
        self.assertEqual(by_article["59"]["article_heading"], "Örnek bağış sınırı")
        # Başlık önceki maddenin metninde kalmaz ve kendi maddesinin (gömülen) metnine de eklenmez.
        self.assertNotIn("Zamanaşımı", by_article["60"]["text"])
        self.assertTrue(by_article["61"]["text"].startswith("Madde 61"))

    def test_wrapped_sentence_line_is_not_a_heading(self):
        blocks = split_legal_chunks("Madde 1 — Bankalar bu işlemleri yapar ve\nKurul Kararı Uyarınca\nMadde 2 — Metin.")
        self.assertIn("Kurul Kararı Uyarınca", next(b["text"] for b in blocks if b["article_no"] == "1"))
        self.assertEqual(next(b["article_heading"] for b in blocks if b["article_no"] == "2"), "")

    def test_heading_is_searchable_but_not_embedded(self):
        from mufettis.rag.lexical import LexicalIndex, query_terms

        index = LexicalIndex(["a", "b"], ["Fon on yıl aranmazsa devredilir.", "Fon on yıl aranmazsa devredilir."],
                             [{"article_heading": "Zamanaşımı"}, {}])
        self.assertEqual(index.search(query_terms("zamanaşımı"), 2)[0][0], "a")

    def test_structure_detection_ignores_references(self):
        info = classify_legal_structure("Likidite Rehberi", GUIDANCE, "Rehber")
        self.assertNotEqual(info["structure_type"], "article")


if __name__ == "__main__":
    unittest.main()
