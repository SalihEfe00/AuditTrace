import datetime as dt

from mufettis.rules.catalog import load_catalog
from mufettis.rules.engine import EMPTY, INSPECTOR, MET, NOT_MET, UNKNOWN, evaluate
from mufettis.rules.fields_kefalet import extract_kefalet_fields
from mufettis.rules.review import QUESTIONS, apply_answers, pending_questions

TEXT = """Kefil Adı Soyadı: Ali Veli
Kefilin Medeni Hali: Evli
Azami Kefalet Tutarı: 100.000 TL
Kefalet Türü: Müteselsil
Sözleşme Tarihi: 01.02.2024
Eşin rızası: ………
"""
DAY = dt.date(2024, 2, 1)


def test_questions_cover_only_fields_needed_by_unknown_rules():
    rules = load_catalog()
    fields = extract_kefalet_fields(TEXT)
    results = evaluate(rules, "kefalet_sozlesmesi", fields, event_date=DAY)
    asked = [q.field for q in pending_questions(rules, results, fields)]
    assert "kefil_ticari_sifat" in asked and "el_yazisi_azami_tutar" in asked
    assert "kefil_medeni_hal" not in asked and "azami_tutar" not in asked  # belgede dolu
    assert len(asked) == len(set(asked)) and all(f in QUESTIONS for f in asked)


def test_answers_become_inspector_evidence_and_change_results():
    rules = load_catalog()
    fields = extract_kefalet_fields(TEXT)
    answered = apply_answers(fields, {"kefil_ticari_sifat": "yok", "el_yazisi_azami_tutar": "var", "ayrilik_durumu": "yok",
                                      "imza_turu": "bilinmiyor", "es_rizasi": None})
    assert answered["kefil_ticari_sifat"].source == INSPECTOR and "imza_turu" not in answered
    by_id = {r.rule_id: r for r in evaluate(rules, "kefalet_sozlesmesi", answered, event_date=DAY)}
    assert by_id["TBK584-es-rizasi"].status == NOT_MET
    assert by_id["TBK583-azami-tutar-el-yazisi"].status == MET
    assert by_id["5070-md5-e-imza-kefalet"].status == UNKNOWN
    empty = apply_answers({}, {"es_rizasi": ""})["es_rizasi"]
    assert empty.state == EMPTY and empty.source == INSPECTOR
