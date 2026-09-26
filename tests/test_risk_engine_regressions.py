# -*- coding: utf-8 -*-
"""risk_engine: clean_evidence_text import edilmeden kullanılıyordu (NameError)."""
from __future__ import annotations

import unittest

from mufettis.analysis.risk_engine import findings_to_dataframe, format_turkish_number, match_anomaly_to_mevzuat


class _FakeRag:
    def query(self, query, k=3):
        return [{"document": "MADDE 1 - Hüküm metni", "metadata": {"source": "BDDK", "title": "Yönetmelik", "file_hash": "h"}, "distance": 0.5}]


class TestRiskEngineRegressions(unittest.TestCase):
    def test_format_turkish_number_non_numeric_value(self):
        self.assertEqual(format_turkish_number("abc"), "abc")
        self.assertEqual(format_turkish_number(1234567.5), "1.234.567,50")

    def test_match_anomaly_to_mevzuat_builds_candidates(self):
        matches = match_anomaly_to_mevzuat({"bulgu_tipi": "zscore", "gerekce": "yüksek tutar"}, _FakeRag())
        self.assertEqual(matches[0]["title"], "Yönetmelik")
        self.assertEqual(matches[0]["snippet"], "MADDE 1 - Hüküm metni")

    def test_findings_dataframe_with_regulation_candidates(self):
        finding = {"bulgu_id": "1", "mevzuat_eslesmeleri": [{"source": "BDDK", "title": "Yönetmelik"}]}
        df = findings_to_dataframe([finding])
        self.assertEqual(len(df), 1)


if __name__ == "__main__":
    unittest.main()
