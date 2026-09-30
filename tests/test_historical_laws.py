# -*- coding: utf-8 -*-
"""Kanunların önceki metinleri (M7.3): sürüm listesi, geçerlilik aralığı, metin doğrulaması, kimlik, yükleme."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mufettis.common.utils import sha256_bytes
from mufettis.corpus import canonical_view
from mufettis.corpus.legislation_sources import LegislationSource
from mufettis.corpus.model import Validity
from mufettis.corpus.validity import preserved_status, validity
from mufettis.importers.historical_laws import (
    HistoricalVersion,
    VersionWindow,
    check_version_text,
    historical_meta,
    import_versions,
    parse_versions,
    version_source,
    version_windows,
    versions_url,
)
from mufettis.importers.legislation_importer import listed_meta
from mufettis.legal.parser import _chunk_is_retrievable, extract_legal_metadata

KART = LegislationSource(code="1.5.5464", title="BANKA KARTLARI VE KREDİ KARTLARI KANUNU", instrument="kanun",
                         number="5464", issuer="TBMM", priority=1, group="kart", reason="test")


def _row(no, kabul, rg, pdf="https://www.mevzuat.gov.tr/MevzuatMetin/1.5.5464-{}.pdf"):
    return {"mevzuatNo": no, "kabulTarih": kabul, "resmiGazeteTarihi": rg,
            "pdfUrl": pdf.format("".join(reversed(kabul.split("."))))}


def _version(no, accepted, published):
    return HistoricalVersion("1.5.5464", no, accepted, published, f"https://www.mevzuat.gov.tr/x-{no}.pdf")


class TestVersionList(unittest.TestCase):
    def test_parse_sorts_by_acceptance_and_skips_unofficial_or_undated(self):
        rows = [_row("6327", "13.06.2012", "29.06.2012"), _row("5915", "24.06.2009", "03.07.2009"),
                _row("9999", "01.01.2020", "02.01.2020", pdf="https://example.com/{}.pdf"),
                _row("8888", "", "02.01.2020")]
        versions = parse_versions(KART, rows)
        self.assertEqual([v.amending_law for v in versions], ["5915", "6327"])
        self.assertEqual((versions[1].accepted_on, versions[1].published_on), ("2012-06-13", "2012-06-29"))
        self.assertEqual(versions[1].key, "1.5.5464@2012-06-13")
        self.assertEqual(parse_versions(KART, {"error": "x"}), [])

    def test_only_listed_laws_have_a_version_list(self):
        self.assertIn("kanunSayisi=5464&mevzuatTertip=5", versions_url(KART))
        self.assertEqual(versions_url(LegislationSource(code="7.5.19864", title="Y", instrument="yönetmelik",
                                                        number="", issuer="BDDK", priority=1, group="g",
                                                        reason="r")), "")


class TestWindows(unittest.TestCase):
    def test_window_ends_at_next_version_or_earlier_amendment_in_current_text(self):
        versions = [_version("5915", "2009-06-24", "2009-07-03"), _version("6327", "2012-06-13", "2012-06-29")]
        current = ("MADDE 9 – (Değişik: 24/6/2009-5915/1 md.) … (Ek: 13/6/2012-6327/43 md.) … "
                   "(Değişik: 7/11/2011-6247/3 md.) … (Değişik: 28/3/2013-6440/2 md.)")
        windows = version_windows(versions, current)
        # 5915 hâli: 6327 listede sonraki sürüm, ama 6247 (2011) arada değiştirmiş → aralık orada biter.
        self.assertEqual((windows[0].valid_from, windows[0].valid_to), ("2009-07-03", "2011-11-07"))
        # Son sürüm: güncel metindeki sonraki ilk not (6440).
        self.assertEqual((windows[1].valid_from, windows[1].valid_to), ("2012-06-29", "2013-03-28"))

    def test_last_version_without_later_amendment_has_no_window(self):
        windows = version_windows([_version("6327", "2012-06-13", "2012-06-29")], "(Ek: 13/6/2012-6327/43 md.)")
        self.assertEqual(windows[0].valid_to, "")

    def test_next_change_accepted_before_publication_leaves_no_window(self):
        windows = version_windows([_version("A", "2012-06-13", "2012-06-29")], "(Değişik: 20/6/2012-6330/1 md.)")
        self.assertEqual(windows[0].valid_to, "")


class TestVersionText(unittest.TestCase):
    def test_text_must_be_the_state_after_the_amending_law(self):
        version = _version("6327", "2012-06-13", "2012-06-29")
        self.assertIsNone(check_version_text(version, "6327 sayılı Kanun ile değişik hali (Ek: 13/6/2012-6327/43 md.)"))
        self.assertIn("sonraki değişiklik", check_version_text(version, "6327 (Değişik: 28/3/2013-6440/2 md.)"))
        self.assertIn("6327", check_version_text(version, "(Değişik: 24/6/2009-5915/1 md.)"))

    def test_constitutional_court_decision_and_first_text_are_recognised(self):
        aym = HistoricalVersion("1.5.5549", "2012205", "2013-10-10", "2013-10-10", "u")
        head = ("Anayasa Mahkemesi'nin 27/12/2012 tarihli ve E.: 2011/139, K.: 2012/205 sayılı Kararı ile değişik "
                "hali\nSUÇ GELİRLERİNİN AKLANMASININ ÖNLENMESİ HAKKINDA KANUN\n(Değişik: 11/10/2011-KHK-666/1 md.)")
        self.assertIsNone(check_version_text(aym, head))
        first = HistoricalVersion("1.5.5411", "5411", "2005-10-19", "2005-11-01", "u")
        self.assertIsNone(check_version_text(first, "BANKACILIK KANUNU\nKanun Numarası : 5411\nMADDE 1"))
        # Numara yalnızca başka bir sayının parçası olarak geçiyorsa yetmez.
        self.assertIsNotNone(check_version_text(HistoricalVersion("c", "662", "2011-10-26", "2011-11-02", "u"),
                                                "Sayı : 26623\nMADDE 1"))


class TestOrdering(unittest.TestCase):
    def test_versions_are_ordered_by_publication_and_compared_by_the_earlier_date(self):
        # Kaynakta kabulü yayımdan sonra görünen karar (TMK 2011143): sırası yayımına göre.
        rows = [_row("6462", "25.04.2013", "03.05.2013"), _row("2011143", "07.02.2013", "07.02.2012"),
                _row("6217", "31.03.2011", "14.04.2011")]
        versions = parse_versions(KART, rows)
        self.assertEqual([v.amending_law for v in versions], ["6217", "2011143", "6462"])
        self.assertEqual(versions[1].changed_on, "2012-02-07")
        windows = version_windows(versions, "")
        self.assertEqual(windows[0].valid_to, "2012-02-07")
        self.assertEqual((windows[1].valid_from, windows[1].valid_to), ("2012-02-07", "2013-04-25"))

    def test_same_day_publications_leave_the_earlier_one_without_a_window(self):
        versions = [HistoricalVersion("c", "200996", "2009-06-25", "2009-10-07", "a"),
                    HistoricalVersion("c", "2009105", "2009-07-02", "2009-10-07", "b")]
        self.assertEqual(version_windows(versions, "")[0].valid_to, "")


class TestIdentityAndStatus(unittest.TestCase):
    def setUp(self):
        version = _version("6327", "2012-06-13", "2012-06-29")
        self.window = VersionWindow(version, "2012-06-29", "2013-03-28")
        text = "BANKA KARTLARI VE KREDİ KARTLARI KANUNU\nKanun Numarası : 5464\nMADDE 1 – amaç"
        version_text = "6327 sayılı Kanun ile değişik hali" + chr(10) + text
        self.meta = extract_legal_metadata(KART.title, version_text, historical_meta(
            KART, self.window, Path("h.pdf"), b"%PDF", 3, False, version_text))
        self.current = extract_legal_metadata(KART.title, text, listed_meta(KART, Path("c.pdf"), b"%PDF-c", 3, False))

    def test_historical_text_is_a_separate_work_with_the_same_law_number(self):
        self.assertEqual(self.meta["law_number"], "5464")
        self.assertEqual(self.meta["document_key"], "mevzuat:1.5.5464@2012-06-13")
        self.assertNotEqual(canonical_view(self.meta).work_id, canonical_view(self.current).work_id)
        self.assertEqual(self.meta["historical_of"], "mevzuat:1.5.5464")
        self.assertEqual(self.meta["legislation_code"], "1.5.5464")
        self.assertIn("(6327 sayılı Kanun ile değişik hali)", self.meta["title"])
        self.assertEqual(self.meta["amended_by"], "6327 sayılı Kanun")
        first = historical_meta(KART, self.window, Path("h.pdf"), b"%PDF", 3, False, "BANKA KARTLARI" + chr(10) + "MADDE 1")
        self.assertEqual((first["amended_by"], first["title"].endswith("(ilk metin)")), ("ilk metin", True))

    def test_status_is_historical_and_in_force_only_inside_its_window(self):
        self.assertEqual(self.meta["status"], "historical")
        self.assertEqual(validity(self.meta, dt.date(2012, 12, 1)), Validity.IN_FORCE)
        self.assertEqual(validity(self.meta, dt.date(2014, 1, 1)), Validity.NOT_IN_FORCE)
        self.assertEqual(validity(self.meta, dt.date(2010, 1, 1)), Validity.NOT_YET_IN_FORCE)
        self.assertFalse(_chunk_is_retrievable({**self.meta, "chunk_schema_version": self.meta.get(
            "chunk_schema_version", ""), "document_indexable": "True"}, "metin"))

    def test_repair_keeps_curated_statuses(self):
        self.assertEqual(preserved_status({"status": "historical"}), "historical")
        self.assertEqual(preserved_status({"status": "excluded"}), "excluded")
        self.assertEqual(preserved_status({"status": "superseded"}), "active")
        self.assertEqual(preserved_status({}), "active")


class _Fetcher:
    def __init__(self, out_dir, payloads):
        self.out_dir, self.payloads, self.seen = Path(out_dir), payloads, []

    def fetch(self, source):
        self.seen.append(source)
        data = self.payloads.get(source.url)
        if data is None:
            return SimpleNamespace(status="failed", path="", sha256="", reason="HTTP 404")
        path = self.out_dir / (source.url.rsplit("/", 1)[-1])
        path.write_bytes(data)
        return SimpleNamespace(status="downloaded", path=path.name, sha256=sha256_bytes(data), reason="")


class TestImport(unittest.TestCase):
    def test_versions_are_verified_and_ingested_without_version_linking(self):
        # Sahte dosyalar metin: document_text uzantıya bakar, bu yüzden adresler .html.
        good, bad, missing = (HistoricalVersion("1.5.5464", no, acc, pub, f"https://www.mevzuat.gov.tr/{no}.html")
                              for no, acc, pub in (("6327", "2012-06-13", "2012-06-29"),
                                                   ("6440", "2013-03-28", "2013-04-10"),
                                                   ("7000", "2014-01-01", "2014-01-10")))
        calls = []

        def ingest(rag, index, text, meta, **kw):
            calls.append((meta, kw))
            return {"status": "indexed", "count": 2}

        with tempfile.TemporaryDirectory() as tmp:
            fetcher = _Fetcher(tmp, {good.pdf_url: "6327 hâli (Ek: 13/6/2012-6327/43 md.)".encode("utf-8"),
                                     bad.pdf_url: "(Ek: 13/6/2012-6327/43 md.)".encode("utf-8")})
            rows = import_versions(None, None, [KART], {KART.key: [good, bad, missing]}, fetcher,
                                   lambda s: "(Değişik: 1/5/2015-6637/1 md.)", Path(tmp), ingest=ingest)
        self.assertEqual([r["status"] for r in rows], ["indexed", "rejected", "not_downloaded"])
        self.assertIn("6440", rows[1]["reason"])
        meta, kw = calls[0]
        self.assertFalse(kw["link_versions"])
        self.assertEqual((meta["status"], meta["valid_from"], meta["valid_to"]),
                         ("historical", "2012-06-29", "2013-03-28"))
        # İndirici güncel kanunun başlığıyla doğrular; adres sürümün kendisi.
        self.assertEqual(fetcher.seen[0].title, KART.title)
        self.assertEqual(version_source(KART, good).html_url, "")


if __name__ == "__main__":
    unittest.main()
