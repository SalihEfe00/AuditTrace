# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Analysis Intelligence Core
Validation Layer.

Provides rigorous semantic and structural validation for Analysis Intelligence
contracts, rejecting malformed, inconsistent, or empty inputs.
"""
from __future__ import annotations

from typing import List

from mufettis.analysis.models import (
    AnalysisParameters,
    AnalysisPlan,
    AnalysisType,
    Citation,
    DocumentClassification,
    DocumentType,
    DocumentUnderstanding,
    Evidence,
    Finding,
    RetrievalPlan,
)


class AnalysisValidationError(ValueError):
    """Raised when an Analysis Intelligence contract fails semantic validation."""

    def __init__(self, message: str, errors: List[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]


def validate_document_understanding(doc: DocumentUnderstanding) -> List[str]:
    """Validates structural and semantic consistency of DocumentUnderstanding."""
    errors: List[str] = []
    if not doc.text or not doc.text.strip():
        errors.append("DocumentUnderstanding: raw text cannot be empty or whitespace.")
    if not doc.normalized_text or not doc.normalized_text.strip():
        errors.append("DocumentUnderstanding: normalized_text cannot be empty.")
    if doc.document_type not in {t.value for t in DocumentType}:
        errors.append(f"DocumentUnderstanding: unknown document_type '{doc.document_type}'.")

    for i, entity in enumerate(doc.entities):
        if not entity.entity_type or not entity.text:
            errors.append(f"DocumentUnderstanding.entities[{i}]: entity_type and text are required.")

    for i, claim in enumerate(doc.claims):
        if not claim.claim_id or not claim.statement:
            errors.append(f"DocumentUnderstanding.claims[{i}]: claim_id and statement are required.")

    for i, event in enumerate(doc.events):
        if not event.event_id or not event.description:
            errors.append(f"DocumentUnderstanding.events[{i}]: event_id and description are required.")

    return errors


def validate_document_classification(classification: DocumentClassification) -> List[str]:
    """Validates DocumentClassification confidence bounds and signals."""
    errors: List[str] = []
    if classification.document_type not in {t.value for t in DocumentType}:
        errors.append(f"DocumentClassification: invalid document_type '{classification.document_type}'.")
    if not (0.0 <= classification.confidence <= 1.0):
        errors.append(f"DocumentClassification: confidence {classification.confidence} out of range [0.0, 1.0].")
    return errors


def validate_analysis_parameters(params: AnalysisParameters) -> List[str]:
    """Validates AnalysisParameters consistency."""
    errors: List[str] = []
    if params.analysis_type not in {a.value for a in AnalysisType}:
        errors.append(f"AnalysisParameters: invalid analysis_type '{params.analysis_type}'.")
    if not params.objective or not params.objective.strip():
        errors.append("AnalysisParameters: objective cannot be empty.")
    return errors


def validate_analysis_plan(plan: AnalysisPlan) -> List[str]:
    """Validates AnalysisPlan against empty plans or inconsistent combinations."""
    errors: List[str] = []
    if plan.analysis_type not in {a.value for a in AnalysisType}:
        errors.append(f"AnalysisPlan: invalid analysis_type '{plan.analysis_type}'.")
    if not plan.objective or not plan.objective.strip():
        errors.append("AnalysisPlan: objective cannot be empty.")

    # Consistency: plan.analysis_type must match parameters.analysis_type
    if plan.analysis_type != plan.parameters.analysis_type:
        errors.append(
            f"AnalysisPlan: analysis_type mismatch between plan ('{plan.analysis_type}') "
            f"and parameters ('{plan.parameters.analysis_type}')."
        )

    errors.extend(validate_analysis_parameters(plan.parameters))

    # Retrieval scope check
    if not plan.retrieval_scope.generated_queries and not plan.retrieval_scope.keywords:
        errors.append("AnalysisPlan: retrieval_scope must have at least one generated_query or keyword.")

    return errors


def validate_retrieval_plan(plan: RetrievalPlan) -> List[str]:
    """Validates that a RetrievalPlan is actionable for the RAG engine."""
    errors: List[str] = []
    non_empty_queries = [q for q in plan.queries if q and q.strip()]
    if not non_empty_queries:
        errors.append("RetrievalPlan: queries list must contain at least one non-empty string.")
    if plan.max_evidence_items < 1:
        errors.append(f"RetrievalPlan: max_evidence_items must be >= 1, got {plan.max_evidence_items}.")
    return errors


def validate_evidence(evidence: Evidence) -> List[str]:
    """Validates that an Evidence item points to valid text and provenance."""
    errors: List[str] = []
    if not evidence.source_document or not evidence.source_document.strip():
        errors.append("Evidence: source_document cannot be empty.")
    if not evidence.document_identity or not evidence.document_identity.strip():
        errors.append("Evidence: document_identity cannot be empty.")
    if not evidence.chunk_reference or not evidence.chunk_reference.strip():
        errors.append("Evidence: chunk_reference cannot be empty.")
    if not evidence.text_span or not evidence.text_span.strip():
        errors.append("Evidence: text_span cannot be empty.")
    return errors


def validate_citation(citation: Citation) -> List[str]:
    """Validates Citation references and text spans."""
    errors: List[str] = []
    if not citation.source or not citation.source.strip():
        errors.append("Citation: source cannot be empty.")
    if not citation.document_identity or not citation.document_identity.strip():
        errors.append("Citation: document_identity cannot be empty.")
    if not citation.reference or not citation.reference.strip():
        errors.append("Citation: reference cannot be empty.")
    if not citation.text_span or not citation.text_span.strip():
        errors.append("Citation: text_span cannot be empty.")
    return errors


def validate_finding(finding: Finding) -> List[str]:
    """Validates Finding integrity."""
    errors: List[str] = []
    if not finding.issue or not finding.issue.strip():
        errors.append("Finding: issue cannot be empty.")
    if not finding.regulation_reference or not finding.regulation_reference.strip():
        errors.append("Finding: regulation_reference cannot be empty.")
    if not finding.explanation or not finding.explanation.strip():
        errors.append("Finding: explanation cannot be empty.")
    if not finding.inspector_action or not finding.inspector_action.strip():
        errors.append("Finding: inspector_action cannot be empty.")
    for i, ev in enumerate(finding.evidence):
        ev_errors = validate_evidence(ev)
        for err in ev_errors:
            errors.append(f"Finding.evidence[{i}]: {err}")
    return errors


def assert_valid_analysis_plan(plan: AnalysisPlan) -> None:
    """Raises AnalysisValidationError if plan fails validation."""
    errors = validate_analysis_plan(plan)
    if errors:
        raise AnalysisValidationError(
            f"AnalysisPlan validation failed with {len(errors)} error(s): {'; '.join(errors)}",
            errors=errors,
        )


def assert_valid_retrieval_plan(plan: RetrievalPlan) -> None:
    """Raises AnalysisValidationError if retrieval plan fails validation."""
    errors = validate_retrieval_plan(plan)
    if errors:
        raise AnalysisValidationError(
            f"RetrievalPlan validation failed with {len(errors)} error(s): {'; '.join(errors)}",
            errors=errors,
        )
