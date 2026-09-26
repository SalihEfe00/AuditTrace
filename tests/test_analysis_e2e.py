# -*- coding: utf-8 -*-
"""
End-to-End Vertical Slice & Semantic Gold Set Tests (FAZ 19.9 Stage 3).
"""
import pytest

from mufettis.analysis.classifier import DocumentClassifierService
from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.engine import AnalysisEngine
from mufettis.analysis.gold_set import (
    GoldSetEvaluationReport,
    build_semantic_gold_set,
    evaluate_semantic_gold_set,
)
from mufettis.analysis.models import (
    AnalysisResult,
    DocumentType,
    Evidence,
)
from mufettis.analysis.planner import AnalysisPlannerService
from mufettis.analysis.retrieval_planner import RetrievalPlannerService
from tests.data_integrity import data_integrity_problems


class TestAnalysisE2E:
    """End-to-end integration and semantic gold set evaluation."""

    def setup_method(self):
        self.doc_service = DocumentUnderstandingService()
        self.classifier = DocumentClassifierService()
        self.planner = AnalysisPlannerService()
        self.retrieval_planner = RetrievalPlannerService()
        self.engine = AnalysisEngine()

    def test_a_e2e_complaint_vertical_slice(self):
        text = (
            "Ziraat Bankası Kadıköy Şubesi nezdindeki hesabımdan bilgim ve onayım olmadan "
            "15.03.2026 tarihinde 650 TL hesap işletim ücreti kesilmiştir. Kesintinin tarafıma iadesini "
            "talep ediyorum ve mağdur edildim."
        )
        mock_evidence = [
            Evidence(
                source_document="Finansal Tüketicilerden Alınacak Ücretlere İlişkin Yönetmelik",
                document_identity="5411",
                chunk_reference="Madde 13 Fıkra 1",
                text_span="Vadesiz mevduat hesaplarından hesap işletim ücreti veya benzeri adlar altında ücret alınamaz.",
                metadata={"source": "BDDK", "file_hash": "e2e_fee_hash"},
            )
        ]

        # 1. Understanding
        doc = self.doc_service.analyze(text)
        assert doc.date == "15.03.2026"
        assert any(e.text == "Ziraat Bankası" for e in doc.entities)

        # 2. Classification
        classification = self.classifier.classify(doc)
        assert classification.document_type == DocumentType.COMPLAINT.value
        assert classification.confidence >= 0.70

        # 3. Planning
        analysis_plan = self.planner.plan(doc, classification)
        assert "tüketici" in analysis_plan.objective.lower() or "şikâyet" in analysis_plan.objective.lower()

        # 4. Retrieval Planning
        retrieval_plan = self.retrieval_planner.plan_retrieval(analysis_plan, doc)
        assert len(retrieval_plan.queries) >= 1

        # 5. Analysis Engine & Citation Validation
        result = self.engine.analyze(
            doc=doc,
            classification=classification,
            analysis_plan=analysis_plan,
            retrieval_plan=retrieval_plan,
            retrieved_evidence=mock_evidence,
        )

        assert isinstance(result, AnalysisResult)
        assert result.status == "completed"
        assert len(result.findings) >= 1
        finding = result.findings[0]
        assert len(finding.evidence) == 1
        assert len(finding.citations) == 1
        assert finding.citations[0].text_span in mock_evidence[0].text_span
        assert "Ziraat Bankası" in finding.inspector_action

    def test_b_e2e_internal_correspondence_vertical_slice(self):
        text = (
            "Sayı: 2026/08\n"
            "Konu: Şube Kredi Tahsis Sınır Aşımı\n"
            "Teftiş Kurulu Başkanlığına\n"
            "Bakırköy Şubesi tarafından 8.000.000 TL tutarındaki ticari kredinin "
            "yetki sınırları aşılarak onaylandığı teftiş kontrollerinde saptanmıştır.\n"
            "Gereğini bilgilerinize arz ederim."
        )
        mock_evidence = [
            Evidence(
                source_document="Bankaların İç Sistemleri Yönetmeliği",
                document_identity="5411",
                chunk_reference="Madde 19 Fıkra 3",
                text_span="Kredi tahsis yetkileri yönetim kurulu tarafından belirlenen limitler dahilinde kullandırılır.",
                metadata={"source": "BDDK", "file_hash": "e2e_audit_hash"},
            )
        ]

        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)
        assert classification.document_type == DocumentType.INTERNAL_CORRESPONDENCE.value

        analysis_plan = self.planner.plan(doc, classification)
        retrieval_plan = self.retrieval_planner.plan_retrieval(analysis_plan, doc)

        result = self.engine.analyze(
            doc=doc,
            classification=classification,
            analysis_plan=analysis_plan,
            retrieval_plan=retrieval_plan,
            retrieved_evidence=mock_evidence,
        )

        assert len(result.findings) >= 1
        assert "Madde 19 Fıkra 3" in result.findings[0].regulation_reference

    def test_c_semantic_gold_set_evaluation(self):
        report: GoldSetEvaluationReport = evaluate_semantic_gold_set(engine=self.engine)

        assert report.total_cases == 18
        assert report.fail_count == 0
        assert report.accuracy >= 0.9
        # Sıfır uydurma bulgu: çeldirici (ilgisiz kanıt) vakaları dahil hiçbir negatif vakada bulgu olmamalı.
        assert report.false_positives == 0
        assert report.false_negatives <= 1
        assert report.unsupported_claims_caught >= 1

        distractor_ids = {"TC-15", "TC-16", "TC-17", "TC-18"}
        distractor_results = [cr for cr in report.case_results if cr.case_id in distractor_ids]
        assert len(distractor_results) == 4
        assert all(cr.findings_count == 0 for cr in distractor_results)

        # Check that individual case results are properly populated
        for cr in report.case_results:
            assert cr.outcome in ["PASS", "PARTIAL"]
            assert cr.doc_type_actual != ""

    def test_d_production_filesystem_isolation_guarantee(self):
        # Production verisinin değişmediği conftest tarafından oturum düzeyinde doğrulanır.
        assert data_integrity_problems() == []

