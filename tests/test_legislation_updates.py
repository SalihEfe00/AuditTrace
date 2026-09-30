# -*- coding: utf-8 -*-
"""Mevzuat listesinin güncel tutulması (M6.8): Resmî Gazete başlığı eşleşmesi, metin özetiyle değişiklik tespiti."""
from __future__ import annotations

import unittest

from mufettis.corpus.legislation_sources import LegislationSource
from mufettis.corpus.legislation_updates import amended_sources, check_sources, mentions
from mufettis.harvesting.official_legislation import FetchResult


def _source(code, title, instrument="yönetmelik", number=""):
    return LegislationSource(code=code, title=title, instrument=instrument, number=number, issuer="-", priority=1,
                             group="bddk", reason="test")


KART = _source("7.5.11180", "BANKA KARTLARI VE KREDİ KARTLARI HAKKINDA YÖNETMELİK")
KREDI = _source("7.5.40520", "BANKALARIN KREDİ İŞLEMLERİNE İLİŞKİN YÖNETMELİK")
TUKETICI = _source("1.5.6502", "TÜKETİCİNİN KORUNMASI HAKKINDA KANUN", "kanun", "6502")


class TestGazetteMatching(unittest.TestCase):
    def test_amendment_titles_name_the_source(self):
        self.assertTrue(mentions(KART, "Banka Kartları ve Kredi Kartları Hakkında Yönetmelikte Değişiklik "
                                       "Yapılmasına Dair Yönetmelik"))
        self.assertTrue(mentions(KREDI, "Bankaların Kredi İşlemlerine İlişkin Yönetmelikte Değişiklik Yapılmasına "
                                        "Dair Yönetmelik"))
        self.assertTrue(mentions(TUKETICI, "Tüketicinin Korunması Hakkında Kanunda Değişiklik Yapılmasına Dair Kanun"))

    def test_unrelated_titles_do_not_match(self):
        self.assertFalse(mentions(KART, "Ödeme Hizmetleri ve Elektronik Para İhracı Hakkında Yönetmelik"))
        self.assertFalse(mentions(KREDI, "Kredi Garanti Fonu Hakkında Karar"))
        self.assertFalse(mentions(TUKETICI, "Bazı Kanunlarda Değişiklik Yapılmasına Dair Kanun"))

    def test_series_member_is_matched_by_its_number(self):
        masak5 = _source("9.5.12073", "MALİ SUÇLARI ARAŞTIRMA KURULU GENEL TEBLİĞİ (SIRA NO: 5)", "tebliğ", "5")
        masak8 = _source("9.5.13073", "MALİ SUÇLARI ARAŞTIRMA KURULU GENEL TEBLİĞİ (SIRA NO: 8)", "tebliğ", "8")
        title = ("Mali Suçları Araştırma Kurulu Genel Tebliği (Sıra No: 5)'nde Değişiklik Yapılmasına Dair Tebliğ "
                 "(Sıra No: 35)")
        self.assertTrue(mentions(masak5, title))
        self.assertFalse(mentions(masak8, title))

    def test_amended_sources_groups_titles(self):
        items = [{"title": "Banka Kartları ve Kredi Kartları Hakkında Yönetmelikte Değişiklik Yapılmasına Dair Yönetmelik"},
                 {"title": "Atama Kararı"}]
        self.assertEqual(list(amended_sources(items, [KART, KREDI])), [KART.key])


class _Fetcher:
    def __init__(self, records, results):
        self.records, self.results = records, results

    def fetch(self, source, force=False):
        assert force
        return self.results[source.key]


class TestCheckSources(unittest.TestCase):
    def test_change_is_measured_on_text_not_bytes(self):
        records = {KART.key: {"text_sha256": "a"}, KREDI.key: {"text_sha256": "b"}}
        results = {KART.key: FetchResult(KART.key, "downloaded", sha256="yeni-bayt", text_sha256="a"),
                   KREDI.key: FetchResult(KREDI.key, "downloaded", text_sha256="c"),
                   TUKETICI.key: FetchResult(TUKETICI.key, "failed", reason="HTTP 503")}
        checks = check_sources(_Fetcher(records, results), [KART, KREDI, TUKETICI])
        self.assertEqual([c.status for c in checks], ["unchanged", "changed", "error"])
        self.assertIn("HTTP 503", checks[2].reason)


if __name__ == "__main__":
    unittest.main()
