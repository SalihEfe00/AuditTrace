# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19: Operational Telemetry & Metrics Aggregator.

Computes accurate, non-fabricated telemetry strictly from:
- RepairAuditLogger (append-only JSON Lines)
- IngestIndex (document inventory)

Key properties:
- Zero duplicate database or state store
- Strictly read-only; 0 mutation to Chroma, index, or logs
- Clear distinction between Event Count and Unique Document Count
- Unverifiable metrics are reported as None rather than fabricated
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from mufettis.rag.index import IngestIndex
from repair.audit import read_all_events
from repair.models import AuditEvent, AuditEventType

logger = logging.getLogger(__name__)


def compute_operational_telemetry(
    log_path: Optional[Path] = None,
    index: Optional[IngestIndex] = None,
) -> Dict[str, Any]:
    """Computes operational telemetry and health metrics.
    
    Strictly read-only; zero write mutations to any store.
    """
    events: List[AuditEvent] = read_all_events(log_path)

    # 1. Event Metrics (Counting distinct operations/actions)
    quality_passed_count = 0
    self_healed_count = 0
    quarantined_count = 0
    retry_started_count = 0
    verification_failures = 0
    rescan_failures = 0
    rollback_count = 0

    # Tracking for unique documents
    seen_hashes: Set[str] = set()
    clean_hashes: Set[str] = set()
    healed_hashes: Set[str] = set()
    quarantined_hashes: Set[str] = set()

    # Quality delta tracking
    quality_deltas: List[float] = []

    # Retry tracking
    retry_attempts_count = 0
    retry_success_count = 0

    for e in events:
        f_hash = e.source_sha256 or e.document_id
        if f_hash:
            seen_hashes.add(f_hash)

        et = e.event_type

        # Check for retry markers in details
        is_retry = bool(e.details.get("is_retry", False)) or et == AuditEventType.INGEST_RETRY_STARTED.value

        if et == AuditEventType.INGEST_QUALITY_PASSED.value:
            quality_passed_count += 1
            if f_hash and f_hash not in healed_hashes and f_hash not in quarantined_hashes:
                clean_hashes.add(f_hash)
            if is_retry:
                retry_success_count += 1

        elif et == AuditEventType.INGEST_SELF_HEALED.value:
            self_healed_count += 1
            if f_hash:
                healed_hashes.add(f_hash)
                clean_hashes.discard(f_hash)
            if is_retry:
                retry_success_count += 1

            # Quality delta
            raw_sc = e.details.get("raw_score")
            fin_sc = e.details.get("final_score")
            if raw_sc is not None and fin_sc is not None:
                try:
                    delta = float(fin_sc) - float(raw_sc)
                    quality_deltas.append(delta)
                except (ValueError, TypeError):
                    pass

        elif et == AuditEventType.INGEST_QUARANTINED.value:
            quarantined_count += 1
            if f_hash:
                quarantined_hashes.add(f_hash)
                clean_hashes.discard(f_hash)

            reason_lower = (e.reason or "").lower()
            issues_str = " ".join(str(i) for i in e.details.get("issues", [])).lower()

            if "verify" in reason_lower or "verification" in reason_lower or "verify" in issues_str:
                verification_failures += 1
            if "rescan" in reason_lower or "rescan" in issues_str:
                rescan_failures += 1

            rollback_count += 1

        elif et == AuditEventType.INGEST_RETRY_STARTED.value:
            retry_started_count += 1
            retry_attempts_count += 1

        elif et == AuditEventType.ROLLBACK_COMPLETED.value:
            rollback_count += 1

        elif et == AuditEventType.REPAIR_VERIFICATION_FAILED.value:
            verification_failures += 1

        elif et == AuditEventType.WAVE_RESCAN_FAILED.value:
            rescan_failures += 1

    total_ingest_events = quality_passed_count + self_healed_count + quarantined_count

    # 2. Unique Document Metrics
    # If IngestIndex is provided, verify current active vs quarantined state directly from index
    current_quarantined_docs = 0
    current_active_docs = 0
    stage_1_count = 0
    stage_2_count = 0
    stage_3_count = 0

    if index is not None:
        all_items = index.data.get("indexed_items", {}) if hasattr(index, "data") else (getattr(index, "indexed_items", {}) or {})
        for h, meta in all_items.items():
            st = str(meta.get("status") or meta.get("ingest_status") or "").lower()
            if st == "quarantined":
                current_quarantined_docs += 1
            elif st in ("active", "indexed"):
                current_active_docs += 1

            if meta.get("healed"):
                if meta.get("source_fallback_used"):
                    stage_3_count += 1
                else:
                    stage_2_count += 1
    else:
        current_quarantined_docs = len(quarantined_hashes - (clean_hashes | healed_hashes))
        current_active_docs = len(clean_hashes | healed_hashes)

    # 3. Calculated Rates & Averages
    avg_quality_delta = (
        round(sum(quality_deltas) / len(quality_deltas), 4)
        if quality_deltas
        else None
    )

    retry_success_rate = (
        round(retry_success_count / retry_attempts_count, 4)
        if retry_attempts_count > 0
        else None
    )

    total_unique_healed_or_quarantined = len(healed_hashes) + current_quarantined_docs
    quarantine_resolution_rate = (
        round(len(healed_hashes) / total_unique_healed_or_quarantined, 4)
        if total_unique_healed_or_quarantined > 0
        else None
    )

    return {
        "event_metrics": {
            "total_ingest_events": total_ingest_events,
            "quality_passed_events": quality_passed_count,
            "self_healed_events": self_healed_count,
            "quarantined_events": quarantined_count,
            "retry_attempt_events": retry_started_count,
            "verification_failure_events": verification_failures,
            "rescan_failure_events": rescan_failures,
            "rollback_events": rollback_count,
        },
        "unique_document_metrics": {
            "unique_documents_processed": len(seen_hashes),
            "unique_documents_clean": len(clean_hashes),
            "unique_documents_healed": len(healed_hashes),
            "unique_documents_in_quarantine": current_quarantined_docs,
            "unique_documents_active": current_active_docs,
        },
        "quality_metrics": {
            "avg_quality_delta": avg_quality_delta,
            "healed_samples_count": len(quality_deltas),
            "stage_distribution": {
                "stage_1_mojibake": stage_1_count,
                "stage_2_glued": stage_2_count,
                "stage_3_source_fallback": stage_3_count,
            } if index is not None else None,
            "retry_success_rate": retry_success_rate,
            "quarantine_resolution_rate": quarantine_resolution_rate,
        },
    }
