# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 15: Audit Log / Provenance / Self-Healing Traceability.

The single write authority and query API for recording and inspecting every
self-healing repair action:
  SCAN -> CLASSIFY -> EXTRACT -> COMPARE -> COMMIT -> VERIFY -> ROLLBACK

Key properties:
- Single Write Authority (RepairAuditLogger)
- Append-only, durable, structured JSON Lines storage
- Tamper-evident hash-chain linking each event to the previous event
- Idempotency & duplicate protection by unique event_id
- Cross-event correlation via repair_id, commit_id, rollback_id
- Non-destructive query API
- Security & Data Minimization: No raw full-text dumps or embedding vectors
- Fault-tolerant reader (tolerant to corrupted lines)
- Total test isolation support via configurable log_path
"""
from __future__ import annotations

import binascii
import hashlib
import json
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mufettis.config import APP_VERSION, DATA_DIR
from mufettis.common.utils import now_iso, safe_json
from repair.models import (
    AuditEvent,
    AuditEventType,
    RepairLifecycleSummary,
)

REPAIR_AUDIT_LOG_DIR = DATA_DIR / "audit"
REPAIR_AUDIT_LOG_FILE = REPAIR_AUDIT_LOG_DIR / "repair_audit.jsonl"
AUDIT_SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Canonical Event Serialization & Hash Calculation
# ---------------------------------------------------------------------------

def calculate_event_hash(event_dict: Dict[str, Any]) -> str:
    """Computes deterministic SHA-256 hash of an event dictionary excluding event_hash."""
    payload = dict(event_dict)
    payload.pop("event_hash", None)
    canonical_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def generate_event_id() -> str:
    """Generates a collision-safe unique event ID."""
    return f"evt_{binascii.hexlify(os.urandom(8)).decode()}"


# ---------------------------------------------------------------------------
# Single Audit Write Authority: RepairAuditLogger
# ---------------------------------------------------------------------------

class RepairAuditLogger:
    """Canonical single write authority for recording self-healing audit events."""

    _global_lock = threading.Lock()

    def __init__(self, log_path: Optional[Path] = None):
        self.log_path = Path(log_path or REPAIR_AUDIT_LOG_FILE)
        self._seen_event_ids: set[str] = set()
        self._init_seen_ids()

    def _init_seen_ids(self) -> None:
        """Loads existing event IDs in memory to guarantee idempotency."""
        if not self.log_path.exists():
            return
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        eid = data.get("event_id")
                        if eid:
                            self._seen_event_ids.add(eid)
                    except Exception:
                        continue
        except Exception:
            pass

    def _get_last_event_info(self) -> Tuple[int, str]:
        """Reads the last valid event to obtain monotonic sequence number and previous hash."""
        if not self.log_path.exists():
            return 0, "0" * 64

        last_seq = 0
        last_hash = "0" * 64
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        last_seq = data.get("sequence_no", last_seq)
                        last_hash = data.get("event_hash", last_hash)
                    except Exception:
                        continue
        except Exception:
            pass
        return last_seq, last_hash

    def log_event(self, event: AuditEvent) -> Tuple[bool, AuditEvent, Optional[str]]:
        """Appends a structured event to the audit log in an append-only, thread-safe manner."""
        with self._global_lock:
            # 1. Idempotency check: duplicate event_id is rejected/ignored
            if event.event_id in self._seen_event_ids:
                return True, event, "DUPLICATE_IGNORED"

            # 2. Sequence and Hash-Chain Link
            last_seq, last_hash = self._get_last_event_info()
            if not event.sequence_no or event.sequence_no <= last_seq:
                event.sequence_no = last_seq + 1
            if not event.previous_event_hash or event.previous_event_hash == "0" * 64:
                event.previous_event_hash = last_hash

            # 3. Canonical hash computation
            event_dict = event.to_dict()
            event.event_hash = calculate_event_hash(event_dict)
            event_dict["event_hash"] = event.event_hash

            # 4. Atomic append to durable storage
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(safe_json(event_dict), ensure_ascii=False) + "\n"
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                self._seen_event_ids.add(event.event_id)
                return True, event, None
            except Exception as exc:
                # Never swallow write failure silently
                return False, event, f"Audit storage write error: {exc}"

    # ── High-Level Event Emitters ────────────────────────────────────────────

    def emit(
        self,
        event_type: str,
        repair_id: str,
        document_id: str,
        source_sha256: str,
        reason: str = "",
        status: str = "SUCCESS",
        commit_id: Optional[str] = None,
        rollback_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        source_document_id: str = "",
        engine: str = "",
        engine_version: str = "",
        old_quality: Optional[Dict[str, Any]] = None,
        new_quality: Optional[Dict[str, Any]] = None,
        old_chunk_count: Optional[int] = None,
        new_chunk_count: Optional[int] = None,
        verification_status: Optional[str] = None,
        rollback_status: Optional[str] = None,
        before_fingerprint: Optional[Dict[str, Any]] = None,
        after_fingerprint: Optional[Dict[str, Any]] = None,
        environment: str = "production",
        actor: str = "system",
        trigger: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, AuditEvent, Optional[str]]:
        """Convenience method to construct and record an AuditEvent."""
        event = AuditEvent(
            event_id=generate_event_id(),
            event_type=str(event_type),
            timestamp=now_iso(),
            repair_id=repair_id,
            commit_id=commit_id,
            rollback_id=rollback_id,
            candidate_id=candidate_id or document_id,
            document_id=document_id,
            source_document_id=source_document_id or document_id,
            source_sha256=source_sha256,
            reason=reason,
            engine=engine,
            engine_version=engine_version,
            old_quality=old_quality,
            new_quality=new_quality,
            old_chunk_count=old_chunk_count,
            new_chunk_count=new_chunk_count,
            status=status,
            verification_status=verification_status,
            rollback_status=rollback_status,
            before_fingerprint=before_fingerprint,
            after_fingerprint=after_fingerprint,
            environment=environment,
            actor=actor,
            trigger=trigger,
            app_version=APP_VERSION,
            audit_schema_version=AUDIT_SCHEMA_VERSION,
            metadata=metadata or {},
            details=details or {},
        )
        return self.log_event(event)

    def get_events(self) -> List[AuditEvent]:
        """Reads all events from this logger's target path."""
        return read_all_events(self.log_path)

    def verify_chain(self) -> Tuple[bool, List[str]]:
        """Verifies hash-chain integrity of this logger's target path."""
        return verify_audit_integrity(self.log_path)


# Default singleton instance
_DEFAULT_LOGGER: Optional[RepairAuditLogger] = None


def get_audit_logger(log_path: Optional[Path] = None) -> RepairAuditLogger:
    """Returns the canonical audit logger instance."""
    global _DEFAULT_LOGGER
    if log_path is not None:
        return RepairAuditLogger(log_path=log_path)
    if _DEFAULT_LOGGER is None:
        _DEFAULT_LOGGER = RepairAuditLogger()
    return _DEFAULT_LOGGER


# ---------------------------------------------------------------------------
# Canonical Read & Query API (Strictly Read-Only)
# ---------------------------------------------------------------------------

def read_all_events(log_path: Optional[Path] = None) -> List[AuditEvent]:
    """Reads all valid audit events from storage in chronological order.

    Tolerant to corrupted lines: skips damaged records while preserving valid ones.
    """
    path = Path(log_path or REPAIR_AUDIT_LOG_FILE)
    if not path.exists():
        return []

    events: List[AuditEvent] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                evt = AuditEvent(**data)
                events.append(evt)
            except Exception:
                # Corrupted line: skip to prevent crashing readers
                continue
    return events


def get_repair_audit(repair_id: str, log_path: Optional[Path] = None) -> List[AuditEvent]:
    """Returns all audit events associated with a specific repair_id."""
    return [e for e in read_all_events(log_path) if e.repair_id == repair_id]


def get_document_audit(document_id: str, log_path: Optional[Path] = None) -> List[AuditEvent]:
    """Returns all audit events associated with a specific document_id."""
    return [e for e in read_all_events(log_path) if e.document_id == document_id]


def get_commit_audit(commit_id: str, log_path: Optional[Path] = None) -> List[AuditEvent]:
    """Returns all audit events associated with a specific commit_id."""
    return [e for e in read_all_events(log_path) if e.commit_id == commit_id]


def get_rollback_audit(rollback_id: str, log_path: Optional[Path] = None) -> List[AuditEvent]:
    """Returns all audit events associated with a specific rollback_id."""
    return [e for e in read_all_events(log_path) if e.rollback_id == rollback_id]


def list_recent_repairs(limit: int = 50, log_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Returns high-level summary of recent repair operations grouped by repair_id."""
    events = read_all_events(log_path)
    repairs_map: Dict[str, Dict[str, Any]] = {}

    for e in events:
        rid = e.repair_id or f"unassigned_{e.event_id}"
        if rid not in repairs_map:
            repairs_map[rid] = {
                "repair_id": rid,
                "document_id": e.document_id,
                "source_sha256": e.source_sha256,
                "environment": e.environment,
                "started_at": e.timestamp,
                "last_timestamp": e.timestamp,
                "events_count": 0,
                "final_status": e.status,
                "latest_event_type": e.event_type,
            }
        r = repairs_map[rid]
        r["events_count"] += 1
        r["last_timestamp"] = e.timestamp
        r["latest_event_type"] = e.event_type
        r["final_status"] = e.status
        if e.verification_status:
            r["verification_status"] = e.verification_status
        if e.rollback_status:
            r["rollback_status"] = e.rollback_status

    sorted_repairs = sorted(repairs_map.values(), key=lambda x: x["last_timestamp"], reverse=True)
    return sorted_repairs[:limit]


def get_repair_lifecycle_summary(
    repair_id: str,
    log_path: Optional[Path] = None,
) -> Optional[RepairLifecycleSummary]:
    """Answers 'WHAT HAPPENED' for a given repair operation in a single query."""
    events = get_repair_audit(repair_id, log_path)
    if not events:
        return None

    first = events[0]
    last = events[-1]

    classification = None
    extraction_engine = None
    q_before = None
    q_after = None
    chunk_before = None
    chunk_after = None
    commit_status = None
    verification_status = None
    rollback_occurred = False
    rollback_status = None
    final_status = "UNKNOWN"

    for e in events:
        if e.event_type == AuditEventType.REPAIR_CLASSIFIED.value:
            classification = e.details.get("classification") or e.reason
        elif e.event_type == AuditEventType.REPAIR_EXTRACTED.value:
            extraction_engine = e.engine
        elif e.event_type == AuditEventType.REPAIR_COMPARED.value:
            q_before = e.old_quality
            q_after = e.new_quality
            chunk_before = e.old_chunk_count
            chunk_after = e.new_chunk_count
        elif e.event_type in (AuditEventType.REPAIR_COMMITTED.value, AuditEventType.REPAIR_COMMIT_FAILED.value):
            commit_status = e.status
            if e.old_chunk_count is not None:
                chunk_before = e.old_chunk_count
            if e.new_chunk_count is not None:
                chunk_after = e.new_chunk_count
        elif e.event_type in (AuditEventType.REPAIR_VERIFIED.value, AuditEventType.REPAIR_VERIFICATION_FAILED.value):
            verification_status = e.verification_status or e.status
        elif e.event_type in (
            AuditEventType.ROLLBACK_STARTED.value,
            AuditEventType.ROLLBACK_COMPLETED.value,
            AuditEventType.ROLLBACK_FAILED.value,
            AuditEventType.ROLLBACK_BLOCKED.value,
        ):
            rollback_occurred = True
            rollback_status = e.rollback_status or e.status

    # Derive comprehensive final repair status
    if rollback_occurred and rollback_status == "ROLLBACK_SUCCESS":
        final_status = "FAILED_BUT_RESTORED"
    elif rollback_occurred and rollback_status in ("ROLLBACK_FAILED", "ROLLBACK_PARTIAL"):
        final_status = "ROLLBACK_FAILED"
    elif verification_status == "VERIFIED_REPAIR":
        final_status = "SUCCESS"
    elif verification_status in ("VERIFY_FAILED", "UNKNOWN_INTEGRITY_STATE"):
        final_status = "FAILED"
    elif commit_status == "FAILED":
        final_status = "COMMIT_FAILED"
    elif classification == "BLOCKED":
        final_status = "BLOCKED"
    elif classification == "REVIEW":
        final_status = "REVIEW"
    else:
        final_status = last.status or "COMPLETED"

    return RepairLifecycleSummary(
        repair_id=repair_id,
        document_id=first.document_id,
        source_sha256=first.source_sha256,
        trigger=first.trigger,
        reason=first.reason,
        classification=classification,
        extraction_engine=extraction_engine,
        quality_before=q_before,
        quality_after=q_after,
        chunk_count_before=chunk_before,
        chunk_count_after=chunk_after,
        commit_status=commit_status,
        verification_status=verification_status,
        rollback_occurred=rollback_occurred,
        rollback_status=rollback_status,
        final_repair_status=final_status,
        events_count=len(events),
        started_at=first.timestamp,
        completed_at=last.timestamp,
    )


# ---------------------------------------------------------------------------
# Hash-Chain & Tamper-Evidence Verification
# ---------------------------------------------------------------------------

def verify_audit_integrity(log_path: Optional[Path] = None) -> Tuple[bool, List[str]]:
    """Verifies cryptographic hash-chain integrity of the audit log."""
    path = Path(log_path or REPAIR_AUDIT_LOG_FILE)
    if not path.exists():
        return True, []

    errors: List[str] = []
    expected_previous_hash = "0" * 64
    seq = 0

    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception as exc:
                errors.append(f"Line {line_no}: JSON parse error - {exc}")
                continue

            event_id = data.get("event_id", f"unknown_line_{line_no}")
            recorded_hash = data.get("event_hash", "")
            recorded_prev = data.get("previous_event_hash", "")
            current_seq = data.get("sequence_no", 0)

            # Check sequence ordering
            if current_seq <= seq:
                errors.append(
                    f"Line {line_no} ({event_id}): sequence_no non-monotonic: {current_seq} <= {seq}"
                )
            seq = current_seq

            # Check previous hash link
            if recorded_prev != expected_previous_hash:
                errors.append(
                    f"Line {line_no} ({event_id}): previous_event_hash mismatch: expected {expected_previous_hash}, got {recorded_prev}"
                )

            # Recompute event hash
            recomputed = calculate_event_hash(data)
            if recomputed != recorded_hash:
                errors.append(
                    f"Line {line_no} ({event_id}): event_hash mismatch: recomputed {recomputed}, recorded {recorded_hash}"
                )

            expected_previous_hash = recorded_hash

    return len(errors) == 0, errors
