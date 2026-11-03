# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 9: Self-Healing Infrastructure / Read-Only Repair Layer
Classifier Module.

Deterministic policy engine to classify documents into SAFE, REVIEW, or BLOCKED.
Zero LLM usage; all decisions are strictly rule-based.
"""
from __future__ import annotations

from typing import List

from mufettis.repair.models import (
    ClassificationResult,
    ClassificationType,
    DocumentScanResult,
    IssueCode,
    IssueSeverity,
)

# Issues that definitively block repair candidate eligibility
BLOCKING_CODES = {
    IssueCode.SOURCE_MISSING,
    IssueCode.SOURCE_HASH_MISSING,
    IssueCode.SOURCE_HASH_MISMATCH,
    IssueCode.CACHE_HASH_MISMATCH,
    IssueCode.UNKNOWN_INTEGRITY_STATE,
}

# Issues that require human or algorithmic review before repair
REVIEW_CODES = {
    IssueCode.QUALITY_GLUED_TEXT,
    IssueCode.QUALITY_MOJIBAKE,
    IssueCode.QUALITY_REPLACEMENT_CHAR,
    IssueCode.QUALITY_VERY_LONG_TOKEN,
    IssueCode.QUALITY_LOW_SCORE,
    IssueCode.STRUCTURE_DRIFT,
    IssueCode.STRUCTURE_EMPTY_CHUNK,
    IssueCode.STRUCTURE_DUPLICATE_CHUNK,
    IssueCode.STRUCTURE_METADATA_MISSING,
    IssueCode.CHROMA_MISSING,
    IssueCode.CHROMA_STALE_SCHEMA,
    IssueCode.CHROMA_EMPTY_CHUNK,
    IssueCode.CHROMA_GLUED_TEXT,
    IssueCode.CHROMA_MOJIBAKE,
    IssueCode.CHROMA_LOW_SCORE,
    IssueCode.CHROMA_REPLACEMENT_CHAR,
    IssueCode.CACHE_MISSING,
    IssueCode.CACHE_EMPTY,
    IssueCode.CACHE_METADATA_INCOMPLETE,
    IssueCode.RETRIEVAL_NOT_RETRIEVABLE,
    IssueCode.RETRIEVAL_METADATA_INVALID,
}


def classify_document(scan_result: DocumentScanResult) -> ClassificationResult:
    """Scan sonucunu deterministik kurallara göre SAFE, REVIEW veya BLOCKED olarak sınıflandırır.

    Asla LLM kullanmaz; kural tablosu deterministiktir.
    """
    blocking_issues: List[str] = []
    review_issues: List[str] = []
    safe_conditions: List[str] = []
    reasons: List[str] = []

    # 1. Kontrol: Kritik veya engelleyici sorunlar
    for issue in scan_result.issues:
        if issue.severity == IssueSeverity.CRITICAL or issue.code in BLOCKING_CODES:
            blocking_issues.append(f"[{issue.severity}] {issue.code}: {issue.message}")

    if blocking_issues:
        reasons.extend(blocking_issues)
        return ClassificationResult(
            document_id=scan_result.document_id,
            status=ClassificationType.BLOCKED.value,
            reasons=reasons,
            blocking_issues=blocking_issues,
            review_issues=[],
            safe_conditions=[],
        )

    # 2. Kontrol: İnceleme gerektiren kalite ve yapı sorunları
    for issue in scan_result.issues:
        if issue.severity in (IssueSeverity.ERROR, IssueSeverity.WARNING) or issue.code in REVIEW_CODES:
            review_issues.append(f"[{issue.severity}] {issue.code}: {issue.message}")

    if review_issues:
        reasons.extend(review_issues)
        return ClassificationResult(
            document_id=scan_result.document_id,
            status=ClassificationType.REVIEW.value,
            reasons=reasons,
            blocking_issues=[],
            review_issues=review_issues,
            safe_conditions=[],
        )

    # 3. Kontrol: Temiz ve tutarlı durum (SAFE)
    safe_conditions.append("Kaynak hash doğrulanmış ve tutarlı.")
    if scan_result.source_cache_chars > 0:
        safe_conditions.append(f"Source cache mevcut ve metin uzunluğu yeterli ({scan_result.source_cache_chars} karakter).")
    if scan_result.current_metrics.get("score", 0.0) >= 0.88:
        safe_conditions.append(f"Metin kalitesi yüksek (Puan: {scan_result.current_metrics.get('score'):.3f}).")
    if scan_result.chunk_summary.get("retrievable_count", 0) > 0:
        safe_conditions.append(f"Chroma chunk'ları retrievable durumda ({scan_result.chunk_summary.get('retrievable_count')} chunk).")

    reasons.append("Tüm bütünlük kontrolleri geçti; bilinen kalite veya yapısal bozulma tespit edilmedi.")
    return ClassificationResult(
        document_id=scan_result.document_id,
        status=ClassificationType.SAFE.value,
        reasons=reasons,
        blocking_issues=[],
        review_issues=[],
        safe_conditions=safe_conditions,
    )
