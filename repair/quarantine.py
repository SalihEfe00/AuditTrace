# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19: Operational Quarantine Management.

Provides safe, isolated, and traceable quarantine management:
- Listing and inspecting quarantined documents
- Isolated quarantine payload cache (data/quarantine_cache/)
- Integrity verification of cached payloads (SHA256 matching)
- Review / Acknowledge workflow without Chroma mutation
- Safe retry routing strictly through versioned_ingest_text() -> AutoHealingIngestionGate
- Complete audit logging and tamper-evident hash-chain preservation

Zero direct write to Chroma is permitted from this module.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mufettis.config import QUARANTINE_CACHE_DIR, QUARANTINE_MAX_PAYLOAD_MB
from mufettis.common.utils import now_iso, sha256_bytes
from mufettis.rag.index import IngestIndex
from mufettis.rag.engine import LocalRAG
from repair.models import AuditEventType
from repair.audit import RepairAuditLogger, get_audit_logger

logger = logging.getLogger(__name__)


# ── Quarantine Payload Storage ────────────────────────────────────────────────

def get_quarantine_cache_dir(custom_path: Optional[Path] = None) -> Path:
    """Returns resolved quarantine cache directory."""
    if custom_path is not None:
        p = Path(custom_path)
    else:
        p = Path(QUARANTINE_CACHE_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_quarantine_payload(
    file_hash: str,
    text: Optional[str] = None,
    raw_bytes: Optional[bytes] = None,
    cache_dir: Optional[Path] = None,
    max_mb: int = QUARANTINE_MAX_PAYLOAD_MB,
) -> Dict[str, Any]:
    """Saves raw_bytes and/or text of a quarantined document into isolated quarantine cache.
    
    Validates SHA256 of raw_bytes against file_hash when raw_bytes is provided.
    Never exposes raw payload to IngestIndex or Chroma.
    """
    file_hash = str(file_hash or "").strip()
    if not file_hash:
        raise ValueError("file_hash is required to store quarantine payload")

    cdir = get_quarantine_cache_dir(cache_dir)
    max_bytes = max_mb * 1024 * 1024
    saved_info = {
        "file_hash": file_hash,
        "saved_bytes": False,
        "saved_text": False,
        "bytes_path": None,
        "text_path": None,
    }

    # 1. Save raw bytes if provided and within size limit
    if raw_bytes is not None:
        if len(raw_bytes) > max_bytes:
            logger.warning("Quarantine payload bytes exceed %d MB limit for %s; skipping binary cache", max_mb, file_hash)
        else:
            bin_path = cdir / f"{file_hash}.bin"
            tmp_bin = bin_path.with_suffix(".bin.tmp")
            tmp_bin.write_bytes(raw_bytes)
            tmp_bin.replace(bin_path)
            saved_info["saved_bytes"] = True
            saved_info["bytes_path"] = str(bin_path)

    # 2. Save text if provided and within size limit
    if text is not None and str(text).strip():
        text_payload = str(text)
        if len(text_payload.encode("utf-8")) > max_bytes:
            logger.warning("Quarantine payload text exceeds %d MB limit for %s; skipping text cache", max_mb, file_hash)
        else:
            txt_path = cdir / f"{file_hash}.txt"
            tmp_txt = txt_path.with_suffix(".txt.tmp")
            tmp_txt.write_text(text_payload, encoding="utf-8")
            tmp_txt.replace(txt_path)
            saved_info["saved_text"] = True
            saved_info["text_path"] = str(txt_path)

    return saved_info


def load_quarantine_payload(
    file_hash: str,
    cache_dir: Optional[Path] = None,
) -> Tuple[Optional[str], Optional[bytes]]:
    """Loads cached text and/or raw_bytes from isolated quarantine cache."""
    file_hash = str(file_hash or "").strip()
    if not file_hash:
        return None, None

    cdir = get_quarantine_cache_dir(cache_dir)
    txt_path = cdir / f"{file_hash}.txt"
    bin_path = cdir / f"{file_hash}.bin"

    loaded_text = None
    loaded_bytes = None

    if bin_path.is_file():
        try:
            loaded_bytes = bin_path.read_bytes()
        except Exception as exc:
            logger.warning("Could not read quarantine binary %s: %s", bin_path, exc)

    if txt_path.is_file():
        try:
            loaded_text = txt_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not read quarantine text %s: %s", txt_path, exc)

    return loaded_text, loaded_bytes


def verify_quarantine_payload_integrity(
    file_hash: str,
    cache_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Verifies that stored binary payload in quarantine cache matches the target file_hash."""
    file_hash = str(file_hash or "").strip()
    if not file_hash:
        return False, "Empty file_hash"

    cdir = get_quarantine_cache_dir(cache_dir)
    bin_path = cdir / f"{file_hash}.bin"
    txt_path = cdir / f"{file_hash}.txt"

    if bin_path.is_file():
        try:
            actual_hash = sha256_bytes(bin_path.read_bytes())
            if actual_hash != file_hash:
                return False, f"Binary payload SHA256 mismatch: expected {file_hash}, got {actual_hash}"
            return True, "Binary payload verified"
        except Exception as exc:
            return False, f"Failed reading binary payload: {exc}"

    if txt_path.is_file():
        return True, "Text payload present (no binary)"

    return False, "No quarantine payload found in cache"


# ── Quarantine Listing & Inspection (Read-Only) ──────────────────────────────

def list_quarantined_documents(
    index: Optional[IngestIndex] = None,
    filter_reviewed: Optional[bool] = None,
    cache_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Lists all currently quarantined documents from IngestIndex (Strictly Read-Only)."""
    if index is None:
        index = IngestIndex()

    all_items = index.data.get("indexed_items", {}) if hasattr(index, "data") else (getattr(index, "indexed_items", {}) or {})
    cdir = get_quarantine_cache_dir(cache_dir)
    results = []

    for f_hash, meta in all_items.items():
        st = str(meta.get("status") or meta.get("ingest_status") or "").lower()
        if st != "quarantined":
            continue

        is_reviewed = bool(meta.get("reviewed", False))
        if filter_reviewed is not None and is_reviewed != filter_reviewed:
            continue

        has_bin = (cdir / f"{f_hash}.bin").is_file()
        has_txt = (cdir / f"{f_hash}.txt").is_file()

        item = {
            "file_hash": f_hash,
            "document_id": meta.get("document_id") or meta.get("source_id") or f_hash,
            "document_key": meta.get("document_key") or "",
            "title": meta.get("title") or "",
            "source_id": meta.get("source_id") or "",
            "source_type": meta.get("source_type") or meta.get("ingest_origin") or "",
            "url": meta.get("url") or "",
            "source_path": meta.get("source_path") or meta.get("local_path") or "",
            "quarantine_reason": meta.get("quarantine_reason") or "",
            "quarantine_issues": meta.get("quarantine_issues") or [],
            "quarantined_at": meta.get("quarantined_at") or "",
            "retry_count": int(meta.get("retry_count", 0) or 0),
            "last_retry_at": meta.get("last_retry_at"),
            "reviewed": is_reviewed,
            "reviewed_at": meta.get("reviewed_at"),
            "reviewed_by": meta.get("reviewed_by"),
            "review_notes": meta.get("review_notes"),
            "quarantine_history": list(meta.get("quarantine_history") or []),
            "has_cached_payload": has_bin or has_txt,
            "has_binary_cache": has_bin,
            "has_text_cache": has_txt,
        }
        results.append(item)

    # Sort newest quarantined first
    results.sort(key=lambda x: str(x.get("quarantined_at") or ""), reverse=True)
    return results


def get_quarantine_detail(
    file_hash: str,
    index: Optional[IngestIndex] = None,
    cache_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieves full quarantine record for a single document."""
    file_hash = str(file_hash or "").strip()
    if not file_hash:
        return None

    if index is None:
        index = IngestIndex()

    meta = index.get(file_hash)
    if not meta or str(meta.get("status", "")).lower() != "quarantined":
        return None

    cdir = get_quarantine_cache_dir(cache_dir)
    has_bin = (cdir / f"{file_hash}.bin").is_file()
    has_txt = (cdir / f"{file_hash}.txt").is_file()

    valid, msg = verify_quarantine_payload_integrity(file_hash, cache_dir=cdir)

    detail = dict(meta)
    detail.update({
        "file_hash": file_hash,
        "has_cached_payload": has_bin or has_txt,
        "has_binary_cache": has_bin,
        "has_text_cache": has_txt,
        "payload_integrity_valid": valid,
        "payload_integrity_msg": msg,
        "retry_count": int(meta.get("retry_count", 0) or 0),
        "quarantine_history": list(meta.get("quarantine_history") or []),
    })
    return detail


# ── Review / Acknowledge Workflow ─────────────────────────────────────────────

def review_quarantine_document(
    file_hash: str,
    reviewer: str = "operator",
    notes: str = "",
    index: Optional[IngestIndex] = None,
    audit_logger: Optional[RepairAuditLogger] = None,
) -> Dict[str, Any]:
    """Marks a quarantined document as reviewed with operator notes.
    
    Updates IngestIndex metadata only; performs ZERO Chroma mutations.
    Emits INGEST_QUARANTINE_REVIEWED event to preserve tamper-evident audit log.
    """
    file_hash = str(file_hash or "").strip()
    if not file_hash:
        raise ValueError("file_hash is required to review quarantine record")

    if index is None:
        index = IngestIndex()

    meta = index.get(file_hash)
    if not meta or str(meta.get("status", "")).lower() != "quarantined":
        return {
            "status": "error",
            "error": f"Document {file_hash} is not in quarantine",
        }

    updated_meta = dict(meta)
    reviewed_at = now_iso()
    updated_meta["reviewed"] = True
    updated_meta["reviewed_at"] = reviewed_at
    updated_meta["reviewed_by"] = reviewer
    updated_meta["review_notes"] = notes

    index.upsert(file_hash, updated_meta)

    # Audit Logging
    logger_instance = audit_logger
    if logger_instance is None and index is not None and getattr(index, "path", None):
        try:
            from mufettis.config import DATA_DIR
            idx_resolved = str(Path(index.path).resolve())
            if not idx_resolved.startswith(str(DATA_DIR.resolve())):
                logger_instance = RepairAuditLogger(log_path=Path(index.path).parent / "repair_audit.jsonl")
        except Exception:
            pass
    if logger_instance is None:
        logger_instance = get_audit_logger()
    try:
        logger_instance.emit(
            event_type=AuditEventType.INGEST_QUARANTINE_REVIEWED.value,
            repair_id=f"review_{file_hash[:12]}",
            document_id=str(meta.get("document_id") or meta.get("source_id") or file_hash),
            source_sha256=file_hash,
            status="REVIEWED",
            reason=notes or "Quarantine record reviewed by operator",
            actor=reviewer,
            trigger="admin_ui",
            details={
                "reviewer": reviewer,
                "notes": notes,
                "quarantined_at": meta.get("quarantined_at"),
            },
        )
    except Exception as exc:
        logger.warning("Quarantine review audit emit failed: %s", exc)

    return {
        "status": "reviewed",
        "file_hash": file_hash,
        "reviewed_at": reviewed_at,
        "reviewed_by": reviewer,
        "notes": notes,
    }


# ── Canonical Retry Routing (Zero Bypass) ─────────────────────────────────────

def retry_quarantined_document(
    rag: LocalRAG,
    index: IngestIndex,
    file_hash: str,
    trigger: str = "manual",
    actor: str = "operator",
    cache_dir: Optional[Path] = None,
    audit_logger: Optional[RepairAuditLogger] = None,
) -> Dict[str, Any]:
    """Retries ingesting a quarantined document through the full canonical gate.
    
    CRITICAL: This function NEVER writes directly to Chroma or bypasses VERIFY/RESCAN.
    It routes payload strictly through versioned_ingest_text() -> AutoHealingIngestionGate.
    """
    file_hash = str(file_hash or "").strip()
    if not file_hash:
        return {"status": "error", "error": "file_hash is required for retry"}

    record = index.get(file_hash)
    if not record or str(record.get("status", "")).lower() != "quarantined":
        return {
            "status": "error",
            "error": f"Document {file_hash} is not currently quarantined",
        }

    cdir = get_quarantine_cache_dir(cache_dir)

    # 1. Resolve payload & check integrity
    cached_text, cached_bytes = load_quarantine_payload(file_hash, cache_dir=cdir)
    effective_text = cached_text
    effective_raw_bytes = cached_bytes
    effective_source_path = record.get("source_path") or record.get("local_path")

    # If cached binary is present, strictly verify hash
    if cached_bytes is not None:
        valid, msg = verify_quarantine_payload_integrity(file_hash, cache_dir=cdir)
        if not valid:
            return {
                "status": "blocked",
                "error": f"Quarantine payload integrity check failed: {msg}",
            }

    # If cache has no text or binary, check local source_path on disk
    if not effective_text and not effective_raw_bytes and effective_source_path:
        sp = Path(effective_source_path)
        if sp.is_file():
            try:
                sp_bytes = sp.read_bytes()
                sp_hash = sha256_bytes(sp_bytes)
                if sp_hash == file_hash:
                    effective_raw_bytes = sp_bytes
                    if sp.suffix.lower() == ".txt":
                        effective_text = sp.read_text(encoding="utf-8")
                else:
                    return {
                        "status": "blocked",
                        "error": f"Source file on disk has hash {sp_hash}, expected quarantined {file_hash}",
                    }
            except Exception as exc:
                logger.warning("Could not read source_path for retry %s: %s", effective_source_path, exc)

    # If still no content and this is a web document with URL, attempt re-fetch
    if not effective_text and not effective_raw_bytes and record.get("url") and record.get("source_id"):
        try:
            from mufettis.harvesting.web_harvester import WebSourceHarvester
            harvester = WebSourceHarvester(record["source_id"], local_only=False)
            fetch_res = harvester.fetch_candidate({"url": record["url"], "title": record.get("title")})
            if fetch_res and fetch_res.get("raw_bytes"):
                fetched_hash = sha256_bytes(fetch_res["raw_bytes"])
                if fetched_hash == file_hash:
                    effective_raw_bytes = fetch_res["raw_bytes"]
                    effective_text = fetch_res.get("text", "")
                else:
                    return {
                        "status": "blocked",
                        "error": f"Re-fetched web content hash {fetched_hash} does not match quarantined {file_hash}",
                    }
        except Exception as exc:
            logger.warning("Re-fetch from web failed during retry for %s: %s", record.get("url"), exc)

    # If content could not be located anywhere
    if not effective_text and not effective_raw_bytes:
        return {
            "status": "error",
            "error": "No cached payload or readable source found for retry; cannot proceed",
        }

    # 2. Record retry attempt metadata
    attempt_no = int(record.get("retry_count", 0) or 0) + 1
    previous_reason = record.get("quarantine_reason", "")
    existing_history = list(record.get("quarantine_history") or [])

    retry_meta = dict(record)
    retry_meta["retry_count"] = attempt_no
    retry_meta["last_retry_at"] = now_iso()
    retry_meta["quarantine_history"] = existing_history
    retry_meta["is_retry"] = True

    # 3. Emit INGEST_RETRY_STARTED audit event
    logger_instance = audit_logger
    if logger_instance is None and index is not None and getattr(index, "path", None):
        try:
            from mufettis.config import DATA_DIR
            idx_resolved = str(Path(index.path).resolve())
            if not idx_resolved.startswith(str(DATA_DIR.resolve())):
                logger_instance = RepairAuditLogger(log_path=Path(index.path).parent / "repair_audit.jsonl")
        except Exception:
            pass
    if logger_instance is None:
        logger_instance = get_audit_logger()
    try:
        logger_instance.emit(
            event_type=AuditEventType.INGEST_RETRY_STARTED.value,
            repair_id=f"retry_{file_hash[:12]}_{attempt_no}",
            document_id=str(record.get("document_id") or record.get("source_id") or file_hash),
            source_sha256=file_hash,
            status="RETRY_STARTED",
            reason=f"Retry attempt #{attempt_no} initiated",
            actor=actor,
            trigger=trigger,
            details={
                "attempt_no": attempt_no,
                "previous_reason": previous_reason,
                "trigger": trigger,
                "actor": actor,
            },
        )
    except Exception as exc:
        logger.warning("Retry started audit emit failed: %s", exc)

    # 4. Canonical Ingestion Pipeline Routing (strictly through versioned_ingest_text)
    from mufettis.rag.maintenance import versioned_ingest_text

    result = versioned_ingest_text(
        rag=rag,
        index=index,
        text=effective_text or "",
        meta=retry_meta,
        force=True,
        raw_bytes=effective_raw_bytes,
        source_path=effective_source_path,
    )

    return result
