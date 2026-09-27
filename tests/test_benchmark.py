# -*- coding: utf-8 -*-
"""Gerçek korpus doğruluk ölçümünün (mufettis/analysis/benchmark.py) karar mantığı."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from mufettis.analysis.benchmark import (
    CASES_FILE, CORRECT, FALSE_POSITIVE, MISSED, TRUE_NEGATIVE, WRONG_BASIS,
    BenchmarkCase, EvidenceSpec, available_specs, evaluate_case, load_cases, run_benchmark, summarize,
)

ART54 = {"law_number": "5411", "article_no": "54", "article_prefix": ""}
ART51 = {"law_number": "5411", "article_no": "51"}
TRANSITIONAL4 = {"law_number": "5464", "article_no": "4", "article_prefix": "GEÇICI"}


def _case(expect, split="dev"):
    return BenchmarkCase(case_id="c", split=split, text="t", expect=[EvidenceSpec(**e) for e in expect])


class TestEvidenceSpec(unittest.TestCase):
    def test_article_prefix_distinguishes_transitional(self):
        self.assertFalse(EvidenceSpec(law_number="5464", article="4").matches(TRANSITIONAL4))
        self.assertTrue(EvidenceSpec(law_number="5464", article="4", article_prefix="geçici").matches(TRANSITIONAL4))

    def test_title_uses_display_name_too(self):
        meta = {"title": "1135", "declared_subject": "Genelge 2022/1: Sır Niteliğindeki Bilgiler"}
        self.assertTrue(EvidenceSpec(title_contains="genelge 2022/1").matches(meta))


class TestEvaluateCase(unittest.TestCase):
    def test_outcomes(self):
        case = _case([{"law_number": "5411", "article": "54"}])
        self.assertEqual(evaluate_case(case, [(ART54, "Madde 54")], [ART51, ART54], True).outcome, CORRECT)
        wrong = evaluate_case(case, [(ART51, "Madde 51")], [ART51, ART54], True)
        self.assertEqual((wrong.outcome, wrong.retrieval_hit, wrong.first_hit_rank), (WRONG_BASIS, True, 1))
        self.assertEqual(evaluate_case(case, [], [ART51], True).outcome, MISSED)

    def test_unavailable_basis_turns_case_negative(self):
        case = _case([{"title_contains": "Ücretler Yönetmeliği"}])
        result = evaluate_case(case, [(ART51, "Madde 51")], [ART51], expected_available=False)
        self.assertEqual((result.outcome, result.retrieval_hit), (FALSE_POSITIVE, None))
        self.assertEqual(evaluate_case(case, [], [], False).outcome, TRUE_NEGATIVE)

    def test_summary_separates_stages(self):
        case = _case([{"law_number": "5411", "article": "54"}])
        results = [
            evaluate_case(case, [(ART54, "")], [ART54], True),
            evaluate_case(case, [(ART51, "")], [ART54], True),
            evaluate_case(case, [(ART51, "")], [ART51], True),
        ]
        m = summarize(results)["all"]
        self.assertEqual(m["retrieval_recall"], round(2 / 3, 3))
        self.assertEqual(m["selection_accuracy_on_hits"], 0.5)


class TestRunBenchmark(unittest.TestCase):
    def test_availability_and_pool_are_read_from_execution(self):
        cases = [_case([{"law_number": "5411", "article": "54"}]), _case([{"law_number": "9999"}])]
        evidence = SimpleNamespace(metadata=ART54, chunk_reference="Madde 54")
        execution = SimpleNamespace(
            success=True, error_message=None, retrieved_evidence=[evidence],
            result=SimpleNamespace(findings=[SimpleNamespace(evidence=[evidence])]),
        )
        results = run_benchmark(cases, lambda _t: execution, [ART51, ART54])
        self.assertEqual([r.outcome for r in results], [CORRECT, FALSE_POSITIVE])
        self.assertEqual(available_specs(cases, [ART54]), {EvidenceSpec(law_number="5411", article="54")})


class TestCaseFile(unittest.TestCase):
    def test_case_file_is_valid(self):
        cases = load_cases(CASES_FILE)
        self.assertEqual(len({c.case_id for c in cases}), len(cases))
        self.assertTrue({c.split for c in cases} <= {"dev", "holdout"})
        raw = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
        for entry in raw:
            for spec in entry.get("expect", []):
                self.assertTrue(set(spec) <= {"law_number", "article", "article_prefix", "section", "title_contains"}, spec)


if __name__ == "__main__":
    unittest.main()
