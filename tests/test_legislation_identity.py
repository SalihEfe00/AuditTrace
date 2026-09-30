# -*- coding: utf-8 -*-
"""Tam mevzuat setinde çıkan kimlik kusurları (M6.7 / M6.13): "Tebligat" tebliğ değildir, başlığa bitişik
dipnot işareti türü ve adı bozmaz, kanun adıyla sorgu kanun numarasına çözülür."""
from __future__ import annotations

import unittest

from mufettis.analysis.cited_references import CatalogEntry, extract_citations, resolve
from mufettis.corpus.identity import instrument_from_title
from mufettis.corpus.model import InstrumentType
from mufettis.legal.parser import extract_declared_title

CATALOG = [CatalogEntry(title="İCRA VE İFLAS KANUNU", law_number="2004", is_law=True),
           CatalogEntry(title="TEBLİGAT KANUNU", law_number="7201", is_law=True),
           CatalogEntry(title="TÜRK TİCARET KANUNU", law_number="6102", is_law=True)]


class TestInstrumentFromTitle(unittest.TestCase):
    def test_tebligat_is_a_law_not_a_communique(self):
        self.assertEqual(instrument_from_title("TEBLİGAT KANUNU"), InstrumentType.LAW)
        self.assertEqual(instrument_from_title("VERGİ USUL KANUNU GENEL TEBLİĞİ"), InstrumentType.COMMUNIQUE)
        self.assertEqual(instrument_from_title("Genel Tebligi (Sira No: 5)"), InstrumentType.COMMUNIQUE)

    def test_footnote_mark_glued_to_title(self):
        self.assertEqual(instrument_from_title("TEBLİGAT KANUNU1"), InstrumentType.LAW)
        text = "İCRA VE İFLAS KANUNU12\nKanun Numarası : 2004\nKabul Tarihi : 9/6/1932\n"
        self.assertEqual(extract_declared_title(text), "İCRA VE İFLAS KANUNU")


class TestNamedLawResolution(unittest.TestCase):
    def test_law_named_in_query_resolves_to_its_number(self):
        for query, number in (("Tebligat Kanunu", "7201"),
                              ("İcra ve İflas Kanunu 89 uncu madde haciz ihbarnamesi", "2004"),
                              ("Türk Ticaret Kanununa göre ticari defterler", "6102")):
            citations = extract_citations(query)
            self.assertTrue(citations, query)
            self.assertEqual(resolve(citations[0], CATALOG).law_number, number, query)

    def test_unknown_law_is_not_resolved(self):
        ref = resolve(extract_citations("Maden Kanunu")[0], CATALOG)
        self.assertEqual((ref.status, ref.law_number), ("not_in_corpus", ""))


if __name__ == "__main__":
    unittest.main()
