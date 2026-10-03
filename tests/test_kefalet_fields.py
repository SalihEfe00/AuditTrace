import datetime as dt

from mufettis.rules.catalog import load_catalog
from mufettis.rules.engine import EMPTY, FILLED, MET, NOT_MET, UNKNOWN, evaluate_rule
from mufettis.rules.fields_kefalet import extract_kefalet_fields

CONTRACT = """KEFALET SÖZLEŞMESİ
Alacaklı Banka: Örnek Bankası A.Ş. İstanbul Şubesi
Asıl Borçlu: Deniz Kaya
Kefil Adı Soyadı: Ali Veli
Kefilin Medeni Hali: Evli
Azami Kefalet Tutarı: 250.000,00 TL
Kefalet Türü: Müteselsil kefalet
Sözleşme Tarihi: 12.03.2024
EŞ RIZASI
Eşin rızası: ……………………
Rıza Tarihi: ....../....../........
"""


def test_three_state_extraction():
    f = extract_kefalet_fields(CONTRACT)
    assert f["kefil"].state == FILLED and f["kefil"].value == "Ali Veli"
    assert f["borclu"].value == "Deniz Kaya"
    assert f["azami_tutar"].value == "250000.00"
    assert f["kefalet_turu"].value == "müteselsil"
    assert f["kefil_medeni_hal"].value == "evli"
    assert f["sozlesme_tarihi"].value == dt.date(2024, 3, 12)
    # Kefalet tarihi etiketi yoksa kefalet sözleşmesinin tarihi kefalet tarihidir; varsa o okunur
    assert f["kefalet_tarihi"].value == dt.date(2024, 3, 12) and "sözleşme tarihi" in f["kefalet_tarihi"].location
    both = extract_kefalet_fields(CONTRACT + "Kefalet Tarihi: 14.03.2024\n")
    assert both["kefalet_tarihi"].value == dt.date(2024, 3, 14) and both["sozlesme_tarihi"].value == dt.date(2024, 3, 12)
    # Ana kredi sözleşmesinin tarihi kefalet tarihinin yerine geçmez
    credit = extract_kefalet_fields(CONTRACT.replace("Sözleşme Tarihi: 12.03.2024", "Kredi Sözleşme Tarihi: 12.03.2024"))
    assert "kefalet_tarihi" not in credit and credit["kredi_sozlesme_tarihi"].value == dt.date(2024, 3, 12)
    # Aynı etiket iki kez: biri boş biri dolu → dolu okunur
    twice = extract_kefalet_fields("Azami Kefalet Tutarı :\n" + CONTRACT)
    assert twice["azami_tutar"].value == "250000.00"
    assert f["es_rizasi"].state == EMPTY and "satır 10" in f["es_rizasi"].location
    assert f["es_rizasi_tarihi"].state == EMPTY
    assert "imza_turu" not in f  # etiket yok: bulunamadı
    assert "el_yazisi_azami_tutar" not in f  # metinden anlaşılmaz; müfettişe sorulur


def test_spouse_consent_rule_end_to_end():
    from mufettis.rules.engine import FieldValue
    rule = next(r for r in load_catalog() if r.id == "TBK584-es-rizasi")
    no_sep = {"ayrilik_durumu": FieldValue.filled("yok", "elle giriş", "müfettiş beyanı")}
    f = {**extract_kefalet_fields(CONTRACT), **no_sep}
    # ticari sıfat yazmıyor: istisna koşulu bilinmiyor → belirsiz (karşılanmadı denmez)
    assert evaluate_rule(rule, f, event_date=dt.date(2024, 3, 12)).status == UNKNOWN
    esnaf = {**extract_kefalet_fields(CONTRACT + "Kefilin mesleği: Esnaf (berber)\nKredi Türü: İşletme kredisi\n"),
             **no_sep}
    assert evaluate_rule(rule, esnaf, event_date=dt.date(2024, 3, 12)).status == UNKNOWN  # mesleki ilgi sorulur
    esnaf["kefalet_isletmeyle_ilgili"] = FieldValue.filled("evet", "elle giriş", "müfettiş beyanı")
    assert evaluate_rule(rule, esnaf, event_date=dt.date(2024, 3, 12)).status == "istisna"
    assert evaluate_rule(rule, {**extract_kefalet_fields(CONTRACT + "Kefilin mesleği: Ücretli çalışan\n"), **no_sep},
                         event_date=dt.date(2024, 3, 12)).status == NOT_MET
    # Ayrılık kararı / yasal ayrı yaşama sorulmadıysa karşılanmadı denmez (TBK 584/1 istisnası)
    assert evaluate_rule(rule, extract_kefalet_fields(CONTRACT + "Kefilin mesleği: Ücretli çalışan\n"),
                         event_date=dt.date(2024, 3, 12)).status == UNKNOWN
    f["kefil_ticari_sifat"] = FieldValue.filled("yok", "elle giriş", "müfettiş beyanı")
    assert evaluate_rule(rule, f, event_date=dt.date(2024, 3, 12)).status == NOT_MET
    signed = extract_kefalet_fields(CONTRACT.replace("Eşin rızası: ……………………", "Eşin rızası: Ayşe Veli (imzalı)"))
    signed["kefil_ticari_sifat"] = FieldValue.filled("yok")
    signed.update(no_sep)
    assert evaluate_rule(rule, signed, event_date=dt.date(2024, 3, 12)).status == MET


def test_missing_label_is_not_found():
    assert extract_kefalet_fields("Kefil: Ali Veli\n").get("es_rizasi") is None


def _status(rule_id, text, **extra):
    from mufettis.rules.engine import FieldValue
    rule = next(r for r in load_catalog() if r.id == rule_id)
    fields = extract_kefalet_fields(text)
    fields.update({k: FieldValue.filled(v, "elle giriş", "müfettiş beyanı") for k, v in extra.items()})
    return evaluate_rule(rule, fields, event_date=dt.date(2024, 3, 12)).status


def test_block1_rules_end_to_end():
    assert _status("5070-md5-e-imza-kefalet", CONTRACT + "İmza Türü: Güvenli elektronik imza\n") == NOT_MET
    assert _status("5070-md5-e-imza-kefalet", CONTRACT + "İmza Türü: Islak imza\n") == MET
    assert _status("5070-md5-e-imza-kefalet", CONTRACT) == UNKNOWN
    assert _status("5070-md5-e-imza-kefalet", CONTRACT + "İmza Türü: Elektronik imza\n") == UNKNOWN  # güvenli mi?
    assert _status("5070-md5-e-imza-kefalet", CONTRACT + "İmza Türü: Basit elektronik imza\n") == MET
    assert _status("TBK14-kefil-imzasi", CONTRACT + "İmza Türü: Basit elektronik imza\n") == NOT_MET
    assert _status("TBK14-kefil-imzasi", CONTRACT + "İmza Türü: Güvenli elektronik imza\n") == MET  # güvenli e-imza imzadır (TBK 15); yasağı 5070 kuralı bildirir
    assert _status("TBK14-kefil-imzasi", CONTRACT + "İmza Türü: Islak imza\n") == MET
    assert _status("TBK14-kefil-imzasi", CONTRACT) == UNKNOWN  # imza bulunamadı ≠ imza yok
    assert _status("6502-4-adi-kefalet", CONTRACT + "Kredi Türü: Bireysel ihtiyaç kredisi\n") == NOT_MET
    assert _status("6502-4-adi-kefalet", CONTRACT + "Kredi Türü: Ticari kredi\n") == "uygulanmaz"
    late = CONTRACT.replace("Rıza Tarihi: ....../....../........", "Rıza Tarihi: 15.03.2024")
    assert _status("TBK584-riza-zamani", late, kefil_ticari_sifat="yok", ayrilik_durumu="yok") == NOT_MET
    same = CONTRACT.replace("Rıza Tarihi: ....../....../........", "Rıza Tarihi: 12.03.2024")
    assert _status("TBK584-riza-zamani", same, kefil_ticari_sifat="yok") == MET
    assert _status("TBK583-muteselsil-el-yazisi", CONTRACT) == UNKNOWN  # el yazısı müfettişe sorulur
    assert _status("TBK583-muteselsil-el-yazisi", CONTRACT, el_yazisi_kefalet_turu="yok") == UNKNOWN  # kredi türü?
    assert _status("TBK583-muteselsil-el-yazisi", CONTRACT, el_yazisi_kefalet_turu="yok", kredi_turu="ticari") == NOT_MET
    assert _status("TBK583-muteselsil-el-yazisi", CONTRACT, el_yazisi_kefalet_turu="yok",
                   kredi_turu="tüketici") == "uygulanmaz"  # kefalet kanunen adi; 6502 kuralı tek bulgu
    assert _status("TBK583-azami-miktar-belirtilmis", CONTRACT.replace("250.000,00 TL", "………")) == NOT_MET


def test_pre_2013_text_has_no_trade_exception():
    from mufettis.rules.engine import FieldValue
    rules = {r.id: r for r in load_catalog()}
    fields = extract_kefalet_fields(CONTRACT + "Kefilin mesleği: Esnaf\nKredi Türü: İşletme kredisi\n")
    fields["ayrilik_durumu"] = FieldValue.filled("yok", "elle giriş", "müfettiş beyanı")
    fields["kefalet_isletmeyle_ilgili"] = FieldValue.filled("evet", "elle giriş", "müfettiş beyanı")
    old_date = dt.date(2013, 1, 15)
    assert evaluate_rule(rules["TBK584-es-rizasi-2013-oncesi"], fields, event_date=old_date).status == NOT_MET
    assert evaluate_rule(rules["TBK584-es-rizasi"], fields, event_date=old_date).status == "uygulanmaz"
    assert evaluate_rule(rules["TBK584-es-rizasi-2013-oncesi"], fields, event_date=dt.date(2024, 3, 12)).status == "uygulanmaz"
