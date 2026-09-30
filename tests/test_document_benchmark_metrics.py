# -*- coding: utf-8 -*-
"""Belge benchmark metrikleri (M4.4): tür, alan, uygunluk kararı; karşılaştırma."""
from __future__ import annotations

import unittest

from mufettis.analysis import document_benchmark as db
from mufettis.analysis.document_understanding import DocumentUnderstandingService


def case(case_id="c", split="dev", document_type="teminat", compliant=False, fields=None, documents=None):
    return db.DocumentCase.from_dict({
        "id": case_id, "split": split, "document_type": "" if documents else document_type, "text": "metin",
        "compliant": compliant, "defects": [] if compliant else [{"description": "kusur"}],
        "fields": fields or {}, "documents": documents or [],
    })


def prediction(types, fields=None, defective=None):
    return db.DocumentPrediction(document_types=types, fields=fields or {}, defective=defective)


class TestEvaluate(unittest.TestCase):
    def test_field_counts_and_decision(self):
        c = case(fields={"amounts": ["100.00 TRY", "200.00 TRY"], "dates": ["2024-01-01"]})
        r = db.evaluate_document_case(c, prediction(["teminat"], {"amounts": ["100.00 TRY", "5.00 TRY"]}, True))
        self.assertEqual(r.field_counts["amounts"], {"tp": 1, "fp": 1, "fn": 1})
        self.assertEqual(r.field_counts["dates"], {"tp": 0, "fp": 0, "fn": 1})
        self.assertEqual(r.missing_fields, {"amounts": ["200.00 TRY"], "dates": ["2024-01-01"]})
        self.assertEqual(r.extra_fields, {"amounts": ["5.00 TRY"]})
        self.assertEqual(r.decision, db.DECISION_CORRECT)

    def test_decision_outcomes(self):
        self.assertEqual(db.evaluate_document_case(case(), prediction(["teminat"], defective=False)).decision,
                         db.MISSED_DEFECT)
        self.assertEqual(db.evaluate_document_case(case(compliant=True), prediction(["teminat"], defective=True)).decision,
                         db.FALSE_ALARM)
        self.assertEqual(db.evaluate_document_case(case(compliant=True), prediction(["teminat"], defective=False)).decision,
                         db.DECISION_CORRECT)
        self.assertIsNone(db.evaluate_document_case(case(), prediction(["teminat"])).decision)

    def test_package_types_per_document(self):
        docs = [{"document_type": "teminat", "title": "a", "text": "a"},
                {"document_type": "mali_tablo", "title": "b", "text": "b"}]
        r = db.evaluate_document_case(case(documents=docs), prediction(["teminat", db.tax.UNKNOWN]))
        self.assertEqual(r.expected_types, ["teminat", "mali_tablo"])
        with self.assertRaises(ValueError):
            db.evaluate_document_case(case(documents=docs), prediction(["teminat"]))


class TestSummary(unittest.TestCase):
    def test_summary(self):
        docs = [{"document_type": "teminat", "title": "a", "text": "a"},
                {"document_type": "mali_tablo", "title": "b", "text": "b"}]
        results = [
            db.evaluate_document_case(case("a", fields={"iban": ["TR330006100519786457841326"]}),
                                      prediction(["teminat"], {"iban": ["TR330006100519786457841326"]}, True)),
            db.evaluate_document_case(case("b", split="holdout", compliant=True),
                                      prediction(["kasa_sayim"], defective=True)),
            db.evaluate_document_case(case("c", documents=docs), prediction(["teminat", "bilinmiyor"], defective=False)),
        ]
        s = db.summarize_documents(results)
        self.assertEqual(set(s), {"dev", "holdout", "all"})
        a = s["all"]
        self.assertEqual((a["cases"], a["documents"], a["type_correct"]), (3, 4, 2))
        self.assertEqual(a["type_accuracy"], 0.5)
        self.assertEqual(a["confusion"]["teminat"], {"teminat": 2, "kasa_sayim": 1})
        self.assertEqual(a["fields"]["iban"], {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0})
        self.assertIsNone(a["fields"]["tckn"]["precision"])
        self.assertEqual((a["decided"], a["decision_correct"], a["missed_defects"], a["false_alarms"]), (3, 1, 1, 1))
        self.assertEqual(a["by_type"]["teminat"], {"documents": 3, "type_correct": 2, "decided": 2, "decision_correct": 1})
        self.assertEqual(s["holdout"]["false_alarms"], 1)

    def test_compare(self):
        before = db.document_results_to_json([db.evaluate_document_case(case("a"), prediction(["teminat"], defective=False))])
        after = [db.evaluate_document_case(case("a", fields={"amounts": ["1.00 TRY"]}),
                                           prediction(["kasa_sayim"], defective=True))]
        (line,) = db.compare_document_results(before, after)
        self.assertIn("tür ['teminat'] -> ['kasa_sayim']", line)
        self.assertIn("karar missed_defect -> correct", line)
        self.assertIn("alan amounts", line)
        self.assertEqual(db.compare_document_results(before, [db.evaluate_document_case(
            case("a"), prediction(["teminat"], defective=False))]), [])


class TestPredictedFields(unittest.TestCase):
    def test_engine_output_is_normalized_not_rescanned(self):
        text = ("Sayın müşterimiz, 12.03.2024 tarihli talebinize istinaden hesabınızdan 1.250,50 TL kesilmiştir. "
                "IBAN: TR33 0006 1005 1978 6457 8413 26. TCKN 12345678901.")
        fields = db.predicted_fields(DocumentUnderstandingService().analyze(text))
        self.assertEqual(fields["amounts"], ["1250.50 TRY"])
        self.assertEqual(fields["dates"], ["2024-03-12"])
        self.assertEqual(fields["iban"], ["TR330006100519786457841326"])
        self.assertEqual(fields["tckn"], [], "motor TCKN çıkarmıyor (M5); benchmark metni kendisi taramaz")


if __name__ == "__main__":
    unittest.main()
