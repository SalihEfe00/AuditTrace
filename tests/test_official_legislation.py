# -*- coding: utf-8 -*-
"""Resmî kaynak indiricisi (M6.3): ağsız; sahte oturumla doğrulama, robots, bekleme, önbellek."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mufettis.corpus.legislation_sources import LegislationSource
from mufettis.harvesting.official_legislation import (
    USER_AGENT,
    OfficialLegislationFetcher,
    title_coverage,
    verify_document,
)

KANUN = LegislationSource(code="1.5.5549", title="SUÇ GELİRLERİNİN AKLANMASININ ÖNLENMESİ HAKKINDA KANUN",
                          instrument="kanun", number="5549", issuer="TBMM", priority=1, group="masak", reason="test")
TEBLIG = LegislationSource(code="9.5.34343", title="FİNANSAL TÜKETİCİLERDEN ALINACAK ÜCRETLERE İLİŞKİN USÛL VE "
                           "ESASLAR HAKKINDA TEBLİĞ", instrument="tebliğ", number="2020/7", issuer="TCMB",
                           priority=1, group="tuketici", reason="test")
KANUN_PAGE = ("SUÇ GELİRLERİNİN AKLANMASININ ÖNLENMESİ HAKKINDA KANUN\nKanun Numarası : 5549\n"
              "Kabul Tarihi : 11/10/2006\nAmaç\nMADDE 1 –")


class _Response:
    def __init__(self, status=200, content=b"", text=""):
        self.status_code, self.content, self.text = status, content, text


class _Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        route = self.routes.get(url)
        if isinstance(route, Exception):
            raise route
        return route or _Response(404)


def _fetcher(tmp, routes, pages):
    sleeps = []
    session = _Session(routes)
    fetcher = OfficialLegislationFetcher(Path(tmp), session=session, delay=3.0,
                                         page_extractor=lambda data: pages, sleep=sleeps.append)
    return fetcher, session, sleeps


PDF_URL = "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.5549.pdf"
ROBOTS_URL = "https://www.mevzuat.gov.tr/robots.txt"


class TestVerification(unittest.TestCase):
    def test_title_coverage_folds_case_and_circumflex(self):
        self.assertEqual(title_coverage(TEBLIG.title, "finansal tüketicilerden alınacak ücretlere ilişkin usul ve esaslar "
                                                       "hakkında tebliğ"), 1.0)
        self.assertLess(title_coverage(KANUN.title, "PATATES SİĞİL HASTALIĞI HAKKINDA YÖNETMELİK"), 0.5)

    def test_wrong_document_or_number_is_rejected(self):
        self.assertIsNone(verify_document(KANUN, [KANUN_PAGE]))
        self.assertIn("resmî ad", verify_document(KANUN, ["TÜRK BORÇLAR KANUNU\nKanun Numarası : 6098"]))
        self.assertIn("5549", verify_document(KANUN, [KANUN_PAGE.replace("5549", "5550")]))
        self.assertEqual(verify_document(KANUN, ["", ""]), "metin çıkarılamadı")


class TestFetcher(unittest.TestCase):
    def test_download_verify_record_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            routes = {ROBOTS_URL: _Response(text="User-agent: Googlebot\nDisallow: /x\n"),
                      PDF_URL: _Response(content=b"%PDF-1.7 ...")}
            fetcher, session, sleeps = _fetcher(tmp, routes, [KANUN_PAGE])
            result = fetcher.fetch(KANUN)
            self.assertEqual(result.status, "downloaded", result.reason)
            self.assertEqual(result.path, "1.5.5549.pdf")  # klasöre göreli
            self.assertTrue((Path(tmp) / result.path).exists())
            self.assertEqual(sleeps and sleeps[0] > 0, True)  # robots ile PDF arasında bekleme
            self.assertTrue(all(h["User-Agent"] == USER_AGENT for _, h in session.calls))
            record = json.loads((Path(tmp) / "downloads.json").read_text(encoding="utf-8"))
            self.assertEqual(record["1.5.5549"]["status"], "downloaded")
            # Yeniden çalıştırma: ağa gitmeden önbellekten.
            again, session2, _ = _fetcher(tmp, {}, [KANUN_PAGE])
            self.assertEqual(again.fetch(KANUN).status, "cached")
            self.assertEqual(session2.calls, [])

    def test_rejections_do_not_write_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            routes = {ROBOTS_URL: _Response(404), PDF_URL: _Response(content=b"<html>hata</html>")}
            fetcher, _, _ = _fetcher(tmp, routes, [KANUN_PAGE])
            # PDF değil ve metin sayfası da yok (404).
            self.assertEqual(fetcher.fetch(KANUN).reason, "PDF: PDF değil; metin sayfası: HTTP 404")
            # Yanlış belge: PDF reddedilir, metin sayfası da yoksa iki sebep birlikte raporlanır.
            routes[PDF_URL] = _Response(content=b"%PDF-1.7")
            fetcher, _, _ = _fetcher(tmp, routes, ["TÜRK BORÇLAR KANUNU 6098"])
            result = fetcher.fetch(KANUN)
            self.assertEqual(result.status, "failed")
            self.assertIn("resmî ad", result.reason)
            routes[PDF_URL] = _Response(status=503)
            fetcher, _, _ = _fetcher(tmp, routes, [KANUN_PAGE])
            self.assertEqual(fetcher.fetch(KANUN).reason, "HTTP 503")
            self.assertEqual(list(Path(tmp).glob("*.pdf")), [])

    def test_html_text_page_when_there_is_no_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_url = TEBLIG.html_url
            self.assertIn("MevzuatTur=9&MevzuatNo=34343&MevzuatTertip=5", html_url)
            page = ("<html><body><p>FİNANSAL TÜKETİCİLERDEN ALINACAK ÜCRETLERE İLİŞKİN</p><p>USÛL VE ESASLAR HAKKINDA "
                    "TEBLİĞ</p><p>MADDE 1 –</p><script>x()</script></body></html>").encode("utf-8")
            routes = {ROBOTS_URL: _Response(404),
                      TEBLIG.download_url: _Response(content=b"<html>ErrorPage</html>"),
                      html_url: _Response(content=page)}
            fetcher, _, _ = _fetcher(tmp, routes, [])
            result = fetcher.fetch(TEBLIG)
            self.assertEqual(result.status, "downloaded", result.reason)
            self.assertTrue(result.path.endswith("9.5.34343.html"))
            again, session, _ = _fetcher(tmp, {}, [])
            self.assertEqual(again.fetch(TEBLIG).status, "cached")
            self.assertEqual(session.calls, [])
            routes[html_url] = _Response(content=b"<html><p>PATATES YONETMELIGI</p></html>")
            fetcher, _, _ = _fetcher(tmp, routes, [])
            self.assertEqual(fetcher.fetch(TEBLIG, force=True).status, "rejected")

    def test_footnote_mark_glued_to_title_still_verifies(self):
        self.assertIsNone(verify_document(KANUN, [KANUN_PAGE.replace("HAKKINDA KANUN\n", "HAKKINDA KANUN1\n")]))

    def test_unverifiable_pdf_falls_back_to_text_page(self):
        # Kararın PDF'inde yalnızca imza sayfası metin; asıl metin resmî metin sayfasında.
        with tempfile.TemporaryDirectory() as tmp:
            page = f"<html><p>{KANUN_PAGE}</p></html>".encode("utf-8")
            routes = {ROBOTS_URL: _Response(404), PDF_URL: _Response(content=b"%PDF-1.7"),
                      KANUN.html_url: _Response(content=page)}
            fetcher, _, _ = _fetcher(tmp, routes, ["CUMHURBAŞKANI KARARI\nKarar Sayısı: 11205"])
            result = fetcher.fetch(KANUN)
            self.assertEqual((result.status, result.path), ("downloaded", "1.5.5549.html"), result.reason)
            routes[KANUN.html_url] = _Response(content=b"<html>bos</html>")
            fetcher, _, _ = _fetcher(tmp, routes, ["CUMHURBAŞKANI KARARI"])
            result = fetcher.fetch(KANUN, force=True)
            self.assertEqual(result.status, "rejected")
            self.assertIn("metin sayfası", result.reason)

    def test_robots_disallow_and_unofficial_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            routes = {ROBOTS_URL: _Response(text="User-agent: *\nDisallow: /MevzuatMetin/\n"),
                      PDF_URL: _Response(content=b"%PDF-1.7")}
            fetcher, session, _ = _fetcher(tmp, routes, [KANUN_PAGE])
            self.assertEqual(fetcher.fetch(KANUN).status, "robots_blocked")
            self.assertNotIn(PDF_URL, [u for u, _ in session.calls])
            other = LegislationSource(code="", url="https://www.mevzuat.org/a.pdf", title="X", instrument="kanun",
                                      number="1", issuer="-", priority=2, group="vergi", reason="-")
            self.assertEqual(fetcher.fetch(other).status, "rejected")

    def test_network_error_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetcher, _, _ = _fetcher(tmp, {ROBOTS_URL: _Response(404), PDF_URL: OSError("zaman aşımı")}, [])
            result = fetcher.fetch(KANUN)
            self.assertEqual(result.status, "failed")
            self.assertIn("zaman aşımı", result.reason)


if __name__ == "__main__":
    unittest.main()
