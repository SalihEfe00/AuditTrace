import datetime as dt

import pytest

from mufettis.rules.catalog import load_catalog
from mufettis.rules.engine import evaluate
from mufettis.rules.fields_teminat import extract_teminat_fields
from mufettis.rules.review import QUESTIONS, apply_answers, pending_questions, question

RULES = load_catalog()
DAY = dt.date(2024, 3, 12)

IPOTEK = ("İPOTEK AKİT TABLOSU\nMalik: Deniz Kaya\nTaşınmaz Bilgileri: Kadıköy 1203 ada 5 parsel\n"
          "İpotek Tutarı: 1.500.000,00 TL\nDerecesi: 1. derece\nYevmiye Tarihi: 05.02.2024\nYevmiye No: 4521\n")
REHIN = ("TAŞINIR REHİN SÖZLEŞMESİ\nRehin Veren: Kaya Tekstil Ltd. Şti.\nRehin Konusu: Dokuma makinesi seri no 778\n"
         "Rehin Tutarı: 800.000,00 TL\nİmza Onayı: Noter onaylı\nSicil No: RTS-2024-118\n")
EKSPERTIZ = ("GAYRİMENKUL DEĞERLEME RAPORU\nRapor Tarihi: 10.01.2024\nDeğerleme Kuruluşu: Örnek Değerleme A.Ş.\n"
             "Değerleme Uzmanı: Ece Tan\nLisans No: 400123\nPiyasa Değeri: 2.400.000,00 TL\n")
DASK = ("ZORUNLU DEPREM SİGORTASI POLİÇESİ\nPoliçe No: 12345678\nBaşlangıç Tarihi: 01.02.2024\n"
        "Bitiş Tarihi: 01.02.2025\nTapu İşlem Tarihi: 05.02.2024\n")
BONO = ("BONO\nDüzenleyen: Deniz Kaya\nLehtar: Örnek Bankası A.Ş.\nTutarı: 50.000,00 TL\nVade: 12.09.2024\n"
        "Ödeme Yeri: İstanbul\nDüzenlenme Tarihi: 12.03.2024\nDüzenlenme Yeri: İstanbul\nİmza: (imzalı)\n")


def run(doc_type, text, answers=None):
    fields = apply_answers(extract_teminat_fields(doc_type, text), answers or {})
    return {r.rule_id: r.status for r in evaluate(RULES, doc_type, fields, text=text, event_date=DAY)}


@pytest.mark.parametrize("doc_type, text, expected", [
    ("ipotek", IPOTEK, {"TMK851-ipotek-tutari": "karşılandı", "TMK856-tapu-tescili": "karşılandı"}),
    ("ipotek", IPOTEK.replace("1.500.000,00 TL", "……………"), {"TMK851-ipotek-tutari": "karşılanmadı"}),
    ("ipotek", IPOTEK.replace("Yevmiye No: 4521", "Yevmiye No: ……"), {"TMK856-tapu-tescili": "belirsiz"}),
    ("ipotek", IPOTEK.replace("Yevmiye No: 4521\n", ""), {"TMK856-tapu-tescili": "belirsiz"}),
    ("rehin", REHIN, {"6750-4-sicil-tescili": "karşılandı", "6750-4-azami-miktar": "karşılandı",
                      "6750-4-imza-onayi": "karşılandı"}),
    ("rehin", REHIN.replace("Noter onaylı", "……"), {"6750-4-imza-onayi": "karşılanmadı"}),
    ("rehin", REHIN.replace("İmza Onayı: Noter onaylı\n", ""), {"6750-4-imza-onayi": "belirsiz"}),
    ("rehin", REHIN.replace("800.000,00 TL", "……"), {"6750-4-azami-miktar": "karşılanmadı"}),
    ("ekspertiz", EKSPERTIZ, {"BDDK-DHY14-lisans-no": "karşılandı", "BDDK-DHY14-rapor-tarihi": "karşılandı",
                              "BDDK-DHY14-nihai-deger": "karşılandı"}),
    ("ekspertiz", EKSPERTIZ.replace("400123", "……"), {"BDDK-DHY14-lisans-no": "karşılanmadı"}),
    ("ekspertiz", EKSPERTIZ, {"BDDK-DHY20-iki-rapor": "uygulanmaz"}),
    ("ekspertiz", EKSPERTIZ.replace("2.400.000,00 TL", "12.500.000,00 TL"), {"BDDK-DHY20-iki-rapor": "manuel kontrol"}),
    ("ekspertiz", EKSPERTIZ.replace("2.400.000,00 TL", "on iki milyon"), {"BDDK-DHY20-iki-rapor": "belirsiz"}),
    ("ekspertiz", EKSPERTIZ.replace("Piyasa Değeri: 2.400.000,00 TL\n", ""), {"BDDK-DHY20-iki-rapor": "belirsiz"}),
    ("dask", DASK, {"6305-11-dask-baslangic": "karşılandı", "6305-11-dask-bitis": "karşılandı"}),
    ("dask", DASK.replace("Başlangıç Tarihi: 01.02.2024", "Başlangıç Tarihi: 10.02.2024"),
     {"6305-11-dask-baslangic": "karşılanmadı"}),
    ("dask", DASK.replace("Bitiş Tarihi: 01.02.2025", "Bitiş Tarihi: 01.02.2024"), {"6305-11-dask-bitis": "karşılanmadı"}),
    ("dask", DASK.replace("Tapu İşlem Tarihi: 05.02.2024\n", ""), {"6305-11-dask-baslangic": "belirsiz"}),
    ("bono", BONO, {"TTK776-bono-bedel": "karşılandı", "TTK776-bono-lehtar": "karşılandı",
                    "TTK776-bono-duzenleme-tarihi": "karşılandı", "TTK776-bono-imza": "karşılandı"}),
    ("bono", BONO.replace("Örnek Bankası A.Ş.", "……"), {"TTK776-bono-lehtar": "karşılanmadı"}),
    ("bono", BONO.replace("Düzenlenme Tarihi: 12.03.2024", "Düzenlenme Tarihi: ../../...."),
     {"TTK776-bono-duzenleme-tarihi": "karşılanmadı"}),
    ("bono", BONO.replace("İmza: (imzalı)\n", ""), {"TTK776-bono-imza": "belirsiz"}),
    # TTK 777/2-4: vade, ödeme ve düzenlenme yeri eksikliği bonoyu geçersiz kılmaz → bu alanlara kural yok
    ("bono", BONO.replace("Vade: 12.09.2024", "Vade: ……").replace("Ödeme Yeri: İstanbul", "Ödeme Yeri: ……"),
     {"TTK776-bono-bedel": "karşılandı", "TTK776-bono-duzenleme-tarihi": "karşılandı"}),
])
def test_collateral_rules(doc_type, text, expected):
    got = run(doc_type, text)
    for rule_id, status in expected.items():
        assert got[rule_id] == status, (rule_id, got[rule_id])


def test_pledge_before_6750_is_not_applicable():
    fields = extract_teminat_fields("rehin", REHIN.replace("Sicil No: RTS-2024-118", "Sicil No: ……"))
    got = {r.rule_id: r.status for r in evaluate(RULES, "rehin", fields, event_date=dt.date(2016, 5, 1))}
    assert got["6750-4-sicil-tescili"] == "uygulanmaz"


def test_inspector_answers_resolve_unknown_signature_approval():
    text = REHIN.replace("İmza Onayı: Noter onaylı\n", "")
    fields = extract_teminat_fields("rehin", text)
    results = evaluate(RULES, "rehin", fields, event_date=DAY)
    assert "imza_onayi" in [q.field for q in pending_questions(RULES, results, fields)]
    assert run("rehin", text, {"imza_onayi": "yok"})["6750-4-imza-onayi"] == "karşılanmadı"
    assert run("rehin", text, {"imza_onayi": "sicil_yetkilisi"})["6750-4-imza-onayi"] == "karşılandı"


def test_unspecified_electronic_signature_is_not_assumed_secure():
    """6750 md. 4/3 güvenli e-imza ister; türü yazmayan "e-imza" karar için yetersiz → belirsiz, müfettişe sorulur."""
    text = REHIN.replace("İmza Onayı: Noter onaylı", "İmza Onayı: E-imza ile imzalandı")
    fields = extract_teminat_fields("rehin", text)
    assert fields["imza_onayi"].state == "bulunamadı" and "yetersiz" in fields["imza_onayi"].location
    assert run("rehin", text)["6750-4-imza-onayi"] == "belirsiz"
    secure = REHIN.replace("İmza Onayı: Noter onaylı", "İmza Onayı: Güvenli elektronik imza")
    assert run("rehin", secure)["6750-4-imza-onayi"] == "karşılandı"


@pytest.mark.parametrize("typed, expected", [
    ("12.500.000", "manuel kontrol"), ("12.500.000,00 TL", "manuel kontrol"), ("950.000", "uygulanmaz"),
    ("on iki milyon", "belirsiz"),
])
def test_inspector_typed_value_drives_the_threshold_rule(typed, expected):
    text = EKSPERTIZ.replace("Piyasa Değeri: 2.400.000,00 TL\n", "")
    fields = extract_teminat_fields("ekspertiz", text)
    results = evaluate(RULES, "ekspertiz", fields, event_date=DAY)
    assert any(q.field == "deger" and q.kind == "amount" for q in pending_questions(RULES, results, fields))
    assert run("ekspertiz", text, {"deger": typed})["BDDK-DHY20-iki-rapor"] == expected


@pytest.mark.parametrize("typed, expected", [
    ("05.02.2024", "karşılandı"), ("20.01.2024", "karşılanmadı"), ("şubat başı", "belirsiz"),
])
def test_inspector_typed_date_resolves_dask_rule(typed, expected):
    text = DASK.replace("Tapu İşlem Tarihi: 05.02.2024\n", "")
    fields = extract_teminat_fields("dask", text)
    results = evaluate(RULES, "dask", fields, event_date=DAY)
    assert any(q.field == "islem_tarihi" and q.kind == "date" for q in pending_questions(RULES, results, fields))
    assert run("dask", text, {"islem_tarihi": typed})["6305-11-dask-baslangic"] == expected


def test_every_date_order_field_can_be_asked_as_a_date():
    for rule in RULES:
        for key in ("before", "after"):
            name = rule.check.get(key)
            if name:
                assert question(name) is not None and question(name).kind == "date", (rule.id, name)


def test_choice_questions_offer_every_value_a_rule_decides_on():
    """Müfettişin seçebileceği değerler, kuralın beklediği / koşul olarak aradığı değerlerin hepsini kapsamalı;
    yoksa soru cevaplansa da kural karar veremez."""
    gaps = []
    for rule in RULES:
        wanted = []
        check = rule.check
        if check["type"] == "field_value":
            wanted.append((check["field"], check["expected"]))
        cond = check.get("applies_if")
        if cond and "values" in cond:
            wanted.append((cond["field"], cond["values"]))
        wanted += [(ex.condition_field, ex.condition_values) for ex in rule.exceptions]
        for name, values in wanted:
            q = question(name)
            if q is None or q.kind != "choice":
                continue
            offered = {v for _, v in q.options}
            gaps += [(rule.id, name, v) for v in values if v not in offered]
    assert not gaps


def test_every_question_targets_a_catalog_field():
    from mufettis.rules.catalog import DOCUMENT_FIELDS
    known = set().union(*DOCUMENT_FIELDS.values())
    assert set(QUESTIONS) <= known


def test_registry_and_signature_blanks_are_asked_and_inspector_no_counts():
    """M9.4d: belgede yevmiye / sicil numarası ya da bono düzenlenme yeri boş olması hukuken yok demek değildir:
    belirsiz, müfettiş 'yok' derse karşılanmadı (TTK 777/4: yer, düzenleyenin adı yanında da yazılı olabilir)."""
    blank = IPOTEK.replace("Yevmiye No: 4521", "Yevmiye No: ……")
    assert run("ipotek", blank)["TMK856-tapu-tescili"] == "belirsiz"
    assert run("ipotek", blank, {"yevmiye_no": ""})["TMK856-tapu-tescili"] == "karşılanmadı"
    bono = "BONO\nDüzenleyen: Ali Veli\nLehtar: Örnek Bankası A.Ş.\nTutarı: 10.000 TL\nDüzenlenme Tarihi: 01.02.2024\nDüzenlenme Yeri: ……\n"
    assert run("bono", bono)["TTK776-bono-duzenleme-yeri"] == "belirsiz"
    assert run("bono", bono, {"duzenleme_yeri": "var"})["TTK776-bono-duzenleme-yeri"] == "karşılandı"
    assert run("bono", bono, {"duzenleme_yeri": ""})["TTK776-bono-duzenleme-yeri"] == "karşılanmadı"
