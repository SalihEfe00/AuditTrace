# -*- coding: utf-8 -*-
"""
Tests for AnalysisPlannerService (FAZ 19.9 Stage 2).
"""

from mufettis.analysis.classifier import DocumentClassifierService
from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.models import (
    AnalysisPlan,
    AnalysisType,
)
from mufettis.analysis.planner import AnalysisPlannerService
from mufettis.analysis.validator import assert_valid_analysis_plan


class TestAnalysisPlanner:
    """Verifies formulation of structured AnalysisPlans from document understanding and classification."""

    def setup_method(self):
        self.doc_service = DocumentUnderstandingService()
        self.classifier = DocumentClassifierService()
        self.planner = AnalysisPlannerService()

    def test_a_plan_complaint_analysis(self):
        text = (
            "Yapı Kredi Bankası hesabımdan 1.200 TL hesap işletim ücreti kesilmiştir. "
            "Onayım olmadan yapılan bu kesintinin iadesini talep ediyorum."
        )
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)

        plan = self.planner.plan(doc, classification)

        assert isinstance(plan, AnalysisPlan)
        assert plan.analysis_type == AnalysisType.REGULATORY_RELEVANCE.value
        assert "tüketici" in plan.objective.lower() or "şikâyet" in plan.objective.lower()
        # Hedef kanunlar belgenin kendi atıflarıdır; metin kanun anmıyorsa varsayılan kanun atanmaz.
        assert plan.retrieval_scope.explicit_laws == []
        assert len(plan.retrieval_scope.generated_queries) >= 1
        assert any("Yapı Kredi" in e for e in plan.parameters.target_entities)
        assert len(plan.required_evidence) > 0
        assert_valid_analysis_plan(plan)

    def test_b_plan_internal_correspondence(self):
        text = (
            "Sayı: 2026/01\n"
            "Konu: Şube Kredi Limit Tahsis Aşımı\n"
            "İç Denetim Başkanlığına\n"
            "Gereğini bilgilerinize arz ederim."
        )
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)

        plan = self.planner.plan(doc, classification)

        assert isinstance(plan, AnalysisPlan)
        assert plan.analysis_type == AnalysisType.COMPLIANCE.value
        assert any("ic_sistemler" in s or "iç sistemler" in s for s in plan.parameters.scope)
        assert len(plan.retrieval_scope.generated_queries) >= 1
        assert_valid_analysis_plan(plan)

    def test_c_plan_unknown_document(self):
        text = "Rastgele genel bir metin parçası."
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)

        plan = self.planner.plan(doc, classification)

        assert isinstance(plan, AnalysisPlan)
        assert plan.analysis_type == AnalysisType.REGULATORY_RELEVANCE.value
        assert len(plan.retrieval_scope.generated_queries) >= 1
        assert_valid_analysis_plan(plan)

    def test_d_planner_never_fabricates_findings(self):
        text = "Banka haksız ücret kesti şikayetçiyim."
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)
        plan = self.planner.plan(doc, classification)

        # Plan must be purely planning, having no findings attribute or finding list
        plan_dict = plan.model_dump()
        assert "findings" not in plan_dict
        assert "evidence_chains" not in plan_dict


def test_explicit_laws_are_the_documents_own_citations():
    from mufettis.analysis.classifier import DocumentClassifierService
    from mufettis.analysis.document_understanding import DocumentUnderstandingService
    from mufettis.analysis.planner import AnalysisPlannerService

    cited = ("Sayı: 1\nKonu: Kart\nUyum Müdürlüğüne\n5464 sayılı Banka Kartları ve Kredi Kartları Kanunu "
             "uyarınca bildirim yapılmadığı tespit edilmiştir. 6698 sayılı Kanun da ihlal edilmiştir.\n"
             "Bilgilerinize arz ederim.")
    uncited = ("Sayı: 2\nKonu: Limit\nTeftiş Kurulu Başkanlığına\nKredi limitinin aşıldığı tespit edilmiştir.\n"
               "Bilgilerinize arz ederim.")
    for text, expected in [(cited, ["5464", "6698"]), (uncited, [])]:
        doc = DocumentUnderstandingService().analyze(text)
        plan = AnalysisPlannerService().plan(doc, DocumentClassifierService().classify(doc))
        assert plan.retrieval_scope.explicit_laws == expected
