# -*- coding: utf-8 -*-
"""Mevzuat listesinin yüklenmesi (M6.4): liste kimliği başlık sezgisinin önünde; belge belge, hatada devam."""
from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from unittest import mock
from pathlib import Path

from mufettis.common.utils import sha256_bytes
from mufettis.corpus.legislation_sources import LegislationSource
from mufettis.importers.legislation_importer import (
    document_type_for,
    import_downloads,
    listed_meta,
    process_memory_mb,
)
from mufettis.legal.parser import extract_legal_metadata

YONETMELIK = LegislationSource(code="7.5.19864", title="BANKALARIN İÇ SİSTEMLERİ VE İÇSEL SERMAYE YETERLİLİĞİ "
                               "DEĞERLENDİRME SÜRECİ HAKKINDA YÖNETMELİK", instrument="yönetmelik", number="",
                               issuer="BDDK", priority=1, group="bddk", reason="test")
KARAR = LegislationSource(code="20.5.11205", title="KAYNAK KULLANIMINI DESTEKLEME FONU KESİNTİSİ HAKKINDA KARAR",
                          instrument="karar", number="11205", issuer="Cumhurbaşkanı", priority=2, group="vergi",
                          reason="test")
YONETMELIK_TEXT = ("BANKALARIN İÇ SİSTEMLERİ VE İÇSEL SERMAYE YETERLİLİĞİ DEĞERLENDİRME SÜRECİ HAKKINDA YÖNETMELİK\n"
                   "Dayanak\nMADDE 3 – (1) Bu Yönetmelik, 19/10/2005 tarihli ve 5411 sayılı Bankacılık Kanununun "
                   "29 uncu maddesine dayanılarak hazırlanmıştır.\n")


class TestListedIdentity(unittest.TestCase):
    def test_listed_identity_overrides_title_guess(self):
        path = Path("7.5.19864.html")
        raw = b"<html>...</html>"
        meta = extract_legal_metadata(YONETMELIK.title, YONETMELIK_TEXT, listed_meta(YONETMELIK, path, raw, 0, False))
        self.assertEqual(meta["document_type"], "Yönetmelik")
        self.assertEqual(meta["law_number"], "")          # gövdedeki 5411 atfı kimlik değil
        self.assertEqual(meta["document_number"], "")     # "sayılı" → "lı" oluşmuyor
        self.assertEqual(meta["document_key"], "mevzuat:7.5.19864")
        self.assertEqual(meta["source_id"], "mevzuat_gov")
        self.assertIn("MevzuatFihristDetayIframe", meta["url"])
        # Yeniden çıkarma (yeniden parçalama) aynı kimliği verir.
        again = extract_legal_metadata(meta["title"], YONETMELIK_TEXT, dict(meta))
        self.assertEqual((again["document_key"], again["document_type"]), ("mevzuat:7.5.19864", "Yönetmelik"))

    def test_long_titles_differing_only_at_the_end_keep_separate_identities(self):
        from mufettis.corpus import canonical_view

        title = "TÜRK PARASI KIYMETİNİ KORUMA HAKKINDA 32 SAYILI KARARA İLİŞKİN TEBLİĞ (TEBLİĞ NO: {})"
        works = set()
        for code, no in (("9.5.11990", "2008-32/34"), ("9.5.24798", "2018-32/48")):
            src = LegislationSource(code=code, title=title.format(no), instrument="tebliğ", number=no,
                                    issuer="Hazine", priority=2, group="kambiyo", reason="test")
            meta = extract_legal_metadata(src.title, "MADDE 1 – metin", listed_meta(src, Path("x.html"), b"x", 0, False))
            works.add(canonical_view(meta).work_id)
        self.assertEqual(len(works), 2)
        law = extract_legal_metadata("SUÇ GELİRLERİNİN AKLANMASININ ÖNLENMESİ HAKKINDA KANUN", "Kanun Numarası : 5549",
                                     listed_meta(dataclasses.replace(YONETMELIK, code="1.5.5549", instrument="kanun", number="5549",
                                                                     title="SUÇ GELİRLERİNİN AKLANMASININ ÖNLENMESİ HAKKINDA KANUN"),
                                                 Path("x.pdf"), b"x", 0, False))
        self.assertEqual(canonical_view(law).work_id, "tr/kanun/5549")

    def test_decision_types(self):
        self.assertEqual(document_type_for(KARAR), "Cumhurbaşkanı Kararı")

    def test_court_decision_is_guidance(self):
        # K2b: RG'deki Yargıtay kararı listede geçerli; yorum gösterir, bulgunun dayanağı kanundur (yol gösterici)
        from mufettis.corpus import canonical_view

        src = LegislationSource(code="", title="Yargıtay Hukuk Genel Kurulu İçtihadı Birleştirme Kararı (E: 2017/4, K: 2018/5)",
                                instrument="yargı kararı", number="E: 2017/4, K: 2018/5", issuer="Yargıtay",
                                priority=2, group="borclar_medeni", reason="test", rg_date="16.10.2018",
                                url="https://www.resmigazete.gov.tr/eskiler/2018/10/20181016-20.pdf")
        self.assertEqual(src.problems(), [])
        self.assertEqual(document_type_for(src), "Yargı Kararı")
        meta = extract_legal_metadata(src.title, "Kefalette eşin rızası ...", listed_meta(src, Path("x.pdf"), b"x", 0, False))
        self.assertEqual(canonical_view(meta).instrument_type, "rehber")

    def test_number_pattern_does_not_read_sayili(self):
        meta = extract_legal_metadata("Bir Genelge", "5411 sayılı Kanun uyarınca ...\nSayı: E-12345\n",
                                      {"source_id": "local_file"})
        self.assertEqual(meta["document_number"], "E-12345")
        meta = extract_legal_metadata("Bir Genelge", "5411 sayılı Kanun uyarınca bankalar ...",
                                      {"source_id": "local_file"})
        self.assertNotEqual(meta["document_number"], "lı")


class TestImportDownloads(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows bellek sayaçları")
    def test_process_memory_is_measured(self):
        memory = process_memory_mb()
        self.assertGreater(memory.get("working_set_mb", 0), 0)
        self.assertGreaterEqual(memory["peak_working_set_mb"], memory["working_set_mb"])

    def test_imports_each_document_and_continues_after_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "7.5.19864.html"
            good.write_bytes(f"<html><p>{YONETMELIK_TEXT}</p></html>".encode("utf-8"))
            bad = Path(tmp) / "20.5.11205.html"
            bad.write_bytes(b"<html>degisti</html>")
            records = {YONETMELIK.key: {"status": "downloaded", "path": str(good),
                                        "sha256": sha256_bytes(good.read_bytes())},
                       KARAR.key: {"status": "cached", "path": str(bad), "sha256": "baska"}}
            calls = []

            def ingest(rag, index, text, meta, raw_bytes=None, source_path=None):
                calls.append((text, meta))
                return {"status": "indexed", "count": 3}

            missing = LegislationSource(code="1.5.1", title="X KANUNU", instrument="kanun", number="1",
                                        issuer="TBMM", priority=2, group="vergi", reason="test")
            rows = import_downloads(None, None, [YONETMELIK, KARAR, missing], records, Path(tmp), ingest=ingest)
            self.assertEqual([r["status"] for r in rows], ["indexed", "error", "not_downloaded"])
            self.assertIn("uyuşmuyor", rows[1]["reason"])
            text, meta = calls[0]
            self.assertEqual(meta["listed_document_key"], "mevzuat:7.5.19864")
            self.assertIn("MADDE 3", text)
            self.assertNotIn("<p>", text)
            log = [json.loads(line) for line in (Path(tmp) / "import_runs.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(log), 3)
            self.assertEqual(log[0]["chunks"], 3)

    def test_loaded_document_is_reextracted_only_when_text_grew(self):
        # K5.1: yüklenmiş belge metni çıkarılmadan atlanır; --reextract'te uzayan metin eski parçaların yerine geçer
        from mufettis.importers import legislation_importer

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "7.5.19864.html"
            path.write_bytes(f"<html><p>{YONETMELIK_TEXT}</p></html>".encode("utf-8"))
            digest = sha256_bytes(path.read_bytes())
            records = {YONETMELIK.key: {"status": "downloaded", "path": str(path), "sha256": digest}}
            index = {digest: {"chunk_count": 4}}
            calls = []

            def ingest(rag, index, text, meta, raw_bytes=None, source_path=None, force=False):
                calls.append(force)
                return {"status": "indexed", "count": 9}

            def run(reextract, old_text):
                with mock.patch.object(legislation_importer, "document_text",
                                       side_effect=AssertionError("çıkarılmamalı")) if not reextract else nullcontext():
                    return import_downloads(None, index, [YONETMELIK], records, Path(tmp), ingest=ingest,
                                            reextract=reextract, cached_text=lambda h: old_text)[0]

            row = run(False, "")
            self.assertEqual((row["status"], row["chunks"], calls), ("skipped", 4, []))
            row = run(True, YONETMELIK_TEXT)                     # aynı metin: dokunulmaz
            self.assertEqual((row["status"], calls), ("skipped", []))
            row = run(True, "imza sayfası")                      # önbellekte yalnızca kısa metin vardı
            self.assertEqual((row["status"], row["chunks"], calls), ("reextracted", 9, [True]))


if __name__ == "__main__":
    unittest.main()
