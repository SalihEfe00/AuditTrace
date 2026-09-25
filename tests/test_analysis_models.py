# -*- coding: utf-8 -*-
"""
Tests for Analysis Intelligence Data Contracts & Validation (FAZ 19.9 Stage 2).
"""
import json
import pytest
from pydantic import ValidationError

from mufettis.analysis.models import (
    AnalysisParameters,
    AnalysisPlan,
    AnalysisType,
    Citation,
    DocumentClaim,
    DocumentClassification,
    DocumentEntity,
    DocumentEvent,
    DocumentType,
    DocumentUnderstanding,
    Evidence,
    Finding,
    RetrievalPlan,
    RetrievalScope,
)
from mufettis.analysis.validator import (
    AnalysisValidationError,
    assert_valid_analysis_plan,
    assert_valid_retrieval_plan,
    validate_analysis_parameters,
    validate_analysis_plan,
    validate_citation,
    validate_document_classification,
    validate_document_understanding,
    validate_evidence,
    validate_finding,
    validate_retrieval_plan,
)


class TestAnalysisModels:
    """Tests Pydantic validation, serialization, and deserialization for all contracts."""

    def test_a_valid_document_understanding(self):
        doc = DocumentUnderstanding(
            text="Banka hesabımdan bilgim dışında 500 TL kesilmiştir.",
            normalized_text="Banka hesabımdan bilgim dışında 500 TL kesilmiştir.",
            title="Şikâyet Başlığı",
            document_type=DocumentType.COMPLAINT.value,
            source="musteri_dilekce.txt",
            date="2026-03-15",
            entities=[
                DocumentEntity(entity_type="amount", text="500 TL", normalized_value="500 TL"),
                DocumentEntity(entity_type="institution", text="Ziraat Bankası", normalized_value="Ziraat Bankası"),
            ],
            subjects=["ücret_ve_komisyon"],
            claims=[
                DocumentClaim(
                    claim_id="claim_1",
                    statement="bilgim dışında 500 TL kesilmiştir",
                    subject="ücret_ve_komisyon",
                    involved_entities=["500 TL"],
                )
            ],
            events=[
                DocumentEvent(
                    event_id="event_1",
                    description="Kesinti gerçekleşti",
                    date="2026-03-15",
                    involved_entities=["500 TL"],
                )
            ],
            metadata={"origin": "web_form"},
        )
        assert doc.text.startswith("Banka")
        assert len(doc.entities) == 2
        assert len(doc.claims) == 1
        assert len(doc.events) == 1
        errors = validate_document_understanding(doc)
        assert errors == []

    def test_b_invalid_document_understanding_rejected(self):
        # Empty text must be rejected
        with pytest.raises(ValidationError):
            DocumentUnderstanding(text="", normalized_text="valid")

        # Invalid document type
        doc_bad_type = DocumentUnderstanding(
            text="metin",
            normalized_text="metin",
            document_type="invalid_type",
        )
        errors = validate_document_understanding(doc_bad_type)
        assert any("unknown document_type" in e for e in errors)

    def test_c_serialization_and_deserialization(self):
        doc = DocumentUnderstanding(
            text="Ham metin",
            normalized_text="Normalize metin",
            title="Test",
            document_type=DocumentType.INTERNAL_CORRESPONDENCE.value,
            entities=[DocumentEntity(entity_type="unit", text="Teftiş Kurulu")],
        )
        json_str = doc.model_dump_json()
        assert "Teftiş Kurulu" in json_str

        reconstructed = DocumentUnderstanding.model_validate_json(json_str)
        assert reconstructed.title == "Test"
        assert reconstructed.entities[0].text == "Teftiş Kurulu"
        assert reconstructed.document_type == DocumentType.INTERNAL_CORRESPONDENCE.value

    def test_d_document_classification_validation(self):
        valid = DocumentClassification(
            document_type=DocumentType.COMPLAINT.value,
            confidence=0.88,
            reasons=["Grievance language detected"],
            signals=["complaint_keyword:şikayet"],
        )
        assert validate_document_classification(valid) == []

        # Out of bounds confidence rejected
        with pytest.raises(ValidationError):
            DocumentClassification(
                document_type=DocumentType.COMPLAINT.value,
                confidence=1.5,
            )

        # Invalid document type rejected
        with pytest.raises(ValidationError):
            DocumentClassification(
                document_type="unrecognized",
                confidence=0.5,
            )

    def test_e_analysis_parameters_validation(self):
        valid = AnalysisParameters(
            analysis_type=AnalysisType.REGULATORY_RELEVANCE.value,
            objective="Mevzuat uyumu incelemesi",
            scope=["tuketici_haklari"],
            target_entities=["Banka A"],
        )
        assert validate_analysis_parameters(valid) == []

        # Invalid analysis type rejected
        with pytest.raises(ValidationError):
            AnalysisParameters(
                analysis_type="bad_analysis",
                objective="objective",
            )

    def test_f_analysis_plan_validation(self):
        params = AnalysisParameters(
            analysis_type=AnalysisType.REGULATORY_RELEVANCE.value,
            objective="Uyum incelemesi",
        )
        scope = RetrievalScope(
            target_domains=["finansal_tuketici"],
            explicit_laws=["5411"],
            keywords=["ücret"],
            generated_queries=["Hesap işletim ücreti mevzuatı"],
        )
        plan = AnalysisPlan(
            analysis_type=AnalysisType.REGULATORY_RELEVANCE.value,
            objective="Uyum incelemesi",
            parameters=params,
            retrieval_scope=scope,
            required_evidence=["prohibition_rule"],
            output_schema={"type": "finding_list"},
        )
        assert validate_analysis_plan(plan) == []
        assert_valid_analysis_plan(plan)

        # Mismatched analysis_type between plan and parameters rejected
        bad_params = AnalysisParameters(
            analysis_type=AnalysisType.COMPLIANCE.value,
            objective="Uyum incelemesi",
        )
        mismatched_plan = AnalysisPlan(
            analysis_type=AnalysisType.REGULATORY_RELEVANCE.value,
            objective="Uyum incelemesi",
            parameters=bad_params,
            retrieval_scope=scope,
        )
        with pytest.raises(AnalysisValidationError):
            assert_valid_analysis_plan(mismatched_plan)

    def test_g_retrieval_plan_validation(self):
        valid_rp = RetrievalPlan(
            queries=["5411 sayılı Kanun madde 4", "Banka ücret ve komisyonları"],
            filters={"law_number": "5411"},
            source_scope=["bddk_official"],
            evidence_requirements=["statutory_rule"],
            max_evidence_items=5,
            citation_requirements=["source", "reference", "text_span"],
        )
        assert validate_retrieval_plan(valid_rp) == []
        assert_valid_retrieval_plan(valid_rp)

        # Empty queries list rejected by Pydantic validator
        with pytest.raises(ValidationError):
            RetrievalPlan(queries=[])

        # Whitespace-only queries rejected
        with pytest.raises(ValidationError):
            RetrievalPlan(queries=["   ", ""])

    def test_h_evidence_citation_finding_validation(self):
        ev = Evidence(
            source_document="5411 Sayılı Bankacılık Kanunu",
            document_identity="5411",
            chunk_reference="Madde 76 Fıkra 1",
            text_span="Bankalar, müşterilerinin hak ve menfaatlerini korumakla yükümlüdür.",
            metadata={"file_hash": "abc123hash"},
        )
        assert validate_evidence(ev) == []

        cit = Citation(
            source="BDDK",
            document_identity="5411",
            reference="Madde 76",
            text_span="Bankalar, müşterilerinin hak ve menfaatlerini korumakla yükümlüdür.",
        )
        assert validate_citation(cit) == []

        finding = Finding(
            issue="Müşteri hakları ve sözleşme öncesi bilgilendirme eksikliği",
            evidence=[ev],
            regulation_reference="5411 sayılı Bankacılık Kanunu Madde 76",
            explanation="Banka tarafından onay alınmadan kesinti yapılması mevzuata aykırılık riski taşımaktadır.",
            inspector_action="Şubenin müşteri sözleşmesini ve onay formunu temin edip inceleyiniz.",
        )
        assert validate_finding(finding) == []

        # Empty text span in evidence rejected
        with pytest.raises(ValidationError):
            Evidence(
                source_document="Doc",
                document_identity="1",
                chunk_reference="Ref",
                text_span="",
            )
