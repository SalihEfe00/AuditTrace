# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Analysis Intelligence Core
Models & Data Contracts.

Defines type-safe, validated Pydantic models for Document Understanding,
Classification, Analysis Planning, Retrieval Planning, Evidence, Citation,
and Findings.
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentType(str, Enum):
    COMPLAINT = "complaint"
    INTERNAL_CORRESPONDENCE = "internal_correspondence"
    UNKNOWN = "unknown"


class AnalysisType(str, Enum):
    REGULATORY_RELEVANCE = "regulatory_relevance"
    COMPLIANCE = "compliance"
    UNKNOWN = "unknown"


class DocumentEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(..., min_length=1, description="Entity type: institution, person, amount, date, unit, etc.")
    text: str = Field(..., min_length=1, description="Original text fragment in the document")
    normalized_value: Any = Field(default=None, description="Normalized or parsed value")


class ExtractedField(BaseModel):
    """Belgeden çıkarılan yapılandırılmış alan (analysis/field_extraction.py)."""
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., min_length=1, description="amount, date, rate, iban, tckn, vkn, account, person, "
                                                     "signature, collateral, limit")
    text: str = Field(..., min_length=1, description="Belgedeki yazımı")
    value: str = Field(..., min_length=1, description="Normalleştirilmiş değer (ör. '1250000.00 TRY', '2023-04-05')")
    start: int = Field(default=0, ge=0, description="Metindeki başlangıç konumu")
    end: int = Field(default=0, ge=0, description="Metindeki bitiş konumu")
    label: str = Field(default="", description="Değeri bulduran etiket / sütun / kural")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Türe özgü ek bilgi (ör. kişi rolü)")


class DocumentClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(..., min_length=1, description="Unique claim identifier")
    statement: str = Field(..., min_length=1, description="Statement or grievance made in document")
    subject: str = Field(..., min_length=1, description="Topic or subject of the claim")
    involved_entities: List[str] = Field(default_factory=list, description="Associated entity names or IDs")


class DocumentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., min_length=1, description="Unique event identifier")
    description: str = Field(..., min_length=1, description="Description of the event or incident")
    date: Optional[str] = Field(default=None, description="Event occurrence date if known")
    involved_entities: List[str] = Field(default_factory=list, description="Entities involved in the event")


class DocumentUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, description="Raw input text")
    normalized_text: str = Field(..., min_length=1, description="Normalized text with encoding/spacing repaired")
    title: str = Field(default="", description="Detected or inferred document title")
    document_type: str = Field(default=DocumentType.UNKNOWN.value, description="Preliminary document type")
    source: str = Field(default="", description="Source origin, filename, or channel")
    date: Optional[str] = Field(default=None, description="Document date if extracted")
    entities: List[DocumentEntity] = Field(default_factory=list, description="Extracted entities")
    fields: List[ExtractedField] = Field(default_factory=list, description="Yapılandırılmış alanlar (tutar, tarih, "
                                                                          "kimlik no, kişi-rol, imza …)")
    subjects: List[str] = Field(default_factory=list, description="Identified topical subjects")
    claims: List[DocumentClaim] = Field(default_factory=list, description="Extracted claims or allegations")
    events: List[DocumentEvent] = Field(default_factory=list, description="Extracted chronological events")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Forwarded metadata")


class DocumentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str = Field(..., min_length=1, description="Classified document type (complaint, internal_correspondence, unknown)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Heuristic confidence score (not calibrated probability)")
    reasons: List[str] = Field(default_factory=list, description="Explanations for the classification decision")
    signals: List[str] = Field(default_factory=list, description="Detected concrete cues or markers")
    branch_type: str = Field(default="bilinmiyor", description="Şube belge türü (analysis/document_taxonomy kodu)")
    branch_signals: List[str] = Field(default_factory=list, description="Şube belge türü kararının işaretleri")

    @field_validator("branch_type")
    @classmethod
    def validate_branch_type(cls, v: str) -> str:
        from mufettis.analysis.document_taxonomy import CODES

        if v not in CODES:
            raise ValueError(f"Invalid branch_type '{v}'")
        return v

    @field_validator("document_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        valid = {t.value for t in DocumentType}
        if v not in valid:
            raise ValueError(f"Invalid document_type '{v}'. Expected one of {valid}")
        return v


class AnalysisParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_type: str = Field(..., min_length=1, description="Target analysis type")
    objective: str = Field(..., min_length=1, description="Core objective of the planned analysis")
    scope: List[str] = Field(default_factory=list, description="In-scope legal or operational areas")
    target_entities: List[str] = Field(default_factory=list, description="Institutions or parties to examine")
    constraints: Dict[str, Any] = Field(default_factory=dict, description="Analysis bounds or thresholds")
    requested_outputs: List[str] = Field(default_factory=list, description="Requested output artifact types")

    @field_validator("analysis_type")
    @classmethod
    def validate_analysis_type(cls, v: str) -> str:
        valid = {a.value for a in AnalysisType}
        if v not in valid:
            raise ValueError(f"Invalid analysis_type '{v}'. Expected one of {valid}")
        return v


class RetrievalScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_domains: List[str] = Field(default_factory=list, description="Target regulatory domains")
    explicit_laws: List[str] = Field(default_factory=list, description="Target law or regulation identifiers")
    keywords: List[str] = Field(default_factory=list, description="Focus keywords for retrieval")
    generated_queries: List[str] = Field(default_factory=list, description="Draft queries for evidence retrieval")
    top_k_per_query: int = Field(default=3, ge=1, le=50, description="Chunk count to request per query")
    document_query_count: int = Field(
        default=0, ge=0,
        description="Leading generated_queries taken from the document's own text (title, claims)",
    )


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_type: str = Field(..., min_length=1, description="Primary analysis type")
    objective: str = Field(..., min_length=1, description="Clear description of what will be analyzed")
    parameters: AnalysisParameters = Field(..., description="Structured analysis parameters")
    retrieval_scope: RetrievalScope = Field(..., description="Target retrieval parameters")
    required_evidence: List[str] = Field(default_factory=list, description="Types of evidence required")
    output_schema: Dict[str, Any] = Field(default_factory=dict, description="Expected schema structure for outputs")

    @field_validator("analysis_type")
    @classmethod
    def validate_plan_type(cls, v: str) -> str:
        valid = {a.value for a in AnalysisType}
        if v not in valid:
            raise ValueError(f"Invalid analysis_type '{v}'. Expected one of {valid}")
        return v


class RetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: List[str] = Field(..., min_length=1, description="Target queries to execute against RAG")
    filters: Dict[str, Any] = Field(default_factory=dict, description="Metadata filters for RAG")
    source_scope: List[str] = Field(default_factory=list, description="Target sources, e.g. bddk_official")
    evidence_requirements: List[str] = Field(default_factory=list, description="Evidence criteria")
    max_evidence_items: int = Field(default=5, ge=1, le=100, description="Maximum evidence items to return")
    query_depths: Dict[str, int] = Field(
        default_factory=dict,
        description="Per-query cap on evidence items contributed to the pool (query text -> count); "
                    "queries not listed share the pool without a cap",
    )
    citation_requirements: List[str] = Field(default_factory=list, description="Required citation fields")

    @field_validator("queries")
    @classmethod
    def validate_non_empty_queries(cls, v: List[str]) -> List[str]:
        cleaned = [q.strip() for q in v if q and q.strip()]
        if not cleaned:
            raise ValueError("RetrievalPlan must contain at least one non-empty query")
        return cleaned


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document: str = Field(..., min_length=1, description="Title or name of source document")
    document_identity: str = Field(..., min_length=1, description="Canonical document ID or file_hash")
    chunk_reference: str = Field(..., min_length=1, description="Chunk pointer, e.g. Madde 4 Fıkra 1")
    text_span: str = Field(..., min_length=1, description="Exact text snippet proving the point")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Provenance and chunk metadata")


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., min_length=1, description="Source registry or official publisher")
    document_identity: str = Field(..., min_length=1, description="Document identifier or law number")
    reference: str = Field(..., min_length=1, description="Hierarchical legal reference")
    text_span: str = Field(..., min_length=1, description="Verbatim cited text span")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Citation metadata")


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str = Field(..., min_length=1, description="Issue or risk identified")
    evidence: List[Evidence] = Field(default_factory=list, description="Supporting evidence items")
    citations: List[Citation] = Field(default_factory=list, description="Verified citations associated with this finding")
    regulation_reference: str = Field(..., min_length=1, description="Relevant regulation or article reference")
    explanation: str = Field(..., min_length=1, description="Objective explanation of the finding")
    inspector_action: str = Field(..., min_length=1, description="Recommended concrete step for inspector")
    claim_reference_id: Optional[str] = Field(default=None, description="Linked document claim ID")
    model_interpretation: Optional[str] = Field(default=None, description="Distinct model analytical interpretation")
    risk_level: Optional[str] = Field(default=None, description="Risk level (Yüksek, Orta, Düşük, Belirsiz)")
    match_confidence: Optional[str] = Field(
        default=None,
        description="Dayanak eşleşmesinin güveni: 'yüksek' / 'düşük' (yeniden sıralayıcı puanına göre); "
                    "None: ölçülmedi (model yok, ayarsız model veya dil modeli kararı)",
    )


class CitedRegulation(BaseModel):
    """Belgenin kendisinin atıf yaptığı düzenleme ve yerel veritabanındaki durumu
    (in_corpus / not_in_corpus / internal / not_checked; bkz. analysis.cited_references)."""
    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, description="Belgedeki atıf")
    status: str = Field(..., description="in_corpus, not_in_corpus, internal, not_checked")
    law_number: str = Field(default="", description="Kanun numarası (varsa)")
    article: str = Field(default="", description="Atıf yapılan madde (varsa)")
    matched_title: str = Field(default="", description="Veritabanında eşleşen belgenin adı")


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: DocumentUnderstanding = Field(..., description="Processed input document understanding")
    classification: DocumentClassification = Field(..., description="Document classification result")
    analysis_plan: AnalysisPlan = Field(..., description="Formulated analysis plan")
    retrieval_plan: RetrievalPlan = Field(..., description="Actionable retrieval plan")
    findings: List[Finding] = Field(default_factory=list, description="Validated substantive findings")
    unsupported_claims: List[DocumentClaim] = Field(default_factory=list, description="Claims without adequate regulatory evidence")
    cited_regulations: List[CitedRegulation] = Field(default_factory=list, description="Regulations the document itself cites and whether they are in the local corpus")
    warnings: List[str] = Field(default_factory=list, description="Pipeline warnings or data limitations")
    status: str = Field(default="completed", description="Overall execution status")
    inspector_summary: str = Field(default="", description="Structured textual summary for the inspector")
    reviewer_status: Optional[str] = Field(default=None, description="Human reviewer state: Bekliyor, Doğrulandı, etc.")
    reviewer_note: Optional[str] = Field(default=None, description="Human reviewer notes")
    reviewer_name: Optional[str] = Field(default=None, description="Human reviewer name or registry number")
    reviewed_at: Optional[str] = Field(default=None, description="Timestamp of the latest human review")
    analysis_id: str = Field(default_factory=lambda: uuid.uuid4().hex, description="Unique identifier for audit traceability")

