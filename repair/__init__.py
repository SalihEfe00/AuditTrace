# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 9, FAZ 10 & FAZ 11A: Self-Healing Infrastructure / Quality Gate Core
Package Initialization.
"""
from __future__ import annotations

from repair.classifier import classify_document
from repair.comparator import (
    QualityGatePolicy,
    build_snapshot,
    calculate_metric_deltas,
    compare_quality,
)
from repair.extractor import (
    CANONICAL_FALLBACK_CHAIN,
    create_repair_plan,
    extract_candidate,
    validate_source,
)
from repair.models import (
    ClassificationResult,
    ClassificationType,
    ComparisonResult,
    ComparisonStatus,
    DocumentScanResult,
    ExtractionCandidate,
    ExtractionPlan,
    FinalGateDecision,
    Issue,
    IssueCode,
    IssueSeverity,
    MetricDirection,
    ProductionScanReport,
    QualitySnapshot,
    RetrievalBenchmarkResult,
    RetrievalComparisonResult,
    SourceValidationResult,
    SourceValidationStatus,
    CommitStatus,
    StagingManifest,
    CommitResult,
)
from repair.committer import (
    CommitPolicy,
    atomic_commit_source_cache,
    cleanup_same_hash_chroma,
    commit_candidate,
    stage_candidate,
    upsert_candidate_chroma,
    validate_pre_commit,
)
from repair.retrieval_comparator import (
    RetrievalGatePolicy,
    compare_retrieval,
    evaluate_final_gate,
    evaluate_single_benchmark,
)
from repair.scanner import scan_document, scan_production

__all__ = [
    # Enums & Constants
    "IssueSeverity",
    "ClassificationType",
    "ComparisonStatus",
    "SourceValidationStatus",
    "MetricDirection",
    "IssueCode",
    "CANONICAL_FALLBACK_CHAIN",
    "QualityGatePolicy",
    "RetrievalGatePolicy",
    "CommitStatus",
    "CommitPolicy",
    # Dataclasses
    "Issue",
    "DocumentScanResult",
    "ProductionScanReport",
    "ClassificationResult",
    "ExtractionPlan",
    "ExtractionCandidate",
    "ComparisonResult",
    "SourceValidationResult",
    "QualitySnapshot",
    "RetrievalBenchmarkResult",
    "RetrievalComparisonResult",
    "FinalGateDecision",
    "StagingManifest",
    "CommitResult",
    # Functions
    "scan_document",
    "scan_production",
    "classify_document",
    "create_repair_plan",
    "extract_candidate",
    "validate_source",
    "compare_quality",
    "calculate_metric_deltas",
    "build_snapshot",
    "compare_retrieval",
    "evaluate_final_gate",
    "evaluate_single_benchmark",
    "commit_candidate",
    "validate_pre_commit",
    "stage_candidate",
    "atomic_commit_source_cache",
    "cleanup_same_hash_chroma",
    "upsert_candidate_chroma",
    # FAZ 16
    "ControlledAutomationOrchestrator",
    "ApprovalManager",
    "AutomationMode",
    "AutomationStatus",
    "AutomationPolicy",
    "AutomationDecision",
    "ApprovalRecord",
    "CandidateAutomationResult",
    "AutomationCycleSummary",
]

from repair.orchestrator import ControlledAutomationOrchestrator, ApprovalManager
from repair.models import (
    AutomationMode,
    AutomationStatus,
    AutomationPolicy,
    AutomationDecision,
    ApprovalRecord,
    CandidateAutomationResult,
    AutomationCycleSummary,
)


