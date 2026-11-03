# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 9 & FAZ 10: Self-Healing Infrastructure / Verified Candidate Extraction
Models & Contracts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class IssueSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ClassificationType(str, Enum):
    SAFE = "SAFE"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"


class ComparisonStatus(str, Enum):
    REJECT = "REJECT"
    READY_TO_COMMIT = "READY_TO_COMMIT"
    REVIEW = "REVIEW"


class SourceValidationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    MISSING = "MISSING"
    UNREADABLE = "UNREADABLE"
    HASH_MISSING = "HASH_MISSING"
    HASH_MISMATCH = "HASH_MISMATCH"
    IDENTITY_UNVERIFIED = "IDENTITY_UNVERIFIED"


# Standard deterministic issue codes
class IssueCode:
    # Source Issues
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_PATH_MISSING = "SOURCE_PATH_MISSING"
    SOURCE_HASH_MISSING = "SOURCE_HASH_MISSING"
    SOURCE_HASH_MISMATCH = "SOURCE_HASH_MISMATCH"

    # Source Cache Issues
    CACHE_MISSING = "CACHE_MISSING"
    CACHE_EMPTY = "CACHE_EMPTY"
    CACHE_HASH_MISMATCH = "CACHE_HASH_MISMATCH"
    CACHE_METADATA_INCOMPLETE = "CACHE_METADATA_INCOMPLETE"

    # Quality Issues
    QUALITY_GLUED_TEXT = "QUALITY_GLUED_TEXT"
    QUALITY_MOJIBAKE = "QUALITY_MOJIBAKE"
    QUALITY_REPLACEMENT_CHAR = "QUALITY_REPLACEMENT_CHAR"
    QUALITY_VERY_LONG_TOKEN = "QUALITY_VERY_LONG_TOKEN"
    QUALITY_LOW_SCORE = "QUALITY_LOW_SCORE"

    # Structure Issues
    STRUCTURE_DRIFT = "STRUCTURE_DRIFT"
    STRUCTURE_EMPTY_CHUNK = "STRUCTURE_EMPTY_CHUNK"
    STRUCTURE_DUPLICATE_CHUNK = "STRUCTURE_DUPLICATE_CHUNK"
    STRUCTURE_METADATA_MISSING = "STRUCTURE_METADATA_MISSING"

    # Chroma Issues
    CHROMA_MISSING = "CHROMA_MISSING"
    CHROMA_STALE_SCHEMA = "CHROMA_STALE_SCHEMA"
    CHROMA_SUPERSEDED_ONLY = "CHROMA_SUPERSEDED_ONLY"
    CHROMA_EMPTY_CHUNK = "CHROMA_EMPTY_CHUNK"
    CHROMA_GLUED_TEXT = "CHROMA_GLUED_TEXT"
    CHROMA_MOJIBAKE = "CHROMA_MOJIBAKE"
    CHROMA_LOW_SCORE = "CHROMA_LOW_SCORE"
    CHROMA_REPLACEMENT_CHAR = "CHROMA_REPLACEMENT_CHAR"

    # Retrievability Issues
    RETRIEVAL_NOT_RETRIEVABLE = "RETRIEVAL_NOT_RETRIEVABLE"
    RETRIEVAL_METADATA_INVALID = "RETRIEVAL_METADATA_INVALID"

    # Unknown / Unverified
    UNKNOWN_INTEGRITY_STATE = "UNKNOWN_INTEGRITY_STATE"


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentScanResult:
    document_id: str
    source_document_id: str = ""
    source_path: Optional[str] = None
    source_hash: str = ""
    title: str = ""
    source_status: str = "ok"
    cache_status: str = "ok"
    chroma_status: str = "ok"
    schema_status: str = "ok"
    quality_status: str = "ok"
    structure_status: str = "ok"
    retrievability_status: str = "ok"
    issues: List[Issue] = field(default_factory=list)
    issue_codes: List[str] = field(default_factory=list)
    current_metrics: Dict[str, Any] = field(default_factory=dict)
    chroma_quality: Dict[str, Any] = field(default_factory=dict)
    chunk_summary: Dict[str, Any] = field(default_factory=dict)
    source_cache_chars: int = 0

    def add_issue(self, code: str, severity: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        if code not in self.issue_codes:
            self.issue_codes.append(code)
        self.issues.append(Issue(code=code, severity=severity, message=message, details=details or {}))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProductionScanReport:
    scanned_documents: int = 0
    healthy_documents: int = 0
    review_documents: int = 0
    blocked_documents: int = 0
    repair_candidates: int = 0
    issues_summary: Dict[str, int] = field(default_factory=dict)
    documents: List[DocumentScanResult] = field(default_factory=list)
    classifications: Dict[str, str] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)
    dry_run: bool = True
    production_write_enabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scanned_documents": self.scanned_documents,
            "healthy_documents": self.healthy_documents,
            "review_documents": self.review_documents,
            "blocked_documents": self.blocked_documents,
            "repair_candidates": self.repair_candidates,
            "issues_summary": self.issues_summary,
            "documents": [d.to_dict() for d in self.documents],
            "classifications": self.classifications,
            "summary": self.summary,
            "dry_run": self.dry_run,
            "production_write_enabled": self.production_write_enabled,
        }


@dataclass
class ClassificationResult:
    document_id: str
    status: str  # SAFE, REVIEW, BLOCKED
    reasons: List[str] = field(default_factory=list)
    blocking_issues: List[str] = field(default_factory=list)
    review_issues: List[str] = field(default_factory=list)
    safe_conditions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def classification(self) -> str:
        return self.status


@dataclass
class SourceValidationResult:
    document_id: str
    source_document_id: str = ""
    source_path: str = ""
    exists: bool = False
    readable: bool = False
    expected_sha256: str = ""
    actual_sha256: str = ""
    hash_match: bool = False
    identity_verified: bool = False
    status: str = SourceValidationStatus.IDENTITY_UNVERIFIED.value
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractionPlan:
    document_id: str
    source_path: str
    source_hash: str
    source_document_id: str = ""
    plan_version: str = "1.0.0"
    primary_method: str = "pypdfium2_fast"
    fallback_methods: List[str] = field(
        default_factory=lambda: ["spatial", "pdfplumber", "pypdf", "ocr"]
    )
    extraction_chain: List[str] = field(
        default_factory=lambda: [
            "pypdfium2_fast",
            "spatial_reconstruction",
            "pdfplumber_fallback",
            "pypdf_fallback",
            "ocr_tesseract",
        ]
    )
    ocr_allowed: bool = True
    normalization_enabled: bool = True
    structure_enabled: bool = True
    chunking_enabled: bool = True
    expected_schema_version: str = "10.0.0"
    expected_quality_metrics: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractionCandidate:
    document_id: str
    source_document_id: str = ""
    source_path: str = ""
    source_sha256: str = ""
    extraction_method: str = "pypdfium2_fast"
    fallback_used: bool = False
    spatial_reconstruction_used: bool = False
    pdfplumber_used: bool = False
    pypdf_used: bool = False
    ocr_used: bool = False
    ocr_details: Dict[str, Any] = field(default_factory=dict)
    page_count: int = 0
    raw_text: str = ""
    normalized_text: str = ""
    structure: Dict[str, Any] = field(default_factory=dict)
    legal_chunks: List[Dict[str, Any]] = field(default_factory=list)
    quality_metrics: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "SUCCESS"  # SUCCESS, FAILED, INCOMPLETE, BLOCKED
    in_memory_only: bool = True

    def __getitem__(self, key: str) -> Any:
        if key == "ok":
            return self.status == "SUCCESS"
        if key == "text":
            return self.normalized_text
        if key == "metrics":
            return self.quality_metrics
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except Exception:
            return default

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def candidate(self) -> Optional[ExtractionCandidate]:
        if self.status != "SUCCESS":
            return None
        return self

    @property
    def errors(self) -> List[str]:
        if self.status != "SUCCESS":
            err = self.metadata.get("error") if isinstance(self.metadata, dict) else None
            return [str(err)] if err else [f"Extraction status: {self.status}"]
        return []


class MetricDirection(str, Enum):
    IMPROVED = "IMPROVED"
    REGRESSED = "REGRESSED"
    NEUTRAL = "NEUTRAL"


@dataclass
class QualitySnapshot:
    document_id: str = ""
    source_document_id: str = ""
    source_sha256: str = ""

    # Text quality metrics
    quality_score: float = 0.0
    glued_token_count: int = 0
    very_long_token_count: int = 0
    mojibake_sequence_count: int = 0
    replacement_char_count: int = 0
    chars: int = 0

    # Structure metrics
    article_count: int = 0
    paragraph_count: int = 0
    item_count: int = 0
    subitem_count: int = 0
    section_count: int = 0
    accounting_code_count: int = 0

    # Chunk metrics
    chunk_count: int = 0
    meaningful_chunk_count: int = 0
    empty_chunk_count: int = 0
    duplicate_chunk_count: int = 0

    # Metadata metrics
    metadata_completeness: float = 1.0
    schema_version: str = "10.0.0"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QualitySnapshot:
        data = dict(data or {})
        return cls(
            document_id=str(data.get("document_id", "") or ""),
            source_document_id=str(data.get("source_document_id", "") or ""),
            source_sha256=str(data.get("source_sha256") or data.get("source_hash") or data.get("file_hash") or ""),
            quality_score=float(data.get("quality_score") or data.get("score") or 0.0),
            glued_token_count=int(data.get("glued_token_count", 0) or 0),
            very_long_token_count=int(data.get("very_long_token_count", 0) or 0),
            mojibake_sequence_count=int(data.get("mojibake_sequence_count", 0) or 0),
            replacement_char_count=int(data.get("replacement_char_count", 0) or 0),
            chars=int(data.get("chars", 0) or 0),
            article_count=int(data.get("article_count", 0) or 0),
            paragraph_count=int(data.get("paragraph_count", 0) or 0),
            item_count=int(data.get("item_count", 0) or 0),
            subitem_count=int(data.get("subitem_count", 0) or 0),
            section_count=int(data.get("section_count", 0) or 0),
            accounting_code_count=int(data.get("accounting_code_count", 0) or 0),
            chunk_count=int(data.get("chunk_count", 0) or 0),
            meaningful_chunk_count=int(data.get("meaningful_chunk_count", data.get("chunk_count", 0)) or 0),
            empty_chunk_count=int(data.get("empty_chunk_count", 0) or 0),
            duplicate_chunk_count=int(data.get("duplicate_chunk_count", 0) or 0),
            metadata_completeness=float(data.get("metadata_completeness", 1.0) or 1.0),
            schema_version=str(data.get("schema_version") or data.get("chunk_schema_version") or "10.0.0"),
        )

    @classmethod
    def from_candidate(cls, candidate: Any) -> QualitySnapshot:
        if isinstance(candidate, QualitySnapshot):
            return candidate
        if isinstance(candidate, dict):
            return cls.from_dict(candidate)

        qm = getattr(candidate, "quality_metrics", {}) or {}
        st = getattr(candidate, "structure", {}) or {}
        chunks = getattr(candidate, "legal_chunks", []) or []

        meaningful = sum(1 for c in chunks if len(str(c.get("text", "")).strip()) >= 50)
        empty_chunks = sum(1 for c in chunks if not str(c.get("text", "")).strip())
        seen_texts = set()
        dups = 0
        for c in chunks:
            t = " ".join(str(c.get("text", "")).split())
            if t:
                if t in seen_texts:
                    dups += 1
                seen_texts.add(t)

        return cls(
            document_id=str(getattr(candidate, "document_id", "") or ""),
            source_document_id=str(getattr(candidate, "source_document_id", "") or ""),
            source_sha256=str(getattr(candidate, "source_sha256", "") or ""),
            quality_score=float(qm.get("score", 0.0) or 0.0),
            glued_token_count=int(qm.get("glued_token_count", 0) or 0),
            very_long_token_count=int(qm.get("very_long_token_count", 0) or 0),
            mojibake_sequence_count=int(qm.get("mojibake_sequence_count", 0) or 0),
            replacement_char_count=int(qm.get("replacement_char_count", 0) or 0),
            chars=int(qm.get("chars", len(getattr(candidate, "normalized_text", ""))) or 0),
            article_count=int(st.get("article_count", len(st.get("articles", []))) if isinstance(st.get("articles"), list) else st.get("article_count", 0) or 0),
            paragraph_count=int(len(st.get("paragraphs", [])) if isinstance(st.get("paragraphs"), list) else st.get("paragraph_marker_count", 0) or 0),
            item_count=int(len(st.get("items", [])) if isinstance(st.get("items"), list) else st.get("item_count", 0) or 0),
            subitem_count=int(len(st.get("subitems", [])) if isinstance(st.get("subitems"), list) else 0),
            section_count=int(len(st.get("sections", [])) if isinstance(st.get("sections"), list) else st.get("section_count", 0) or 0),
            accounting_code_count=int(st.get("account_code_count", 0) or 0),
            chunk_count=len(chunks),
            meaningful_chunk_count=meaningful,
            empty_chunk_count=empty_chunks,
            duplicate_chunk_count=dups,
            metadata_completeness=1.0 if getattr(candidate, "status", "") == "SUCCESS" else 0.5,
            schema_version=str(getattr(candidate, "metadata", {}).get("schema_version", "10.0.0")),
        )


@dataclass
class ComparisonResult:
    document_id: str
    status: str  # REJECT, READY_TO_COMMIT, REVIEW
    current_score: float = 0.0
    candidate_score: float = 0.0
    delta_score: float = 0.0
    quality_reasons: List[str] = field(default_factory=list)
    structure_reasons: List[str] = field(default_factory=list)
    retrieval_reasons: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    metrics_diff: Dict[str, Any] = field(default_factory=dict)

    # FAZ 11A Enhanced Fields
    hard_failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metric_deltas: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    quality_before: Dict[str, Any] = field(default_factory=dict)
    quality_after: Dict[str, Any] = field(default_factory=dict)
    structure_before: Dict[str, Any] = field(default_factory=dict)
    structure_after: Dict[str, Any] = field(default_factory=dict)
    chunks_before: Dict[str, Any] = field(default_factory=dict)
    chunks_after: Dict[str, Any] = field(default_factory=dict)
    source_identity_status: str = "VERIFIED"
    metadata_status: str = "VALID"
    confidence_explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalBenchmarkResult:
    benchmark_id: str
    query: str
    query_type: str
    current_hit: bool = False
    candidate_hit: bool = False
    current_rank: Optional[int] = None
    candidate_rank: Optional[int] = None
    current_document_match: bool = False
    candidate_document_match: bool = False
    current_account_match: bool = False
    candidate_account_match: bool = False
    current_reference_match: bool = False
    candidate_reference_match: bool = False
    current_metadata_match: bool = False
    candidate_metadata_match: bool = False
    current_retrievable: bool = True
    candidate_retrievable: bool = True
    status: str = "NEUTRAL"  # IMPROVED, REGRESSED, NEUTRAL, FAILED
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalComparisonResult:
    status: str  # REJECT, REVIEW, READY_TO_COMMIT
    benchmark_results: List[RetrievalBenchmarkResult] = field(default_factory=list)
    current_summary: Dict[str, Any] = field(default_factory=dict)
    candidate_summary: Dict[str, Any] = field(default_factory=dict)
    aggregate_delta: Dict[str, Any] = field(default_factory=dict)
    hard_failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    confidence_explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FinalGateDecision:
    faz11a_status: str
    faz11b_status: str
    final_status: str  # REJECT, REVIEW, READY_TO_COMMIT
    reasons: List[str] = field(default_factory=list)
    confidence_explanation: str = ""
    faz11a_result: Optional[ComparisonResult] = None
    faz11b_result: Optional[RetrievalComparisonResult] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "faz11a_status": self.faz11a_status,
            "faz11b_status": self.faz11b_status,
            "final_status": self.final_status,
            "reasons": self.reasons,
            "confidence_explanation": self.confidence_explanation,
            "faz11a_result": self.faz11a_result.to_dict() if self.faz11a_result else None,
            "faz11b_result": self.faz11b_result.to_dict() if self.faz11b_result else None,
        }


class CommitStatus(str, Enum):
    SUCCESS = "SUCCESS"
    COMMITTED = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    ABORTED = "ABORTED"
    NO_OP = "NO_OP"


@dataclass
class StagingManifest:
    commit_id: str
    candidate_id: str
    document_id: str
    source_document_id: str
    source_sha256: str
    chunk_schema_version: str
    candidate_chunk_count: int
    final_decision: str
    target_collection: str
    created_at: str
    staging_dir: str
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CommitResult:
    status: str  # SUCCESS, FAILED, PARTIAL_FAILURE, ABORTED, NO_OP
    commit_id: str
    candidate_id: str
    document_id: str
    source_sha256: str
    cache_commit: bool = False
    chroma_cleanup: bool = False
    chroma_upsert: bool = False
    old_chunk_count: int = 0
    new_chunk_count: int = 0
    dry_run: bool = False
    warnings: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    error: Optional[str] = None
    manifest: Optional[Dict[str, Any]] = None

    @property
    def issues(self) -> List[str]:
        res = list(self.reasons)
        if self.error and self.error not in res:
            res.append(self.error)
        return res

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# FAZ 13: Post-Repair Verification Models
# ---------------------------------------------------------------------------

class VerificationStatus(str, Enum):
    VERIFIED_REPAIR = "VERIFIED_REPAIR"
    VERIFY_FAILED = "VERIFY_FAILED"
    REVIEW = "REVIEW"
    UNKNOWN_INTEGRITY_STATE = "UNKNOWN_INTEGRITY_STATE"


@dataclass
class CheckResult:
    """Single deterministic check result produced by the FAZ 13 verifier."""
    check_id: str
    name: str
    status: str  # PASS, FAIL, REVIEW, UNKNOWN
    expected: str = ""
    actual: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationResult:
    """Aggregate result of a post-repair verification run (FAZ 13)."""
    status: str  # VerificationStatus value
    commit_id: str
    candidate_id: str
    document_id: str
    source_sha256: str

    # Per-domain status summaries
    cache_status: str = "UNKNOWN"
    chunk_status: str = "UNKNOWN"
    chroma_status: str = "UNKNOWN"
    exact_retrieval_status: str = "UNKNOWN"
    document_retrieval_status: str = "UNKNOWN"
    account_retrieval_status: str = "UNKNOWN"
    semantic_retrieval_status: str = "UNKNOWN"
    stale_data_status: str = "UNKNOWN"
    metadata_status: str = "UNKNOWN"
    quality_status: str = "UNKNOWN"

    checks: List[CheckResult] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# FAZ 14: Rollback & State Restoration Models
# ---------------------------------------------------------------------------

class RollbackStatus(str, Enum):
    ROLLBACK_SUCCESS = "ROLLBACK_SUCCESS"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    ROLLBACK_PARTIAL = "ROLLBACK_PARTIAL"
    ROLLBACK_BLOCKED = "ROLLBACK_BLOCKED"
    ROLLBACK_ALREADY_RESTORED = "ROLLBACK_ALREADY_RESTORED"


@dataclass
class BeforeSnapshot:
    """Deterministic snapshot of state before commit mutation."""
    commit_id: str
    document_id: str
    source_document_id: str = ""
    source_sha256: str = ""
    timestamp: str = ""
    chunk_schema_version: str = "10.0.0"
    cache_state: Dict[str, Any] = field(default_factory=dict)
    index_state: Dict[str, Any] = field(default_factory=dict)
    chroma_state: Dict[str, Any] = field(default_factory=dict)
    fingerprints: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RollbackResult:
    """Aggregate result of a rollback / state restoration run (FAZ 14)."""
    status: str  # RollbackStatus value
    rollback_id: str = ""
    commit_id: str = ""
    candidate_id: str = ""
    document_id: str = ""
    source_sha256: str = ""

    # Per-domain status summaries
    cache_status: str = "UNKNOWN"
    index_status: str = "UNKNOWN"
    chroma_status: str = "UNKNOWN"
    retrieval_status: str = "UNKNOWN"
    fingerprint_status: str = "UNKNOWN"

    restored_state: Dict[str, Any] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# FAZ 15: Audit Log & Self-Healing Traceability Models
# ---------------------------------------------------------------------------

class AuditEventType(str, Enum):
    REPAIR_STARTED = "REPAIR_STARTED"
    REPAIR_CLASSIFIED = "REPAIR_CLASSIFIED"
    REPAIR_EXTRACTED = "REPAIR_EXTRACTED"
    REPAIR_COMPARED = "REPAIR_COMPARED"
    REPAIR_READY_TO_COMMIT = "REPAIR_READY_TO_COMMIT"
    REPAIR_COMMITTED = "REPAIR_COMMITTED"
    REPAIR_COMMIT_FAILED = "REPAIR_COMMIT_FAILED"
    REPAIR_VERIFIED = "REPAIR_VERIFIED"
    REPAIR_VERIFICATION_FAILED = "REPAIR_VERIFICATION_FAILED"
    ROLLBACK_STARTED = "ROLLBACK_STARTED"
    ROLLBACK_COMPLETED = "ROLLBACK_COMPLETED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    ROLLBACK_BLOCKED = "ROLLBACK_BLOCKED"
    AUDIT_CORRECTION = "AUDIT_CORRECTION"
    AUTOMATION_CYCLE_STARTED = "AUTOMATION_CYCLE_STARTED"
    AUTOMATION_CYCLE_COMPLETED = "AUTOMATION_CYCLE_COMPLETED"
    AUTOMATION_DECISION_MADE = "AUTOMATION_DECISION_MADE"
    AUTOMATION_APPROVAL_REQUESTED = "AUTOMATION_APPROVAL_REQUESTED"
    AUTOMATION_APPROVAL_CONSUMED = "AUTOMATION_APPROVAL_CONSUMED"
    AUTOMATION_APPROVAL_REJECTED = "AUTOMATION_APPROVAL_REJECTED"
    AUTOMATION_CIRCUIT_OPEN = "AUTOMATION_CIRCUIT_OPEN"
    AUTOMATION_HALTED = "AUTOMATION_HALTED"
    WAVE_STARTED = "WAVE_STARTED"
    WAVE_COMPLETED = "WAVE_COMPLETED"
    WAVE_HALTED = "WAVE_HALTED"
    WAVE_RESCAN_PASSED = "WAVE_RESCAN_PASSED"
    WAVE_RESCAN_FAILED = "WAVE_RESCAN_FAILED"
    INGEST_QUALITY_PASSED = "INGEST_QUALITY_PASSED"
    INGEST_SELF_HEALED = "INGEST_SELF_HEALED"
    INGEST_QUARANTINED = "INGEST_QUARANTINED"
    INGEST_RETRY_STARTED = "INGEST_RETRY_STARTED"
    INGEST_QUARANTINE_REVIEWED = "INGEST_QUARANTINE_REVIEWED"


@dataclass
class AuditEvent:
    """Immutable, append-only structured audit event for self-healing actions."""
    event_id: str
    event_type: str  # AuditEventType value
    timestamp: str
    sequence_no: int = 0
    repair_id: str = ""
    commit_id: Optional[str] = None
    rollback_id: Optional[str] = None
    candidate_id: Optional[str] = None

    document_id: str = ""
    source_document_id: str = ""
    source_sha256: str = ""

    reason: str = ""
    engine: str = ""
    engine_version: str = ""

    old_quality: Optional[Dict[str, Any]] = None
    new_quality: Optional[Dict[str, Any]] = None
    old_chunk_count: Optional[int] = None
    new_chunk_count: Optional[int] = None

    status: str = ""
    verification_status: Optional[str] = None
    rollback_status: Optional[str] = None

    before_fingerprint: Optional[Dict[str, Any]] = None
    after_fingerprint: Optional[Dict[str, Any]] = None

    environment: str = "production"
    actor: str = "system"
    trigger: str = "manual"
    app_version: str = "1.0"
    audit_schema_version: str = "1.0.0"

    previous_event_hash: str = "0" * 64
    event_hash: str = ""

    metadata: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RepairLifecycleSummary:
    """Aggregated lifecycle traceability story for a single repair operation."""
    repair_id: str
    document_id: str
    source_sha256: str
    trigger: str = ""
    reason: str = ""
    classification: Optional[str] = None
    extraction_engine: Optional[str] = None
    quality_before: Optional[Dict[str, Any]] = None
    quality_after: Optional[Dict[str, Any]] = None
    chunk_count_before: Optional[int] = None
    chunk_count_after: Optional[int] = None
    commit_status: Optional[str] = None
    verification_status: Optional[str] = None
    rollback_occurred: bool = False
    rollback_status: Optional[str] = None
    final_repair_status: str = "UNKNOWN"
    events_count: int = 0
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# FAZ 16: Controlled Automation / Safe Auto-Repair / Review / Blocked Models
# ---------------------------------------------------------------------------

class AutomationMode(str, Enum):
    SCAN_ONLY = "SCAN_ONLY"
    DRY_RUN = "DRY_RUN"
    SAFE_AUTO_REPAIR = "SAFE_AUTO_REPAIR"
    MANUAL_APPROVAL = "MANUAL_APPROVAL"
    DISABLED = "DISABLED"


class AutomationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    HALTED = "HALTED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    BLOCKED = "BLOCKED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    VERIFIED_REPAIR = "VERIFIED_REPAIR"
    FAILED_BUT_RESTORED = "FAILED_BUT_RESTORED"
    APPROVAL_INVALID = "APPROVAL_INVALID"


@dataclass
class AutomationPolicy:
    """Deterministic configuration governing automated repair execution."""
    mode: str = AutomationMode.SCAN_ONLY.value
    environment: str = "production"
    allow_production_auto_repair: bool = False
    kill_switch_enabled: bool = False
    max_auto_repairs_per_cycle: int = 10
    max_candidates_per_cycle: int = 50
    circuit_breaker_threshold: int = 3
    failure_budget: int = 5
    rollback_on_verify_failure: bool = True
    approval_expiration_seconds: int = 86400  # 24 hours
    actor: str = "automation_engine"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutomationDecision:
    """Evaluation result determining whether a candidate may be automated."""
    candidate_id: str
    document_id: str
    source_sha256: str
    classification: str  # SAFE, REVIEW, BLOCKED
    mode: str            # AutomationMode value
    allowed: bool
    action: str = "NONE"  # NONE, SIMULATE, COMMIT, REQUIRE_APPROVAL, REPAIR
    reason_codes: List[str] = field(default_factory=list)
    blocking_reasons: List[str] = field(default_factory=list)
    review_reasons: List[str] = field(default_factory=list)
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    requires_approval: bool = False
    approval_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalRecord:
    """Human approval record securely binding approval to document, hash, and candidate."""
    approval_id: str
    repair_id: str
    candidate_id: str
    document_id: str
    source_sha256: str
    actor: str
    created_at: str
    expires_at: str
    consumed: bool = False
    consumed_at: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateAutomationResult:
    """Execution outcome for a single candidate processed during an automation cycle."""
    candidate_id: str
    document_id: str
    source_sha256: str
    decision: Optional[AutomationDecision] = None
    action_taken: str = "NONE"  # NONE, SIMULATE, COMMIT, VERIFY, ROLLBACK, ABORT
    commit_status: Optional[str] = None
    verification_status: Optional[str] = None
    rollback_status: Optional[str] = None
    final_status: str = AutomationStatus.SKIPPED.value
    candidate: Optional[Any] = None
    quality_comparison: Optional[Any] = None
    retrieval_comparison: Optional[Any] = None
    final_gate: Optional[Any] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutomationCycleSummary:
    """Aggregate metrics and status for an executed automation cycle."""
    cycle_id: str
    mode: str
    status: str = AutomationStatus.SUCCESS.value
    scanned_count: int = 0
    safe_count: int = 0
    review_count: int = 0
    blocked_count: int = 0
    attempted_count: int = 0
    committed_count: int = 0
    verified_count: int = 0
    rolled_back_count: int = 0
    failed_count: int = 0
    circuit_broken: bool = False
    halt_reason: Optional[str] = None
    production_mutation_count: int = 0
    sandbox_mutation_count: int = 0
    results: List[CandidateAutomationResult] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# FAZ 17: Controlled Production Repair Waves / Verify + Rescan Models
# ---------------------------------------------------------------------------

class WaveScope(str, Enum):
    PILOT = "PILOT"                    # Exactly 1 document
    SMALL_BATCH = "SMALL_BATCH"        # 3 to 5 documents
    MEDIUM_BATCH = "MEDIUM_BATCH"      # 10 documents
    BDDK_ACTIVE = "BDDK_ACTIVE"        # Active BDDK documents
    OTHER_SOURCES = "OTHER_SOURCES"    # Other regulatory sources
    ALL = "ALL"                        # All remaining (subject to wave progression)
    CUSTOM = "CUSTOM"                  # Explicit document list


class WaveStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    HALTED = "HALTED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    BLOCKED = "BLOCKED"
    PRODUCTION_CORPUS_ABSENT = "PRODUCTION_CORPUS_ABSENT"
    COMPLETED = "COMPLETED"


class WaveDocumentStatus(str, Enum):
    VERIFIED_AND_RESCANNED = "VERIFIED_AND_RESCANNED"
    SIMULATED = "SIMULATED"
    SKIPPED = "SKIPPED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    RESCAN_FAILED = "RESCAN_FAILED"
    VERIFY_FAILED_RESTORED = "VERIFY_FAILED_RESTORED"
    VERIFY_FAILED_ROLLBACK_FAILED = "VERIFY_FAILED_ROLLBACK_FAILED"
    FAILED = "FAILED"


@dataclass
class WaveConfig:
    """Deterministic configuration governing a controlled repair wave."""
    scope: str = WaveScope.PILOT.value
    target_document_ids: List[str] = field(default_factory=list)
    doc_limit: int = 1
    require_rescan: bool = True
    halt_on_failure: bool = True
    max_consecutive_failures: int = 1
    failure_budget: int = 2
    rollback_on_verify_fail: bool = True
    rollback_on_rescan_fail: bool = True
    allow_production_repair: bool = False
    dry_run: bool = True
    environment: str = "rag_lab"  # "rag_lab" or "production"
    actor: str = "wave_runner"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WaveDocumentResult:
    """Outcome for a single document evaluated within a repair wave."""
    document_id: str
    source_sha256: str = ""
    status: str = WaveDocumentStatus.SKIPPED.value
    action_taken: str = "NONE"
    classification: Optional[str] = None
    commit_id: Optional[str] = None
    verify_status: Optional[str] = None
    rescan_passed: bool = False
    rollback_occurred: bool = False
    rollback_status: Optional[str] = None
    candidate: Optional[Any] = None
    quality_score_before: Optional[float] = None
    quality_score_after: Optional[float] = None
    comparison_status: Optional[str] = None
    before_fingerprint: Dict[str, Any] = field(default_factory=dict)
    after_fingerprint: Dict[str, Any] = field(default_factory=dict)
    regressions_detected: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WaveSummary:
    """Aggregate metrics and lifecycle state for an entire repair wave."""
    wave_id: str
    scope: str
    status: str = WaveStatus.SUCCESS.value
    environment: str = "rag_lab"
    dry_run: bool = True
    target_count: int = 0
    scanned_count: int = 0
    safe_count: int = 0
    review_count: int = 0
    blocked_count: int = 0
    attempted_count: int = 0
    committed_count: int = 0
    verified_count: int = 0
    rescanned_count: int = 0
    rolled_back_count: int = 0
    failed_count: int = 0
    circuit_broken: bool = False
    halt_reason: Optional[str] = None
    production_mutation_count: int = 0
    sandbox_mutation_count: int = 0
    documents: List[WaveDocumentResult] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


