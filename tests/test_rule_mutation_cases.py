import datetime as dt

import pytest

from mufettis.rules.catalog import load_catalog
from mufettis.rules.engine import evaluate
from mufettis.rules.fields_kefalet import extract_kefalet_fields
from mufettis.rules.mutation_cases import CASES, case_answers
from mufettis.rules.review import apply_answers

RULES = load_catalog()


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_mutation_case(case):
    fields = apply_answers(extract_kefalet_fields(case.text), case_answers(case))
    got = {r.rule_id: r.status for r in evaluate(RULES, "kefalet_sozlesmesi", fields, text=case.text,
                                                 event_date=dt.date(2024, 3, 12))}
    for rule_id, expected in case.expected.items():
        assert got[rule_id] == expected, f"{case.id} {rule_id}: {got[rule_id]} ≠ {expected} ({case.basis})"


def test_every_rule_has_a_met_and_a_violating_case():
    expected = [(rid, status) for c in CASES for rid, status in c.expected.items()]
    current = {r.id for r in RULES if r.document_type == "kefalet_sozlesmesi" and r.valid_to is None
               and r.result_class != "kısmen"}
    for rid in current:
        assert (rid, "karşılandı") in expected or rid == "6502-4-adi-kefalet", rid
        assert (rid, "karşılanmadı") in expected, rid
