# -*- coding: utf-8 -*-
"""Belge analizi inceleme geçmişi: denetim kaydından okunur, bozuk satır sessizce yutulmaz."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mufettis.analysis.analysis_presenter import AnalysisPresenter
from mufettis.analysis.models import Evidence
from mufettis.audit import service
from mufettis.ui.analysis_view import review_history_rows

EVIDENCE = Evidence(
    source_document="Örnek Ücretler Yönetmeliği", document_identity="ucret", chunk_reference="Madde 5",
    text_span="Vadesiz mevduat hesaplarından hesap işletim ücreti alınamaz ve müşteri onayı olmadan kesinti yapılamaz.",
    metadata={"retrieval_distance": 0.4},
)


class TestReviewHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(service, "AUDIT_LOG_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _review(self, status, note):
        presenter = AnalysisPresenter(audit_sink=service.append_audit)
        result = presenter.execute_analysis(
            text="Bankanız hesabımdan onayım olmadan hesap işletim ücreti kesmiştir. Şikayetçiyim.",
            title="Ücret İtirazı", sample_evidence=[EVIDENCE]).result
        presenter.record_review(presenter.apply_reviewer_action(result=result, status=status, note=note,
                                                                reviewer="Denetçi 7"))
        return result.analysis_id

    def test_history_is_newest_first_and_keeps_every_decision(self):
        first = self._review("İncelendi", "ilk bakış")
        second = self._review("Kabul edildi", "TCKN 12345678901 kontrol edildi")
        history, unreadable = service.analysis_review_history()
        self.assertEqual(unreadable, 0)
        self.assertEqual([h["analysis_id"] for h in history], [second, first])
        self.assertNotIn("12345678901", history[0]["reviewer_note"])

    def test_unreadable_lines_are_counted_and_rows_filter_by_status(self):
        self._review("Reddedildi", "")
        (Path(self.tmp.name) / "audit_2000-01-01.jsonl").write_text("{bozuk\n", encoding="utf-8")
        history, unreadable = service.analysis_review_history()
        self.assertEqual(unreadable, 1)
        self.assertEqual(len(review_history_rows(history, "Reddedildi")), 1)
        self.assertEqual(review_history_rows(history, "Kabul edildi"), [])
        self.assertEqual(review_history_rows(history)[0]["Denetçi"], "Denetçi 7")


if __name__ == "__main__":
    unittest.main()
