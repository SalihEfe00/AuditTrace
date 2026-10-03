import datetime as dt

import pytest

from mufettis.rules.catalog import DOCUMENT_FIELDS
from mufettis.rules.engine import EMPTY, FILLED
from mufettis.rules.fields_kefalet import LABELS as KEFALET_LABELS
from mufettis.rules.fields_teminat import DOCUMENT_LABELS, extract_teminat_fields


def test_extractor_fields_are_known_to_the_catalog():
    for doc_type, labels in DOCUMENT_LABELS.items():
        assert set(labels) <= DOCUMENT_FIELDS[doc_type], doc_type
    assert set(KEFALET_LABELS) <= DOCUMENT_FIELDS["kefalet_sozlesmesi"]


def test_mortgage_fields():
    text = ("İPOTEK AKİT TABLOSU\nMalik: Deniz Kaya\nTaşınmaz Bilgileri: Kadıköy 1203 ada 5 parsel\n"
            "İpotek Tutarı: 1.500.000,00 TL\nDerecesi: 1. derece\nYevmiye Tarihi: 05.02.2024\nYevmiye No: ………")
    f = extract_teminat_fields("ipotek", text)
    assert f["ipotek_tutari"].value == "1500000.00" and f["derece"].value == "1"
    assert f["tescil_tarihi"].value == dt.date(2024, 2, 5)
    assert f["yevmiye_no"].state == EMPTY and "alacakli" not in f


def test_dask_and_appraisal_fields_and_unparsed_value_stays_filled():
    dask = extract_teminat_fields("dask", "Poliçe No: 12345678\nBitiş Tarihi: 3 Mart 2025\nDain-i Mürtehin: ____")
    assert dask["police_no"].value == "12345678"
    assert dask["bitis_tarihi"].state == FILLED and dask["bitis_tarihi"].value == "3 Mart 2025"
    assert dask["dain_murtehin"].state == EMPTY
    exp = extract_teminat_fields("ekspertiz", "Rapor Tarihi: 10.01.2024\nPiyasa Değeri: 2.000.000 TL\n"
                                              "Değerleme Kuruluşu: Örnek Değerleme A.Ş.")
    assert exp["deger"].value == "2000000" and exp["degerleme_kurulusu"].value == "Örnek Değerleme A.Ş."


def test_unknown_document_type():
    with pytest.raises(ValueError):
        extract_teminat_fields("tapu", "")
