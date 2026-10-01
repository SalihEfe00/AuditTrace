# -*- coding: utf-8 -*-
"""Mülga kanunlar önceki metin olarak (M7.3c): aralık son değişiklikten kalkışa, kimlik güncel kanundan ayrı."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mufettis.importers.historical_laws import HISTORICAL_STATUS
from mufettis.importers.repealed_laws import REPEALED_LAWS, RepealedLaw, import_repealed, repeal_window, repealed_meta

LAW = RepealedLaw("6762", "TÜRK TİCARET KANUNU", "https://www.mevzuat.gov.tr/MevzuatMetin/5.3.6762.pdf",
                  "1956-07-09", "2012-07-01", "6102 sayılı Kanun md. 1534", "cek_ticaret_icra")
TEXT = ("TÜRK TİCARET KANUNU\nKanun Numarası : 6762\nMadde 1 – ...\n"
        "Madde 692 – (Değişik: 29/3/2011-6217/9 md.) Çek ...\n"
        "Madde 700 – (Değişik: 14/1/2004-5030/1 md.) ...\n"
        "Madde 900 – (Değişik: 13/7/2013-9999/1 md.) kalkıştan sonraki not sayılmaz\n")


class TestRepealedLaws(unittest.TestCase):
    def test_window_from_last_amendment_before_repeal(self):
        self.assertEqual(repeal_window(LAW, TEXT), {"valid_from": "2011-03-29", "valid_to": "2012-07-01"})
        self.assertEqual(repeal_window(LAW, "notsuz metin"), {"valid_from": "1956-07-09", "valid_to": "2012-07-01"})

    def test_meta_is_historical_with_its_own_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.pdf"
            meta = repealed_meta(LAW, path, b"%PDF", 3, False, TEXT)
        self.assertEqual(meta["status"], HISTORICAL_STATUS)
        self.assertEqual((meta["valid_from"], meta["valid_to"]), ("2011-03-29", "2012-07-01"))
        self.assertEqual(meta["listed_law_number"], "6762")  # olay tarihli seçim kanun numarasına bakar
        self.assertEqual(meta["declared_work_id"], "mevzuat:mulga:6762")
        self.assertIn("mülga", meta["title"])

    def test_import_ingests_without_version_link(self):
        calls = []

        def ingest(rag, index, text, meta, **kw):
            calls.append((meta, kw))
            return {"status": "indexed", "count": 7}

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.pdf").write_bytes(b"%PDF-1.7")
            from mufettis.common.utils import sha256_bytes

            fetcher = SimpleNamespace(out_dir=tmp, fetch=lambda s: SimpleNamespace(
                status="downloaded", path="a.pdf", sha256=sha256_bytes(b"%PDF-1.7"), reason=""))
            import mufettis.importers.repealed_laws as mod
            original = mod.document_text
            mod.document_text = lambda path, raw, main_content=False: (TEXT, 3, False)
            try:
                rows = import_repealed(None, None, fetcher, Path(tmp), laws=[LAW], ingest=ingest)
            finally:
                mod.document_text = original
        self.assertEqual(rows[0]["status"], "indexed")
        self.assertFalse(calls[0][1]["link_versions"])

    def test_table_entries_are_official(self):
        from urllib.parse import urlparse

        from mufettis.corpus.legislation_sources import OFFICIAL_HOSTS

        for law in REPEALED_LAWS:
            self.assertIn(urlparse(law.url).hostname, OFFICIAL_HOSTS)
            self.assertLess(law.published_on, law.repealed_on)
            self.assertEqual(law.source().problems(), [])


if __name__ == "__main__":
    unittest.main()
