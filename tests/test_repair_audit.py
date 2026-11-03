# -*- coding: utf-8 -*-
"""
FAZ 15: Audit Log / Provenance / Self-Healing Traceability — Test Suite.

36 comprehensive tests covering:
- All audit event types (Start, Classify, Extract, Compare, Commit, Verify, Rollback)
- Complete successful and failed repair lifecycles
- Correlation IDs (repair_id, commit_id, rollback_id)
- Provenance (engine, version, quality before/after, chunk counts before/after)
- Append-only behavior, idempotency, duplicate protection
- Cryptographic hash-chain integrity & tamper detection
- Storage corruption tolerance
- Concurrent multithreaded logging
- Read-only query API & single-query lifecycle summary
- 100% test isolation (production audit file never touched)
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import List

from repair.audit import (
    REPAIR_AUDIT_LOG_FILE,
    RepairAuditLogger,
    generate_event_id,
    get_commit_audit,
    get_document_audit,
    get_repair_audit,
    get_repair_lifecycle_summary,
    get_rollback_audit,
    list_recent_repairs,
    read_all_events,
    verify_audit_integrity,
)
from repair.models import (
    AuditEvent,
    AuditEventType,
)

_SAMPLE_HASH = "c" * 64
_SAMPLE_DOC_ID = "doc_audit_test"


class TestRepairAudit(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="faz15_audit_test_"))
        self.test_log_file = self.temp_dir / "audit" / "repair_audit.jsonl"
        self.logger = RepairAuditLogger(log_path=self.test_log_file)
        self.repair_id = "rep_test_001"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Repair start event
    def test_01_repair_start_event(self):
        ok, evt, err = self.logger.emit(
            event_type=AuditEventType.REPAIR_STARTED.value,
            repair_id=self.repair_id,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            reason="scanner_glued_text",
            trigger="scan",
        )
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(evt.event_type, AuditEventType.REPAIR_STARTED.value)
        self.assertEqual(evt.repair_id, self.repair_id)
        self.assertEqual(evt.sequence_no, 1)
        self.assertTrue(self.test_log_file.exists())

    # 2. Classification event
    def test_02_classification_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.REPAIR_CLASSIFIED.value,
            repair_id=self.repair_id,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            status="SAFE",
            reason="quality_regression",
            details={"classification": "SAFE", "signals": ["glued_words", "low_score"]},
        )
        self.assertTrue(ok)
        self.assertEqual(evt.status, "SAFE")
        self.assertIn("glued_words", evt.details.get("signals", []))

    # 3. Extraction event
    def test_03_extraction_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.REPAIR_EXTRACTED.value,
            repair_id=self.repair_id,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            engine="pdfplumber_spatial",
            engine_version="0.11.4",
            metadata={"pages": 5, "ocr_used": False},
        )
        self.assertTrue(ok)
        self.assertEqual(evt.engine, "pdfplumber_spatial")
        self.assertEqual(evt.metadata.get("pages"), 5)

    # 4. Compare event
    def test_04_compare_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.REPAIR_COMPARED.value,
            repair_id=self.repair_id,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            old_quality={"score": 62.5, "glued_tokens": 12},
            new_quality={"score": 95.0, "glued_tokens": 0},
            old_chunk_count=25,
            new_chunk_count=25,
            status="READY_TO_COMMIT",
        )
        self.assertTrue(ok)
        self.assertEqual(evt.old_quality["score"], 62.5)
        self.assertEqual(evt.new_quality["score"], 95.0)

    # 5. Commit event
    def test_05_commit_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.REPAIR_COMMITTED.value,
            repair_id=self.repair_id,
            commit_id="com_01",
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            status="SUCCESS",
            old_chunk_count=25,
            new_chunk_count=25,
        )
        self.assertTrue(ok)
        self.assertEqual(evt.commit_id, "com_01")
        self.assertEqual(evt.status, "SUCCESS")

    # 6. Commit failure event
    def test_06_commit_failure_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.REPAIR_COMMIT_FAILED.value,
            repair_id=self.repair_id,
            commit_id="com_fail_01",
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            status="FAILED",
            reason="chroma_cleanup_timeout",
        )
        self.assertTrue(ok)
        self.assertEqual(evt.status, "FAILED")
        self.assertEqual(evt.reason, "chroma_cleanup_timeout")

    # 7. Verification success event
    def test_07_verification_success_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.REPAIR_VERIFIED.value,
            repair_id=self.repair_id,
            commit_id="com_01",
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            verification_status="VERIFIED_REPAIR",
            status="SUCCESS",
            metadata={"passed_checks": 21, "failed_checks": 0},
        )
        self.assertTrue(ok)
        self.assertEqual(evt.verification_status, "VERIFIED_REPAIR")

    # 8. Verification failure event
    def test_08_verification_failure_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.REPAIR_VERIFICATION_FAILED.value,
            repair_id=self.repair_id,
            commit_id="com_01",
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            verification_status="VERIFY_FAILED",
            status="FAILED",
            reason="CHUNK_COUNT mismatch",
        )
        self.assertTrue(ok)
        self.assertEqual(evt.verification_status, "VERIFY_FAILED")

    # 9. Rollback start event
    def test_09_rollback_start_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.ROLLBACK_STARTED.value,
            repair_id=self.repair_id,
            commit_id="com_01",
            rollback_id="rb_01",
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            reason="triggered_by_verification_failure",
        )
        self.assertTrue(ok)
        self.assertEqual(evt.rollback_id, "rb_01")

    # 10. Rollback success event
    def test_10_rollback_success_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.ROLLBACK_COMPLETED.value,
            repair_id=self.repair_id,
            commit_id="com_01",
            rollback_id="rb_01",
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            rollback_status="ROLLBACK_SUCCESS",
            status="SUCCESS",
        )
        self.assertTrue(ok)
        self.assertEqual(evt.rollback_status, "ROLLBACK_SUCCESS")

    # 11. Rollback failure event
    def test_11_rollback_failure_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.ROLLBACK_FAILED.value,
            repair_id=self.repair_id,
            commit_id="com_01",
            rollback_id="rb_01",
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            rollback_status="ROLLBACK_FAILED",
            status="FAILED",
            reason="disk_io_error",
        )
        self.assertTrue(ok)
        self.assertEqual(evt.rollback_status, "ROLLBACK_FAILED")

    # 12. Rollback blocked event
    def test_12_rollback_blocked_event(self):
        ok, evt, _ = self.logger.emit(
            event_type=AuditEventType.ROLLBACK_BLOCKED.value,
            repair_id=self.repair_id,
            commit_id="com_01",
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            rollback_status="ROLLBACK_BLOCKED",
            status="BLOCKED",
            reason="snapshot_missing",
        )
        self.assertTrue(ok)
        self.assertEqual(evt.rollback_status, "ROLLBACK_BLOCKED")

    # 13. Complete repair lifecycle
    def test_13_complete_repair_lifecycle(self):
        rid = "rep_lifecycle_01"
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH)
        self.logger.emit(AuditEventType.REPAIR_CLASSIFIED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH, status="SAFE")
        self.logger.emit(AuditEventType.REPAIR_EXTRACTED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH, engine="pdfplumber")
        self.logger.emit(AuditEventType.REPAIR_COMPARED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH, status="READY_TO_COMMIT")
        self.logger.emit(AuditEventType.REPAIR_COMMITTED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH, status="SUCCESS", commit_id="com_lc")
        self.logger.emit(AuditEventType.REPAIR_VERIFIED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH, verification_status="VERIFIED_REPAIR", status="SUCCESS")

        events = get_repair_audit(rid, log_path=self.test_log_file)
        self.assertEqual(len(events), 6)
        types = [e.event_type for e in events]
        self.assertEqual(
            types,
            [
                AuditEventType.REPAIR_STARTED.value,
                AuditEventType.REPAIR_CLASSIFIED.value,
                AuditEventType.REPAIR_EXTRACTED.value,
                AuditEventType.REPAIR_COMPARED.value,
                AuditEventType.REPAIR_COMMITTED.value,
                AuditEventType.REPAIR_VERIFIED.value,
            ],
        )

    # 14. Failed repair lifecycle
    def test_14_failed_repair_lifecycle(self):
        rid = "rep_failed_lc"
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH)
        self.logger.emit(AuditEventType.REPAIR_COMMITTED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH, commit_id="com_fl")
        self.logger.emit(AuditEventType.REPAIR_VERIFICATION_FAILED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH, verification_status="VERIFY_FAILED", status="FAILED")
        self.logger.emit(AuditEventType.ROLLBACK_STARTED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH, rollback_id="rb_fl")
        self.logger.emit(AuditEventType.ROLLBACK_COMPLETED.value, rid, _SAMPLE_DOC_ID, _SAMPLE_HASH, rollback_id="rb_fl", rollback_status="ROLLBACK_SUCCESS", status="SUCCESS")

        summary = get_repair_lifecycle_summary(rid, log_path=self.test_log_file)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.verification_status, "VERIFY_FAILED")
        self.assertTrue(summary.rollback_occurred)
        self.assertEqual(summary.rollback_status, "ROLLBACK_SUCCESS")
        self.assertEqual(summary.final_repair_status, "FAILED_BUT_RESTORED")

    # 15. Repair correlation_id
    def test_15_repair_correlation_id(self):
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, "rep_A", _SAMPLE_DOC_ID, _SAMPLE_HASH)
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, "rep_B", _SAMPLE_DOC_ID, _SAMPLE_HASH)
        self.logger.emit(AuditEventType.REPAIR_COMMITTED.value, "rep_A", _SAMPLE_DOC_ID, _SAMPLE_HASH)

        events_a = get_repair_audit("rep_A", log_path=self.test_log_file)
        events_b = get_repair_audit("rep_B", log_path=self.test_log_file)
        self.assertEqual(len(events_a), 2)
        self.assertEqual(len(events_b), 1)

    # 16. Commit correlation
    def test_16_commit_correlation(self):
        self.logger.emit(AuditEventType.REPAIR_COMMITTED.value, "rep_1", _SAMPLE_DOC_ID, _SAMPLE_HASH, commit_id="com_target")
        self.logger.emit(AuditEventType.REPAIR_VERIFIED.value, "rep_1", _SAMPLE_DOC_ID, _SAMPLE_HASH, commit_id="com_target")
        self.logger.emit(AuditEventType.REPAIR_COMMITTED.value, "rep_2", _SAMPLE_DOC_ID, _SAMPLE_HASH, commit_id="com_other")

        matched = get_commit_audit("com_target", log_path=self.test_log_file)
        self.assertEqual(len(matched), 2)

    # 17. Rollback correlation
    def test_17_rollback_correlation(self):
        self.logger.emit(AuditEventType.ROLLBACK_STARTED.value, "rep_1", _SAMPLE_DOC_ID, _SAMPLE_HASH, rollback_id="rb_target")
        self.logger.emit(AuditEventType.ROLLBACK_COMPLETED.value, "rep_1", _SAMPLE_DOC_ID, _SAMPLE_HASH, rollback_id="rb_target")

        matched = get_rollback_audit("rb_target", log_path=self.test_log_file)
        self.assertEqual(len(matched), 2)

    # 18. Source identity fields
    def test_18_source_identity_fields(self):
        _, evt, _ = self.logger.emit(
            AuditEventType.REPAIR_STARTED.value,
            self.repair_id,
            document_id="doc_identity",
            source_document_id="doc_identity_src",
            source_sha256=_SAMPLE_HASH,
        )
        self.assertEqual(evt.document_id, "doc_identity")
        self.assertEqual(evt.source_document_id, "doc_identity_src")
        self.assertEqual(evt.source_sha256, _SAMPLE_HASH)

    # 19. Quality before/after
    def test_19_quality_before_after(self):
        q_b = {"glued": 5, "score": 70.0}
        q_a = {"glued": 0, "score": 98.0}
        _, evt, _ = self.logger.emit(
            AuditEventType.REPAIR_COMPARED.value,
            self.repair_id,
            _SAMPLE_DOC_ID,
            _SAMPLE_HASH,
            old_quality=q_b,
            new_quality=q_a,
        )
        self.assertEqual(evt.old_quality, q_b)
        self.assertEqual(evt.new_quality, q_a)

    # 20. Chunk count before/after
    def test_20_chunk_count_before_after(self):
        _, evt, _ = self.logger.emit(
            AuditEventType.REPAIR_COMMITTED.value,
            self.repair_id,
            _SAMPLE_DOC_ID,
            _SAMPLE_HASH,
            old_chunk_count=30,
            new_chunk_count=28,
        )
        self.assertEqual(evt.old_chunk_count, 30)
        self.assertEqual(evt.new_chunk_count, 28)

    # 21. Engine provenance
    def test_21_engine_provenance(self):
        _, evt, _ = self.logger.emit(
            AuditEventType.REPAIR_EXTRACTED.value,
            self.repair_id,
            _SAMPLE_DOC_ID,
            _SAMPLE_HASH,
            engine="pdfplumber",
            engine_version="0.11.4",
        )
        self.assertEqual(evt.engine, "pdfplumber")
        self.assertEqual(evt.engine_version, "0.11.4")

    # 22. Actor and trigger
    def test_22_actor_and_trigger(self):
        _, evt, _ = self.logger.emit(
            AuditEventType.REPAIR_STARTED.value,
            self.repair_id,
            _SAMPLE_DOC_ID,
            _SAMPLE_HASH,
            actor="admin_user",
            trigger="manual",
        )
        self.assertEqual(evt.actor, "admin_user")
        self.assertEqual(evt.trigger, "manual")

    # 23. Environment separation
    def test_23_environment_separation(self):
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, "rep_prod", _SAMPLE_DOC_ID, _SAMPLE_HASH, environment="production")
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, "rep_lab", _SAMPLE_DOC_ID, _SAMPLE_HASH, environment="rag_lab")

        events = read_all_events(log_path=self.test_log_file)
        self.assertEqual(events[0].environment, "production")
        self.assertEqual(events[1].environment, "rag_lab")

    # 24. Event uniqueness
    def test_24_event_uniqueness(self):
        ids = {generate_event_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)

    # 25. Duplicate event idempotency
    def test_25_duplicate_event_idempotency(self):
        fixed_id = "evt_fixed_123"
        e1 = AuditEvent(
            event_id=fixed_id,
            event_type=AuditEventType.REPAIR_STARTED.value,
            timestamp="2026-09-21T00:00:00Z",
            repair_id=self.repair_id,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
        )
        ok1, _, msg1 = self.logger.log_event(e1)
        self.assertTrue(ok1)
        self.assertIsNone(msg1)

        # Re-log same event
        ok2, _, msg2 = self.logger.log_event(e1)
        self.assertTrue(ok2)
        self.assertEqual(msg2, "DUPLICATE_IGNORED")

        # Verify only one record in file
        all_e = read_all_events(log_path=self.test_log_file)
        self.assertEqual(len(all_e), 1)

    # 26. Append-only behavior
    def test_26_append_only_behavior(self):
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)
        size1 = self.test_log_file.stat().st_size

        self.logger.emit(AuditEventType.REPAIR_COMMITTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)
        size2 = self.test_log_file.stat().st_size

        self.assertGreater(size2, size1)
        events = read_all_events(log_path=self.test_log_file)
        self.assertEqual(len(events), 2)

    # 27. Audit query read-only
    def test_27_audit_query_read_only(self):
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)
        size_before = self.test_log_file.stat().st_size

        _ = get_repair_audit(self.repair_id, log_path=self.test_log_file)
        _ = get_document_audit(_SAMPLE_DOC_ID, log_path=self.test_log_file)
        _ = list_recent_repairs(log_path=self.test_log_file)

        size_after = self.test_log_file.stat().st_size
        self.assertEqual(size_before, size_after)

    # 28. Audit storage failure handling
    def test_28_audit_storage_failure_handling(self):
        # A directory cannot be opened in append mode ('a') - guarantees cross-platform IOError
        dir_as_file = self.temp_dir / "dir_blocked"
        dir_as_file.mkdir(parents=True, exist_ok=True)
        bad_logger = RepairAuditLogger(log_path=dir_as_file)
        event = AuditEvent(
            event_id=generate_event_id(),
            event_type=AuditEventType.REPAIR_STARTED.value,
            timestamp="2026-09-21T00:00:00Z",
            repair_id=self.repair_id,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
        )
        ok, evt, err = bad_logger.log_event(event)
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        # Okuma (zincir başı) ya da yazma aşamasında raporlanabilir; önemli olan sessizce yutulmaması.
        self.assertIn("Audit storage", err)

    # 29. Temporary test isolation
    def test_29_temporary_test_isolation(self):
        # Assert logger uses test_log_file and not the production REPAIR_AUDIT_LOG_FILE
        self.assertNotEqual(self.logger.log_path.resolve(), REPAIR_AUDIT_LOG_FILE.resolve())
        prod_exists_before = REPAIR_AUDIT_LOG_FILE.exists()
        prod_size_before = REPAIR_AUDIT_LOG_FILE.stat().st_size if prod_exists_before else 0

        self.logger.emit(AuditEventType.REPAIR_STARTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)

        prod_size_after = REPAIR_AUDIT_LOG_FILE.stat().st_size if REPAIR_AUDIT_LOG_FILE.exists() else 0
        self.assertEqual(prod_size_before, prod_size_after)

    # 30. Audit corruption handling
    def test_30_audit_corruption_handling(self):
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)
        # Inject corrupted line
        with open(self.test_log_file, "a", encoding="utf-8") as f:
            f.write("{corrupt json line\n")
        self.logger.emit(AuditEventType.REPAIR_COMMITTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)

        events = read_all_events(log_path=self.test_log_file)
        self.assertEqual(len(events), 2)  # Corrupt line was safely skipped without crashing

    # 31. Hash-chain integrity
    def test_31_hash_chain_integrity(self):
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)
        self.logger.emit(AuditEventType.REPAIR_CLASSIFIED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)
        self.logger.emit(AuditEventType.REPAIR_COMMITTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)

        is_valid, errors = verify_audit_integrity(log_path=self.test_log_file)
        self.assertTrue(is_valid, f"Hash-chain errors: {errors}")
        self.assertEqual(len(errors), 0)

    # 32. Tamper detection
    def test_32_tamper_detection(self):
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)
        self.logger.emit(AuditEventType.REPAIR_COMMITTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH)

        # Tamper with the file: modify a character in line 1
        lines = self.test_log_file.read_text(encoding="utf-8").splitlines()
        tampered_data = json.loads(lines[0])
        tampered_data["reason"] = "tampered_unauthorized_reason"
        lines[0] = json.dumps(tampered_data)
        self.test_log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        is_valid, errors = verify_audit_integrity(log_path=self.test_log_file)
        self.assertFalse(is_valid)
        self.assertTrue(any("event_hash mismatch" in e for e in errors))

    # 33. Correction event
    def test_33_correction_event(self):
        self.logger.emit(AuditEventType.REPAIR_STARTED.value, self.repair_id, _SAMPLE_DOC_ID, _SAMPLE_HASH, reason="typo_here")
        ok, evt, _ = self.logger.emit(
            AuditEventType.AUDIT_CORRECTION.value,
            self.repair_id,
            _SAMPLE_DOC_ID,
            _SAMPLE_HASH,
            reason="Correcting previous reason typo to correct_reason",
        )
        self.assertTrue(ok)
        self.assertEqual(evt.event_type, AuditEventType.AUDIT_CORRECTION.value)

    # 34. Concurrent audit writes
    def test_34_concurrent_audit_writes(self):
        errors: List[Exception] = []

        def worker(worker_id: int):
            try:
                for i in range(10):
                    self.logger.emit(
                        AuditEventType.REPAIR_STARTED.value,
                        f"rep_concurrent_{worker_id}_{i}",
                        _SAMPLE_DOC_ID,
                        _SAMPLE_HASH,
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        events = read_all_events(log_path=self.test_log_file)
        self.assertEqual(len(events), 50)
        # Verify hash-chain remains completely unbroken despite concurrency
        is_valid, chain_errs = verify_audit_integrity(log_path=self.test_log_file)
        self.assertTrue(is_valid, f"Chain errors under concurrency: {chain_errs}")

    # 35. Recent repairs query
    def test_35_recent_repairs_query(self):
        for i in range(5):
            self.logger.emit(AuditEventType.REPAIR_STARTED.value, f"rep_batch_{i}", f"doc_{i}", _SAMPLE_HASH)

        repairs = list_recent_repairs(limit=3, log_path=self.test_log_file)
        self.assertEqual(len(repairs), 3)

    # 36. Lifecycle summary extraction
    def test_36_lifecycle_summary_extraction(self):
        rid = "rep_story_01"
        self.logger.emit(
            AuditEventType.REPAIR_STARTED.value,
            rid,
            "doc_test_story",
            _SAMPLE_HASH,
            reason="glued_text",
            trigger="scan",
        )
        self.logger.emit(
            AuditEventType.REPAIR_CLASSIFIED.value,
            rid,
            "doc_test_story",
            _SAMPLE_HASH,
            details={"classification": "SAFE"},
        )
        self.logger.emit(
            AuditEventType.REPAIR_EXTRACTED.value,
            rid,
            "doc_test_story",
            _SAMPLE_HASH,
            engine="spatial_pdfplumber",
        )
        self.logger.emit(
            AuditEventType.REPAIR_COMPARED.value,
            rid,
            "doc_test_story",
            _SAMPLE_HASH,
            old_quality={"score": 50},
            new_quality={"score": 90},
            old_chunk_count=10,
            new_chunk_count=10,
        )
        self.logger.emit(
            AuditEventType.REPAIR_COMMITTED.value,
            rid,
            "doc_test_story",
            _SAMPLE_HASH,
            status="SUCCESS",
        )
        self.logger.emit(
            AuditEventType.REPAIR_VERIFIED.value,
            rid,
            "doc_test_story",
            _SAMPLE_HASH,
            verification_status="VERIFIED_REPAIR",
        )

        summary = get_repair_lifecycle_summary(rid, log_path=self.test_log_file)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.repair_id, rid)
        self.assertEqual(summary.document_id, "doc_test_story")
        self.assertEqual(summary.trigger, "scan")
        self.assertEqual(summary.reason, "glued_text")
        self.assertEqual(summary.classification, "SAFE")
        self.assertEqual(summary.extraction_engine, "spatial_pdfplumber")
        self.assertEqual(summary.quality_before["score"], 50)
        self.assertEqual(summary.quality_after["score"], 90)
        self.assertEqual(summary.final_repair_status, "SUCCESS")
        self.assertEqual(summary.events_count, 6)


if __name__ == "__main__":
    unittest.main()
