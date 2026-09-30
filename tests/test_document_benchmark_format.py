# -*- coding: utf-8 -*-
"""Belge vakası biçimi ve alan normalleştirme (M4.2)."""
from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from mufettis.analysis import document_benchmark as db


def _case(**overrides):
    data = {"id": "c1", "split": "dev", "document_type": "teminat", "text": "Kefalet tutarı 250.000,00 TL.",
            "compliant": False, "defects": [{"description": "eş rızası yok", "quote": "Kefalet tutarı"}]}
    data.update(overrides)
    return data


class TestNormalization(unittest.TestCase):
    def test_numbers(self):
        for text, value in {"1.250.000,50": "1250000.50", "1,250,000.50": "1250000.50", "250.000": "250000",
                            "2,5": "2.5", "12.5": "12.5", "1 250 000": "1250000", "1500": "1500"}.items():
            self.assertEqual(db.parse_number(text), Decimal(value), text)
        self.assertIsNone(db.parse_number("abc"))
        self.assertIsNone(db.parse_number(""))

    def test_amounts(self):
        text = "Limit 5.000.000 TL, masraf ₺1.250,50 ve USD 10,000.00; faiz %2,5, vade 36 ay; 3 milyon TL teminat."
        self.assertEqual(db.find_amounts(text),
                         ["5000000.00 TRY", "1250.50 TRY", "10000.00 USD", "3000000.00 TRY"])
        self.assertEqual(db.find_amounts("Kredi 2 milyar Türk Lirası"), ["2000000000.00 TRY"])
        self.assertEqual(db.find_amounts("12 adet"), [])

    def test_key_amounts(self):
        self.assertEqual(db.key_amounts("250.000,00"), ["250000.00 TRY"])
        self.assertEqual(db.key_amounts("kredi_limiti: 5000000"), ["5000000.00 TRY"])
        self.assertEqual(db.key_amounts("Kefalet tutarı: 250.000 EUR"), ["250000.00 EUR"])
        self.assertEqual(db.key_amounts("Vade 36 ay"), [])

    def test_dates(self):
        text = "12.03.2024 tarihli talep, 2024-03-15 onay, 1 Nisan 2024 kullandırım, 31.02.2024 geçersiz."
        self.assertEqual(db.find_dates(text), ["2024-03-12", "2024-03-15", "2024-04-01"])
        self.assertEqual(db.find_dates("5 AĞUSTOS 2023"), ["2023-08-05"])

    def test_ids(self):
        text = "IBAN: TR33 0006 1005 1978 6457 8413 26, TCKN 12345678901, VKN 1234567890, tel 05321234567"
        self.assertEqual(db.find_ibans(text), ["TR330006100519786457841326"])
        self.assertEqual(db.find_tckns(text), ["12345678901"], "0 ile başlayan telefon TCKN değildir")
        self.assertEqual(db.find_vkns(text), ["1234567890"])

    def test_answer_key_fields(self):
        fields = db.ExpectedFields.from_answer_key({
            "tutarlar": ["Kredi limiti: 5.000.000 TL", {"kefalet": "250.000,00"}, "Vade: 36 ay"],
            "tarihler": ["Teklif: 12.03.2024", {"onay": "2024-03-15"}],
            "kisiler_ve_rolleri": ["Ahmet Yılmaz - kefil"],
            "imza_onay": ["Şube müdürü imzası"],
            "kimlik_numaralari": ["TCKN: 12345678901", {"vkn": "1234567890"}],
            "diger": {"hesap": "TR330006100519786457841326", "telefon": "05321234567"},
        })
        self.assertEqual(fields.amounts, ["5000000.00 TRY", "250000.00 TRY"])
        self.assertEqual(fields.dates, ["2024-03-12", "2024-03-15"])
        self.assertEqual(fields.tckn, ["12345678901"])
        self.assertEqual(fields.vkn, ["1234567890"])
        self.assertEqual(fields.iban, ["TR330006100519786457841326"])
        self.assertEqual(fields.persons, ["Ahmet Yılmaz - kefil"])
        self.assertEqual(fields.other["telefon"], "05321234567")

    def test_locate_quote(self):
        text = 'Kefil  "Ayşe Kaya" medeni hali: Evli.\nEş rızası: —\nİmza: ...'
        self.assertTrue(db.locate_quote("kefil ayşe kaya", text))
        self.assertTrue(db.locate_quote("Kefil ... Eş rızası: —", text))
        self.assertFalse(db.locate_quote("Eş rızası alınmıştır", text))
        self.assertFalse(db.locate_quote("Eş rızası ... Kefil", text), "parçalar sırayla")
        self.assertFalse(db.locate_quote("", text))


class TestCaseFormat(unittest.TestCase):
    def test_valid_case_round_trips(self):
        case = db.DocumentCase.from_dict(_case(fields={"amounts": ["250000.00 TRY"]}))
        self.assertEqual(db.DocumentCase.from_dict(case.to_dict()), case)

    def test_invalid_cases_rejected_with_reason(self):
        bad = {
            "bilinmeyen belge türü": _case(document_type="yok"),
            "kusursuz denmiş": _case(compliant=True),
            "kusurlu denmiş": _case(defects=[]),
            "bilinmeyen bölüm": _case(split="test"),
            "ISO tarih değil": _case(issue_date="12.03.2024"),
            "normalleştirilmemiş": _case(fields={"amounts": ["250.000 TL"]}),
            "bilinmeyen önem": _case(defects=[{"description": "x", "severity": "kritik"}]),
            "metin boş": _case(text=" "),
        }
        for reason, data in bad.items():
            with self.assertRaises(ValueError) as ctx:
                db.DocumentCase.from_dict(data)
            self.assertIn(reason, str(ctx.exception))

    def test_package_types_live_in_documents(self):
        docs = [{"document_type": "teminat", "title": "Kefalet", "text": "a"},
                {"document_type": "kkb_istihbarat", "title": "KKB", "text": "b"}]
        case = db.DocumentCase.from_dict(_case(document_type="", documents=docs))
        self.assertEqual([d.document_type for d in case.documents], ["teminat", "kkb_istihbarat"])
        with self.assertRaises(ValueError):
            db.DocumentCase.from_dict(_case(documents=docs))

    def test_dir_loading_hides_sealed_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [db.DocumentCase.from_dict(_case(id="a")), db.DocumentCase.from_dict(_case(id="s", split="sealed"))]
            db.write_cases(Path(tmp) / "x.json", cases)
            self.assertEqual([c.case_id for c in db.load_case_dir(Path(tmp))], ["a"])
            self.assertEqual(len(db.load_case_dir(Path(tmp), include_sealed=True)), 2)
            db.write_cases(Path(tmp) / "y.json", cases[:1])
            with self.assertRaises(ValueError):
                db.load_case_dir(Path(tmp))


if __name__ == "__main__":
    unittest.main()
