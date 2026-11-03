# -*- coding: utf-8 -*-
"""Alan çıkarımında yanlış ama geçerli görünen değer üretilmemesi: benzer adlar, olumsuz ifadeler, Türkçe dışı
tutar biçimleri, geçersiz tarih. Tanınmayan girdi yanlış değer değil, None / UNDECIDED (kural belirsiz) olmalı."""
import datetime as dt

import pytest

from mufettis.rules.catalog import load_catalog
from mufettis.rules.engine import INSPECTOR, MET, NOT_APPLICABLE, NOT_MET, FieldValue, evaluate
from mufettis.rules.fields import UNDECIDED, as_amount, as_date, as_money
from mufettis.rules.fields_kefalet import CONVERT, extract_kefalet_fields

F = FieldValue.filled


def _differ_status(kefil, borclu):
    rule = next(r for r in load_catalog() if r.id == "TBK581-kefil-ucuncu-kisi")
    return evaluate([rule], "kefalet_sozlesmesi", {"kefil": F(kefil), "borclu": F(borclu)})[0].status


@pytest.mark.parametrize("kefil, borclu", [
    ("Can Er", "Can Eren"),
    ("Ali Veli", "Ali Velioğlu"),
    ("Ayşe Kaya", "Ayşe Kayalı"),
])
def test_similar_names_are_different_people(kefil, borclu):
    assert _differ_status(kefil, borclu) == MET
    assert _differ_status(borclu, kefil) == MET


@pytest.mark.parametrize("kefil, borclu", [
    ("Selim Arslan", "Selim Arslan"),
    ("SELİM ARSLAN", "Selim Arslan"),
    ("Selim Arslan", "Arslan Kuaförlüğü - Selim Arslan"),
    ("Şükrü Öztürk", "SUKRU OZTURK"),
])
def test_same_person_is_detected(kefil, borclu):
    assert _differ_status(kefil, borclu) == NOT_MET


@pytest.mark.parametrize("field, raw, expected", [
    ("kefil_medeni_hal", "Evli", "evli"),
    ("kefil_medeni_hal", "Evli değil", UNDECIDED),
    ("kefil_medeni_hal", "Bekar", "bekar"),
    ("kefalet_turu", "Müteselsil kefalet", "müteselsil"),
    ("kefalet_turu", "Müteselsil olmayan adi kefalet", "adi"),
    ("kefil_ticari_sifat", "Şirket ortağı", "sirket_ortagi_yoneticisi"),
    ("kefil_ticari_sifat", "Şirket ortağı değildir", UNDECIDED),
    ("kefil_ticari_sifat", "Ortaköy şubesi çalışanı", "yok"),
    ("kefil_ticari_sifat", "Esnaf değildir, emekli", "yok"),
    ("kredi_turu", "Ticari taşıt kredisi", "ticari"),
    ("kredi_turu", "KOBİ konut kredisi", "ticari"),
    ("kredi_turu", "Bireysel ihtiyaç kredisi", "tüketici"),
    ("imza_turu", "Güvenli olmayan elektronik imza", "diger_elektronik"),
    ("imza_turu", "Güvenli elektronik imza", "guvenli_e_imza"),
])
def test_category_values_respect_negation(field, raw, expected):
    assert CONVERT[field](raw) == expected


SPOUSE = """KEFALET SÖZLEŞMESİ
Kefil Adı Soyadı: Ali Kaya
Borçlu Adı Soyadı: Veli Demir
Sözleşme Tarihi: 12.03.2024
Medeni Hali: {medeni}
Ayrılık Durumu: Yok
Kefilin Sıfatı: {sifat}
Eşin Rızası: ______
"""


def _spouse_consent(medeni, sifat, related=None):
    fields = extract_kefalet_fields(SPOUSE.format(medeni=medeni, sifat=sifat))
    if related:
        fields["kefalet_isletmeyle_ilgili"] = FieldValue.filled(related, "müfettiş", INSPECTOR)
    results = evaluate(load_catalog(), "kefalet_sozlesmesi", fields, "", dt.date(2024, 3, 12))
    return next(r for r in results if r.rule_id == "TBK584-es-rizasi").status


def test_not_married_does_not_require_spouse_consent():
    assert _spouse_consent("Bekar", "Öğretmen") == NOT_APPLICABLE
    assert _spouse_consent("Evli değil", "Öğretmen") != NOT_MET  # eskiden: evli sayılıp yanlış bulgu


def test_negated_partner_does_not_trigger_commercial_exemption():
    assert _spouse_consent("Evli", "Öğretmen", related="evet") == NOT_MET
    # eskiden: şirket ortağı sayılıp TBK 584/3 istisnası uygulanıyor, eksik rıza bulgusu kayboluyordu
    assert _spouse_consent("Evli", "Şirket ortağı değildir", related="evet") != "istisna"


@pytest.mark.parametrize("raw, expected", [
    ("1.000.000,00 TL", "1000000.00"),
    ("250.000 TL", "250000"),
    ("500.000,50TL", "500000.50"),
    ("1.000.000 ₺ (bir milyon)", "1000000"),
    ("1000000 TL", "1000000"),
    ("1000000.50 TL", None),       # eskiden "50"
    ("1,000,000.00 TL", None),     # eskiden "00"
])
def test_amount_never_reads_a_fragment(raw, expected):
    assert as_amount(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("1.000.000,00 TL", "1000000.00 TRY"),
    ("100.000 EUR", "100000 EUR"),
    ("USD 250.000", "250000 USD"),
    ("Tutar: 5.000.000 TL, faiz %3", "5000000 TRY"),
    ("1000000.50 TL", None),       # eskiden "50 TRY"
    ("USD 1,000,000.00", None),    # eskiden "1.00 USD"
])
def test_money_never_reads_a_fragment(raw, expected):
    assert as_money(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("12.03.2024", dt.date(2024, 3, 12)),
    ("12/03/2024", dt.date(2024, 3, 12)),
    ("2024-03-12", dt.date(2024, 3, 12)),
    ("12 Mart 2024", dt.date(2024, 3, 12)),
    ("31.02.2024 / 05.03.2024", dt.date(2024, 3, 5)),  # geçersiz ilk tarih aramayı bitirmez
    ("31.02.2024", None),
])
def test_dates(raw, expected):
    assert as_date(raw) == expected
