# -*- coding: utf-8 -*-
"""M6.15: kural önce (yol gösterici metin eşiği geçen kuralın önüne geçemez) ve listeden çıkarılan kayıtların
ayıklanması."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from mufettis.analysis.engine import AnalysisEngine
from mufettis.analysis.models import Evidence
from mufettis.corpus.curation import find_delisted


def _ev(role_instrument: str, name: str) -> Evidence:
    return Evidence(source_document=name, document_identity=name, chunk_reference="Madde 1", text_span=name,
                    metadata={"declared_instrument_type": role_instrument, "instrument_type": role_instrument,
                              "normative_role": "guidance" if role_instrument == "rehber" else "rule"})


class TestRuleFirst(unittest.TestCase):
    def setUp(self):
        self.engine = AnalysisEngine.__new__(AnalysisEngine)
        self.engine.reranker = SimpleNamespace(min_score=-2.95)

    def test_rule_above_threshold_beats_higher_scored_guidance(self):
        guide, rule = _ev("rehber", "izahname"), _ev("teblig", "ucret tebligi")
        self.assertIs(self.engine._pick([(guide, 1.15), (rule, -1.38)])[0], rule)

    def test_guidance_is_used_when_no_rule_passes(self):
        guide, rule = _ev("rehber", "rehber"), _ev("teblig", "teblig")
        self.assertIs(self.engine._pick([(guide, 1.0), (rule, -4.0)])[0], guide)

    def test_nothing_above_threshold(self):
        self.assertEqual(self.engine._pick([(_ev("kanun", "k"), -3.5)]), (None, -3.5))


class TestDelisted(unittest.TestCase):
    def test_only_active_list_records_missing_from_the_list(self):
        records = [
            ("a", {"ingest_origin": "legislation_list", "legislation_code": "1.5.5411", "status": "active"}),
            ("b", {"ingest_origin": "legislation_list", "legislation_code": "https://tbb/gerekce", "status": "active"}),
            ("c", {"ingest_origin": "legislation_list", "legislation_code": "https://x", "status": "historical"}),
            ("d", {"ingest_origin": "bulk", "legislation_code": "", "status": "active"}),
            ("e", {"ingest_origin": "legislation_list", "legislation_code": "https://tbb/x", "status": "excluded"}),
        ]
        out = find_delisted(records, {"1.5.5411"})
        self.assertEqual([x["file_hash"] for x in out], ["b"])


if __name__ == "__main__":
    unittest.main()
