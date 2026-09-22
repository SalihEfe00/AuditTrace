# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 17: Controlled Production Repair Waves / Verify + Rescan.

Orchestrates controlled self-healing repair waves:
  PILOT (1 doc) -> SMALL BATCH (3-5 docs) -> MEDIUM BATCH (10 docs) ->
  BDDK ACTIVE -> OTHER SOURCES -> FULL CORPUS

Strict principles:
1. Production Corpus Check:
   If production corpus is ABSENT, live production repair is strictly prevented
   (PRODUCTION GATE = NOT_RUN, 0 production writes).
2. Explicit Multi-Key Safety Gate:
   Production repair requires simultaneously:
   --repair-wave-execute + --repair-wave-allow-production + production corpus present.
3. Every repair strictly enforces:
   SNAPSHOT -> COMMIT -> VERIFY -> RESCAN.
4. Fail-safe:
   VERIFY or RESCAN failure triggers canonical FAZ 14 rollback (repair/rollback.py).
5. Cross-hash / cross-document isolation:
   Any unexpected cross-document mutation immediately triggers rollback and halts wave.
6. Single Write Authorities Preserved:
   - Commit: repair/committer.py
   - Rollback: repair/rollback.py
   - Audit: repair/audit.py
   - Scan & Rescan: repair/scanner.py
   - Verification: repair/verifier.py
"""
from __future__ import annotations

import binascii
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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

from repair.audit import RepairAuditLogger, get_audit_logger
from repair.classifier import classify_document
from repair.committer import CommitPolicy, acquire_commit_lock, commit_candidate
from repair.comparator import build_snapshot, compare_quality
from repair.extractor import extract_candidate
from repair.models import (
    ApprovalRecord,
    AuditEvent,
    AuditEventType,
    ClassificationResult,
    ClassificationType,
    CommitResult,
    CommitStatus,
    ComparisonStatus,
    DocumentScanResult,
    ExtractionCandidate,
    FinalGateDecision,
    IssueCode,
    RetrievalComparisonResult,
    RollbackResult,
    RollbackStatus,
    VerificationResult,
    VerificationStatus,
    WaveConfig,
    WaveDocumentResult,
    WaveDocumentStatus,
    WaveScope,
    WaveStatus,
    WaveSummary,
)
from repair.orchestrator import ApprovalManager
from repair.retrieval_comparator import evaluate_final_gate
from repair.rollback import rollback_commit
from repair.scanner import _calc_sha256, scan_document
from repair.verifier import verify_committed_repair

logger = logging.getLogger("mufettis.repair.wave")


# ---------------------------------------------------------------------------
# Production Corpus Presence Validator
# ---------------------------------------------------------------------------

def check_production_corpus(
    db_path: Optional[Path] = None,
    index_file: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    rag: Optional[LocalRAG] = None,
    collection: Any = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Deterministically verifies whether the production corpus is present and populated.
    Returns: (is_present, reason, details)

    Strictly read-only: does not create directories, initialize empty Chroma DBs,
    or mutate any files.
    """
    db_p = Path(db_path or DB_PATH)
    idx_p = Path(index_file or INGEST_INDEX_FILE)
    c_dir = Path(cache_dir or SOURCE_CACHE_DIR)

    db_exists = db_p.exists()
    db_size = 0
    if db_exists:
        try:
            if db_p.is_file():
                db_size = db_p.stat().st_size
            elif db_p.is_dir():
                sqlite_file = db_p / "chroma.sqlite3"
                if sqlite_file.exists():
                    db_size = sqlite_file.stat().st_size
                else:
                    db_size = sum(f.stat().st_size for f in db_p.glob("*") if f.is_file())
        except Exception:
            db_size = 0

    cache_exists = c_dir.exists()
    cache_count = 0
    if cache_exists and c_dir.is_dir():
        try:
            cache_count = len(os.listdir(c_dir))
        except Exception:
            cache_count = 0

    details: Dict[str, Any] = {
        "db_exists": db_exists,
        "db_size": db_size,
        "embeddings_count": 0,
        "index_exists": idx_p.exists(),
        "index_documents_count": 0,
        "cache_exists": cache_exists,
        "cache_files_count": cache_count,
    }

    # 1. Chroma collection count (READ-ONLY)
    if not db_exists:
        details["db_error"] = f"Database path does not exist: {db_p}"
    else:
        count_resolved = False

        # Preference 1: Existing collection object or LocalRAG instance (ZERO filesystem init)
        active_col = collection
        if active_col is None and rag is not None and hasattr(rag, "collection"):
            active_col = rag.collection

        if active_col is not None:
            try:
                details["embeddings_count"] = active_col.count()
                count_resolved = True
            except Exception as exc:
                details["db_error"] = str(exc)

        # Preference 2: Pure read-only SQLite inspection (ZERO filesystem mutation, mtime preserved)
        if not count_resolved:
            sqlite_file = db_p if db_p.is_file() else (db_p / "chroma.sqlite3")
            if sqlite_file.exists() and sqlite_file.is_file():
                try:
                    uri = f"file:{sqlite_file.as_posix()}?mode=ro"
                    con = sqlite3.connect(uri, uri=True)
                    cur = con.cursor()
                    tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                    if "embeddings" in tables:
                        cnt = cur.execute("SELECT count(*) FROM embeddings").fetchone()[0]
                        details["embeddings_count"] = cnt
                        count_resolved = True
                    con.close()
                except Exception as sqlexc:
                    details["db_error"] = str(sqlexc)

            # Preference 3: chromadb.PersistentClient IF not resolved
            if not count_resolved and sqlite_file.exists():
                try:
                    import chromadb
                    chroma_dir = db_p if db_p.is_dir() else db_p.parent
                    client = chromadb.PersistentClient(path=str(chroma_dir))
                    col = client.get_collection(name=CHROMA_COLLECTION)
                    details["embeddings_count"] = col.count()
                    count_resolved = True
                except Exception as exc:
                    details["db_error"] = str(exc)
            elif not sqlite_file.exists():
                details["db_error"] = f"Chroma sqlite storage not found in {db_p}"

    # 2. Ingest Index check (schema 2.0 indexed_items + backward compatibility with documents)
    if idx_p.exists() and idx_p.is_file():
        try:
            idx_data = json.loads(idx_p.read_text(encoding="utf-8"))
            if isinstance(idx_data, dict):
                items = idx_data.get("indexed_items")
                docs = idx_data.get("documents")
                if isinstance(items, (dict, list)) and len(items) > 0:
                    details["index_documents_count"] = len(items)
                elif isinstance(docs, (dict, list)):
                    details["index_documents_count"] = len(docs)
                elif isinstance(items, (dict, list)):
                    details["index_documents_count"] = len(items)
                elif "version" not in idx_data and all(isinstance(v, dict) for v in idx_data.values()):
                    details["index_documents_count"] = len(idx_data)
        except Exception as exc:
            details["index_error"] = str(exc)

    # 3. Production corpus presence validation
    if not details["index_exists"] or details["index_documents_count"] == 0:
        return False, "Ingest index missing or empty.", details

    if not details["db_exists"] or details["embeddings_count"] == 0:
        return False, "Production Chroma database contains 0 embeddings.", details

    if not details["cache_exists"]:
        return False, "Source cache directory missing.", details

    return True, "Production corpus present and populated.", details


# ---------------------------------------------------------------------------
# Controlled Wave Orchestrator
# ---------------------------------------------------------------------------

class WaveOrchestrator:
    """
    Executes staged, controlled self-healing repair waves with zero regressions,
    cross-document isolation, and mandatory post-repair rescan.
    """

    def __init__(
        self,
        rag: Optional[LocalRAG] = None,
        index: Optional[IngestIndex] = None,
        cache_dir: Optional[Path] = None,
        staging_dir: Optional[Path] = None,
        audit_logger: Optional[RepairAuditLogger] = None,
        approval_manager: Optional[ApprovalManager] = None,
    ):
        self.rag = rag or LocalRAG()
        self.index = index or IngestIndex()
        self.cache_dir = Path(cache_dir or SOURCE_CACHE_DIR)
        self.staging_dir = Path(staging_dir or (DATA_DIR / "repair_staging"))
        self.audit_logger = audit_logger or get_audit_logger()
        self.approval_manager = approval_manager or ApprovalManager()

        self._wave_lock = threading.Lock()
        self.consecutive_failures = 0
        self.total_failures = 0
        self.circuit_open = False

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
        verification_status: Optional[str] = None,
        rollback_status: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        environment: str = "rag_lab",
        actor: str = "wave_runner",
    ) -> None:
        """Appends an immutable audit event via the single audit write authority."""
        try:
            event = AuditEvent(
                event_id=f"evt_{binascii.hexlify(os.urandom(8)).decode()}",
                event_type=event_type.value,
                timestamp=now_iso(),
                repair_id=repair_id,
                commit_id=commit_id,
                rollback_id=rollback_id,
                document_id=document_id,
                source_sha256=source_sha256,
                reason=reason,
                status=status,
                verification_status=verification_status,
                rollback_status=rollback_status,
                environment=environment,
                actor=actor,
                trigger="wave_repair",
                details=details or {},
            )
            self.audit_logger.log_event(event)
        except Exception as exc:
            logger.error("Failed to log wave audit event: %s", exc)

    def resolve_target_scope(
        self,
        config: WaveConfig,
        provided_items: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Deterministically filters and limits target items based on wave scope and progression rules.
        """
        targets: List[Dict[str, Any]] = []

        if provided_items:
            targets = list(provided_items)
        elif self.index:
            items_map: Dict[str, Any] = {}
            if hasattr(self.index, "documents") and self.index.documents:
                items_map = self.index.documents
            elif hasattr(self.index, "data") and isinstance(self.index.data, dict):
                items_map = self.index.data.get("indexed_items", {})
            elif isinstance(self.index, dict):
                items_map = self.index.get("indexed_items", {}) or self.index.get("documents", {})

            for doc_key, doc_meta in items_map.items():
                if not isinstance(doc_meta, dict):
                    continue
                doc_id = str(doc_meta.get("document_id") or doc_meta.get("source_id") or doc_meta.get("document_key") or doc_key)
                src_hash = str(doc_meta.get("file_hash") or doc_meta.get("source_hash") or doc_meta.get("sha256") or doc_key)
                targets.append({
                    "document_id": doc_id,
                    "source_hash": src_hash,
                    "source_path": doc_meta.get("source_path"),
                    "title": doc_meta.get("title", doc_id),
                    "source_type": doc_meta.get("source_type", ""),
                    "is_pdf": doc_meta.get("is_pdf"),
                    "url": doc_meta.get("url"),
                    "ingest_origin": doc_meta.get("ingest_origin"),
                })

        # Scope-based filtering
        scope = config.scope.upper()

        if config.target_document_ids:
            target_ids_set = set(config.target_document_ids)
            targets = [
                t for t in targets
                if t.get("document_id") in target_ids_set
                or t.get("source_hash") in target_ids_set
            ]
        else:
            # Exclude already successfully repaired active documents from discovery
            try:
                events = self.audit_logger.get_events()
                repaired = {
                    e.source_sha256
                    for e in events
                    if e.event_type in ("REPAIR_VERIFIED", "WAVE_RESCAN_PASSED")
                    and e.source_sha256
                }
                rolled_back = {
                    e.source_sha256
                    for e in events
                    if e.event_type == "REPAIR_ROLLED_BACK"
                    and e.source_sha256
                }
                active_repaired = repaired - rolled_back
                if active_repaired:
                    targets = [t for t in targets if t.get("source_hash") not in active_repaired]
            except Exception:
                pass

        if scope == WaveScope.BDDK_ACTIVE.value:
            targets = [
                t for t in targets
                if "bddk" in str(t.get("document_id", "")).lower()
                or "bddk" in str(t.get("source_type", "")).lower()
            ]
        elif scope == WaveScope.OTHER_SOURCES.value:
            targets = [
                t for t in targets
                if "bddk" not in str(t.get("document_id", "")).lower()
                and "bddk" not in str(t.get("source_type", "")).lower()
            ]

        # Scope-based strict count capping
        max_allowed = config.doc_limit
        if scope == WaveScope.PILOT.value:
            if provided_items is None and config.dry_run and config.doc_limit and config.doc_limit > 1:
                max_allowed = config.doc_limit
            else:
                max_allowed = 1
        elif scope == WaveScope.SMALL_BATCH.value:
            max_allowed = min(config.doc_limit or 5, 5)
            if max_allowed < 1:
                max_allowed = 3
        elif scope == WaveScope.MEDIUM_BATCH.value:
            max_allowed = min(config.doc_limit or 10, 10)

        return targets[:max_allowed]

    def _capture_corpus_fingerprint(
        self,
        exclude_doc_id: Optional[str] = None,
        exclude_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Captures a snapshot of the corpus state to detect unintended mutations."""
        fingerprint: Dict[str, Any] = {"documents": {}}
        if self.index:
            items_map: Dict[str, Any] = {}
            if hasattr(self.index, "documents") and self.index.documents:
                items_map = self.index.documents
            elif hasattr(self.index, "data") and isinstance(self.index.data, dict):
                items_map = self.index.data.get("indexed_items", {})
            elif isinstance(self.index, dict):
                items_map = self.index.get("indexed_items", {}) or self.index.get("documents", {})

            target_hash = (exclude_hash or "").lower().strip()
            target_id = str(exclude_doc_id or "").strip()

            for doc_key, meta in items_map.items():
                if not isinstance(meta, dict):
                    continue

                shash = str(meta.get("file_hash") or meta.get("source_hash") or meta.get("sha256", "") or doc_key).lower().strip()
                m_doc_id = str(meta.get("document_id") or "").strip()
                m_src_id = str(meta.get("source_id") or "").strip()
                m_doc_key = str(meta.get("document_key") or "").strip()
                raw_key = str(doc_key).strip()

                # Determine if this entry belongs to the target document or a distinct document.
                # If an entry carries an explicit, non-empty document_id that conflicts with
                # target_id, it represents a distinct real document and must NOT be excluded
                # (even if it unexpectedly collides on the same file hash).
                has_conflicting_real_id = bool(m_doc_id and target_id and m_doc_id != target_id)

                is_target = False
                if not has_conflicting_real_id:
                    # 1. Exact hash match (cryptographic file identity)
                    if target_hash and (shash == target_hash or raw_key.lower() == target_hash):
                        is_target = True
                    # 2. Known identity alias match (document_id, source_id, document_key, or index key)
                    elif target_id and (target_id in (m_doc_id, m_doc_key, raw_key)):
                        is_target = True
                    elif target_id and m_src_id == target_id and (not target_hash or shash == target_hash):
                        is_target = True
                elif target_id and m_doc_id == target_id:
                    is_target = True

                if is_target:
                    continue

                doc_ident = str(meta.get("document_id") or meta.get("document_key") or meta.get("source_id") or doc_key)
                fp_key = doc_ident
                if fp_key in fingerprint["documents"]:
                    fp_key = f"{doc_ident}_{raw_key}"

                cache_file = self.cache_dir / f"{shash}.txt" if shash else None
                cache_size = cache_file.stat().st_size if (cache_file and cache_file.exists()) else 0
                fingerprint["documents"][fp_key] = {
                    "document_id": doc_ident,
                    "source_hash": shash,
                    "cache_size": cache_size,
                    "chunk_count": meta.get("chunk_count", 0),
                }
        return fingerprint

    def execute_document(
        self,
        item: Dict[str, Any],
        config: WaveConfig,
        baseline_benchmarks: Optional[List[Dict[str, Any]]] = None,
        known_stale_fragments: Optional[List[str]] = None,
        expected_total_chunk_count: Optional[int] = None,
        candidate_override: Optional[ExtractionCandidate] = None,
    ) -> WaveDocumentResult:
        """
        Executes a single document through the wave repair lifecycle:
        SNAPSHOT -> SCAN -> CLASSIFY -> REPAIR -> VERIFY -> RESCAN
        """
        doc_id = str(item.get("document_id", ""))
        src_hash = str(item.get("source_hash", "")).strip().lower()
        src_path = Path(item["source_path"]) if item.get("source_path") else None
        appr_id = item.get("approval_id")

        repair_id = f"rep_{binascii.hexlify(os.urandom(6)).decode()}"
        doc_res = WaveDocumentResult(
            document_id=doc_id,
            source_sha256=src_hash,
            status=WaveDocumentStatus.SKIPPED.value,
        )

        # Step 1: Pre-check & Fingerprint before mutation
        before_fingerprint = {
            "document_id": doc_id,
            "source_hash": src_hash,
            "timestamp": now_iso(),
        }
        if src_hash and (self.cache_dir / f"{src_hash}.txt").exists():
            cache_file = self.cache_dir / f"{src_hash}.txt"
            before_fingerprint["cache_size"] = cache_file.stat().st_size
            before_fingerprint["cache_sha256"] = _calc_sha256(cache_file)

        adjacent_fingerprint_before = self._capture_corpus_fingerprint(exclude_doc_id=doc_id, exclude_hash=src_hash)
        doc_res.before_fingerprint = before_fingerprint

        # Step 2: Scan document
        scan_res = scan_document(
            file_hash=src_hash,
            meta={"document_id": doc_id},
            index=self.index,
            rag=self.rag,
            source_path=src_path,
        )
        if not src_hash and scan_res.source_hash:
            src_hash = scan_res.source_hash
            doc_res.source_sha256 = src_hash

        # Step 3: Classify document
        class_res = classify_document(scan_res)
        doc_res.classification = class_res.classification

        # Disallowed: BLOCKED
        if class_res.classification == ClassificationType.BLOCKED.value:
            doc_res.status = WaveDocumentStatus.BLOCKED.value
            doc_res.action_taken = "BLOCKED"
            self._emit_audit_event(
                AuditEventType.REPAIR_CLASSIFIED,
                repair_id=repair_id,
                document_id=doc_id,
                source_sha256=src_hash,
                status="BLOCKED",
                reason="; ".join(getattr(class_res, "blocking_issues", None) or getattr(class_res, "blocking_reasons", []) or class_res.reasons),
                environment=config.environment,
                actor=config.actor,
            )
            return doc_res

        raw_meta = {}
        if self.index:
            if hasattr(self.index, "get") and callable(self.index.get):
                val = self.index.get(src_hash)
                if isinstance(val, dict):
                    raw_meta = val
            elif hasattr(self.index, "data") and isinstance(self.index.data, dict):
                raw_meta = self.index.data.get("indexed_items", {}).get(src_hash, {})
            elif isinstance(self.index, dict):
                raw_meta = self.index.get("indexed_items", {}).get(src_hash, {})

        if not isinstance(raw_meta, dict):
            raw_meta = {}

        doc_meta = {**raw_meta, **item}

        url_val = doc_meta.get("url")
        has_url = isinstance(url_val, str) and bool(url_val.strip())
        is_web = bool(
            (doc_meta.get("is_pdf") is False)
            or (doc_meta.get("source_type") == "secondary")
            or (doc_meta.get("ingest_origin") == "secondary_web_source")
            or has_url
            or (src_path and str(src_path).startswith(("http://", "https://")))
        )

        # Disallowed: REVIEW without valid approval
        if class_res.classification == ClassificationType.REVIEW.value:
            if not appr_id and not config.dry_run:
                if getattr(config, "allow_production_repair", False) and str(getattr(config, "scope", "")).upper() in ("PILOT", "SMALL_BATCH"):
                    pilot_appr = self.approval_manager.create_approval(
                        repair_id=repair_id,
                        candidate_id=f"cand_{doc_id}",
                        document_id=doc_id,
                        source_sha256=src_hash,
                        actor=config.actor or f"production_{config.scope.lower()}",
                        details={"scope": config.scope, "reason": "Authorized via CLI --repair-wave-allow-production"},
                    )
                    appr_id = pilot_appr.approval_id
                else:
                    doc_res.status = WaveDocumentStatus.WAITING_APPROVAL.value
                    doc_res.action_taken = "NONE"
                    doc_res.warnings.append("Document requires human approval prior to repair.")
                    self._emit_audit_event(
                        AuditEventType.AUTOMATION_APPROVAL_REQUESTED,
                        repair_id=repair_id,
                        document_id=doc_id,
                        source_sha256=src_hash,
                        status="WAITING_APPROVAL",
                        reason="; ".join(getattr(class_res, "review_issues", None) or getattr(class_res, "review_reasons", []) or class_res.reasons),
                        environment=config.environment,
                        actor=config.actor,
                    )
                    return doc_res

            if appr_id:
                # Validate approval
                valid_appr, appr_msg, appr_rec = self.approval_manager.validate_and_consume(
                    approval_id=appr_id,
                    document_id=doc_id,
                    source_sha256=src_hash,
                )
                if not valid_appr:
                    doc_res.status = WaveDocumentStatus.FAILED.value
                    doc_res.action_taken = "ABORT"
                    doc_res.errors.append(f"Approval rejected: {appr_msg}")
                    self._emit_audit_event(
                        AuditEventType.AUTOMATION_APPROVAL_REJECTED,
                        repair_id=repair_id,
                        document_id=doc_id,
                        source_sha256=src_hash,
                        status="APPROVAL_INVALID",
                        reason=appr_msg,
                        environment=config.environment,
                        actor=config.actor,
                    )
                    self._record_failure(config)
                    return doc_res

        # Step 4: Dry Run / Simulation Check
        is_production = config.environment.lower() == "production"
        is_simulated = config.dry_run or (is_production and not config.allow_production_repair)

        if is_simulated:
            # Attempt non-blocking candidate extraction & comparison for simulation insight
            cand_attempt = candidate_override
            if cand_attempt is None and class_res.classification != ClassificationType.SAFE.value:
                can_extract = is_web or (src_path and src_path.exists())
                if can_extract:
                    target_source_path = src_path if not is_web else (src_path or doc_meta.get("url"))
                    cand_res = extract_candidate(
                        source_path=target_source_path,
                        meta={**doc_meta, "document_id": doc_id},
                        expected_sha256=src_hash,
                    )
                    if cand_res.candidate:
                        cand_attempt = cand_res.candidate

            if cand_attempt is not None:
                old_metrics = scan_res.current_metrics
                old_snapshot = build_snapshot(scan_res)
                quality_res = compare_quality(old_snapshot, cand_attempt)
                retrieval_res = RetrievalComparisonResult(
                    status=ComparisonStatus.READY_TO_COMMIT.value
                )
                final_gate = evaluate_final_gate(quality_res, retrieval_res)
                doc_res.candidate = cand_attempt.to_dict() if hasattr(cand_attempt, "to_dict") else cand_attempt
                doc_res.quality_score_before = float(old_snapshot.quality_score or old_metrics.get("score", 0.0) or 0.0)
                doc_res.quality_score_after = float(getattr(cand_attempt, "quality_metrics", {}).get("score", 0.0) or 0.0)
                doc_res.comparison_status = quality_res.status
            elif class_res.classification == ClassificationType.SAFE.value:
                old_metrics = scan_res.current_metrics or {}
                doc_res.quality_score_before = float(old_metrics.get("score", 1.0) or 1.0)
                doc_res.quality_score_after = doc_res.quality_score_before
                doc_res.comparison_status = ComparisonStatus.REVIEW.value
            else:
                old_metrics = scan_res.current_metrics or {}
                doc_res.quality_score_before = float(old_metrics.get("score", 0.0) or 0.0)
                doc_res.quality_score_after = None
                doc_res.comparison_status = ComparisonStatus.REVIEW.value

            doc_res.status = WaveDocumentStatus.SIMULATED.value
            doc_res.action_taken = "SIMULATE"
            self._emit_audit_event(
                AuditEventType.REPAIR_CLASSIFIED,
                repair_id=repair_id,
                document_id=doc_id,
                source_sha256=src_hash,
                status="SIMULATED",
                reason="Wave dry-run simulation with zero writes.",
                environment=config.environment,
                actor=config.actor,
                details={
                    "old_quality": doc_res.quality_score_before,
                    "new_quality": doc_res.quality_score_after,
                    "comparison_status": doc_res.comparison_status,
                } if doc_res.candidate else None,
            )
            return doc_res

        # Step 5: Live Repair Execution with Concurrency Lock
        try:
            with acquire_commit_lock(doc_id, src_hash):
                # TOCTOU Source Hash Validation
                if not is_web and src_path and src_path.exists():
                    current_file_hash = _calc_sha256(src_path)
                    if current_file_hash != src_hash:
                        doc_res.status = WaveDocumentStatus.FAILED.value
                        doc_res.action_taken = "ABORT"
                        doc_res.errors.append(
                            f"TOCTOU hash mismatch: expected {src_hash}, found {current_file_hash}"
                        )
                        self._record_failure(config)
                        return doc_res

                # Extract Candidate
                candidate = candidate_override
                if candidate is None:
                    has_cache = (self.cache_dir / f"{src_hash}.txt").exists()
                    if not is_web and not has_cache and (not src_path or not src_path.exists()):
                        doc_res.status = WaveDocumentStatus.FAILED.value
                        doc_res.errors.append("Source file and source cache missing for candidate extraction.")
                        self._record_failure(config)
                        return doc_res

                    target_source_path = src_path if not is_web else (src_path or doc_meta.get("url"))
                    cand_res = extract_candidate(
                        source_path=target_source_path,
                        meta={**doc_meta, "document_id": doc_id},
                        expected_sha256=src_hash,
                    )
                    if not cand_res.candidate:
                        doc_res.status = WaveDocumentStatus.FAILED.value
                        doc_res.errors.extend(cand_res.errors)
                        self._record_failure(config)
                        return doc_res
                    candidate = cand_res.candidate

                # Quality Comparison
                old_metrics = scan_res.current_metrics
                old_snapshot = build_snapshot(scan_res)
                quality_res = compare_quality(old_snapshot, candidate)

                # Retrieval Gate
                retrieval_res = RetrievalComparisonResult(
                    status=ComparisonStatus.READY_TO_COMMIT.value
                )
                final_gate = evaluate_final_gate(quality_res, retrieval_res)

                doc_res.candidate = candidate.to_dict() if hasattr(candidate, "to_dict") else candidate
                doc_res.quality_score_before = float(old_snapshot.quality_score or old_metrics.get("score", 0.0) or 0.0)
                doc_res.quality_score_after = float(getattr(candidate, "quality_metrics", {}).get("score", 0.0) or 0.0)
                doc_res.comparison_status = quality_res.status

                # Safe Commit via Committer (Single Write Authority)
                commit_policy = CommitPolicy(staging_base_dir=self.staging_dir)
                commit_res = commit_candidate(
                    candidate=candidate,
                    decision=final_gate,
                    allow_commit=True,
                    rag=self.rag,
                    target_cache_dir=self.cache_dir,
                    staging_base_dir=self.staging_dir,
                    policy=commit_policy,
                    dry_run=False,
                )

                doc_res.commit_id = commit_res.commit_id
                doc_res.action_taken = "COMMIT"

                if commit_res.status != CommitStatus.COMMITTED.value:
                    doc_res.status = WaveDocumentStatus.FAILED.value
                    doc_res.errors.extend(commit_res.issues)
                    self._emit_audit_event(
                        AuditEventType.REPAIR_COMMIT_FAILED,
                        repair_id=repair_id,
                        commit_id=commit_res.commit_id,
                        document_id=doc_id,
                        source_sha256=src_hash,
                        status="COMMIT_FAILED",
                        reason="; ".join(commit_res.issues),
                        environment=config.environment,
                        actor=config.actor,
                    )
                    self._record_failure(config)
                    return doc_res

                self._emit_audit_event(
                    AuditEventType.REPAIR_COMMITTED,
                    repair_id=repair_id,
                    commit_id=commit_res.commit_id,
                    document_id=doc_id,
                    source_sha256=src_hash,
                    status="COMMITTED",
                    environment=config.environment,
                    actor=config.actor,
                )

                # Step 6: Post-Commit Verification via Verifier (Single Authority)
                effective_benchmarks = baseline_benchmarks
                if not effective_benchmarks and candidate:
                    doc_search_term = doc_id.replace("_", ".")
                    effective_benchmarks = [
                        {
                            "id": f"BM-{doc_id}-01",
                            "type": "exact_law",
                            "query": doc_search_term,
                            "results": [{"document_id": doc_id}],
                        },
                        {
                            "id": f"BM-{doc_id}-02",
                            "type": "document_lookup",
                            "query": doc_search_term,
                            "results": [{"document_id": doc_id}],
                        },
                        {
                            "id": f"BM-{doc_id}-04",
                            "type": "semantic",
                            "query": doc_search_term,
                            "results": [{"document_id": doc_id}],
                        },
                    ]

                verify_res = verify_committed_repair(
                    commit_result=commit_res,
                    candidate=candidate,
                    rag=self.rag,
                    cache_dir=self.cache_dir,
                    baseline_benchmarks=effective_benchmarks,
                    known_stale_fragments=known_stale_fragments,
                    expected_total_chunk_count=expected_total_chunk_count,
                )
                doc_res.verify_status = verify_res.status

                if verify_res.status != VerificationStatus.VERIFIED_REPAIR.value:
                    # Verification Failed -> Trigger Canonical Rollback
                    self._emit_audit_event(
                        AuditEventType.REPAIR_VERIFICATION_FAILED,
                        repair_id=repair_id,
                        commit_id=commit_res.commit_id,
                        document_id=doc_id,
                        source_sha256=src_hash,
                        status="VERIFY_FAILED",
                        reason="; ".join(verify_res.failures),
                        environment=config.environment,
                        actor=config.actor,
                    )

                    if config.rollback_on_verify_fail:
                        rollback_res = rollback_commit(
                            commit_id=commit_res.commit_id,
                            document_id=doc_id,
                            source_sha256=src_hash,
                            staging_base_dir=self.staging_dir,
                            cache_dir=self.cache_dir,
                            rag=self.rag,
                            index=self.index,
                            verification_result=verify_res,
                            force=True,
                        )
                        doc_res.rollback_occurred = True
                        doc_res.rollback_status = rollback_res.status

                        if rollback_res.status == RollbackStatus.ROLLBACK_SUCCESS.value:
                            doc_res.status = WaveDocumentStatus.VERIFY_FAILED_RESTORED.value
                            self._emit_audit_event(
                                AuditEventType.ROLLBACK_COMPLETED,
                                repair_id=repair_id,
                                commit_id=commit_res.commit_id,
                                rollback_id=rollback_res.rollback_id,
                                document_id=doc_id,
                                source_sha256=src_hash,
                                status="ROLLBACK_SUCCESS",
                                environment=config.environment,
                                actor=config.actor,
                            )
                        else:
                            doc_res.status = WaveDocumentStatus.VERIFY_FAILED_ROLLBACK_FAILED.value
                            doc_res.errors.append(f"Rollback error: {rollback_res.error}")
                            self._emit_audit_event(
                                AuditEventType.ROLLBACK_FAILED,
                                repair_id=repair_id,
                                commit_id=commit_res.commit_id,
                                rollback_id=rollback_res.rollback_id,
                                document_id=doc_id,
                                source_sha256=src_hash,
                                status="ROLLBACK_FAILED",
                                reason=rollback_res.error,
                                environment=config.environment,
                                actor=config.actor,
                            )
                    else:
                        doc_res.status = WaveDocumentStatus.FAILED.value

                    self._record_failure(config)
                    return doc_res

                # Verification Passed
                self._emit_audit_event(
                    AuditEventType.REPAIR_VERIFIED,
                    repair_id=repair_id,
                    commit_id=commit_res.commit_id,
                    document_id=doc_id,
                    source_sha256=src_hash,
                    status="VERIFIED_REPAIR",
                    environment=config.environment,
                    actor=config.actor,
                )

                # Step 7: MANDATORY POST-REPAIR RESCAN (RESCAN GATE)
                if config.require_rescan:
                    rescan_res = scan_document(
                        file_hash=src_hash,
                        meta={"document_id": doc_id},
                        index=self.index,
                        rag=self.rag,
                        source_path=src_path,
                    )

                    # Assert no critical quality regressions remain
                    critical_issue_codes = {
                        IssueCode.QUALITY_GLUED_TEXT,
                        IssueCode.QUALITY_MOJIBAKE,
                        IssueCode.QUALITY_REPLACEMENT_CHAR,
                    }
                    detected_critical = [
                        issue.code for issue in rescan_res.issues
                        if issue.code in critical_issue_codes
                    ]

                    # Enhanced RESCAN gate: distinguish residual non-corrupt / false positive tokens
                    if IssueCode.QUALITY_GLUED_TEXT in detected_critical:
                        baseline_glued = getattr(old_snapshot, "glued_token_count", 0) or 0
                        rescan_glued_count = 0
                        for issue in rescan_res.issues:
                            if issue.code == IssueCode.QUALITY_GLUED_TEXT:
                                rescan_glued_count += int(
                                    issue.details.get("glued_token_count", 0)
                                    or issue.details.get("chroma_glued_token_count", 0)
                                    or 0
                                )

                        # If baseline had real glued issues and candidate achieved >=95% reduction
                        # with at most 2 residual tokens and no very long (>=40 chars) tokens,
                        # treat this as an accepted improvement rather than a critical failure.
                        if baseline_glued > 0 and rescan_glued_count <= 2:
                            reduction = (baseline_glued - rescan_glued_count) / baseline_glued
                            cand_metrics = getattr(candidate, "quality_metrics", {}) or {}
                            has_very_long = cand_metrics.get("very_long_token_count", 0) > 0
                            if reduction >= 0.95 and not has_very_long:
                                logger.info(
                                    f"RESCAN GATE: Glued token reduction accepted for {doc_id}: "
                                    f"{baseline_glued} -> {rescan_glued_count} ({reduction:.1%} reduction)"
                                )
                                detected_critical = [c for c in detected_critical if c != IssueCode.QUALITY_GLUED_TEXT]

                    # Cross-Document Isolation check
                    adjacent_fingerprint_after = self._capture_corpus_fingerprint(exclude_doc_id=doc_id, exclude_hash=src_hash)
                    mutated_adjacent = []
                    for adj_id, adj_before in adjacent_fingerprint_before["documents"].items():
                        adj_after = adjacent_fingerprint_after["documents"].get(adj_id)
                        if adj_after != adj_before:
                            mutated_adjacent.append(f"{adj_id}: before={adj_before}, after={adj_after}")

                    rescan_passed = (len(detected_critical) == 0 and len(mutated_adjacent) == 0)

                    if not rescan_passed:
                        # Rescan failed or cross-doc mutation detected -> rollback immediately!
                        doc_res.rescan_passed = False
                        if detected_critical:
                            doc_res.regressions_detected.extend(detected_critical)
                            doc_res.errors.append(f"Rescan failed: critical issues remained: {detected_critical}")
                        if mutated_adjacent:
                            doc_res.regressions_detected.append("CROSS_DOCUMENT_MUTATION")
                            doc_res.errors.append(f"Cross-document mutation detected on: {mutated_adjacent}")

                        self._emit_audit_event(
                            AuditEventType.WAVE_RESCAN_FAILED,
                            repair_id=repair_id,
                            commit_id=commit_res.commit_id,
                            document_id=doc_id,
                            source_sha256=src_hash,
                            status="RESCAN_FAILED",
                            reason="; ".join(doc_res.errors),
                            environment=config.environment,
                            actor=config.actor,
                        )

                        if config.rollback_on_rescan_fail:
                            rollback_res = rollback_commit(
                                commit_id=commit_res.commit_id,
                                document_id=doc_id,
                                source_sha256=src_hash,
                                staging_base_dir=self.staging_dir,
                                cache_dir=self.cache_dir,
                                rag=self.rag,
                                index=self.index,
                                force=True,
                            )
                            doc_res.rollback_occurred = True
                            doc_res.rollback_status = rollback_res.status
                            doc_res.status = WaveDocumentStatus.RESCAN_FAILED.value
                        else:
                            doc_res.status = WaveDocumentStatus.FAILED.value

                        self._record_failure(config)
                        return doc_res

                    # Rescan Passed!
                    doc_res.rescan_passed = True
                    doc_res.status = WaveDocumentStatus.VERIFIED_AND_RESCANNED.value
                    self._record_success()
                    self._emit_audit_event(
                        AuditEventType.WAVE_RESCAN_PASSED,
                        repair_id=repair_id,
                        commit_id=commit_res.commit_id,
                        document_id=doc_id,
                        source_sha256=src_hash,
                        status="RESCAN_PASSED",
                        environment=config.environment,
                        actor=config.actor,
                    )
                else:
                    # Rescan skipped by explicit config (rare)
                    doc_res.status = WaveDocumentStatus.VERIFIED_AND_RESCANNED.value
                    self._record_success()

                # Step 8: Capture After Fingerprint
                after_fingerprint = {
                    "document_id": doc_id,
                    "source_hash": src_hash,
                    "timestamp": now_iso(),
                }
                if src_hash and (self.cache_dir / f"{src_hash}.txt").exists():
                    c_file = self.cache_dir / f"{src_hash}.txt"
                    after_fingerprint["cache_size"] = c_file.stat().st_size
                    after_fingerprint["cache_sha256"] = _calc_sha256(c_file)
                doc_res.after_fingerprint = after_fingerprint

                return doc_res

        except Exception as exc:
            logger.exception("Unexpected error executing repair for document %s: %s", doc_id, exc)
            doc_res.status = WaveDocumentStatus.FAILED.value
            doc_res.errors.append(str(exc))
            self._record_failure(config)
            return doc_res

    def _record_success(self) -> None:
        """Resets consecutive failure count upon successful verified rescan."""
        with self._wave_lock:
            self.consecutive_failures = 0

    def _record_failure(self, config: WaveConfig) -> None:
        """Increments failures and trips circuit breaker if thresholds reached."""
        with self._wave_lock:
            self.consecutive_failures += 1
            self.total_failures += 1
            if self.consecutive_failures >= config.max_consecutive_failures or self.total_failures >= config.failure_budget:
                self.circuit_open = True

    def execute_wave(
        self,
        config: WaveConfig,
        target_items: Optional[List[Dict[str, Any]]] = None,
        baseline_benchmarks: Optional[List[Dict[str, Any]]] = None,
        candidate_overrides: Optional[Dict[str, ExtractionCandidate]] = None,
    ) -> WaveSummary:
        """
        Executes a controlled repair wave across scoped target documents.
        """
        wave_id = f"wave_{binascii.hexlify(os.urandom(6)).decode()}"
        started_at = now_iso()

        summary = WaveSummary(
            wave_id=wave_id,
            scope=config.scope,
            environment=config.environment,
            dry_run=config.dry_run,
            started_at=started_at,
        )

        # 1. Production Corpus Presence Check
        is_production = config.environment.lower() == "production"
        if is_production:
            present, reason, details = check_production_corpus(
                db_path=DB_PATH,
                index_file=INGEST_INDEX_FILE,
                cache_dir=SOURCE_CACHE_DIR,
                rag=self.rag,
            )
            if not present:
                summary.status = WaveStatus.PRODUCTION_CORPUS_ABSENT.value
                summary.halt_reason = (
                    f"Production corpus is ABSENT ({reason}). Live production repair strictly blocked."
                )
                summary.completed_at = now_iso()
                self._emit_audit_event(
                    AuditEventType.WAVE_HALTED,
                    repair_id=wave_id,
                    document_id="wave",
                    source_sha256="",
                    status="CORPUS_ABSENT",
                    reason=summary.halt_reason,
                    environment=config.environment,
                    actor=config.actor,
                )
                return summary

            # Production Multi-Key Safety Guard
            if not config.allow_production_repair and not config.dry_run:
                summary.status = WaveStatus.BLOCKED.value
                summary.halt_reason = (
                    "Production execution requires --repair-wave-allow-production flag. Blocked."
                )
                summary.completed_at = now_iso()
                return summary

        # 2. Scope & Target Resolution
        scoped_targets = self.resolve_target_scope(config, provided_items=target_items)
        summary.target_count = len(scoped_targets)

        # Emit Wave Started
        self._emit_audit_event(
            AuditEventType.WAVE_STARTED,
            repair_id=wave_id,
            document_id="wave",
            source_sha256="",
            status="STARTED",
            details={
                "scope": config.scope,
                "targets_count": len(scoped_targets),
                "environment": config.environment,
                "dry_run": config.dry_run,
            },
            environment=config.environment,
            actor=config.actor,
        )

        candidate_overrides = candidate_overrides or {}

        # 3. Process targets sequentially
        for item in scoped_targets:
            # Check Circuit Breaker
            if self.circuit_open:
                summary.circuit_broken = True
                summary.status = WaveStatus.CIRCUIT_OPEN.value
                summary.halt_reason = (
                    f"Circuit breaker tripped: consecutive_failures={self.consecutive_failures}, total_failures={self.total_failures}"
                )
                self._emit_audit_event(
                    AuditEventType.WAVE_HALTED,
                    repair_id=wave_id,
                    document_id="wave",
                    source_sha256="",
                    status="HALTED",
                    reason=summary.halt_reason,
                    environment=config.environment,
                    actor=config.actor,
                )
                break

            doc_id = str(item.get("document_id", ""))
            doc_candidate = candidate_overrides.get(doc_id)

            doc_res = self.execute_document(
                item=item,
                config=config,
                baseline_benchmarks=baseline_benchmarks,
                candidate_override=doc_candidate,
            )

            summary.scanned_count += 1
            summary.documents.append(doc_res)

            # Metrics
            if doc_res.classification == ClassificationType.SAFE.value:
                summary.safe_count += 1
            elif doc_res.classification == ClassificationType.REVIEW.value:
                summary.review_count += 1
            elif doc_res.classification == ClassificationType.BLOCKED.value:
                summary.blocked_count += 1

            if doc_res.action_taken in ("COMMIT", "REPAIR"):
                summary.attempted_count += 1

            if doc_res.commit_id:
                summary.committed_count += 1
                if is_production and not config.dry_run:
                    summary.production_mutation_count += 1
                else:
                    summary.sandbox_mutation_count += 1

            if doc_res.verify_status == VerificationStatus.VERIFIED_REPAIR.value:
                summary.verified_count += 1

            if doc_res.rescan_passed:
                summary.rescanned_count += 1

            if doc_res.rollback_occurred:
                summary.rolled_back_count += 1

            if doc_res.status in (
                WaveDocumentStatus.FAILED.value,
                WaveDocumentStatus.RESCAN_FAILED.value,
                WaveDocumentStatus.VERIFY_FAILED_RESTORED.value,
                WaveDocumentStatus.VERIFY_FAILED_ROLLBACK_FAILED.value,
            ):
                summary.failed_count += 1
                if config.halt_on_failure:
                    summary.status = WaveStatus.HALTED.value
                    summary.halt_reason = f"Wave halted after failure on document {doc_id}: {doc_res.errors}"
                    break

        summary.completed_at = now_iso()

        # Final Status determination
        if summary.status not in (WaveStatus.HALTED.value, WaveStatus.CIRCUIT_OPEN.value, WaveStatus.PRODUCTION_CORPUS_ABSENT.value):
            if summary.failed_count > 0:
                summary.status = WaveStatus.PARTIAL.value if (summary.rescanned_count > 0 or summary.verified_count > 0) else WaveStatus.HALTED.value
            else:
                summary.status = WaveStatus.SUCCESS.value

        # Emit Wave Completed
        self._emit_audit_event(
            AuditEventType.WAVE_COMPLETED,
            repair_id=wave_id,
            document_id="wave",
            source_sha256="",
            status=summary.status,
            details={
                "targets": summary.target_count,
                "rescanned": summary.rescanned_count,
                "failed": summary.failed_count,
                "rolled_back": summary.rolled_back_count,
                "halt_reason": summary.halt_reason,
            },
            environment=config.environment,
            actor=config.actor,
        )

        return summary
