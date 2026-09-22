# -*- coding: utf-8 -*-
"""Auto-Healing Ingestion Gate for Web and External Regulatory Sources.

Ensures that any new document ingested from web harvesting, official gazette,
bulk import, or manual upload undergoes automated quality inspection,
self-healing (if corrupted), verification, and post-ingest rescan before being
permanently committed to Chroma.
Corrupted documents that cannot be healed are safely quarantined with zero
dirty chunks left in Chroma.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mufettis.audit.service import append_audit
from mufettis.common.utils import now_iso, safe_json
from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.extraction.pdf import _is_true_glued_token, _pdf_generic_quality_metrics
from mufettis.extraction.text_repair import (
    _repair_glued_turkish_words,
    _repair_glued_uppercase_words,
    normalize_extracted_text,
    repair_extracted_spacing,
    repair_mojibake,
)
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex
from repair.audit import RepairAuditLogger, get_audit_logger
from repair.models import AuditEventType

logger = logging.getLogger(__name__)

DEFAULT_QUALITY_THRESHOLD = 0.88


class AutoHealingIngestionGate:
    """Automated Quality and Self-Healing Gate for Ingestion Pipelines."""

    def __init__(
        self,
        rag: Optional[LocalRAG] = None,
        index: Optional[IngestIndex] = None,
        audit_logger: Optional[RepairAuditLogger] = None,
        quality_threshold: float = DEFAULT_QUALITY_THRESHOLD,
    ):
        self.rag = rag
        self.index = index
        self.quality_threshold = float(quality_threshold)
        if audit_logger is not None:
            self.audit_logger = audit_logger
        elif index is not None and getattr(index, "path", None):
            from mufettis.config import DATA_DIR
            idx_resolved = str(Path(index.path).resolve())
            if not idx_resolved.startswith(str(DATA_DIR.resolve())):
                self.audit_logger = RepairAuditLogger(log_path=Path(index.path).parent / "repair_audit.jsonl")
            else:
                self.audit_logger = get_audit_logger()
        else:
            self.audit_logger = get_audit_logger()

    # ── Quality Evaluation ────────────────────────────────────────────────────

    def evaluate_text_quality(
        self, text: str, title: str = ""
    ) -> Dict[str, Any]:
        """Evaluates text quality using standard metrics and Turkish morphological rules."""
        raw_metrics = _pdf_generic_quality_metrics(str(text or ""))

        # Accurately count true glued tokens (distinguishing Turkish agglutinative words)
        tokens = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]{24,}", str(text or ""))
        true_glued_count = 0
        glued_samples = []
        for t in tokens:
            if _is_true_glued_token(t):
                true_glued_count += 1
                if len(glued_samples) < 5:
                    glued_samples.append(t)

        score = float(raw_metrics.get("score", 0.0) or 0.0)
        mojibake_count = int(raw_metrics.get("mojibake_sequence_count", 0) or 0)
        replacement_count = int(raw_metrics.get("replacement_char_count", 0) or 0)

        # Invariant: a document is clean only if score >= threshold and has 0 corruptions
        is_clean = (
            score >= self.quality_threshold
            and true_glued_count == 0
            and mojibake_count == 0
            and replacement_count == 0
        )

        issues = []
        if score < self.quality_threshold:
            issues.append(f"LOW_QUALITY_SCORE_{score:.4f}")
        if true_glued_count > 0:
            issues.append(f"GLUED_TOKENS_{true_glued_count}")
        if mojibake_count > 0:
            issues.append(f"MOJIBAKE_{mojibake_count}")
        if replacement_count > 0:
            issues.append(f"REPLACEMENT_CHARS_{replacement_count}")

        return {
            "score": score,
            "is_clean": is_clean,
            "glued_token_count": true_glued_count,
            "glued_samples": glued_samples,
            "mojibake_sequence_count": mojibake_count,
            "replacement_char_count": replacement_count,
            "issues": issues,
            "chars": int(raw_metrics.get("chars", 0) or 0),
            "tokens": int(raw_metrics.get("tokens", 0) or 0),
        }

    # ── Self-Healing Engine ───────────────────────────────────────────────────

    def attempt_self_healing(
        self,
        text: str,
        meta: Dict[str, Any],
        raw_bytes: Optional[bytes] = None,
        source_path: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Attempts multi-stage self-healing for corrupted extracted text.

        Stages:
        1. Mojibake recovery & encoding reconstruction
        2. Glued word / spacing repair & word boundary segmentation
        3. Fallback re-extraction if raw bytes / source path are available
        """
        original_text = str(text or "")
        current_text = original_text

        # Stage 1: Mojibake repair
        try:
            current_text = repair_mojibake(current_text)
            # Try whole-string encoding round-trips if mojibake markers remain
            if any(m in current_text for m in ("Ã", "Å", "Ä", "â€")):
                for enc in ("latin1", "cp1254", "cp1252", "iso-8859-9"):
                    try:
                        fixed = current_text.encode(enc).decode("utf-8")
                        if "ç" in fixed or "ğ" in fixed or "ı" in fixed or "ö" in fixed or "ş" in fixed or "ü" in fixed:
                            current_text = fixed
                            break
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("AutoHealing: mojibake repair stage failed: %s", exc)

        # Stage 2: Spacing and Glued Token repair
        try:
            title = meta.get("title", "")
            doc_type = meta.get("document_type", "")
            current_text = repair_extracted_spacing(current_text, title, doc_type)
            current_text = _repair_glued_uppercase_words(current_text)
            current_text = _repair_glued_turkish_words(current_text)

            # Split obvious camelCase / punctuation glue: e.g. "madde1" -> "madde 1", "kanunMaddesi" -> "kanun Maddesi"
            current_text = re.sub(r"([a-zçğıöşü])([A-ZÇĞİÖŞÜ])", r"\1 \2", current_text)
            current_text = re.sub(r"([A-Za-zÇĞİÖŞÜçğıöşü])(\.)([A-ZÇĞİÖŞÜ])", r"\1. \3", current_text)

            # Specific known multi-word glued token fixes
            tokens = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]{24,}", current_text)
            for t in tokens:
                if _is_true_glued_token(t):
                    # Try splitting concatenated Turkish words
                    split_candidate = re.sub(r"([a-zçğıöşü]{4,})([a-zçğıöşü]{4,})", r"\1 \2", t)
                    if split_candidate != t:
                        current_text = current_text.replace(t, split_candidate)
        except Exception as exc:
            logger.warning("AutoHealing: spacing/glued repair stage failed: %s", exc)

        # Stage 3: Source Fallback Re-Extraction if source available and still not clean
        eval_metrics = self.evaluate_text_quality(current_text, meta.get("title", ""))
        if not eval_metrics["is_clean"] and (raw_bytes or source_path):
            try:
                candidate_text = None
                from repair.extractor import extract_candidate
                candidate = extract_candidate(
                    source_path=source_path,
                    meta=meta,
                    expected_hash=meta.get("file_hash"),
                    raw_bytes=raw_bytes,
                    title=meta.get("title", ""),
                    document_type=meta.get("document_type", ""),
                )
                if candidate and candidate.status == "SUCCESS" and candidate.raw_text.strip():
                    candidate_text = candidate.raw_text
                elif raw_bytes:
                    # Direct raw_bytes fallback if extractor returned non-success
                    if raw_bytes.startswith(b"%PDF") or meta.get("is_pdf"):
                        from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper
                        scraper = ResmiGazeteScraper(local_only=True)
                        extracted, _, _ = scraper._extract_pdf_text(raw_bytes)
                        if extracted and extracted.strip():
                            candidate_text = normalize_extracted_text(extracted)
                            candidate_text = repair_extracted_spacing(candidate_text, meta.get("title", ""), meta.get("document_type", ""))
                    else:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(raw_bytes.decode("utf-8", errors="replace"), "html.parser")
                        for el in soup(["script", "style", "header", "footer", "nav"]):
                            el.extract()
                        raw_html = re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip()
                        if raw_html:
                            candidate_text = normalize_extracted_text(raw_html)
                            candidate_text = repair_extracted_spacing(candidate_text, meta.get("title", ""), meta.get("document_type", ""))

                if candidate_text:
                    cand_metrics = self.evaluate_text_quality(candidate_text, meta.get("title", ""))
                    if cand_metrics["is_clean"] or cand_metrics["score"] > eval_metrics["score"]:
                        current_text = candidate_text
                        eval_metrics = cand_metrics
                        meta["source_fallback_used"] = True
            except Exception as exc:
                logger.warning("AutoHealing: fallback re-extraction stage failed: %s", exc)

        # Final evaluation of repaired text
        final_metrics = self.evaluate_text_quality(current_text, meta.get("title", ""))
        is_improved = final_metrics["is_clean"] or (
            final_metrics["score"] > self.evaluate_text_quality(original_text)["score"]
            and final_metrics["glued_token_count"] == 0
            and final_metrics["mojibake_sequence_count"] == 0
        )

        return is_improved, current_text, final_metrics

    # ── Chroma Chunk Verification & Rescan ───────────────────────────────────

    def verify_committed_chunks(self, rag: LocalRAG, file_hash: str) -> Tuple[bool, List[str]]:
        """Verifies that chunks for file_hash exist in Chroma and carry valid metadata."""
        try:
            got = rag.collection.get(where={"file_hash": file_hash}, include=["metadatas"])
            ids = got.get("ids", []) if got else []
            if not ids:
                return False, [f"Chroma contains 0 chunks for hash {file_hash}"]
            return True, []
        except Exception as exc:
            return False, [f"Chroma verification query failed: {exc}"]

    def rescan_committed_chunks(
        self, rag: LocalRAG, file_hash: str
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Rescans all committed Chroma chunks to ensure 0 corruptions entered storage."""
        try:
            got = rag.collection.get(
                where={"file_hash": file_hash},
                include=["documents", "metadatas"],
            )
            docs = got.get("documents", []) if got else []
            if not docs:
                return False, ["Rescan failed: no documents returned from Chroma"], {}

            glued_total = 0
            mojibake_total = 0
            replacement_total = 0
            scores = []
            issues = []

            for doc_str in docs:
                doc_text = str(doc_str or "")
                q = _pdf_generic_quality_metrics(doc_text)
                mojibake_total += int(q.get("mojibake_sequence_count", 0) or 0)
                replacement_total += int(q.get("replacement_char_count", 0) or 0)
                if doc_text.strip():
                    scores.append(float(q.get("score", 0.0) or 0.0))

                # True glued token evaluation
                tokens = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]{24,}", doc_text)
                for t in tokens:
                    if _is_true_glued_token(t):
                        glued_total += 1

            min_score = min(scores) if scores else 0.0
            avg_score = sum(scores) / len(scores) if scores else 0.0

            if glued_total > 0:
                issues.append(f"RESCAN_CHROMA_GLUED_TOKENS_{glued_total}")
            if mojibake_total > 0:
                issues.append(f"RESCAN_CHROMA_MOJIBAKE_{mojibake_total}")
            if replacement_total > 0:
                issues.append(f"RESCAN_CHROMA_REPLACEMENT_{replacement_total}")
            if min_score < self.quality_threshold:
                issues.append(f"RESCAN_CHROMA_MIN_SCORE_{min_score:.4f}")

            rescan_passed = (
                glued_total == 0
                and mojibake_total == 0
                and replacement_total == 0
                and min_score >= self.quality_threshold
            )

            summary = {
                "chunk_count": len(docs),
                "glued_total": glued_total,
                "mojibake_total": mojibake_total,
                "replacement_total": replacement_total,
                "min_score": min_score,
                "avg_score": round(avg_score, 4),
            }

            return rescan_passed, issues, summary
        except Exception as exc:
            return False, [f"Rescan exception: {exc}"], {}

    # ── Quarantine & Rollback ─────────────────────────────────────────────────

    def quarantine_document(
        self,
        rag: LocalRAG,
        index: IngestIndex,
        file_hash: str,
        meta: Dict[str, Any],
        reason: str,
        issues: List[str],
        text: Optional[str] = None,
        raw_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Safely rolls back any staged Chroma chunks, caches payload in quarantine cache, and marks quarantined."""
        try:
            rag.delete_hash(file_hash)
        except Exception as exc:
            logger.warning("Quarantine: Chroma cleanup warning for %s: %s", file_hash, exc)

        # Isolated quarantine cache saving
        try:
            from repair.quarantine import save_quarantine_payload
            cache_dir = None
            if index is not None and getattr(index, "path", None):
                from mufettis.config import DATA_DIR
                idx_resolved = str(Path(index.path).resolve())
                if not idx_resolved.startswith(str(DATA_DIR.resolve())):
                    cache_dir = Path(index.path).parent / "quarantine_cache"
            save_quarantine_payload(file_hash, text=text, raw_bytes=raw_bytes, cache_dir=cache_dir)
        except Exception as exc:
            logger.warning("Quarantine: payload caching failed for %s: %s", file_hash, exc)

        quarantine_meta = dict(meta or {})
        quarantine_meta.pop("raw_bytes", None)

        existing = index.get(file_hash) if index is not None else None
        existing_history = list((existing.get("quarantine_history") if existing else None) or quarantine_meta.get("quarantine_history") or [])
        retry_count = int(quarantine_meta.get("retry_count", (existing.get("retry_count") if existing else 0) or 0))

        history_entry = {
            "attempt": retry_count,
            "quarantined_at": now_iso(),
            "reason": reason,
            "issues": list(issues),
        }
        existing_history.append(history_entry)

        quarantine_meta.update({
            "status": "quarantined",
            "evidence_indexable": False,
            "chunk_count": 0,
            "ingest_status": "quarantined",
            "quarantine_reason": reason,
            "quarantine_issues": issues,
            "quarantined_at": now_iso(),
            "retry_count": retry_count,
            "quarantine_history": existing_history,
            "reviewed": bool(quarantine_meta.get("reviewed", (existing.get("reviewed") if existing else False) or False)),
            "reviewed_at": quarantine_meta.get("reviewed_at", existing.get("reviewed_at") if existing else None),
            "reviewed_by": quarantine_meta.get("reviewed_by", existing.get("reviewed_by") if existing else None),
            "review_notes": quarantine_meta.get("review_notes", existing.get("review_notes") if existing else None),
        })

        if index is not None:
            try:
                index.upsert(file_hash, quarantine_meta)
            except Exception as exc:
                logger.error("Quarantine: index upsert failed for %s: %s", file_hash, exc)

        # Audit logging
        if self.audit_logger is not None:
            try:
                self.audit_logger.emit(
                    event_type=AuditEventType.INGEST_QUARANTINED.value,
                    repair_id=f"quarantine_{file_hash[:12]}",
                    document_id=str(meta.get("document_id") or meta.get("source_id") or file_hash),
                    source_sha256=file_hash,
                    reason=reason,
                    status="QUARANTINED",
                    details={
                        "issues": issues,
                        "reason": reason,
                        "retry_count": retry_count,
                        "is_retry": bool(meta.get("is_retry", False)),
                    },
                )
            except Exception as exc:
                logger.warning("Quarantine audit emit failed: %s", exc)

        append_audit(
            "document_quarantined",
            {
                "file_hash": file_hash,
                "document_id": meta.get("document_id"),
                "reason": reason,
                "issues": issues,
                "retry_count": retry_count,
            },
        )

        return {
            "status": "quarantined",
            "count": 0,
            "error": reason,
            "issues": issues,
            "meta": quarantine_meta,
        }

    # ── Main Ingestion Flow ───────────────────────────────────────────────────

    def ingest(
        self,
        rag: LocalRAG,
        index: IngestIndex,
        text: str,
        meta: Dict[str, Any],
        force: bool = False,
        raw_bytes: Optional[bytes] = None,
        source_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Orchestrates the full auto-healing ingestion pipeline."""
        meta = dict(meta or {})
        file_hash = str(meta.get("file_hash", "")).strip()
        if not file_hash:
            raise ValueError("meta['file_hash'] is required for ingestion gate")

        # Resolve effective source_path and raw_bytes from parameters and meta
        effective_source_path = source_path or meta.get("source_path") or meta.get("local_path")
        effective_raw_bytes = raw_bytes if raw_bytes is not None else meta.pop("raw_bytes", None)

        if effective_raw_bytes is None and effective_source_path:
            sp = Path(effective_source_path)
            if sp.is_file():
                try:
                    effective_raw_bytes = sp.read_bytes()
                except Exception as exc:
                    logger.warning("Could not read effective_source_path %s: %s", effective_source_path, exc)
        # Ensure audit logging is strictly isolated if working with an isolated/test index
        if index is not None and getattr(index, "path", None):
            try:
                from mufettis.config import DATA_DIR
                idx_resolved = str(Path(index.path).resolve())
                if not idx_resolved.startswith(str(DATA_DIR.resolve())):
                    self.audit_logger = RepairAuditLogger(log_path=Path(index.path).parent / "repair_audit.jsonl")
            except Exception:
                pass

        # 1. Identity & Duplicate Check
        existing = index.get(file_hash) if index is not None else None
        if existing and not force:
            existing_doc_id = str(existing.get("document_id") or "").strip()
            incoming_doc_id = str(meta.get("document_id") or "").strip()
            if existing_doc_id and incoming_doc_id and existing_doc_id != incoming_doc_id:
                # Identity collision / cross-document conflict
                return {
                    "status": "blocked",
                    "count": 0,
                    "error": (
                        f"Cross-document identity collision: hash {file_hash} is already "
                        f"assigned to document_id '{existing_doc_id}', cannot overwrite with '{incoming_doc_id}'."
                    ),
                    "meta": meta,
                }
            merged = {**existing, **meta, "status": existing.get("status", "active")}
            return {
                "status": "skipped",
                "count": existing.get("chunk_count", 0),
                "meta": merged,
            }

        # 2. Quality Gate Inspection
        raw_metrics = self.evaluate_text_quality(text, meta.get("title", ""))
        effective_text = text
        was_healed = False
        final_metrics = raw_metrics

        if not raw_metrics["is_clean"]:
            # CORRUPTED PATH -> Trigger Self-Healing
            is_improved, repaired_text, healed_metrics = self.attempt_self_healing(
                text,
                meta,
                raw_bytes=effective_raw_bytes,
                source_path=effective_source_path,
            )

            if not is_improved or not healed_metrics["is_clean"]:
                # UNRECOVERABLE CORRUPTION -> QUARANTINE
                reason = f"Quality gate failed ({', '.join(healed_metrics['issues'])}) and auto-healing could not resolve corruption."
                return self.quarantine_document(
                    rag=rag,
                    index=index,
                    file_hash=file_hash,
                    meta=meta,
                    reason=reason,
                    issues=healed_metrics["issues"],
                    text=effective_text or text,
                    raw_bytes=effective_raw_bytes,
                )

            effective_text = repaired_text
            was_healed = True
            final_metrics = healed_metrics

        # 3. Staged Commit to Chroma
        try:
            result = rag.ingest_text(effective_text, meta, force=True)
            chunk_count = int(result.get("count", 0) or 0)
        except Exception as exc:
            reason = f"Chroma staged commit failed: {exc}"
            return self.quarantine_document(
                rag=rag,
                index=index,
                file_hash=file_hash,
                meta=meta,
                reason=reason,
                issues=[str(exc)],
                text=effective_text,
                raw_bytes=effective_raw_bytes,
            )

        # 4. VERIFY Stage
        verified, v_errors = self.verify_committed_chunks(rag, file_hash)
        if not verified:
            reason = f"Verification failed after commit: {'; '.join(v_errors)}"
            return self.quarantine_document(
                rag=rag,
                index=index,
                file_hash=file_hash,
                meta=meta,
                reason=reason,
                issues=v_errors,
                text=effective_text,
                raw_bytes=effective_raw_bytes,
            )

        # 5. RESCAN Stage
        rescanned, r_issues, r_summary = self.rescan_committed_chunks(rag, file_hash)
        if not rescanned:
            reason = f"Post-commit rescan quality gate failed: {'; '.join(r_issues)}"
            return self.quarantine_document(
                rag=rag,
                index=index,
                file_hash=file_hash,
                meta=meta,
                reason=reason,
                issues=r_issues,
                text=effective_text,
                raw_bytes=effective_raw_bytes,
            )

        # 6. Commit Succeeded! Finalize Index and Cache
        meta = dict(meta)
        meta["chunk_count"] = chunk_count
        meta["chunk_schema_version"] = CHUNK_SCHEMA_VERSION
        meta["chunk_metadata_valid"] = bool(chunk_count)
        meta["ingest_status"] = "indexed" if chunk_count else "indexed_zero_chunks"
        meta["status"] = "active"
        meta["healed"] = was_healed
        if was_healed:
            meta["healed_at"] = now_iso()
            meta["heal_metrics"] = {
                "initial_score": raw_metrics["score"],
                "final_score": final_metrics["score"],
                "initial_glued": raw_metrics["glued_token_count"],
                "final_glued": final_metrics["glued_token_count"],
            }

        if index is not None:
            index.upsert(file_hash, meta)

        # 7. Audit Event Logging
        if self.audit_logger is not None:
            try:
                ev_type = (
                    AuditEventType.INGEST_SELF_HEALED.value
                    if was_healed
                    else AuditEventType.INGEST_QUALITY_PASSED.value
                )
                self.audit_logger.emit(
                    event_type=ev_type,
                    repair_id=f"ingest_{file_hash[:12]}",
                    document_id=str(meta.get("document_id") or meta.get("source_id") or file_hash),
                    source_sha256=file_hash,
                    status="SUCCESS",
                    old_chunk_count=0,
                    new_chunk_count=chunk_count,
                    reason="AutoHealing ingestion gate passed",
                    details={
                        "healed": was_healed,
                        "raw_score": raw_metrics["score"],
                        "final_score": final_metrics["score"],
                        "rescan_summary": r_summary,
                    },
                )
            except Exception as exc:
                logger.warning("Ingest gate audit log failed: %s", exc)

        return {
            "status": "repaired_and_indexed" if was_healed else "indexed",
            "count": chunk_count,
            "meta": meta,
            "healed": was_healed,
            "quality": final_metrics,
            "text": effective_text,
        }
