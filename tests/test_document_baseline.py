# -*- coding: utf-8 -*-
"""Belge benchmark tabanı (benchmarks/document_baseline.json) korunuyor mu? (M4.5)

understanding seviyesi (belge türü + alan çıkarımı) korpussuz ve hızlıdır; her test koşusunda çalışır.
decision seviyesi tam hattı çalıştırır ve bir korpus kopyası ister; yalnızca MUFETTIS_BENCHMARK_DATA_DIR
verildiğinde çalışır:

    MUFETTIS_BENCHMARK_DATA_DIR=<data kopyası> python -m pytest tests/test_document_baseline.py

Taban bilinçli olarak güncellenir: scripts/eval_documents.py [--level decision] --write-baseline "<not>".
Vaka eklenince (yeni sentetik örnekler) taban yeniden yazılmadan bu test geçmez.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from mufettis.analysis import document_benchmark as db

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = os.environ.get("MUFETTIS_BENCHMARK_DATA_DIR", "")


def _baseline():
    return json.loads(db.DOCUMENT_BASELINE_FILE.read_text(encoding="utf-8"))


class TestUnderstandingBaseline(unittest.TestCase):
    def test_no_metric_is_worse_than_baseline(self):
        cases = db.load_case_dir()
        self.assertTrue(cases, "benchmarks/documents altında vaka yok")
        results = [db.evaluate_document_case(c, db.predict_understanding(c)) for c in cases]
        summary = db.summarize_documents(results)
        problems = db.check_document_baseline(summary, _baseline()["levels"]["understanding"],
                                              db.case_set_fingerprint(cases))
        self.assertEqual(problems, [])

    def test_sealed_cases_never_in_baseline(self):
        for entry in _baseline()["levels"].values():
            self.assertNotIn("sealed", entry["splits"])


class TestBaselineCheck(unittest.TestCase):
    def summary(self, type_correct=3, amounts_fp=1, false_alarms=0):
        fields = {k: {"tp": 1, "fp": 0, "fn": 0} for k in db.FIELD_KINDS}
        fields["amounts"]["fp"] = amounts_fp
        return {"all": {"type_correct": type_correct, "fields": fields, "decision_correct": 2,
                        "missed_defects": 1, "false_alarms": false_alarms}}

    def test_detects_worse_metrics_and_changed_case_set(self):
        entry = db.baseline_level_entry(self.summary(), "decision", "abc", "not", "ft1")
        self.assertIn("false_alarms", entry["splits"]["all"])
        self.assertEqual(db.check_document_baseline(self.summary(type_correct=4, amounts_fp=0), entry, "abc"), [])
        problems = db.check_document_baseline(self.summary(type_correct=2, amounts_fp=2, false_alarms=1), entry, "abc")
        self.assertEqual(len(problems), 3)
        self.assertTrue(any("all.type_correct: 2 (taban 3)" == p for p in problems))
        self.assertIn("vaka kümesi değişti", db.check_document_baseline(self.summary(), entry, "xyz")[0])

    def test_understanding_entry_has_no_decision_metrics(self):
        entry = db.baseline_level_entry(self.summary(), "understanding", "abc", "not")
        self.assertNotIn("false_alarms", entry["splits"]["all"])
        self.assertNotIn("reranker", entry)

    def test_fingerprint_follows_content(self):
        cases = db.load_case_dir()
        self.assertEqual(db.case_set_fingerprint(cases), db.case_set_fingerprint(list(reversed(cases))))
        changed = [db.DocumentCase.from_dict(dict(cases[0].to_dict(), text=cases[0].text + " "))] + cases[1:]
        self.assertNotEqual(db.case_set_fingerprint(cases), db.case_set_fingerprint(changed))


@unittest.skipUnless(DATA_DIR, "MUFETTIS_BENCHMARK_DATA_DIR verilmedi (isteğe bağlı karar tabanı testi)")
class TestDecisionBaseline(unittest.TestCase):
    def test_no_metric_is_worse_than_baseline(self):
        env = dict(os.environ, MUFETTIS_DATA_DIR=DATA_DIR, HF_HUB_OFFLINE="1", OMP_NUM_THREADS="2",
                   PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "eval_documents.py"), "--level", "decision", "--check-baseline"],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=3600,
        )
        tail = "\n".join(proc.stdout.splitlines()[-12:])
        self.assertEqual(proc.returncode, 0, f"{tail}\n{proc.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main()
