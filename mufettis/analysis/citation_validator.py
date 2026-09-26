# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Stage 3 Analysis Intelligence
Citation & Evidence Validator.

Ensures that every substantive Finding produced by the Analysis Engine is
truthfully grounded in real retrieved Evidence. Prevents hallucinated legal
assertions, citation drift, cross-document mismatches, and ungrounded claims.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from mufettis.analysis.models import Citation, Evidence, Finding
from mufettis.analysis.turkish_text import turkish_lower
from mufettis.extraction.text_repair import normalize_legal_whitespace, repair_mojibake


@dataclass
class ValidationReport:
    """Report detailing evidence and citation validation outcome."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    verified_citations: int = 0
    unsupported_reasons: List[str] = field(default_factory=list)


class CitationValidator:
    """Validates grounding, text span presence, and identity consistency for Findings."""

    @staticmethod
    def normalize_for_comparison(text: str) -> str:
        """Normalizes text for robust semantic comparison without losing identity."""
        if not text:
            return ""
        repaired = repair_mojibake(str(text))
        cleaned = turkish_lower(normalize_legal_whitespace(repaired))
        # Collapse multiple spaces and remove non-alphanumeric noise for substring matching
        cleaned = re.sub(r"[\"“”'’`.,;:!?()\[\]{}/\\]+", " ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def validate_finding(
        self,
        finding: Finding,
        retrieved_evidence: Optional[List[Evidence]] = None,
    ) -> ValidationReport:
        """Validates that a finding is grounded in evidence and its citations are truthful."""
        errors: List[str] = []
        warnings: List[str] = []
        unsupported: List[str] = []
        verified_citations = 0

        # 1. Unsupported Finding Guard: Must have at least one Evidence item
        if not finding.evidence:
            msg = f"Finding '{finding.issue}' has no supporting evidence attached."
            errors.append(msg)
            unsupported.append(msg)
            return ValidationReport(
                is_valid=False,
                errors=errors,
                warnings=warnings,
                verified_citations=0,
                unsupported_reasons=unsupported,
            )

        # 2. Check each Evidence item integrity
        evidence_identities = set()
        evidence_spans_normalized = []

        for idx, ev in enumerate(finding.evidence):
            if not ev.document_identity or not ev.document_identity.strip():
                errors.append(f"Evidence[{idx}] has missing or empty document_identity.")
            else:
                evidence_identities.add(ev.document_identity.strip().lower())

            if not ev.chunk_reference or not ev.chunk_reference.strip():
                errors.append(f"Evidence[{idx}] has missing or empty chunk_reference.")

            if not ev.text_span or not ev.text_span.strip():
                errors.append(f"Evidence[{idx}] has missing or empty text_span.")
            else:
                evidence_spans_normalized.append(self.normalize_for_comparison(ev.text_span))

        # 3. If a pool of retrieved evidence is provided, verify evidence came from RAG
        if retrieved_evidence is not None:
            pool_identities = {e.document_identity.strip().lower() for e in retrieved_evidence}
            pool_spans = [self.normalize_for_comparison(e.text_span) for e in retrieved_evidence]

            for idx, ev in enumerate(finding.evidence):
                ev_id = ev.document_identity.strip().lower()
                if ev_id not in pool_identities:
                    errors.append(
                        f"Evidence[{idx}] document_identity '{ev.document_identity}' was not found in retrieved RAG evidence pool."
                    )
                ev_norm = self.normalize_for_comparison(ev.text_span)
                if not any(ev_norm in p or p in ev_norm for p in pool_spans):
                    warnings.append(
                        f"Evidence[{idx}] text span may not be identical to any retrieved RAG chunk."
                    )

        # 4. Check Citations and Cross-Document Mismatch
        for idx, cit in enumerate(finding.citations):
            if not cit.source or not cit.source.strip():
                errors.append(f"Citation[{idx}] has empty source.")
            if not cit.document_identity or not cit.document_identity.strip():
                errors.append(f"Citation[{idx}] has empty document_identity.")
            if not cit.reference or not cit.reference.strip():
                errors.append(f"Citation[{idx}] has empty reference.")
            if not cit.text_span or not cit.text_span.strip():
                errors.append(f"Citation[{idx}] has empty text_span.")

            cit_id = cit.document_identity.strip().lower() if cit.document_identity else ""

            # Cross-document mismatch guard: Citation identity must match an attached evidence identity
            if cit_id and evidence_identities and cit_id not in evidence_identities:
                err_msg = (
                    f"Cross-document mismatch in Citation[{idx}]: citation identity '{cit.document_identity}' "
                    f"does not match any attached evidence identity ({', '.join(evidence_identities)})."
                )
                errors.append(err_msg)
                unsupported.append(err_msg)
                continue

            # Grounding check: Citation text_span must exist inside evidence text_span
            if cit.text_span:
                cit_span_norm = self.normalize_for_comparison(cit.text_span)
                is_grounded = any(cit_span_norm in ev_span for ev_span in evidence_spans_normalized)
                if not is_grounded:
                    # Also check reverse in case citation is longer or evidence is a snippet
                    is_grounded = any(ev_span in cit_span_norm for ev_span in evidence_spans_normalized if len(ev_span) > 30)

                if is_grounded:
                    verified_citations += 1
                else:
                    err_msg = (
                        f"Citation[{idx}] text span is not grounded in the attached evidence text: "
                        f"'{cit.text_span[:60]}...'"
                    )
                    errors.append(err_msg)
                    unsupported.append(err_msg)

        # 5. Regulation Reference Grounding Check
        reg_ref = finding.regulation_reference.strip().lower()
        if not reg_ref:
            errors.append("Finding has empty regulation_reference.")
        else:
            # Check if at least one evidence identity or title keyword appears in regulation_reference
            matched_ref = any(ident in reg_ref for ident in evidence_identities)
            if not matched_ref:
                # Check document title keywords (e.g. 'kanun', 'yönetmelik')
                ev_titles = [e.source_document.lower() for e in finding.evidence]
                matched_ref = any(any(word in reg_ref for word in title.split() if len(word) > 4) for title in ev_titles)
            if not matched_ref:
                warnings.append(
                    f"Finding regulation_reference '{finding.regulation_reference}' does not clearly reference attached evidence."
                )

        is_valid = len(errors) == 0
        return ValidationReport(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            verified_citations=verified_citations,
            unsupported_reasons=unsupported,
        )
