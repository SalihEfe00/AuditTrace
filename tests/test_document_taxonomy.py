# -*- coding: utf-8 -*-
"""Belge türü etiket kümesi (M4.1): kodlar tekil, her BRIEF görevi bir koda bağlı, motorun türleri eşleniyor."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from mufettis.analysis import document_taxonomy as tax
from mufettis.analysis.models import DocumentType

BRIEF = Path(__file__).resolve().parent.parent / "benchmarks" / "synthetic" / "BRIEF.md"


class TestDocumentTaxonomy(unittest.TestCase):
    def test_codes_unique_and_groups_known(self):
        codes = [label.code for label in tax.LABELS]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertNotIn(tax.UNKNOWN, codes)
        for label in tax.LABELS:
            self.assertIn(label.group, tax.GROUPS, label.code)
        self.assertEqual({label.group for label in tax.LABELS}, set(tax.GROUPS), "her grupta en az bir tür")

    def test_every_brief_task_has_a_label(self):
        tasks = set(re.findall(r"\*\*(T\d{2}) ", BRIEF.read_text(encoding="utf-8")))
        self.assertEqual(len(tasks), 12)
        self.assertEqual(tasks, set(tax.BY_TASK))
        self.assertEqual(tax.from_task("T01_003"), "kredi_teklif_onay")
        self.assertEqual(tax.from_task("t7"), "musteri_sikayeti")
        self.assertIsNone(tax.from_task("D01"))
        self.assertIsNone(tax.from_task("T99"))

    def test_engine_types_map(self):
        for value in DocumentType:
            self.assertIn(tax.from_engine(value.value), tax.CODES)
        self.assertEqual(tax.from_engine("complaint"), "musteri_sikayeti")
        self.assertEqual(tax.from_engine("teminat"), "teminat", "kanonik kod olduğu gibi geçer")
        self.assertEqual(tax.from_engine("başka"), tax.UNKNOWN)

    def test_free_text(self):
        cases = {
            "Kefalet Sözleşmesi": "teminat",
            "KREDİ KULLANDIRIM TALİMATI": "kredi_kullandirim",
            "Kredi Teklif ve Onay Formu": "kredi_teklif_onay",
            "Mali Tablo Özeti": "mali_tablo",
            "KKB Raporu Özeti": "kkb_istihbarat",
            "Müşteri Şikâyet Dilekçesi": "musteri_sikayeti",
        }
        for text, code in cases.items():
            self.assertEqual(tax.from_free_text(text), code, text)
        self.assertIsNone(tax.from_free_text("defter"), "eft kelime içinde sayılmaz")
        self.assertIsNone(tax.from_free_text(""))


if __name__ == "__main__":
    unittest.main()
