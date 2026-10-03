"""M9.4c: kullanıcı vakalarının ilk ölçümünde bulunan sınıf hataları (her biri sentetik, vaka metni kopyalanmadan)."""
import datetime as dt

from mufettis.rules.doctype import detect_collateral_type
from mufettis.rules.engine import EMPTY, FILLED, NOT_FOUND
from mufettis.rules.fields import extract_labeled_fields, is_blank, one_of
from mufettis.rules.fields_kefalet import extract_kefalet_fields
from mufettis.rules.fields_teminat import extract_teminat_fields


def test_document_types_from_title_variants_labels_and_ocr_noise():
    assert detect_collateral_type("KEFİL SÖZLEŞMESİ\nBanka: Örnek\nKefil: Ali Veli\n").document_type == "kefalet_sozlesmesi"
    assert detect_collateral_type("MENKUL REHİN MUKAVELESİ\nRehin Veren: X Ltd.\n").document_type == "rehin"
    # başlıksız değerleme özeti: etiketlerden
    assert detect_collateral_type("Rapor Tarihi: 01.02.2024\nDeğerleme Kuruluşu: X A.Ş.\nLisans No: 1\n"
                                  "Piyasa Değeri: 1.000.000 TL\n").document_type == "ekspertiz"
    assert detect_collateral_type("KEFALFT SOZLESMESl\nKefil Adı Soyadı: Ali\nAzami Kefalet Tutarı: 1 TL\n"
                                  "Kefalet Türü: Adi\n").document_type == "kefalet_sozlesmesi"
    # kredi sözleşmesi bono ve müteselsil kefil anabilir; tür değişmez
    gks = "GENEL KREDİ SÖZLEŞMESİ\nMadde 1: Teminat olarak bono verilir; müteselsil kefil alınabilir.\n"
    assert detect_collateral_type(gks).document_type == "senet_gks"


def test_label_qualifiers_inline_labels_dates_and_unit_only_blanks():
    f = extract_teminat_fields("rehin", "Rehin Tutarı / Azami Miktar (TL): 4.850.000 TL\nSözleşme Tarihi: 2021-03-18\n"
                                        "RTS Sicil No: RTS-1\n")
    assert f["rehin_tutari"].value == "4850000" and f["sozlesme_tarihi"].value == dt.date(2021, 3, 18)
    assert f["sicil_no"].value == "RTS-1"
    inline = extract_teminat_fields("rehin", "Sicil Bilgisi: Tescil Tarihi: 17.08.2021, Sicil No: T-9\n")
    assert inline["sicil_no"].value == "T-9"
    assert is_blank("…………… TL") and is_blank("(BOŞ - İMZA DOLDURULMAMIŞTIR)") and not is_blank("12 TL")


def test_unrecognised_category_is_asked_not_guessed():
    fields = extract_labeled_fields("Kredi Türü: Rotatif\n", {"kredi_turu": [r"kredi türü"]},
                                    {"kredi_turu": one_of([(r"ticari", "ticari")])})
    assert fields["kredi_turu"].state == NOT_FOUND
    k = extract_kefalet_fields("Kefilin Mesleği: Servis Müdürü\n")
    assert k["kefil_ticari_sifat"].state == NOT_FOUND  # çalışan unvanı şirket yöneticiliği sayılmaz
    assert extract_kefalet_fields("Mesleği: Limited Şirket Ortağı\n")["kefil_ticari_sifat"].value == "sirket_ortagi_yoneticisi"


def test_spouse_consent_needs_signature_or_name_with_date_not_printed_text():
    printed_name = "EŞ RIZASI\nEşin Adı Soyadı: Ayşe Veli\nRıza Tarihi: ……\nEşin İmzası: ……\n"
    assert extract_kefalet_fields(printed_name)["es_rizasi"].state == EMPTY
    statement = "Eşin Muvafakati: Eşimin kefil olmasına muvafakat ediyorum.\nEşin Adı Soyadı: ……\nTarih: ……\nEş İmza: ……\n"
    assert extract_kefalet_fields(statement)["es_rizasi"].state == EMPTY
    signed = "Eş: Ayşe Veli\nEş Rızası Tarihi: 12.03.2024\nEş İmzası: Ayşe Veli\n"
    f = extract_kefalet_fields(signed)
    assert f["es_rizasi"].state == FILLED and f["es_rizasi_tarihi"].value == dt.date(2024, 3, 12)
    assert extract_kefalet_fields("Eşin Adı Soyadı: Ayşe Veli\n")["es_rizasi"].state == NOT_FOUND


def test_blank_guarantor_signature_overrides_wet_signature_line_but_not_e_signature():
    wet = extract_kefalet_fields("İmza Türü: Islak imza\nKefil İmzası: ________\n")
    assert wet["imza_turu"].state == NOT_FOUND and "boş" in wet["imza_turu"].location
    secure = extract_kefalet_fields("İmza Türü: Güvenli elektronik imza\nKefil İmzası: ________\n")
    assert secure["imza_turu"].value == "guvenli_e_imza"
