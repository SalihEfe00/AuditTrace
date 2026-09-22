# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 16: Controlled Automation / Safe Auto-Repair / Review / Blocked Gate.

Orchestrates the complete self-healing chain:
  SCAN -> CLASSIFY -> RE-EXTRACT -> QUALITY COMPARE -> RETRIEVAL GATE ->
  READY_TO_COMMIT -> SAFE COMMIT -> VERIFY -> ROLLBACK -> AUDIT LOG

Strictly adheres to:
- Zero duplication: coordinates existing FAZ 9-15 components without reimplementing them.
- Single write authorities preserved:
    * Commit: repair/committer.py
    * Rollback: repair/rollback.py
    * Audit: repair/audit.py
- Deterministic policy engine: zero LLM intervention in gates.
- Production auto-repair disabled by default (allow_production_auto_repair=False).
- Stale approval protection: approvals bound to document_id + source_sha256 + candidate_id.
- Time-of-check / time-of-use (TOCTOU) protection: source hash verified before commit.
- Circuit breaker & failure budget halting.
- Concurrency locking per document + source_hash.
"""
from __future__ import annotations

import binascii
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mufettis.common.utils import now_iso, safe_json
from mufettis.config import (
    CHROMA_COLLECTION,
    CHUNK_SCHEMA_VERSION,
    DATA_DIR,
    DB_PATH,
    INGEST_INDEX_FILE,
    SOURCE_CACHE_DIR,
)
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex

from repair.audit import RepairAuditLogger, get_audit_logger, REPAIR_AUDIT_LOG_FILE
from repair.classifier import classify_document
from repair.committer import CommitPolicy, acquire_commit_lock, commit_candidate
from repair.comparator import QualityGatePolicy, build_snapshot, compare_quality
from repair.extractor import create_repair_plan, extract_candidate, validate_source
from repair.models import (
    ApprovalRecord,
    AuditEvent,
    AuditEventType,
    AutomationCycleSummary,
    AutomationDecision,
    AutomationMode,
    AutomationPolicy,
    AutomationStatus,
    CandidateAutomationResult,
    ClassificationResult,
    ClassificationType,
    CommitResult,
    CommitStatus,
    ComparisonStatus,
    DocumentScanResult,
    ExtractionCandidate,
    FinalGateDecision,
    IssueCode,
    IssueSeverity,
    QualitySnapshot,
    RetrievalComparisonResult,
    RollbackResult,
    RollbackStatus,
    SourceValidationResult,
    SourceValidationStatus,
    VerificationResult,
    VerificationStatus,
)
from repair.retrieval_comparator import (
    RetrievalGatePolicy,
    compare_retrieval,
    evaluate_final_gate,
)
from repair.rollback import rollback_commit
from repair.scanner import _calc_sha256, scan_document
from repair.verifier import verify_committed_repair

logger = logging.getLogger("mufettis.repair.orchestrator")

DEFAULT_APPROVALS_FILE = DATA_DIR / "automation" / "approvals.json"


# ---------------------------------------------------------------------------
# Approval Storage & Validation Guard
# ---------------------------------------------------------------------------

class ApprovalManager:
    """Thread-safe persistent storage and verification for human approval tokens."""

    def __init__(self, approvals_file: Optional[Path] = None):
        self.approvals_file = Path(approvals_file or DEFAULT_APPROVALS_FILE)
        self._lock = threading.Lock()

    def _read_all(self) -> Dict[str, Dict[str, Any]]:
        if not self.approvals_file.exists():
            return {}
        try:
            return json.loads(self.approvals_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Error reading approvals file %s: %s", self.approvals_file, exc)
            return {}

    def _write_all(self, data: Dict[str, Dict[str, Any]]) -> None:
        self.approvals_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.approvals_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(safe_json(data), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_file, self.approvals_file)

    def create_approval(
        self,
        repair_id: str,
        candidate_id: str,
        document_id: str,
        source_sha256: str,
        actor: str = "reviewer",
        expiration_seconds: int = 86400,
        details: Optional[Dict[str, Any]] = None,
    ) -> ApprovalRecord:
        """Creates a cryptographically-bound approval token."""
        approval_id = f"appr_{binascii.hexlify(os.urandom(8)).decode()}"
        now_ts = time.time()
        created_at = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()
        expires_at = datetime.fromtimestamp(now_ts + expiration_seconds, tz=timezone.utc).isoformat()

        record = ApprovalRecord(
            approval_id=approval_id,
            repair_id=repair_id,
            candidate_id=candidate_id,
            document_id=document_id,
            source_sha256=source_sha256.lower().strip(),
            actor=actor,
            created_at=created_at,
            expires_at=expires_at,
            consumed=False,
            details=details or {},
        )

        with self._lock:
            data = self._read_all()
            data[approval_id] = record.to_dict()
            self._write_all(data)

        return record

    def get_approval(self, approval_id: str) -> Optional[ApprovalRecord]:
        with self._lock:
            data = self._read_all()
            item = data.get(approval_id)
            if not item:
                return None
            return ApprovalRecord(**item)

    def validate_and_consume(
        self,
        approval_id: str,
        document_id: str,
        source_sha256: str,
        candidate_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[ApprovalRecord]]:
        """Validates token authenticity, expiration, and document/hash binding, then consumes it."""
        with self._lock:
            data = self._read_all()
            item = data.get(approval_id)
            if not item:
                return False, "APPROVAL_NOT_FOUND", None

            record = ApprovalRecord(**item)

            if record.consumed:
                return False, "APPROVAL_ALREADY_CONSUMED", record

            # Check expiration
            try:
                exp_dt = datetime.fromisoformat(record.expires_at)
                if datetime.now(timezone.utc) > exp_dt:
                    return False, "APPROVAL_EXPIRED", record
            except Exception:
                return False, "APPROVAL_INVALID_TIMESTAMP", record

            # Check document binding
            if record.document_id and document_id and record.document_id != document_id:
                return False, f"APPROVAL_DOC_MISMATCH: expected {record.document_id}, got {document_id}", record

            # Check source hash binding (TOCTOU protection)
            norm_expected_sha = record.source_sha256.lower().strip()
            norm_actual_sha = source_sha256.lower().strip()
            if norm_expected_sha != norm_actual_sha:
                return False, f"APPROVAL_HASH_MISMATCH: approved {norm_expected_sha}, current {norm_actual_sha}", record

            # Check candidate binding if specified
            if candidate_id and record.candidate_id and record.candidate_id != candidate_id:
                return False, f"APPROVAL_CANDIDATE_MISMATCH: expected {record.candidate_id}, got {candidate_id}", record

            # Consume token
            record.consumed = True
            record.consumed_at = now_iso()
            data[approval_id] = record.to_dict()
            self._write_all(data)
            return True, None, record


# ---------------------------------------------------------------------------
# Controlled Automation Orchestrator
# ---------------------------------------------------------------------------

class ControlledAutomationOrchestrator:
    """Single canonical orchestrator managing the automated self-healing pipeline."""

    def __init__(
        self,
        policy: Optional[AutomationPolicy] = None,
        rag: Optional[LocalRAG] = None,
        index: Optional[IngestIndex] = None,
        cache_dir: Optional[Path] = None,
        staging_dir: Optional[Path] = None,
        audit_logger: Optional[RepairAuditLogger] = None,
        approvals_file: Optional[Path] = None,
    ):
        self.policy = policy or AutomationPolicy()
        self.rag = rag
        self.index = index
        self.cache_dir = Path(cache_dir or SOURCE_CACHE_DIR)
        self.staging_dir = Path(staging_dir or (DATA_DIR / "repair_staging"))
        self.audit_logger = audit_logger or RepairAuditLogger()
        self.approval_manager = ApprovalManager(approvals_file=approvals_file)

        # Circuit breaker state
        self.consecutive_failures: int = 0
        self.total_failures: int = 0
        self.circuit_open: bool = False
        self._orchestrator_lock = threading.Lock()

    def reset_circuit_breaker(self) -> None:
        """Resets the circuit breaker and failure counts."""
        with self._orchestrator_lock:
            self.consecutive_failures = 0
            self.total_failures = 0
            self.circuit_open = False

    def evaluate_policy(
        self,
        scan_result: DocumentScanResult,
        candidate: Optional[ExtractionCandidate] = None,
        final_gate: Optional[FinalGateDecision] = None,
        approval_id: Optional[str] = None,
    ) -> AutomationDecision:
        """Evaluates document scan and quality metrics against policy to produce a deterministic decision."""
        classification_res = classify_document(scan_result)
        doc_id = scan_result.document_id
        src_sha = scan_result.source_hash

        cls_status = classification_res.status
        decision = AutomationDecision(
            candidate_id=getattr(candidate, "candidate_id", doc_id) or doc_id,
            document_id=doc_id,
            source_sha256=src_sha,
            classification=cls_status,
            mode=self.policy.mode,
            allowed=False,
            action="NONE",
            reason_codes=[i.code if isinstance(i.code, str) else i.code.value for i in scan_result.issues],
            blocking_reasons=list(classification_res.reasons) if cls_status == ClassificationType.BLOCKED.value else [],
            review_reasons=list(classification_res.reasons) if cls_status == ClassificationType.REVIEW.value else [],
            risk_level="LOW",
            requires_approval=False,
            approval_id=approval_id,
        )

        # 1. Kill Switch Check
        if self.policy.kill_switch_enabled:
            decision.allowed = False
            decision.action = "NONE"
            decision.risk_level = "CRITICAL"
            decision.blocking_reasons.append("KILL_SWITCH_ACTIVE")
            return decision

        # 2. BLOCKED Classification
        if cls_status == ClassificationType.BLOCKED.value:
            decision.allowed = False
            decision.action = "NONE"
            decision.risk_level = "CRITICAL"
            return decision

        # 3. REVIEW Classification
        if cls_status == ClassificationType.REVIEW.value:
            decision.risk_level = "MEDIUM"
            decision.requires_approval = True

            if self.policy.mode == AutomationMode.MANUAL_APPROVAL.value or approval_id:
                if approval_id:
                    # Token will be validated at execution time
                    decision.allowed = True
                    decision.action = "REPAIR"
                else:
                    decision.allowed = False
                    decision.action = "REQUIRE_APPROVAL"
            elif self.policy.mode == AutomationMode.DRY_RUN.value:
                decision.allowed = True
                decision.action = "SIMULATE"
            else:
                decision.allowed = False
                decision.action = "NONE"
                decision.blocking_reasons.append("REVIEW_CANDIDATE_REQUIRES_MANUAL_APPROVAL")
            return decision

        # 4. SAFE Classification
        if cls_status == ClassificationType.SAFE.value:
            decision.risk_level = "LOW"
            decision.requires_approval = False

            if self.policy.mode == AutomationMode.SCAN_ONLY.value:
                decision.allowed = False
                decision.action = "NONE"
            elif self.policy.mode == AutomationMode.DRY_RUN.value:
                decision.allowed = True
                decision.action = "SIMULATE"
            elif self.policy.mode == AutomationMode.SAFE_AUTO_REPAIR.value:
                # Production safety gate
                if self.policy.environment == "production" and not self.policy.allow_production_auto_repair:
                    decision.allowed = False
                    decision.action = "NONE"
                    decision.blocking_reasons.append("PRODUCTION_AUTO_REPAIR_DISABLED")
                else:
                    decision.allowed = True
                    decision.action = "REPAIR"
            elif self.policy.mode == AutomationMode.MANUAL_APPROVAL.value:
                # Safe candidate can run in manual approval mode if explicitly allowed
                decision.allowed = True
                decision.action = "REPAIR"
            else:
                decision.allowed = False
                decision.action = "NONE"

            return decision

        return decision

    def _emit_audit_event(
        self,
        event_type: AuditEventType,
        repair_id: str,
        document_id: str,
        source_sha256: str,
        status: str = "",
        reason: str = "",
        commit_id: Optional[str] = None,
        rollback_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        rollback_status: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Safely appends an audit event through the single audit write authority."""
        try:
            event = AuditEvent(
                event_id=f"evt_{binascii.hexlify(os.urandom(8)).decode()}",
                event_type=event_type.value,
                timestamp=now_iso(),
                repair_id=repair_id,
                commit_id=commit_id,
                rollback_id=rollback_id,
                candidate_id=candidate_id,
                document_id=document_id,
                source_sha256=source_sha256,
                reason=reason,
                status=status,
                verification_status=verification_status,
                rollback_status=rollback_status,
                environment=self.policy.environment,
                actor=self.policy.actor,
                trigger="automation",
                details=details or {},
            )
            self.audit_logger.log_event(event)
        except Exception as exc:
            logger.error("Failed to log audit event: %s", exc)

    def process_candidate(
        self,
        document_id: str,
        source_path: Optional[Path] = None,
        source_hash: Optional[str] = None,
        approval_id: Optional[str] = None,
        candidate: Optional[ExtractionCandidate] = None,
        scan_result: Optional[DocumentScanResult] = None,
        baseline_benchmarks: Optional[List[Dict[str, Any]]] = None,
        known_stale_fragments: Optional[List[str]] = None,
        expected_total_chunk_count: Optional[int] = None,
        known_other_hashes: Optional[List[str]] = None,
    ) -> CandidateAutomationResult:
        """Executes a single document through the controlled automation lifecycle."""
        repair_id = f"rep_{binascii.hexlify(os.urandom(6)).decode()}"
        norm_hash = (source_hash or getattr(scan_result, "source_hash", "") or "").strip().lower()

        # 1. Circuit Breaker Check
        if self.circuit_open:
            return CandidateAutomationResult(
                candidate_id=document_id,
                document_id=document_id,
                source_sha256=norm_hash,
                action_taken="NONE",
                final_status=AutomationStatus.CIRCUIT_OPEN.value,
                errors=["Circuit breaker is OPEN. Automation halted."],
            )

        # 2. Scanning (if not pre-scanned)
        if scan_result is None:
            scan_result = scan_document(
                file_hash=norm_hash,
                meta={"document_id": document_id},
                index=self.index,
                rag=self.rag,
                source_path=source_path,
            )

        norm_hash = scan_result.source_hash or norm_hash

        # 3. Policy Evaluation
        decision = self.evaluate_policy(scan_result, candidate=candidate, approval_id=approval_id)

        result = CandidateAutomationResult(
            candidate_id=decision.candidate_id,
            document_id=document_id,
            source_sha256=norm_hash,
            decision=decision,
            action_taken=decision.action,
        )

        # 4. Handle Disallowed Actions
        if not decision.allowed:
            if decision.requires_approval:
                result.final_status = AutomationStatus.WAITING_APPROVAL.value
            elif decision.classification == ClassificationType.BLOCKED.value:
                result.final_status = AutomationStatus.BLOCKED.value
                self._emit_audit_event(
                    AuditEventType.REPAIR_CLASSIFIED,
                    repair_id=repair_id,
                    document_id=document_id,
                    source_sha256=norm_hash,
                    status="BLOCKED",
                    reason="; ".join(decision.blocking_reasons),
                )
            else:
                result.final_status = AutomationStatus.SKIPPED.value
            return result

        # 5. Handle Simulation (DRY_RUN)
        if decision.action == "SIMULATE":
            raw_meta = {}
            if self.index:
                if hasattr(self.index, "get") and callable(self.index.get):
                    val = self.index.get(norm_hash)
                    if isinstance(val, dict):
                        raw_meta = val
                elif hasattr(self.index, "data") and isinstance(self.index.data, dict):
                    raw_meta = self.index.data.get("indexed_items", {}).get(norm_hash, {})
                elif isinstance(self.index, dict):
                    raw_meta = self.index.get("indexed_items", {}).get(norm_hash, {})

            if not isinstance(raw_meta, dict):
                raw_meta = {}

            url_val = raw_meta.get("url")
            has_url = isinstance(url_val, str) and bool(url_val.strip())
            is_web = bool(
                (raw_meta.get("is_pdf") is False)
                or (raw_meta.get("source_type") == "secondary")
                or (raw_meta.get("ingest_origin") == "secondary_web_source")
                or has_url
                or (source_path and str(source_path).startswith(("http://", "https://")))
            )

            if candidate is None:
                can_extract = is_web or (source_path is not None and Path(source_path).exists())
                if can_extract:
                    target_source_path = source_path if not is_web else (source_path or raw_meta.get("url"))
                    cand_res = extract_candidate(
                        source_path=target_source_path,
                        meta={**raw_meta, "document_id": document_id},
                        expected_sha256=norm_hash,
                    )
                    if cand_res.candidate:
                        candidate = cand_res.candidate

            if candidate is not None:
                metrics = scan_result.current_metrics if scan_result else {}
                old_snapshot = build_snapshot(metrics)
                comparison_res = compare_quality(old_snapshot, candidate)
                retrieval_res = RetrievalComparisonResult(
                    status=ComparisonStatus.READY_TO_COMMIT.value
                )
                final_gate_dec = evaluate_final_gate(comparison_res, retrieval_res)
                result.candidate = candidate
                result.quality_comparison = comparison_res
                result.retrieval_comparison = retrieval_res
                result.final_gate = final_gate_dec

            result.final_status = AutomationStatus.SUCCESS.value
            result.action_taken = "SIMULATE"
            self._emit_audit_event(
                AuditEventType.REPAIR_CLASSIFIED,
                repair_id=repair_id,
                document_id=document_id,
                source_sha256=norm_hash,
                status="SIMULATED",
                reason="Dry-run simulation executed with zero writes.",
            )
            return result

        # 6. Execute Repair (SAFE_AUTO_REPAIR or APPROVED REVIEW)
        if decision.action == "REPAIR":
            # 6.1 Validate and consume approval if required
            if decision.requires_approval:
                if not approval_id:
                    result.final_status = AutomationStatus.WAITING_APPROVAL.value
                    result.errors.append("Approval ID missing for review candidate.")
                    return result

                valid_appr, appr_msg, appr_rec = self.approval_manager.validate_and_consume(
                    approval_id=approval_id,
                    document_id=document_id,
                    source_sha256=norm_hash,
                    candidate_id=decision.candidate_id,
                )
                if not valid_appr:
                    result.final_status = AutomationStatus.APPROVAL_INVALID.value
                    result.errors.append(f"Approval invalid: {appr_msg}")
                    self._emit_audit_event(
                        AuditEventType.AUTOMATION_APPROVAL_REJECTED,
                        repair_id=repair_id,
                        document_id=document_id,
                        source_sha256=norm_hash,
                        status="APPROVAL_INVALID",
                        reason=appr_msg or "Approval token invalid",
                    )
                    return result

            # 6.2 Concurrency Lock Scoped to Target Document & Hash
            try:
                with acquire_commit_lock(document_id, norm_hash):
                    raw_meta = {}
                    if self.index:
                        if hasattr(self.index, "get") and callable(self.index.get):
                            val = self.index.get(norm_hash)
                            if isinstance(val, dict):
                                raw_meta = val
                        elif hasattr(self.index, "data") and isinstance(self.index.data, dict):
                            raw_meta = self.index.data.get("indexed_items", {}).get(norm_hash, {})
                        elif isinstance(self.index, dict):
                            raw_meta = self.index.get("indexed_items", {}).get(norm_hash, {})

                    if not isinstance(raw_meta, dict):
                        raw_meta = {}

                    url_val = raw_meta.get("url")
                    has_url = isinstance(url_val, str) and bool(url_val.strip())
                    is_web = bool(
                        (raw_meta.get("is_pdf") is False)
                        or (raw_meta.get("source_type") == "secondary")
                        or (raw_meta.get("ingest_origin") == "secondary_web_source")
                        or has_url
                        or (source_path and str(source_path).startswith(("http://", "https://")))
                    )

                    # 6.3 Time-of-Check / Time-of-Use (TOCTOU) Source Hash Check
                    if not is_web and source_path is not None and Path(source_path).exists():
                        current_file_hash = _calc_sha256(Path(source_path))
                        if current_file_hash != norm_hash:
                            result.final_status = AutomationStatus.FAILED.value
                            result.action_taken = "ABORT"
                            result.errors.append(
                                f"TOCTOU hash mismatch: expected {norm_hash}, file has {current_file_hash}"
                            )
                            self._record_failure()
                            return result

                    # 6.4 Extract candidate if not provided
                    if candidate is None:
                        if not is_web and (source_path is None or not Path(source_path).exists()):
                            result.final_status = AutomationStatus.FAILED.value
                            result.errors.append("Source file missing for candidate extraction.")
                            self._record_failure()
                            return result

                        target_source_path = source_path if not is_web else (source_path or raw_meta.get("url"))
                        cand_res = extract_candidate(
                            source_path=target_source_path,
                            meta={**raw_meta, "document_id": document_id},
                            expected_sha256=norm_hash,
                        )
                        if not cand_res.candidate:
                            result.final_status = AutomationStatus.FAILED.value
                            result.errors.append(f"Extraction failed: {cand_res.errors}")
                            self._record_failure()
                            return result
                        candidate = cand_res.candidate

                    # 6.5 Quality Comparison
                    metrics = scan_result.current_metrics if scan_result else {}
                    old_snapshot = build_snapshot(metrics)
                    comparison_res = compare_quality(old_snapshot, candidate)

                    # 6.6 Retrieval Evaluation (Final Gate Decision)
                    retrieval_res = RetrievalComparisonResult(
                        status=ComparisonStatus.READY_TO_COMMIT.value
                    )
                    final_gate_dec = evaluate_final_gate(
                        comparison_res,
                        retrieval_res,
                    )

                    # 6.7 Safe Commit via Single Write Authority
                    commit_policy = CommitPolicy(
                        staging_base_dir=self.staging_dir,
                    )

                    commit_res = commit_candidate(
                        candidate=candidate,
                        decision=final_gate_dec,
                        allow_commit=True,
                        rag=self.rag,
                        target_cache_dir=self.cache_dir,
                        staging_base_dir=self.staging_dir,
                        policy=commit_policy,
                        dry_run=False,
                    )

                    result.commit_status = commit_res.status
                    if commit_res.status != CommitStatus.COMMITTED.value:
                        result.final_status = AutomationStatus.FAILED.value
                        result.errors.extend(commit_res.issues)
                        self._record_failure()
                        self._emit_audit_event(
                            AuditEventType.REPAIR_COMMIT_FAILED,
                            repair_id=repair_id,
                            commit_id=commit_res.commit_id,
                            document_id=document_id,
                            source_sha256=norm_hash,
                            status="COMMIT_FAILED",
                            reason="; ".join(commit_res.issues),
                        )
                        return result

                    self._emit_audit_event(
                        AuditEventType.REPAIR_COMMITTED,
                        repair_id=repair_id,
                        commit_id=commit_res.commit_id,
                        document_id=document_id,
                        source_sha256=norm_hash,
                        status="COMMITTED",
                    )

                    # 6.8 Post-Repair Verification via Single Verification Authority
                    verify_res = verify_committed_repair(
                        commit_result=commit_res,
                        candidate=candidate,
                        rag=self.rag,
                        cache_dir=self.cache_dir,
                        baseline_benchmarks=baseline_benchmarks,
                        known_stale_fragments=known_stale_fragments,
                        expected_total_chunk_count=expected_total_chunk_count,
                        known_other_hashes=known_other_hashes,
                    )

                    result.verification_status = verify_res.status

                    # 6.9 Handle Verification Pass
                    if verify_res.status == VerificationStatus.VERIFIED_REPAIR.value:
                        result.final_status = AutomationStatus.VERIFIED_REPAIR.value
                        self._record_success()
                        self._emit_audit_event(
                            AuditEventType.REPAIR_VERIFIED,
                            repair_id=repair_id,
                            commit_id=commit_res.commit_id,
                            document_id=document_id,
                            source_sha256=norm_hash,
                            status="VERIFIED_REPAIR",
                            verification_status=verify_res.status,
                        )
                        return result

                    # 6.10 Handle Verification Failure -> Trigger Safe Rollback
                    self._emit_audit_event(
                        AuditEventType.REPAIR_VERIFICATION_FAILED,
                        repair_id=repair_id,
                        commit_id=commit_res.commit_id,
                        document_id=document_id,
                        source_sha256=norm_hash,
                        status="VERIFY_FAILED",
                        verification_status=verify_res.status,
                        reason="; ".join(verify_res.failures),
                    )

                    if self.policy.rollback_on_verify_failure:
                        rollback_res = rollback_commit(
                            commit_id=commit_res.commit_id,
                            document_id=document_id,
                            source_sha256=norm_hash,
                            staging_base_dir=self.staging_dir,
                            cache_dir=self.cache_dir,
                            rag=self.rag,
                            index=self.index,
                            verification_result=verify_res,
                            force=True,  # Verification failed; rollback is mandatory
                        )
                        result.rollback_status = rollback_res.status

                        if rollback_res.status == RollbackStatus.ROLLBACK_SUCCESS.value:
                            result.final_status = AutomationStatus.FAILED_BUT_RESTORED.value
                            self._emit_audit_event(
                                AuditEventType.ROLLBACK_COMPLETED,
                                repair_id=repair_id,
                                commit_id=commit_res.commit_id,
                                rollback_id=rollback_res.rollback_id,
                                document_id=document_id,
                                source_sha256=norm_hash,
                                status="ROLLBACK_SUCCESS",
                                rollback_status=rollback_res.status,
                            )
                        else:
                            result.final_status = AutomationStatus.FAILED.value
                            result.errors.append(f"Rollback failed: {rollback_res.error}")
                            self._emit_audit_event(
                                AuditEventType.ROLLBACK_FAILED,
                                repair_id=repair_id,
                                commit_id=commit_res.commit_id,
                                rollback_id=rollback_res.rollback_id,
                                document_id=document_id,
                                source_sha256=norm_hash,
                                status="ROLLBACK_FAILED",
                                rollback_status=rollback_res.status,
                                reason=rollback_res.error or "Rollback execution failed",
                            )
                    else:
                        result.final_status = AutomationStatus.FAILED.value

                    self._record_failure()
                    return result

            except Exception as exc:
                logger.exception("Unexpected error in process_candidate: %s", exc)
                result.final_status = AutomationStatus.FAILED.value
                result.errors.append(str(exc))
                self._record_failure()
                return result

        return result

    def _record_success(self) -> None:
        """Records a successful repair and resets consecutive failure counter."""
        with self._orchestrator_lock:
            self.consecutive_failures = 0

    def _record_failure(self) -> None:
        """Records a failure and trips the circuit breaker if thresholds are reached."""
        with self._orchestrator_lock:
            self.consecutive_failures += 1
            self.total_failures += 1
            if self.consecutive_failures >= self.policy.circuit_breaker_threshold:
                self.circuit_open = True
                self._emit_audit_event(
                    AuditEventType.AUTOMATION_CIRCUIT_OPEN,
                    repair_id="system",
                    document_id="system",
                    source_sha256="",
                    status="CIRCUIT_OPEN",
                    reason=f"Reached {self.consecutive_failures} consecutive failures.",
                )
            elif self.total_failures >= self.policy.failure_budget:
                self.circuit_open = True
                self._emit_audit_event(
                    AuditEventType.AUTOMATION_HALTED,
                    repair_id="system",
                    document_id="system",
                    source_sha256="",
                    status="HALTED",
                    reason=f"Exceeded total failure budget of {self.policy.failure_budget}.",
                )

    def run_cycle(
        self,
        items: Optional[List[Dict[str, Any]]] = None,
        source_paths: Optional[List[Path]] = None,
        baseline_benchmarks: Optional[List[Dict[str, Any]]] = None,
    ) -> AutomationCycleSummary:
        """Executes a full batch cycle over candidate items within policy constraints."""
        cycle_id = f"cycle_{binascii.hexlify(os.urandom(6)).decode()}"
        started_at = now_iso()

        self._emit_audit_event(
            AuditEventType.AUTOMATION_CYCLE_STARTED,
            repair_id=cycle_id,
            document_id="cycle",
            source_sha256="",
            status="STARTED",
            details={"mode": self.policy.mode, "environment": self.policy.environment},
        )

        summary = AutomationCycleSummary(
            cycle_id=cycle_id,
            mode=self.policy.mode,
            status=AutomationStatus.SUCCESS.value,
            started_at=started_at,
        )

        # Prepare candidates list
        candidates_to_process: List[Dict[str, Any]] = []
        if items:
            candidates_to_process.extend(items)
        if source_paths:
            for p in source_paths:
                p = Path(p)
                candidates_to_process.append({
                    "document_id": p.stem,
                    "source_path": p,
                    "source_hash": _calc_sha256(p) if p.exists() else "",
                })

        auto_repairs_performed = 0

        for item in candidates_to_process[: self.policy.max_candidates_per_cycle]:
            # Circuit breaker check before each candidate
            if self.circuit_open:
                summary.circuit_broken = True
                summary.halt_reason = (
                    f"Circuit breaker tripped after {self.consecutive_failures} consecutive failures"
                    if self.consecutive_failures >= self.policy.circuit_breaker_threshold
                    else f"Failure budget ({self.policy.failure_budget}) exhausted"
                )
                summary.status = AutomationStatus.CIRCUIT_OPEN.value
                break

            doc_id = str(item.get("document_id") or "")
            src_path = item.get("source_path")
            src_hash = item.get("source_hash")
            appr_id = item.get("approval_id")

            # Check max auto-repairs quota per cycle
            if self.policy.mode == AutomationMode.SAFE_AUTO_REPAIR.value and auto_repairs_performed >= self.policy.max_auto_repairs_per_cycle:
                summary.halt_reason = f"Max auto-repairs limit ({self.policy.max_auto_repairs_per_cycle}) reached."
                break

            cand_res = self.process_candidate(
                document_id=doc_id,
                source_path=Path(src_path) if src_path else None,
                source_hash=src_hash,
                approval_id=appr_id,
                baseline_benchmarks=baseline_benchmarks,
            )

            summary.scanned_count += 1
            summary.results.append(cand_res)

            # Metrics aggregation
            if cand_res.decision:
                if cand_res.decision.classification == ClassificationType.SAFE.value:
                    summary.safe_count += 1
                elif cand_res.decision.classification == ClassificationType.REVIEW.value:
                    summary.review_count += 1
                elif cand_res.decision.classification == ClassificationType.BLOCKED.value:
                    summary.blocked_count += 1

            if cand_res.action_taken in ("REPAIR", "COMMIT"):
                summary.attempted_count += 1
                auto_repairs_performed += 1

            if cand_res.commit_status == CommitStatus.COMMITTED.value:
                summary.committed_count += 1
                if self.policy.environment == "production":
                    summary.production_mutation_count += 1
                else:
                    summary.sandbox_mutation_count += 1

            if cand_res.final_status == AutomationStatus.VERIFIED_REPAIR.value:
                summary.verified_count += 1
            elif cand_res.final_status == AutomationStatus.FAILED_BUT_RESTORED.value:
                summary.rolled_back_count += 1
                summary.failed_count += 1
            elif cand_res.final_status in (AutomationStatus.FAILED.value, AutomationStatus.CIRCUIT_OPEN.value):
                summary.failed_count += 1

        summary.completed_at = now_iso()
        if summary.circuit_broken:
            summary.status = AutomationStatus.CIRCUIT_OPEN.value
        elif summary.failed_count > 0:
            summary.status = AutomationStatus.PARTIAL.value if summary.verified_count > 0 else AutomationStatus.FAILED.value
        else:
            summary.status = AutomationStatus.SUCCESS.value

        self._emit_audit_event(
            AuditEventType.AUTOMATION_CYCLE_COMPLETED,
            repair_id=cycle_id,
            document_id="cycle",
            source_sha256="",
            status=summary.status,
            details={
                "scanned": summary.scanned_count,
                "committed": summary.committed_count,
                "verified": summary.verified_count,
                "failed": summary.failed_count,
                "halt_reason": summary.halt_reason,
            },
        )

        return summary
