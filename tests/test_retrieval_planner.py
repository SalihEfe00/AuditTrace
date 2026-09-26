# -*- coding: utf-8 -*-
"""
Tests for RetrievalPlannerService & LLM Structured Support (FAZ 19.9 Stage 2).
"""
import pytest

from mufettis.analysis.classifier import DocumentClassifierService
from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.llm_support import (
    execute_structured_llm_call,
    extract_json_from_llm_response,
)
from mufettis.analysis.models import RetrievalPlan
from mufettis.analysis.planner import AnalysisPlannerService
from mufettis.analysis.retrieval_planner import RetrievalPlannerService
from mufettis.analysis.validator import assert_valid_retrieval_plan
from tests.data_integrity import data_integrity_problems


class TestRetrievalPlanner:
    """Verifies retrieval planning and LLM integration safety."""

    def setup_method(self):
        self.doc_service = DocumentUnderstandingService()
        self.classifier = DocumentClassifierService()
        self.planner = AnalysisPlannerService()
        self.retrieval_planner = RetrievalPlannerService()

    def test_a_generate_valid_retrieval_plan(self):
        text = (
            "Garanti BBVA hesabımdan bilgim dışında 3.000 TL nakit çekim yapılmıştır. "
            "Şüpheli işlem bildirimi yapmama rağmen işlem iptal edilmedi. Şikayetçiyim."
        )
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)
        analysis_plan = self.planner.plan(doc, classification)

        retrieval_plan = self.retrieval_planner.plan_retrieval(analysis_plan, doc)

        assert isinstance(retrieval_plan, RetrievalPlan)
        assert len(retrieval_plan.queries) >= 1
        assert "bddk_official" in retrieval_plan.source_scope
        assert retrieval_plan.max_evidence_items >= 3
        assert "source" in retrieval_plan.citation_requirements
        assert_valid_retrieval_plan(retrieval_plan)

    def test_b_retrieval_plan_contains_relevant_terms(self):
        text = "Akbank şubesinde konut kredisi erken kapatma cezası haksız yere tahsil edildi."
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)
        analysis_plan = self.planner.plan(doc, classification)
        retrieval_plan = self.retrieval_planner.plan_retrieval(analysis_plan, doc)

        combined_queries = " ".join(retrieval_plan.queries).lower()
        assert "kredi" in combined_queries or "tüketici" in combined_queries or "5411" in combined_queries

    def test_c_malformed_llm_json_handled_safely(self):
        # 1. Valid markdown code fence JSON
        valid_fence = "Here is the response:\n```json\n{\"decision\": \"ok\", \"code\": 200}\n```"
        parsed = extract_json_from_llm_response(valid_fence)
        assert parsed == {"decision": "ok", "code": 200}

        # 2. Corrupted JSON with syntax error
        corrupted = "```json\n{\"decision\": \"incomplete\", \n```"
        parsed_corrupt = extract_json_from_llm_response(corrupted)
        assert parsed_corrupt is None

        # 3. Garbage non-JSON string
        garbage = "Just plain text without any JSON structure."
        assert extract_json_from_llm_response(garbage) is None

    def test_d_execute_structured_llm_call_with_fallback(self):
        # Successful call
        def mock_good_generator(prompt: str) -> str:
            return '{"status": "analyzed", "items": [1, 2]}'

        res = execute_structured_llm_call(
            prompt="Analyze this",
            generate_fn=mock_good_generator,
            fallback_factory=lambda: {"status": "fallback"},
        )
        assert res == {"status": "analyzed", "items": [1, 2]}

        # Error / Malformed call returns fallback without crashing
        def mock_bad_generator(prompt: str) -> str:
            return "[LLM HATASI] Connection refused"

        res_fallback = execute_structured_llm_call(
            prompt="Analyze this",
            generate_fn=mock_bad_generator,
            fallback_factory=lambda: {"status": "fallback"},
        )
        assert res_fallback == {"status": "fallback"}

    def test_e_test_isolation_production_files_untouched(self):
        # Production verisinin değişmediği conftest tarafından oturum düzeyinde doğrulanır.
        assert data_integrity_problems() == []

    def test_f_retrieval_plan_format_compatible_with_rag_query(self):
        text = "Müşteri kartından habersiz para çekilmiş şikayetçiyim."
        doc = self.doc_service.analyze(text)
        classification = self.classifier.classify(doc)
        analysis_plan = self.planner.plan(doc, classification)
        retrieval_plan = self.retrieval_planner.plan_retrieval(analysis_plan, doc)

        # Every query in retrieval_plan must be a non-empty string ready for LocalRAG.query
        assert len(retrieval_plan.queries) > 0
        for q in retrieval_plan.queries:
            assert isinstance(q, str)
            assert len(q.strip()) > 0

