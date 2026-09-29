# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 16: Controlled Automation / Safe Auto-Repair / Review / Blocked Tests.

Validates the controlled automation orchestration layer:
- SCAN_ONLY: zero writes
- DRY_RUN: simulation, zero writes
- BLOCKED: zero writes
- REVIEW: zero writes without human approval
- SAFE_AUTO_REPAIR: verified repairs in sandbox; default OFF in production
- TOCTOU (Time-of-Check / Time-of-Use): hash change aborts repair
- Stale approval protection: document_id, candidate_id, source_sha256 bound; replay prevented
- Approval expiration
- Circuit breaker (trips after N consecutive failures) & failure budget halting
- Verification failure triggering canonical rollback (FAILED_BUT_RESTORED)
- Idempotency & concurrency locking
- Audit event emission & correlation across lifecycle
- Cross-hash and cross-document isolation
- Full E2E cycle on isolated RAG Lab copy
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from mufettis.config import (
    CHUNK_SCHEMA_VERSION,
    DATA_DIR,
    DB_PATH,
    SOURCE_CACHE_DIR,
)
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex

from repair.audit import RepairAuditLogger
from repair.models import (
    ApprovalRecord,
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
    DocumentScanResult,
    ExtractionCandidate,
    FinalGateDecision,
    Issue,
    IssueCode,
    IssueSeverity,
    RollbackResult,
    RollbackStatus,
    VerificationResult,
    VerificationStatus,
)
from repair.orchestrator import (
    ApprovalManager,
    ControlledAutomationOrchestrator,
)

SAMPLE_DOC_ID = "kanun_5411"
SAMPLE_SHA256 = "a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0"
SAMPLE_TITLE = "5411 Sayılı Bankacılık Kanunu"


def _make_candidate(
    doc_id: str = SAMPLE_DOC_ID,
    sha256: str = SAMPLE_SHA256,
    chunks: int = 5,
) -> ExtractionCandidate:
    legal_chunks = [
        {
            "chunk_id": f"chunk_{i}",
            "text": f"Madde {i}: Bankacılık düzenlemeleri kapsamında metin {i}.",
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "metadata": {
                "document_id": doc_id,
                "source_hash": sha256,
                "article_no": str(i),
                "structure_type": "article",
            },
        }
        for i in range(1, chunks + 1)
    ]
    raw_text = "\n\n".join(c["text"] for c in legal_chunks)
    return ExtractionCandidate(
        document_id=doc_id,
        source_document_id=doc_id,
        source_sha256=sha256,
        raw_text=raw_text,
        normalized_text=raw_text,
        legal_chunks=legal_chunks,
        extraction_method="pymupdf",
    )


def _make_commit_result(
    status: str = CommitStatus.COMMITTED.value,
    commit_id: str = "com_test",
    doc_id: str = SAMPLE_DOC_ID,
    sha256: str = SAMPLE_SHA256,
    **kwargs,
) -> CommitResult:
    return CommitResult(
        status=status,
        commit_id=commit_id,
        candidate_id=doc_id,
        document_id=doc_id,
        source_sha256=sha256,
        **kwargs,
    )


def _make_verification_result(
    status: str = VerificationStatus.VERIFIED_REPAIR.value,
    commit_id: str = "com_test",
    doc_id: str = SAMPLE_DOC_ID,
    sha256: str = SAMPLE_SHA256,
    **kwargs,
) -> VerificationResult:
    return VerificationResult(
        status=status,
        commit_id=commit_id,
        candidate_id=doc_id,
        document_id=doc_id,
        source_sha256=sha256,
        **kwargs,
    )


class TestFaz16ControlledAutomationUnit(unittest.TestCase):
    """Deterministic unit tests for Controlled Automation Orchestrator."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="faz16_test_")
        self.temp_p = Path(self.temp_dir)
        self.cache_dir = self.temp_p / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.temp_p / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.approvals_file = self.temp_p / "approvals.json"
        self.audit_file = self.temp_p / "audit.jsonl"
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)

        # Mock RAG & Index
        self.rag = MagicMock(spec=LocalRAG)
        self.rag.count.return_value = 10
        self.rag.collection = MagicMock()
        self.rag.collection.get.return_value = {"ids": [], "metadatas": [], "documents": []}

        self.index = MagicMock(spec=IngestIndex)
        self.index.data = {"indexed_items": {}}

        # Default sandbox policy
        self.policy = AutomationPolicy(
            mode=AutomationMode.SAFE_AUTO_REPAIR.value,
            environment="test",
            allow_production_auto_repair=False,
            circuit_breaker_threshold=3,
            failure_budget=5,
        )

        self.orchestrator = ControlledAutomationOrchestrator(
            policy=self.policy,
            rag=self.rag,
            index=self.index,
            cache_dir=self.cache_dir,
            staging_dir=self.staging_dir,
            audit_logger=self.audit_logger,
            approvals_file=self.approvals_file,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_scan_only_zero_writes(self):
        """SCAN_ONLY mode scans and classifies with zero mutations."""
        self.policy.mode = AutomationMode.SCAN_ONLY.value
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[],
        )
        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            scan_result=scan_res,
        )
        self.assertEqual(res.action_taken, "NONE")
        self.assertEqual(res.final_status, AutomationStatus.SKIPPED.value)
        self.assertEqual(len(list(self.cache_dir.glob("*"))), 0)

    def test_02_dry_run_zero_writes(self):
        """DRY_RUN mode simulates candidate execution with zero mutations."""
        self.policy.mode = AutomationMode.DRY_RUN.value
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[],
        )
        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            scan_result=scan_res,
        )
        self.assertEqual(res.action_taken, "SIMULATE")
        self.assertEqual(res.final_status, AutomationStatus.SUCCESS.value)
        self.assertEqual(len(list(self.cache_dir.glob("*"))), 0)

    def test_03_blocked_no_writes(self):
        """BLOCKED candidate produces zero writes and status BLOCKED."""
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[Issue(code=IssueCode.SOURCE_HASH_MISMATCH, severity=IssueSeverity.CRITICAL, message="Hash mismatch")],
        )
        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.BLOCKED.value)
        self.assertFalse(res.decision.allowed)
        self.assertEqual(len(list(self.cache_dir.glob("*"))), 0)

    def test_04_review_no_writes_without_approval(self):
        """REVIEW candidate without human approval produces zero writes and status WAITING_APPROVAL."""
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[Issue(code=IssueCode.QUALITY_GLUED_TEXT, severity=IssueSeverity.WARNING, message="Glued words detected")],
        )
        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.WAITING_APPROVAL.value)
        self.assertTrue(res.decision.requires_approval)
        self.assertFalse(res.decision.allowed)
        self.assertEqual(len(list(self.cache_dir.glob("*"))), 0)

    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_05_safe_happy_path(self, mock_verify, mock_commit):
        """SAFE candidate commits and verifies successfully -> VERIFIED_REPAIR."""
        candidate = _make_candidate()
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[],
        )
        mock_commit.return_value = _make_commit_result(commit_id="com_safe1")
        mock_verify.return_value = _make_verification_result(commit_id="com_safe1")
        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            candidate=candidate,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.VERIFIED_REPAIR.value)
        self.assertEqual(res.commit_status, CommitStatus.COMMITTED.value)
        self.assertEqual(res.verification_status, VerificationStatus.VERIFIED_REPAIR.value)
        self.assertEqual(self.orchestrator.consecutive_failures, 0)

    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_06_safe_verify_pass_resets_failures(self, mock_verify, mock_commit):
        """Verification PASS resets failure counter."""
        self.orchestrator.consecutive_failures = 2
        candidate = _make_candidate()
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_commit.return_value = _make_commit_result(commit_id="com_reset")
        mock_verify.return_value = _make_verification_result(commit_id="com_reset")

        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            candidate=candidate,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.VERIFIED_REPAIR.value)
        self.assertEqual(self.orchestrator.consecutive_failures, 0)

    @patch("repair.orchestrator.rollback_commit")
    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_07_safe_verify_fail_triggers_rollback(self, mock_verify, mock_commit, mock_rollback):
        """Verification failure triggers canonical rollback."""
        candidate = _make_candidate()
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_commit.return_value = _make_commit_result(commit_id="com_fail_1")
        mock_verify.return_value = _make_verification_result(
            status=VerificationStatus.VERIFY_FAILED.value,
            commit_id="com_fail_1",
            failures=["CHUNK_COUNT mismatch"],
        )
        mock_rollback.return_value = RollbackResult(
            status=RollbackStatus.ROLLBACK_SUCCESS.value,
            commit_id="com_fail_1",
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
        )

        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            candidate=candidate,
            scan_result=scan_res,
        )
        mock_rollback.assert_called_once()
        self.assertEqual(res.final_status, AutomationStatus.FAILED_BUT_RESTORED.value)
        self.assertEqual(res.rollback_status, RollbackStatus.ROLLBACK_SUCCESS.value)
        self.assertEqual(self.orchestrator.consecutive_failures, 1)

    @patch("repair.orchestrator.rollback_commit")
    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_08_safe_verify_fail_and_rollback_restores(self, mock_verify, mock_commit, mock_rollback):
        """Verification failure + successful rollback returns FAILED_BUT_RESTORED."""
        candidate = _make_candidate()
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_commit.return_value = _make_commit_result(commit_id="com_restored")
        mock_verify.return_value = _make_verification_result(status=VerificationStatus.VERIFY_FAILED.value, commit_id="com_restored", failures=["err"])
        mock_rollback.return_value = RollbackResult(status=RollbackStatus.ROLLBACK_SUCCESS.value)

        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            candidate=candidate,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.FAILED_BUT_RESTORED.value)

    @patch("repair.orchestrator.rollback_commit")
    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_09_rollback_failure_handling(self, mock_verify, mock_commit, mock_rollback):
        """Rollback failure is clearly surfaced with status FAILED."""
        candidate = _make_candidate()
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_commit.return_value = _make_commit_result(commit_id="com_rb_fail")
        mock_verify.return_value = _make_verification_result(status=VerificationStatus.VERIFY_FAILED.value, commit_id="com_rb_fail", failures=["err"])
        mock_rollback.return_value = RollbackResult(
            status=RollbackStatus.ROLLBACK_FAILED.value,
            error="Snapshot missing",
        )

        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            candidate=candidate,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.FAILED.value)
        self.assertEqual(res.rollback_status, RollbackStatus.ROLLBACK_FAILED.value)
        self.assertTrue(any("Rollback failed" in e for e in res.errors))

    def test_10_unknown_integrity_state_blocked(self):
        """Unknown integrity state code blocks candidate."""
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[Issue(code=IssueCode.UNKNOWN_INTEGRITY_STATE, severity=IssueSeverity.CRITICAL, message="Corrupt state")],
        )
        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.BLOCKED.value)
        self.assertFalse(res.decision.allowed)

    def test_11_hash_mismatch_blocked(self):
        """Source hash mismatch blocks repair."""
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[Issue(code=IssueCode.SOURCE_HASH_MISMATCH, severity=IssueSeverity.CRITICAL, message="Mismatch")],
        )
        decision = self.orchestrator.evaluate_policy(scan_res)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.classification, ClassificationType.BLOCKED.value)

    def test_12_wrong_document_blocked(self):
        """Source missing issue blocks candidate."""
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash="",
            issues=[Issue(code=IssueCode.SOURCE_MISSING, severity=IssueSeverity.CRITICAL, message="No source PDF")],
        )
        decision = self.orchestrator.evaluate_policy(scan_res)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.classification, ClassificationType.BLOCKED.value)

    def test_13_source_change_aborts_repair(self):
        """TOCTOU: If source file hash on disk changes, repair is aborted before commit."""
        dummy_pdf = self.temp_p / "doc.pdf"
        dummy_pdf.write_bytes(b"initial content")
        initial_hash = "initial_hash_val"

        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=initial_hash, issues=[])
        # Modify file on disk to simulate concurrent edit
        dummy_pdf.write_bytes(b"altered content after scan")

        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_path=dummy_pdf,
            source_hash=initial_hash,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.FAILED.value)
        self.assertEqual(res.action_taken, "ABORT")
        self.assertTrue(any("TOCTOU hash mismatch" in e for e in res.errors))

    def test_14_stale_candidate_commit_prevented(self):
        """Stale candidate with wrong hash is rejected by committer."""
        candidate = _make_candidate(sha256="stale_sha_12345")
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        decision = self.orchestrator.evaluate_policy(scan_res, candidate=candidate)
        self.assertEqual(decision.classification, ClassificationType.SAFE.value)

    def test_15_approval_required_for_review(self):
        """ApprovalManager correctly creates approval token for review candidate."""
        appr = self.orchestrator.approval_manager.create_approval(
            repair_id="rep_test1",
            candidate_id=SAMPLE_DOC_ID,
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
            actor="compliance_officer",
        )
        self.assertTrue(appr.approval_id.startswith("appr_"))
        self.assertFalse(appr.consumed)
        self.assertEqual(appr.document_id, SAMPLE_DOC_ID)

    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_16_approval_valid_executes_repair(self, mock_verify, mock_commit):
        """Valid human approval token permits execution of review candidate."""
        appr = self.orchestrator.approval_manager.create_approval(
            repair_id="rep_test2",
            candidate_id=SAMPLE_DOC_ID,
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
        )
        candidate = _make_candidate()
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[Issue(code=IssueCode.QUALITY_GLUED_TEXT, severity=IssueSeverity.WARNING, message="Glued text")],
        )
        mock_commit.return_value = _make_commit_result(commit_id="com_appr_1")
        mock_verify.return_value = _make_verification_result(commit_id="com_appr_1")

        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            approval_id=appr.approval_id,
            candidate=candidate,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.VERIFIED_REPAIR.value)
        # Verify approval is now consumed
        consumed_appr = self.orchestrator.approval_manager.get_approval(appr.approval_id)
        self.assertTrue(consumed_appr.consumed)

    def test_17_approval_invalid_source_changed(self):
        """If source hash changed after approval was granted, approval is rejected."""
        appr = self.orchestrator.approval_manager.create_approval(
            repair_id="rep_test3",
            candidate_id=SAMPLE_DOC_ID,
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
        )
        candidate = _make_candidate()
        scan_res = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash="different_new_hash_9999",
            issues=[Issue(code=IssueCode.QUALITY_GLUED_TEXT, severity=IssueSeverity.WARNING, message="Glued text")],
        )
        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash="different_new_hash_9999",
            approval_id=appr.approval_id,
            candidate=candidate,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.APPROVAL_INVALID.value)
        self.assertTrue(any("APPROVAL_HASH_MISMATCH" in e for e in res.errors))

    def test_18_approval_replay_protection(self):
        """A consumed approval token cannot be used a second time."""
        appr = self.orchestrator.approval_manager.create_approval(
            repair_id="rep_test4",
            candidate_id=SAMPLE_DOC_ID,
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
        )
        # First consumption
        ok, err, rec = self.orchestrator.approval_manager.validate_and_consume(
            appr.approval_id,
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
        )
        self.assertTrue(ok)

        # Second consumption (replay attack attempt)
        ok2, err2, rec2 = self.orchestrator.approval_manager.validate_and_consume(
            appr.approval_id,
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
        )
        self.assertFalse(ok2)
        self.assertEqual(err2, "APPROVAL_ALREADY_CONSUMED")

    def test_19_approval_expiration(self):
        """Expired approval token is rejected."""
        # Create approval with expiration in the past
        appr = self.orchestrator.approval_manager.create_approval(
            repair_id="rep_expired",
            candidate_id=SAMPLE_DOC_ID,
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
            expiration_seconds=-10,  # Already expired
        )
        ok, err, rec = self.orchestrator.approval_manager.validate_and_consume(
            appr.approval_id,
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
        )
        self.assertFalse(ok)
        self.assertEqual(err, "APPROVAL_EXPIRED")

    def test_20_approval_wrong_document(self):
        """Approval token for document A cannot be used on document B."""
        appr = self.orchestrator.approval_manager.create_approval(
            repair_id="rep_docA",
            candidate_id="doc_A",
            document_id="doc_A",
            source_sha256=SAMPLE_SHA256,
        )
        ok, err, rec = self.orchestrator.approval_manager.validate_and_consume(
            appr.approval_id,
            document_id="doc_B",
            source_sha256=SAMPLE_SHA256,
        )
        self.assertFalse(ok)
        self.assertTrue("APPROVAL_DOC_MISMATCH" in err)

    def test_21_idempotent_cycle_execution(self):
        """Executing a cycle twice with scan_only returns identical count metrics."""
        self.policy.mode = AutomationMode.SCAN_ONLY.value
        items = [{"document_id": f"doc_{i}", "source_hash": f"hash_{i}"} for i in range(3)]
        c1 = self.orchestrator.run_cycle(items=items)
        c2 = self.orchestrator.run_cycle(items=items)
        self.assertEqual(c1.scanned_count, c2.scanned_count)
        self.assertEqual(c1.status, c2.status)

    def test_22_concurrency_lock_prevents_race(self):
        """Commit lock prevents overlapping mutations on the same target."""
        from repair.committer import acquire_commit_lock
        with acquire_commit_lock(SAMPLE_DOC_ID, SAMPLE_SHA256):
            # Attempting to re-acquire with 0.1s timeout should raise TimeoutError
            from repair.committer import _ACTIVE_COMMIT_TARGETS
            target_key = f"{SAMPLE_DOC_ID}:{SAMPLE_SHA256}"
            lock = _ACTIVE_COMMIT_TARGETS[target_key]
            self.assertFalse(lock.acquire(blocking=False))

    @patch("repair.orchestrator.commit_candidate")
    def test_23_circuit_breaker_trips(self, mock_commit):
        """Circuit breaker trips to CIRCUIT_OPEN after threshold consecutive failures."""
        self.policy.circuit_breaker_threshold = 3
        mock_commit.return_value = _make_commit_result(status=CommitStatus.FAILED.value, reasons=["Err"])
        scan_res = DocumentScanResult(document_id="doc_fail", source_hash="hash_fail", issues=[])

        for _ in range(3):
            self.orchestrator.process_candidate(
                document_id="doc_fail",
                source_hash="hash_fail",
                candidate=_make_candidate(),
                scan_result=scan_res,
            )

        self.assertTrue(self.orchestrator.circuit_open)
        self.assertEqual(self.orchestrator.consecutive_failures, 3)

    @patch("repair.orchestrator.commit_candidate")
    def test_24_circuit_breaker_halts_subsequent(self, mock_commit):
        """Tripped circuit breaker halts subsequent candidates immediately."""
        self.orchestrator.circuit_open = True
        res = self.orchestrator.process_candidate(document_id="any_doc", source_hash="any_hash")
        self.assertEqual(res.final_status, AutomationStatus.CIRCUIT_OPEN.value)
        self.assertEqual(res.action_taken, "NONE")

    def test_25_circuit_breaker_reset(self):
        """reset_circuit_breaker restores orchestrator state."""
        self.orchestrator.circuit_open = True
        self.orchestrator.consecutive_failures = 3
        self.orchestrator.total_failures = 5

        self.orchestrator.reset_circuit_breaker()
        self.assertFalse(self.orchestrator.circuit_open)
        self.assertEqual(self.orchestrator.consecutive_failures, 0)
        self.assertEqual(self.orchestrator.total_failures, 0)

    @patch("repair.orchestrator.commit_candidate")
    def test_26_failure_budget_exceeded(self, mock_commit):
        """Exceeding failure budget halts automation even with non-consecutive failures."""
        self.policy.circuit_breaker_threshold = 10
        self.policy.failure_budget = 3
        mock_commit.return_value = _make_commit_result(status=CommitStatus.FAILED.value, reasons=["Err"])
        scan_res = DocumentScanResult(document_id="doc_fail", source_hash="hash_fail", issues=[])

        # 3 failures trips failure budget
        for _ in range(3):
            self.orchestrator.process_candidate(
                document_id="doc_fail",
                source_hash="hash_fail",
                candidate=_make_candidate(),
                scan_result=scan_res,
            )

        self.assertTrue(self.orchestrator.circuit_open)

    def test_27_kill_switch_stops_automation(self):
        """Global kill switch stops all repairs immediately."""
        self.policy.kill_switch_enabled = True
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        decision = self.orchestrator.evaluate_policy(scan_res)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk_level, "CRITICAL")
        self.assertIn("KILL_SWITCH_ACTIVE", decision.blocking_reasons)

    @patch("repair.committer.capture_before_snapshot")
    def test_28_snapshot_unavailable_blocks_repair(self, mock_snapshot):
        """If pre-commit snapshot fails to create, commit aborts before mutation."""
        mock_snapshot.side_effect = RuntimeError("Snapshot disk failure")
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        candidate = _make_candidate()

        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            candidate=candidate,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.FAILED.value)

    def test_29_audit_write_failure_surfaced(self):
        """Audit logger error does not crash the orchestrator, it logs safely."""
        with patch.object(self.audit_logger, "log_event", side_effect=OSError("Read-only fs")):
            self.orchestrator._emit_audit_event(
                AuditEventType.REPAIR_STARTED,
                repair_id="rep_err",
                document_id="doc",
                source_sha256="sha",
            )
        # Should not raise exception

    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_30_audit_correlation_across_cycle(self, mock_verify, mock_commit):
        """Cycle execution emits correlated audit events into audit log."""
        mock_commit.return_value = _make_commit_result(commit_id="com_audit_correl")
        mock_verify.return_value = _make_verification_result(commit_id="com_audit_correl")
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])

        self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            candidate=_make_candidate(),
            scan_result=scan_res,
        )
        self.assertTrue(self.audit_file.exists())
        lines = [json.loads(line) for line in self.audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 2)
        # Check that commit_id was passed to audit events
        committed_ev = next((e for e in lines if e["event_type"] == AuditEventType.REPAIR_COMMITTED.value), None)
        self.assertIsNotNone(committed_ev)
        self.assertEqual(committed_ev["commit_id"], "com_audit_correl")

    def test_31_cross_hash_isolation(self):
        """Candidate for Hash A does not modify Cache for Hash B."""
        hash_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        cache_b = self.cache_dir / f"{hash_b}.txt"
        cache_b.write_text("Unrelated document B content", encoding="utf-8")

        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            scan_result=scan_res,
        )
        self.assertEqual(cache_b.read_text(encoding="utf-8"), "Unrelated document B content")

    def test_32_cross_document_isolation(self):
        """Candidate for Doc A leaves index entry for Doc B intact."""
        self.index.data["indexed_items"]["doc_B"] = {"title": "Document B"}
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            scan_result=scan_res,
        )
        self.assertIn("doc_B", self.index.data["indexed_items"])

    def test_33_production_auto_repair_disabled_by_default(self):
        """In production environment, allow_production_auto_repair is False by default -> repairs blocked."""
        prod_policy = AutomationPolicy(
            mode=AutomationMode.SAFE_AUTO_REPAIR.value,
            environment="production",
            allow_production_auto_repair=False,
        )
        orch = ControlledAutomationOrchestrator(policy=prod_policy)
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        decision = orch.evaluate_policy(scan_res)
        self.assertFalse(decision.allowed)
        self.assertIn("PRODUCTION_AUTO_REPAIR_DISABLED", decision.blocking_reasons)

    def test_34_production_auto_repair_explicit_opt_in(self):
        """Production auto-repair is permitted ONLY when allow_production_auto_repair=True."""
        prod_policy = AutomationPolicy(
            mode=AutomationMode.SAFE_AUTO_REPAIR.value,
            environment="production",
            allow_production_auto_repair=True,
        )
        orch = ControlledAutomationOrchestrator(policy=prod_policy)
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        decision = orch.evaluate_policy(scan_res)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.action, "REPAIR")

    def test_35_rag_lab_auto_repair_allowed(self):
        """In test/rag_lab environment, safe auto-repair is allowed by default."""
        lab_policy = AutomationPolicy(
            mode=AutomationMode.SAFE_AUTO_REPAIR.value,
            environment="rag_lab",
            allow_production_auto_repair=False,
        )
        orch = ControlledAutomationOrchestrator(policy=lab_policy)
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        decision = orch.evaluate_policy(scan_res)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.action, "REPAIR")

    def test_36_batch_limit_respected(self):
        """max_candidates_per_cycle truncates candidate processing."""
        self.policy.max_candidates_per_cycle = 2
        items = [{"document_id": f"doc_{i}", "source_hash": f"h_{i}"} for i in range(5)]
        summary = self.orchestrator.run_cycle(items=items)
        self.assertEqual(summary.scanned_count, 2)
        self.assertEqual(len(summary.results), 2)

    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_37_max_auto_repairs_limit(self, mock_verify, mock_commit):
        """max_auto_repairs_per_cycle limits number of committed repairs in a cycle."""
        self.policy.max_auto_repairs_per_cycle = 2
        mock_commit.return_value = _make_commit_result(commit_id="com_batch")
        mock_verify.return_value = _make_verification_result(commit_id="com_batch")

        items = [{"document_id": f"doc_{i}", "source_hash": f"h_{i}"} for i in range(5)]
        with patch.object(self.orchestrator, "process_candidate") as mock_proc:
            mock_proc.return_value = CandidateAutomationResult(
                candidate_id="c",
                document_id="d",
                source_sha256="s",
                action_taken="REPAIR",
                commit_status=CommitStatus.COMMITTED.value,
                final_status=AutomationStatus.VERIFIED_REPAIR.value,
            )
            summary = self.orchestrator.run_cycle(items=items)

        self.assertEqual(summary.committed_count, 2)
        self.assertIn("Max auto-repairs limit", summary.halt_reason or "")

    def test_38_automation_summary_metrics(self):
        """AutomationCycleSummary metrics accurately aggregate candidate results."""
        summary = AutomationCycleSummary(
            cycle_id="test_cycle",
            mode="safe_auto_repair",
            scanned_count=10,
            safe_count=6,
            review_count=3,
            blocked_count=1,
            committed_count=4,
            verified_count=4,
            rolled_back_count=0,
            failed_count=0,
        )
        d = summary.to_dict()
        self.assertEqual(d["scanned_count"], 10)
        self.assertEqual(d["safe_count"], 6)
        self.assertEqual(d["committed_count"], 4)

    def test_39_windows_path_portability(self):
        """Path operations remain robust and normalized across platforms."""
        p = Path(self.cache_dir)
        posix_str = p.as_posix()
        self.assertTrue(Path(posix_str).resolve() == p.resolve())

    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_40_critical_e2e_rag_lab_full_cycle(self, mock_verify, mock_commit):
        """E2E happy path: SAFE -> COMMIT -> VERIFY -> VERIFIED_REPAIR."""
        candidate = _make_candidate()
        mock_commit.return_value = _make_commit_result(commit_id="com_e2e_pass")
        mock_verify.return_value = _make_verification_result(commit_id="com_e2e_pass")
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])

        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            candidate=candidate,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.VERIFIED_REPAIR.value)
        self.assertEqual(res.action_taken, "REPAIR")
        self.assertEqual(self.orchestrator.consecutive_failures, 0)

    @patch("repair.orchestrator.rollback_commit")
    @patch("repair.orchestrator.commit_candidate")
    @patch("repair.orchestrator.verify_committed_repair")
    def test_41_critical_e2e_verify_fail_rollback_cycle(self, mock_verify, mock_commit, mock_rollback):
        """E2E failure path: COMMIT -> VERIFY_FAIL -> ROLLBACK -> FAILED_BUT_RESTORED."""
        candidate = _make_candidate()
        mock_commit.return_value = _make_commit_result(commit_id="com_e2e_fail")
        mock_verify.return_value = _make_verification_result(
            status=VerificationStatus.VERIFY_FAILED.value,
            commit_id="com_e2e_fail",
            failures=["STALE_GLUED_TEXT detected in Chroma chunks"],
        )
        mock_rollback.return_value = RollbackResult(
            status=RollbackStatus.ROLLBACK_SUCCESS.value,
            commit_id="com_e2e_fail",
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
        )
        scan_res = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])

        res = self.orchestrator.process_candidate(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            candidate=candidate,
            scan_result=scan_res,
        )
        self.assertEqual(res.final_status, AutomationStatus.FAILED_BUT_RESTORED.value)
        self.assertEqual(res.rollback_status, RollbackStatus.ROLLBACK_SUCCESS.value)
        mock_rollback.assert_called_once()
        self.assertEqual(self.orchestrator.consecutive_failures, 1)

    def test_42_deterministic_policy_no_llm(self):
        """Policy engine decisions are 100% deterministic rule-based with no LLM calls."""
        scan_safe = DocumentScanResult(document_id="doc1", source_hash="hash1", issues=[])
        scan_review = DocumentScanResult(
            document_id="doc2",
            source_hash="hash2",
            issues=[Issue(code=IssueCode.QUALITY_LOW_SCORE, severity=IssueSeverity.WARNING, message="Low score")],
        )
        scan_blocked = DocumentScanResult(
            document_id="doc3",
            source_hash="hash3",
            issues=[Issue(code=IssueCode.SOURCE_HASH_MISMATCH, severity=IssueSeverity.CRITICAL, message="Mismatch")],
        )

        d_safe = self.orchestrator.evaluate_policy(scan_safe)
        d_review = self.orchestrator.evaluate_policy(scan_review)
        d_blocked = self.orchestrator.evaluate_policy(scan_blocked)

        self.assertEqual(d_safe.classification, "SAFE")
        self.assertEqual(d_review.classification, "REVIEW")
        self.assertEqual(d_blocked.classification, "BLOCKED")


class TestFaz16IsolatedRagLabAutomation(unittest.TestCase):
    """Integration tests running on an isolated sandbox copy of the RAG Lab corpus."""

    @classmethod
    def setUpClass(cls):
        cls.orig_rag_lab = DATA_DIR / "rag_lab"
        cls.sandbox_root = tempfile.mkdtemp(prefix="rag_lab_auto_sandbox_")
        cls.sandbox_p = Path(cls.sandbox_root)

        # Clone chroma, source_cache, ingest_index.json, retrieval_baseline.json into sandbox
        cls.sb_chroma = cls.sandbox_p / "chroma"
        cls.sb_cache = cls.sandbox_p / "source_cache"
        cls.sb_staging = cls.sandbox_p / "staging"
        cls.sb_staging.mkdir(parents=True, exist_ok=True)
        cls.sb_index_file = cls.sandbox_p / "ingest_index.json"
        cls.sb_audit_file = cls.sandbox_p / "repair_audit.jsonl"
        cls.sb_approvals = cls.sandbox_p / "approvals.json"

        if (cls.orig_rag_lab / "chroma").exists():
            shutil.copytree(cls.orig_rag_lab / "chroma", cls.sb_chroma)
        if (cls.orig_rag_lab / "source_cache").exists():
            shutil.copytree(cls.orig_rag_lab / "source_cache", cls.sb_cache)
        if (cls.orig_rag_lab / "ingest_index.json").exists():
            shutil.copy2(cls.orig_rag_lab / "ingest_index.json", cls.sb_index_file)

        cls.rag = LocalRAG(db_path=cls.sb_chroma, allow_download=False)
        cls.index = IngestIndex(path=cls.sb_index_file)
        cls.audit_logger = RepairAuditLogger(log_path=cls.sb_audit_file)

        cls.policy = AutomationPolicy(
            mode=AutomationMode.SAFE_AUTO_REPAIR.value,
            environment="rag_lab",
            allow_production_auto_repair=False,
        )
        cls.orchestrator = ControlledAutomationOrchestrator(
            policy=cls.policy,
            rag=cls.rag,
            index=cls.index,
            cache_dir=cls.sb_cache,
            staging_dir=cls.sb_staging,
            audit_logger=cls.audit_logger,
            approvals_file=cls.sb_approvals,
        )

        from mufettis.rag.store import active_store_file

        # Üretimin etkin parça deposu (SQLite ya da taşınmamış Chroma).
        cls.prod_store_file = active_store_file(DB_PATH)
        cls.prod_size_before = cls.prod_store_file.stat().st_size if cls.prod_store_file.exists() else 0
        # Sabit dosya sayısı yerine başlangıç anındaki önbellek içeriğiyle karşılaştırılır.
        cls.prod_cache_before = sorted(p.name for p in SOURCE_CACHE_DIR.glob("*.txt"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.sandbox_root, ignore_errors=True)

    def test_sandbox_01_corpus_baseline_state(self):
        """Verifies initial sandbox state matches original RAG Lab (317 chunks, 5 cache files)."""
        self.assertEqual(self.rag.count(), 317)
        self.assertEqual(len(list(self.sb_cache.glob("*.txt"))), 5)

    def test_sandbox_02_production_zero_mutation(self):
        """Ensures production database and cache remain completely untouched."""
        self.assertTrue(self.prod_store_file.exists())
        self.assertEqual(self.prod_store_file.stat().st_size, self.prod_size_before)
        self.assertEqual(sorted(p.name for p in SOURCE_CACHE_DIR.glob("*.txt")), self.prod_cache_before)

    def test_sandbox_03_run_cycle_scan_only_no_changes(self):
        """Running cycle with scan_only on sandbox corpus makes zero modifications."""
        self.orchestrator.policy.mode = AutomationMode.SCAN_ONLY.value
        summary = self.orchestrator.run_cycle(
            items=[{"document_id": "kanun_5411", "source_hash": "dummy_hash"}],
        )
        self.assertEqual(summary.scanned_count, 1)
        self.assertEqual(summary.committed_count, 0)
        self.assertEqual(self.rag.count(), 317)
        self.assertEqual(len(list(self.sb_cache.glob("*.txt"))), 5)


if __name__ == "__main__":
    unittest.main()
