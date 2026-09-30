# -*- coding: utf-8 -*-
"""Olay tarihi (M7.1): tarih rolleri ve seçim kuralı; genel banka dilinden örnekler."""
from __future__ import annotations

import unittest

from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.event_date import date_role, extract_event_date
from mufettis.analysis.field_extraction import extract_fields


def _event(text):
    return extract_event_date(text, extract_fields(text))


def _roles(text):
    return {f.value: date_role(text, f) for f in extract_fields(text) if f.kind == "date"}


class TestRoles(unittest.TestCase):
    def test_labelled_dates(self):
        text = ("KREDİ KULLANDIRIM FORMU\nTarih: 10.05.2024\nKullandırım tarihi: 12.05.2024\n"
                "Vade: 12.05.2026\nDoğum tarihi: 01.01.1980\nSorgu Tarihi: 02.05.2024\n")
        self.assertEqual(_roles(text), {"2024-05-10": "düzenlenme", "2024-05-12": "olay", "2026-05-12": "diğer",
                                        "1980-01-01": "diğer", "2024-05-02": "diğer"})

    def test_prose_and_references(self):
        text = ("Sayın Yetkili,\n05.03.2024 tarihinde kartımdan izinsiz çekim yapıldı. 01.03.2024 tarihli "
                "dilekçeme cevap verilmedi. Borç 31.03.2024 itibarıyla 1.000 TL.")
        self.assertEqual(_roles(text), {"2024-03-05": "olay", "2024-03-01": "diğer", "2024-03-31": "diğer"})

    def test_transaction_table_rows(self):
        text = "tarih;tutar\n2026-01-05;100,00\n2026-01-09;-50,00\nDönem: 01.01.2026 – 31.01.2026"
        roles = _roles(text)
        self.assertEqual((roles["2026-01-05"], roles["2026-01-09"], roles["2026-01-31"]), ("olay", "olay", "diğer"))


class TestSelection(unittest.TestCase):
    def test_earliest_event_wins_over_issue_date(self):
        ev = _event("Tarih: 15.09.2025\nİlgi: 10.09.2025 tarihinde tebliğ edilen haciz ihbarnameniz.")
        self.assertEqual((ev.value, ev.basis, ev.issue_date), ("2025-09-10", "düzyazı", "2025-09-15"))

    def test_header_date_is_the_issue_date(self):
        ev = _event("Teklif Tarihi: 14.03.2024\nİstihbarat — Tarih: 02.02.2024. Olumsuz bilgi yok.")
        self.assertEqual((ev.value, ev.basis), ("2024-03-14", "düzenlenme tarihi"))

    def test_no_date(self):
        self.assertEqual((_event("Tarihsiz not.").value, _event("Tarihsiz not.").basis), (None, "yok"))

    def test_document_understanding_carries_event_date(self):
        doc = DocumentUnderstandingService().analyze("Sayın Yetkili,\n05.03.2024 tarihinde hesabımdan 500 TL kesildi.")
        self.assertEqual((doc.event_date, doc.event_date_basis), ("2024-03-05", "düzyazı"))


if __name__ == "__main__":
    unittest.main()
