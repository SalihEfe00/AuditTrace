# -*- coding: utf-8 -*-
"""Belge analizi DOCX çalışma kağıdı."""
from __future__ import annotations

import io
import unittest

from docx import Document

from mufettis.analysis.analysis_presenter import AnalysisPresenter
from mufettis.analysis.models import Evidence
from mufettis.exports.analysis_report import create_analysis_docx
from mufettis.exports.report_generator import validate_export_bytes

TEXT = ("Bankanız TR12 0001 0002 0003 0004 0005 06 nolu hesabımdan onayım olmadan hesap işletim ücreti "
        "kesmiştir. Şikayetçiyim.")
EVIDENCE = Evidence(
    source_document="Örnek Ücretler Yönetmeliği", document_identity="ucret", chunk_reference="Madde 5",
    text_span="Vadesiz mevduat hesaplarından hesap işletim ücreti alınamaz ve müşteri onayı olmadan kesinti yapılamaz.",
    metadata={"retrieval_distance": 0.4},
)


def _docx_text(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        parts.extend(cell.text for row in table.rows for cell in row.cells)
    return "\n".join(parts)


class TestAnalysisReport(unittest.TestCase):
    def setUp(self):
        presenter = AnalysisPresenter()
        result = presenter.execute_analysis(text=TEXT, title="Ücret İtirazı", sample_evidence=[EVIDENCE]).result
        self.result = presenter.apply_reviewer_action(result=result, status="İncelendi", note="Kayıtlar istenecek.",
                                                      reviewer="Denetçi 7")

    def test_report_contains_findings_citations_and_review(self):
        data = create_analysis_docx(self.result, generated_at="2026-09-26 12:00:00")
        validate_export_bytes(data, "docx")
        text = _docx_text(data)
        self.assertIn(self.result.analysis_id, text)
        self.assertIn("Bulgu 1", text)
        self.assertIn("Örnek Ücretler Yönetmeliği Madde 5", text)
        self.assertIn("alıntı kontrolü: doğrulandı", text)
        self.assertIn("Denetçi 7", text)
        self.assertIn("Kayıtlar istenecek.", text)

    def test_personal_data_is_masked(self):
        text = _docx_text(create_analysis_docx(self.result))
        self.assertNotIn("0003 0004 0005", text)

    def test_no_findings_is_not_presented_as_compliance(self):
        result = AnalysisPresenter().execute_analysis(text="Şubenin otoparkı dar, aracımı park edemedim.",
                                                      sample_evidence=[]).result
        text = _docx_text(create_analysis_docx(result))
        self.assertIn("uygun olduğu anlamına gelmez", text)


if __name__ == "__main__":
    unittest.main()
