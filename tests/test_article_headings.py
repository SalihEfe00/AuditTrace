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

    def test_structure_detection_ignores_references(self):
        info = classify_legal_structure("Likidite Rehberi", GUIDANCE, "Rehber")
        self.assertNotEqual(info["structure_type"], "article")


if __name__ == "__main__":
    unittest.main()
