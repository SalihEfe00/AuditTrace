# -*- coding: utf-8 -*-\n"""Yapılandırılmış alan çıkarımı (M5.2–M5.4): tutar, tarih, oran, IBAN, TCKN, VKN, hesap no."""
from __future__ import annotations

import unittest

from mufettis.analysis import field_extraction as fx
from mufettis.analysis.document_understanding import DocumentUnderstandingService


def values(text, kind):
    return fx.field_values(fx.extract_fields(text)).get(kind, [])


class TestAmounts(unittest.TestCase):
    def test_amount_does_not_cross_line_end(self):
        text = "USD: 18.400,00 USD — Fark: 0\nEUR: 9.750,00 EUR — Fark: 0"
        self.assertEqual(values(text, "amount"), ["18400.00 USD", "9750.00 EUR"])

    def test_bare_amounts_in_csv_amount_columns(self):
        text = ('tarih,tutar,karsi_taraf,bakiye\n2026-01-05,"42.500,00",ARDA LTD,"48.310,25"\n'
                '2026-01-06,"-3.250,00",SİTE YÖNETİMİ,"45.060,25"\n')
        self.assertEqual(values(text, "amount"), ["42500.00 TRY", "48310.25 TRY", "3250.00 TRY", "45060.25 TRY"])
        fields = [f for f in fx.extract_fields(text) if f.kind == "amount"]
        self.assertEqual(fields[0].label, "sütun: tutar")
        self.assertEqual(text[fields[0].start:fields[0].end], "42.500,00")

    def test_markdown_table_and_labels(self):
        text = ("| Tarih | Hizmet | Tutar |\n|---|---|---|\n| 16.01.2023 | Kart ücreti | 450,00 |\n\n"
                "Dönem Toplamı: 9.280,43\nKısa Vadeli Borçlar / Özkaynak: 1,34\nKredi Sayısı: 12\nVade: 36")
        self.assertEqual(values(text, "amount"), ["450.00 TRY", "9280.43 TRY"])

    def test_bare_numbers_outside_amount_context_are_not_amounts(self):
        self.assertEqual(values("Sayfa 1.250 satır, ekspertiz no 2023.10\nAdet: 1.240", "amount"), [])

    def test_prose_with_commas_is_not_a_table(self):
        text = "Müşteri, şubeye gelerek, hesabını, kapatmak istedi.\nTutar, ücret, masraf, yok."
        self.assertEqual(values(text, "amount"), [])


class TestDatesRates(unittest.TestCase):
    def test_every_date_in_a_sentence(self):
        text = "Talep 12.03.2024 tarihinde alındı, 15 Mart 2024 ve 2024-03-20 tarihlerinde işlendi."
        self.assertEqual(values(text, "date"), ["2024-03-12", "2024-03-15", "2024-03-20"])

    def test_rates(self):
        self.assertEqual(values("Faiz yıllık %54,00, aylık % 4,5; ortaklık 60 %", "rate"), ["54.00", "4.50", "60.00"])


class TestIdentityNumbers(unittest.TestCase):
    VALID_TCKN = "10000000146"          # denetim haneleri tutan örnek numara

    def test_checksums(self):
        self.assertTrue(fx.tckn_checksum_ok(self.VALID_TCKN))
        self.assertFalse(fx.tckn_checksum_ok("12345678901"))
        self.assertFalse(fx.tckn_checksum_ok("01234567890"))
        self.assertFalse(fx.vkn_checksum_ok("123"))

    def test_labeled_or_checksum_tckn(self):
        text = (f"Müşteri TCKN: 28471036592. Eşi {self.VALID_TCKN} numaralı. Dosya 28471036593 sıra. "
                "T.C. Kimlik No: 52930417684")
        self.assertEqual(values(text, "tckn"), ["28471036592", self.VALID_TCKN, "52930417684"])

    def test_phone_and_iban_digits_are_not_ids(self):
        text = f"Tel: {self.VALID_TCKN}\nIBAN TR33 0006 1005 1978 6457 8413 26\nTCKN 12345678901,"
        self.assertEqual(values(text, "tckn"), ["12345678901"])

    def test_vkn_needs_a_label(self):
        text = "Vergi Dairesi / VKN: Nilüfer V.D. / 3810472956\nMüşteri No 5321234567\n(VKN 0582917364)"
        self.assertEqual(values(text, "vkn"), ["3810472956", "0582917364"])

    def test_labeled_accounts(self):
        text = "Hesap No: 0418-60221874-351\nMüşteri No: 60221874\nIBAN: TR330006100519786457841326"
        self.assertEqual(values(text, "account"), ["041860221874351"])


class TestPersonsSignatures(unittest.TestCase):
    def persons(self, text):
        return [(f.value, f.attributes.get("role", "")) for f in fx.extract_fields(text) if f.kind == "person"]

    def test_role_labels_titles_and_trailing_roles(self):
        text = ("Kefil: Kemal ÖZDEMİR\n"
                "Hazırlayan: Pazarlama Yetkilisi Burak Soylu — İmza: (imzalı)\n"
                "Onaylayan: Hülya Kaplan, Şube Operasyon Yönetmeni — (imzalı)\n"
                "Bilgi için Şube Müdürü Murat Aksoy ile görüşülmüştür.\n"
                "Kime: Nazlı Erdem (Şube Müdür Yardımcısı); Tuncay Kurt (Operasyon Yönetmeni)\n"
                "yönetim kurulu başkanı Hüseyin Toprak'ın (TCKN 17640392851) kefaleti")
        names = [n for n, _ in self.persons(text)]
        self.assertEqual(names, ["kemal özdemir", "burak soylu", "hülya kaplan", "murat aksoy", "nazlı erdem",
                                 "tuncay kurt", "hüseyin toprak"])
        self.assertEqual(dict(self.persons(text))["kemal özdemir"], "kefil")

    def test_companies_units_and_headings_are_not_persons(self):
        text = ("Asıl Borçlu: Deniz Tekstil Konfeksiyon San. Tic. A.Ş. (VKN 2940183376)\n"
                "Müşteri: Akdeniz Soğuk Hava Deposu İşletmeciliği A. Ş.\n"
                "KEFİL BİLGİLERİ\nBursa Nilüfer Şubesi\nŞube Kredi Komitesi Kararı: ONAYLANMIŞTIR\n"
                "Genel Müdürlük Kredi Tahsis Birimi")
        self.assertEqual(self.persons(text), [])

    def test_signature_and_approval_states(self):
        text = ("Kefil İmzası: (imzalı)\nİmza: ..................\nİcra Müdürü: Ahmet Can Başaran (e-imzalı)\n"
                "Şube Kredi Komitesi Kararı: ONAYLANMIŞTIR\nBölge Kredi Komitesi: —")
        states = [(f.kind, f.value) for f in fx.extract_fields(text) if f.kind in ("signature", "approval")]
        self.assertEqual(states, [("signature", "imzalı"), ("signature", "boş"), ("signature", "e-imzalı"),
                                  ("approval", "onaylandı"), ("approval", "yok")])
        signed = next(f for f in fx.extract_fields(text) if f.value == "e-imzalı")
        self.assertEqual(signed.attributes["person"], "ahmet can başaran")


class TestCollateralLimit(unittest.TestCase):
    def test_collateral_types_and_limit_scope(self):
        text = ("Talep Edilen Limit: 7.500.000,00 TL\n"
                "Şube Kredi Komitesi yetki limiti 5.000.000,00 TL\n"
                "- Hakan Ertuğrul müşterek borçlu müteselsil kefaleti: 7.500.000,00 TL\n"
                "1) İpotek: 1. derece, ipotek tutarı 3.000.000,00 TL\n"
                "Müşteri çeki temlik: 3.000.000,00 TL")
        fields = fx.extract_fields(text)
        limits = [(f.value, f.attributes["scope"]) for f in fields if f.kind == "limit"]
        self.assertEqual(limits, [("7500000.00 TRY", "kredi_limiti"), ("5000000.00 TRY", "yetki_limiti")])
        collateral = [(f.value, f.attributes["collateral_type"]) for f in fields if f.kind == "collateral"]
        self.assertEqual(collateral, [("7500000.00 TRY", "kefalet"), ("3000000.00 TRY", "ipotek"),
                                      ("3000000.00 TRY", "temlik")])


class TestDocumentUnderstandingIntegration(unittest.TestCase):
    def test_fields_on_document_and_entities_from_same_source(self):
        text = "Sayın yetkili, hesabımdan 1.250,50 TL kesildi.\nIBAN: TR33 0006 1005 1978 6457 8413 26\nTCKN 28471036592"
        doc = DocumentUnderstandingService().analyze(text)
        kinds = {f.kind for f in doc.fields}
        self.assertTrue({"amount", "iban", "tckn"} <= kinds)
        self.assertEqual([e.text for e in doc.entities if e.entity_type == "amount"], ["1.250,50 TL"])
        for f in doc.fields:
            self.assertEqual(doc.normalized_text[f.start:f.end].strip(), f.text)


if __name__ == "__main__":
    unittest.main()
