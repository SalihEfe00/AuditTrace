# -*- coding: utf-8 -*-
"""
Tests for AnalysisEngine (FAZ 19.9 Stage 3).
"""

from mufettis.analysis.classifier import DocumentClassifierService
from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.engine import AnalysisEngine
from mufettis.analysis.models import (
    AnalysisResult,
    Evidence,
)
from mufettis.analysis.planner import AnalysisPlannerService
from mufettis.analysis.retrieval_planner import RetrievalPlannerService


class MockRAGClient:
    """Mock RAG client returning deterministic chunk results without neural dependencies."""

    def __init__(self, chunks=None):
        self.chunks = chunks or []

    def query(self, query: str, k: int = 5):
        return self.chunks[:k]


class TestAnalysisEngine:
    """Verifies vertical slice analysis execution, evidence binding, and safe fallback."""

    def setup_method(self):
        self.doc_service = DocumentUnderstandingService()
        self.classifier = DocumentClassifierService()
        self.planner = AnalysisPlannerService()
        self.retrieval_planner = RetrievalPlannerService()
        self.engine = AnalysisEngine()

        self.mock_chunk_fee = {
            "document": (
                "Bankalar tarafından finansal tüketicilere sunulan vadesiz mevduat hesaplarından "
                "hesap işletim ücreti veya benzeri adlar altında herhangi bir ücret alınamaz."
            ),
            "metadata": {
                "title": "Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği",
                "law_number": "5411",
                "source": "BDDK",
                "source_id": "bddk_official",
                "article_no": "13",
                "paragraph_no": "1",
                "file_hash": "fee_hash_abc",
                "chunk_no": 2,
            },
            "distance": 0.35,
        }

    def test_a_chunk_to_evidence_adapter(self):
        ev = self.engine.chunk_to_evidence(self.mock_chunk_fee)
        assert isinstance(ev, Evidence)
        assert ev.document_identity == "5411"
        assert ev.chunk_reference == "Madde 13 Fıkra 1"
        assert "hesap işletim ücreti" in ev.text_span
        assert ev.metadata.get("source_id") == "bddk_official"

    def test_b_complaint_analysis_with_matching_evidence(self):
        text = (
            "Garanti BBVA hesabımdan onayım olmadan 450 TL hesap işletim ücreti kesilmiştir. "
            "Haksız kesintinin tarafıma iadesini talep ediyorum ve mağdur edildim."
        )
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)
        plan = self.planner.plan(doc, classification)
        ret_plan = self.retrieval_planner.plan_retrieval(plan, doc)

        rag_mock = MockRAGClient([self.mock_chunk_fee])
        result = self.engine.analyze(
            doc=doc,
            classification=classification,
            analysis_plan=plan,
            retrieval_plan=ret_plan,
            rag_client=rag_mock,
        )

        assert isinstance(result, AnalysisResult)
        assert result.status == "completed"
        assert len(result.findings) >= 1
        finding = result.findings[0]
        assert "ücret" in finding.issue.lower() or "kesinti" in finding.issue.lower()
        assert len(finding.evidence) == 1
        assert len(finding.citations) == 1
        assert finding.risk_level in ["Yüksek", "Orta", "Düşük"]
        assert "Garanti BBVA" in finding.inspector_action
        assert "Madde 13 Fıkra 1" in finding.regulation_reference
        assert result.reviewer_status == "Bekliyor"
        assert len(result.inspector_summary) > 50

    def test_c_empty_retrieval_produces_zero_findings_safely(self):
        text = "Müşteri kartımdan 500 TL kesinti yapıldı şikayetçiyim."
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)
        plan = self.planner.plan(doc, classification)
        ret_plan = self.retrieval_planner.plan_retrieval(plan, doc)

        rag_empty = MockRAGClient([])  # Zero chunks returned
        result = self.engine.analyze(
            doc=doc,
            classification=classification,
            analysis_plan=plan,
            retrieval_plan=ret_plan,
            rag_client=rag_empty,
        )

        assert len(result.findings) == 0  # Strictly NO fabricated findings
        assert len(result.unsupported_claims) >= 1
        assert any("doğrulanabilir kanıt parçası bulunamadı" in w for w in result.warnings)
        assert result.status == "completed_insufficient_evidence"

    def test_d_analysis_result_serialization_and_deserialization(self):
        text = "Ziraat Bankası hesabımdan 100 TL hesap işletim ücreti kesilmiştir şikayetçiyim."
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)
        plan = self.planner.plan(doc, classification)
        ret_plan = self.retrieval_planner.plan_retrieval(plan, doc)

        rag_mock = MockRAGClient([self.mock_chunk_fee])
        result = self.engine.analyze(
            doc=doc,
            classification=classification,
            analysis_plan=plan,
            retrieval_plan=ret_plan,
            rag_client=rag_mock,
        )

        json_str = result.model_dump_json()
        assert "fee_hash_abc" in json_str

        restored = AnalysisResult.model_validate_json(json_str)
        assert restored.status == result.status
        assert len(restored.findings) == len(result.findings)
        assert restored.document.title == result.document.title
