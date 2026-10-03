import datetime as dt

import pytest

from mufettis.rules.catalog import load_catalog, parse_rule
from mufettis.rules.engine import (EMPTY, EXEMPT, FILLED, INSPECTOR, MANUAL, MET, NOT_APPLICABLE, NOT_FOUND,
                                   NOT_MET, UNKNOWN, FieldValue, evaluate, evaluate_rule)

RULES = {r.id: r for r in load_catalog()}
SPOUSE = RULES["TBK584-es-rizasi"]
HAND = RULES["TBK583-azami-tutar-el-yazisi"]
TODAY = dt.date(2024, 5, 1)
F = FieldValue.filled


def spouse(**fields):
    base = {"kefil_medeni_hal": F("evli", "s.1"), "kefil_ticari_sifat": F("yok", "s.1")}
    base.update(fields)
    return evaluate_rule(SPOUSE, base, event_date=TODAY)


def test_positive_evidence_is_met():
    r = spouse(es_rizasi=F("Ayşe Yılmaz, imza, 01.03.2024", "s.3 'Eş rızası' bölümü"))
    assert r.status == MET and r.finding == "" and ("es_rizasi", FILLED, "Ayşe Yılmaz, imza, 01.03.2024",
                                                     "s.3 'Eş rızası' bölümü", "belge") in r.evidence


def test_empty_field_with_absence_violation_is_not_met_and_names_the_missing_element():
    r = spouse(es_rizasi=FieldValue(EMPTY, None, "s.3 'Eş rızası: ……'"))
    assert r.status == NOT_MET and "Eş rızası" in r.finding and r.basis == "6098 md. 584/1"


def test_field_not_found_is_unknown_never_met():
    r = spouse()
    assert r.status == UNKNOWN and "bulunamadı" in r.reason


def test_exception_applies():
    r = spouse(es_rizasi=FieldValue(EMPTY), kefil_ticari_sifat=F("esnaf_sanatkar"))
    assert r.status == EXEMPT and "584/3" in r.reason


def test_unknown_exception_condition_blocks_not_met():
    r = spouse(es_rizasi=FieldValue(EMPTY), kefil_ticari_sifat=FieldValue(NOT_FOUND))
    assert r.status == UNKNOWN and "istisna" in r.finding


def test_rule_not_applicable_for_unmarried_and_unknown_when_marital_status_missing():
    assert spouse(es_rizasi=FieldValue(EMPTY), kefil_medeni_hal=F("bekar")).status == NOT_APPLICABLE
    assert spouse(es_rizasi=FieldValue(EMPTY), kefil_medeni_hal=FieldValue(NOT_FOUND)).status == UNKNOWN


def test_event_date_outside_rule_text_validity():
    r = evaluate_rule(SPOUSE, {"kefil_medeni_hal": F("evli")}, event_date=dt.date(2012, 9, 1))
    assert r.status == NOT_APPLICABLE


def test_field_value_check_and_inspector_declaration():
    met = evaluate_rule(HAND, {"el_yazisi_azami_tutar": F("var", "elle giriş", INSPECTOR)}, event_date=TODAY)
    assert met.status == MET and met.evidence[0][4] == INSPECTOR
    assert evaluate_rule(HAND, {"el_yazisi_azami_tutar": F("yok")}, event_date=TODAY).status == NOT_MET
    assert evaluate_rule(HAND, {}, event_date=TODAY).status == UNKNOWN


def _rule(check, **extra):
    raw = {"id": "T", "document_type": "kefalet_sozlesmesi", "title": "t", "check": check,
           "basis": {"law": "6098", "article": "583", "quote": "x"}, "result_class": "otomatik",
           "norm_type": "yükümlülük", "obligor": "banka", "absence_is_violation": False,
           "finding": "bulgu", "rationale": "r"}
    raw.update(extra)
    return parse_rule(raw)


def test_human_rule_produces_checklist_item():
    r = evaluate_rule(_rule({"type": "human", "instruction": "Kefilin kimliğini asıl belgeyle karşılaştırın"}), {})
    assert r.status == MANUAL and r.finding.startswith("Kefilin kimliğini")


def test_date_order_threshold_and_text_pattern():
    order = _rule({"type": "date_order", "before": "es_rizasi_tarihi", "after": "sozlesme_tarihi"})
    assert evaluate_rule(order, {"es_rizasi_tarihi": F(dt.date(2024, 1, 2)), "sozlesme_tarihi": F(dt.date(2024, 1, 1))}).status == NOT_MET
    assert evaluate_rule(order, {"es_rizasi_tarihi": F(dt.date(2024, 1, 1)), "sozlesme_tarihi": F(dt.date(2024, 1, 1))}).status == MET
    cap = _rule({"type": "threshold", "field": "azami_tutar", "max": 1000})
    assert evaluate_rule(cap, {"azami_tutar": F("1500")}).status == NOT_MET
    pat = _rule({"type": "text_pattern", "pattern": r"müteselsil\s+kefil"})
    assert evaluate_rule(pat, {}, text="Müteselsil kefil sıfatıyla").status == MET
    assert evaluate_rule(pat, {}, text="").status == UNKNOWN


def test_empty_without_absence_violation_stays_unknown():
    r = evaluate_rule(_rule({"type": "field_present", "field": "kefil"}), {"kefil": FieldValue(EMPTY)})
    assert r.status == UNKNOWN


def test_evaluate_filters_by_document_type():
    out = evaluate(load_catalog(), "kefalet_sozlesmesi", {}, event_date=TODAY)
    assert {r.rule_id for r in out} == set(RULES) and all(r.status != MET for r in out)
    assert evaluate(load_catalog(), "ipotek", {}) == []
