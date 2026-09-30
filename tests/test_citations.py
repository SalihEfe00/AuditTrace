# -*- coding: utf-8 -*-
"""Mevzuat metnindeki atıfların çıkarımı (M6.15.1, atıf kapanışı)."""
from __future__ import annotations

import unittest

from mufettis.corpus.citations import citation_key, extract_citations, tally


def _got(text):
    return [(c.kind, c.number, c.name, c.date) for c in extract_citations(text)]


class TestNumbered(unittest.TestCase):
    def test_law_with_date_and_name(self):
        self.assertEqual(_got("4/12/2003 tarihli ve 5018 sayılı Kamu Mali Yönetimi ve Kontrol Kanunu uyarınca"),
                         [("kanun", "5018", "Kamu Mali Yönetimi ve Kontrol", "4/12/2003")])
        self.assertEqual(_got("5411 sayılı Kanunun 73 üncü maddesi"), [("kanun", "5411", "", "")])

    def test_decrees_and_decisions(self):
        got = _got("32 sayılı Karar ile 2009/15481 sayılı Bakanlar Kurulu Kararı; 1 sayılı Cumhurbaşkanlığı Teşkilatı "
                   "Hakkında Cumhurbaşkanlığı Kararnamesi ve 660 sayılı Kanun Hükmünde Kararname; Kurulun 12/3/2020 "
                   "tarihli ve 8948 sayılı Kurul Kararı")
        self.assertEqual([(k, n) for k, n, _, _ in got],
                         [("karar", "32"), ("bkk", "2009/15481"), ("cbk", "1"), ("khk", "660"),
                          ("kurul_karari", "8948")])

    def test_gazette_number_is_not_a_citation(self):
        got = _got("1/11/2006 tarihli ve 26333 sayılı Resmî Gazete'de yayımlanan Bankaların Kredi İşlemlerine İlişkin "
                   "Yönetmelik hükümleri")
        self.assertEqual(got, [("yonetmelik", "", "Bankaların Kredi İşlemlerine İlişkin Yönetmelik", "")])


class TestNamed(unittest.TestCase):
    def test_series_number_is_kept(self):
        got = _got("Mali Suçları Araştırma Kurulu Genel Tebliği (Sıra No: 5) ile Kredi Riski Azaltım Tekniklerine "
                   "İlişkin Tebliğ")
        self.assertEqual(got, [("teblig", "5", "Mali Suçları Araştırma Kurulu Genel Tebliği", ""),
                               ("teblig", "", "Kredi Riski Azaltım Tekniklerine İlişkin Tebliğ", "")])

    def test_tally_merges_by_key_across_documents(self):
        rows = tally([("A", "5411 sayılı Bankacılık Kanunu"), ("B", "5411 sayılı Kanun ve 6102 sayılı Kanun")])
        self.assertEqual(rows[("kanun", "5411")]["count"], 2)
        self.assertEqual(rows[("kanun", "5411")]["documents"], {"A", "B"})
        self.assertEqual(citation_key(extract_citations("Kredi Riski Azaltım Tekniklerine İlişkin Tebliğ")[0]),
                         ("teblig", "kredi riski azaltım tekniklerine ilişkin tebliğ"))


if __name__ == "__main__":
    unittest.main()
