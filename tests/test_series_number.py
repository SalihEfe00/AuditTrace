# -*- coding: utf-8 -*-
"""Sıra numaralı seri belgeler (M6.11): sorgudaki "sıra no" kimliktir, aynı adlı seride doğru belge öne geçer."""
from __future__ import annotations

import unittest

from mufettis.extraction.text_repair import extract_series_number
from mufettis.legal.parser import prefer_official_matches

TITLE = "MALİ SUÇLARI ARAŞTIRMA KURULU GENEL TEBLİĞİ (SIRA NO: {n})"


def _chunk(n: str, distance: float) -> dict:
    return {"document": "Bu Tebliğin amacı, yükümlülerin uyacağı usul ve esasları belirlemektir.",
            "distance": distance,
            "metadata": {"title": TITLE.format(n=n), "document_number": n, "document_type": "Tebliğ",
                         "status": "active", "legislation_code": f"9.5.{n}"}}


class TestSeriesNumber(unittest.TestCase):
    def test_extraction(self):
        self.assertEqual(extract_series_number("mali suçları araştırma kurulu genel tebliği (sıra no: 5)"), "5")
        self.assertEqual(extract_series_number("MASAK Genel Tebliği Sıra No 12 kimlik tespiti"), "12")
        self.assertEqual(extract_series_number("594 SIRA NOLU VUK GENEL TEBLİĞİ (SIRA NO_ 531)"), "531")
        self.assertIsNone(extract_series_number("5549 sayılı Kanun madde 5"))
        self.assertEqual(extract_series_number("32 sayılı karara ilişkin tebliğ (tebliğ no: 2008-32/34)"), "2008-32/34")

    def test_series_number_digits_are_not_account_codes(self):
        from mufettis.legal.parser import extract_query_account_code

        self.assertIsNone(extract_query_account_code("32 Sayılı Karara ilişkin Tebliğ (Tebliğ No: 2008-32/34)"))
        self.assertEqual(extract_query_account_code("100 Kasa hesabı"), "100")

    def test_matching_series_member_wins_over_closer_siblings(self):
        # Kardeşler anlamsal olarak daha yakın; sıra numarası tutan belge yine de ilk sırada.
        results = [_chunk("34", 0.20), _chunk("29", 0.22), _chunk("12", 0.25), _chunk("5", 0.40)]
        ranked = prefer_official_matches(results, limit=4, query="MASAK genel tebliği sıra no: 5")
        self.assertEqual(ranked[0]["metadata"]["document_number"], "5")
        self.assertTrue(ranked[0]["_series_match"])
        # Sıra numarası verilmeyen sorguda sıralama değişmez (anlamsal yakınlık).
        plain = prefer_official_matches(results, limit=4, query="MASAK genel tebliği")
        self.assertEqual(plain[0]["metadata"]["document_number"], "34")


if __name__ == "__main__":
    unittest.main()
