# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 9: Self-Healing Infrastructure / Read-Only Repair Layer
Scanner Module.

Strictly read-only inspection of production documents, source cache,
Chroma collection chunks, and retrievability status.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.extraction.pdf import _pdf_generic_quality_metrics
from mufettis.legal.parser import _chunk_is_retrievable, _metadata_bool
from mufettis.rag.maintenance import load_source_cache
from repair.models import (
    DocumentScanResult,
    IssueCode,
    IssueSeverity,
    ProductionScanReport,
)

logger = logging.getLogger("mufettis.repair.scanner")


def _calc_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def scan_document(
    file_hash: str,
    meta: Optional[Dict[str, Any]] = None,
    index: Any = None,
    rag: Any = None,
    source_path: Optional[Path | str] = None,
    quality_threshold: float = 0.88,
) -> DocumentScanResult:
    """Tek bir belgeyi salt-okunur olarak inceler ve tespit edilen sorunları döndürür.

    Hiçbir üretim verisini, Chroma'yı veya source cache'i değiştirmez.
    """
    meta = dict(meta or {})
    doc_id = str(meta.get("document_id") or meta.get("source_id") or file_hash or "")
    title = str(meta.get("title", "") or "")
    source_doc_id = str(meta.get("source_document_id", "") or "")

    result = DocumentScanResult(
        document_id=doc_id,
        source_document_id=source_doc_id,
        source_hash=file_hash,
        title=title,
    )

    try:
        # -------------------------------------------------------------
        # 1. SOURCE VERIFICATION
        # -------------------------------------------------------------
        resolved_source_path = source_path or meta.get("path") or meta.get("file_path")
        if resolved_source_path:
            p = Path(resolved_source_path)
            result.source_path = str(p)
            if not p.is_file():
                result.source_status = "missing"
                result.add_issue(
                    IssueCode.SOURCE_MISSING,
                    IssueSeverity.CRITICAL,
                    f"Kaynak dosya diskte bulunamadı: {p}",
                    {"path": str(p)},
                )
            else:
                try:
                    actual_hash = _calc_sha256(p)
                    if file_hash and actual_hash != file_hash:
                        result.source_status = "hash_mismatch"
                        result.add_issue(
                            IssueCode.SOURCE_HASH_MISMATCH,
                            IssueSeverity.CRITICAL,
                            f"Kaynak dosya hash uyumsuzluğu: disk={actual_hash}, beklenen={file_hash}",
                            {"actual_hash": actual_hash, "expected_hash": file_hash},
                        )
                except Exception as exc:
                    result.source_status = "read_error"
                    result.add_issue(
                        IssueCode.UNKNOWN_INTEGRITY_STATE,
                        IssueSeverity.CRITICAL,
                        f"Kaynak dosya okunamadı: {exc}",
                        {"error": str(exc)},
                    )
        elif not file_hash:
            result.source_status = "hash_missing"
            result.add_issue(
                IssueCode.SOURCE_HASH_MISSING,
                IssueSeverity.CRITICAL,
                "Belge için herhangi bir source hash bulunamadı.",
            )

        # -------------------------------------------------------------
        # 2. SOURCE CACHE INSPECTION
        # -------------------------------------------------------------
        cache_text = ""
        if file_hash:
            try:
                cache_text = load_source_cache(file_hash)
            except Exception as exc:
                result.cache_status = "read_error"
                result.add_issue(
                    IssueCode.UNKNOWN_INTEGRITY_STATE,
                    IssueSeverity.ERROR,
                    f"Source cache okunamadı: {exc}",
                    {"error": str(exc)},
                )

        if not cache_text or not cache_text.strip():
            result.cache_status = "missing"
            result.add_issue(
                IssueCode.CACHE_MISSING,
                IssueSeverity.ERROR,
                "Source cache bulunamadı veya boş.",
            )
            cache_quality = {
                "score": 0.0,
                "chars": 0,
                "glued_token_count": 0,
                "very_long_token_count": 0,
                "replacement_char_count": 0,
                "mojibake_sequence_count": 0,
            }
        else:
            result.source_cache_chars = len(cache_text)
            try:
                cache_quality = _pdf_generic_quality_metrics(cache_text)
            except Exception as exc:
                cache_quality = {"score": 0.0, "chars": len(cache_text)}
                result.add_issue(
                    IssueCode.UNKNOWN_INTEGRITY_STATE,
                    IssueSeverity.ERROR,
                    f"Kalite metriği hesaplanamadı: {exc}",
                )

            # Check specific quality issues
            glued = int(cache_quality.get("glued_token_count", 0) or 0)
            if glued > 0:
                result.quality_status = "glued_text"
                result.add_issue(
                    IssueCode.QUALITY_GLUED_TEXT,
                    IssueSeverity.ERROR,
                    f"Cache metninde yapışık sözcükler tespit edildi (adet: {glued})",
                    {"glued_token_count": glued},
                )

            mojibake = int(cache_quality.get("mojibake_sequence_count", 0) or 0)
            if mojibake > 0:
                result.quality_status = "mojibake"
                result.add_issue(
                    IssueCode.QUALITY_MOJIBAKE,
                    IssueSeverity.ERROR,
                    f"Cache metninde mojibake/bozuk karakter dizisi tespit edildi (adet: {mojibake})",
                    {"mojibake_sequence_count": mojibake},
                )

            rep_char = int(cache_quality.get("replacement_char_count", 0) or 0)
            if rep_char > 0:
                result.quality_status = "replacement_char"
                result.add_issue(
                    IssueCode.QUALITY_REPLACEMENT_CHAR,
                    IssueSeverity.ERROR,
                    f"Cache metninde replacement karakter (\\ufffd) tespit edildi (adet: {rep_char})",
                    {"replacement_char_count": rep_char},
                )

            long_token = int(cache_quality.get("very_long_token_count", 0) or 0)
            if long_token > 0:
                result.add_issue(
                    IssueCode.QUALITY_VERY_LONG_TOKEN,
                    IssueSeverity.WARNING,
                    f"Cache metninde aşırı uzun sözcükler tespit edildi (adet: {long_token})",
                    {"very_long_token_count": long_token},
                )

            score = float(cache_quality.get("score", 0.0) or 0.0)
            if score < quality_threshold and cache_text.strip():
                result.quality_status = "low_score"
                result.add_issue(
                    IssueCode.QUALITY_LOW_SCORE,
                    IssueSeverity.WARNING,
                    f"Cache metin kalite puanı eşiğin altında: {score:.3f} < {quality_threshold}",
                    {"score": score, "threshold": quality_threshold},
                )

        result.current_metrics = cache_quality

        # -------------------------------------------------------------
        # 3. CHROMA COLLECTION & RETRIEVABILITY INSPECTION
        # -------------------------------------------------------------
        if rag is not None and getattr(rag, "collection", None) is not None and file_hash:
            try:
                got = rag.collection.get(
                    where={"file_hash": file_hash},
                    include=["documents", "metadatas"],
                )
                if not got or not got.get("ids"):
                    got = rag.collection.get(
                        where={"source_hash": file_hash},
                        include=["documents", "metadatas"],
                    )
            except Exception as exc:
                result.chroma_status = "error"
                result.add_issue(
                    IssueCode.UNKNOWN_INTEGRITY_STATE,
                    IssueSeverity.CRITICAL,
                    f"Chroma collection sorgulanırken hata oluştu: {exc}",
                    {"error": str(exc)},
                )
                got = {}

            docs = got.get("documents", []) or []
            metas = got.get("metadatas", []) or []
            chunk_ids = got.get("ids", []) or []
            chunk_count = len(docs)

            result.chunk_summary = {
                "chunk_count": chunk_count,
                "retrievable_count": 0,
                "empty_chunk_count": 0,
                "stale_schema_count": 0,
                "superseded_count": 0,
            }

            if chunk_count == 0:
                result.chroma_status = "missing"
                result.add_issue(
                    IssueCode.CHROMA_MISSING,
                    IssueSeverity.WARNING,
                    "Chroma indeksinde bu belgeye ait herhangi bir chunk bulunamadı.",
                )
            else:
                retrievable_count = 0
                empty_chunk_count = 0
                stale_schema_count = 0
                superseded_count = 0
                seen_texts = set()
                duplicate_count = 0

                # Chunk-level quality metrics accumulation
                chroma_glued_total = 0
                chroma_very_long_total = 0
                chroma_replacement_total = 0
                chroma_mojibake_total = 0
                chunk_scores: List[float] = []

                for c_id, c_doc, c_meta in zip(
                    chunk_ids, docs, metas if metas else [{}] * len(docs)
                ):
                    c_meta = dict(c_meta or {})
                    doc_str = str(c_doc or "")

                    if not doc_str.strip():
                        empty_chunk_count += 1
                    else:
                        normalized_doc = " ".join(doc_str.split())
                        if normalized_doc in seen_texts:
                            duplicate_count += 1
                        seen_texts.add(normalized_doc)

                    # Chunk quality evaluation
                    q_metric = _pdf_generic_quality_metrics(doc_str)
                    chroma_glued_total += int(q_metric.get("glued_token_count", 0) or 0)
                    chroma_very_long_total += int(q_metric.get("very_long_token_count", 0) or 0)
                    chroma_replacement_total += int(q_metric.get("replacement_char_count", 0) or 0)
                    chroma_mojibake_total += int(q_metric.get("mojibake_sequence_count", 0) or 0)
                    if doc_str.strip():
                        chunk_scores.append(float(q_metric.get("score", 0.0) or 0.0))

                    # Schema version check
                    schema_ver = str(c_meta.get("chunk_schema_version", ""))
                    if schema_ver != str(CHUNK_SCHEMA_VERSION):
                        stale_schema_count += 1

                    # Superseded check
                    status_val = str(c_meta.get("status", "")).strip().lower()
                    if status_val in {"superseded", "mülga", "mulga", "repealed", "repealed_or_changed"}:
                        superseded_count += 1

                    # Retrievability check
                    if _chunk_is_retrievable(c_meta, doc_str):
                        retrievable_count += 1

                min_score = min(chunk_scores) if chunk_scores else None
                max_score = max(chunk_scores) if chunk_scores else None
                avg_score = round(sum(chunk_scores) / len(chunk_scores), 4) if chunk_scores else None

                result.chroma_quality = {
                    "chroma_available": True,
                    "chunk_count": chunk_count,
                    "glued_token_count": chroma_glued_total,
                    "very_long_token_count": chroma_very_long_total,
                    "replacement_char_count": chroma_replacement_total,
                    "mojibake_sequence_count": chroma_mojibake_total,
                    "min_quality_score": min_score,
                    "max_quality_score": max_score,
                    "avg_quality_score": avg_score,
                }

                # Update current_metrics to reflect Chroma chunk degradation if worse
                if chroma_glued_total > int(result.current_metrics.get("glued_token_count", 0) or 0):
                    result.current_metrics["glued_token_count"] = chroma_glued_total
                if chroma_mojibake_total > int(result.current_metrics.get("mojibake_sequence_count", 0) or 0):
                    result.current_metrics["mojibake_sequence_count"] = chroma_mojibake_total
                if chroma_replacement_total > int(result.current_metrics.get("replacement_char_count", 0) or 0):
                    result.current_metrics["replacement_char_count"] = chroma_replacement_total
                if chroma_very_long_total > int(result.current_metrics.get("very_long_token_count", 0) or 0):
                    result.current_metrics["very_long_token_count"] = chroma_very_long_total
                if avg_score is not None and (chroma_glued_total > 0 or chroma_mojibake_total > 0 or avg_score < float(result.current_metrics.get("score", 1.0) or 1.0)):
                    result.current_metrics["score"] = min(float(result.current_metrics.get("score", 1.0) or 1.0), avg_score)
                result.current_metrics["chroma_glued_token_count"] = chroma_glued_total
                result.current_metrics["chroma_mojibake_sequence_count"] = chroma_mojibake_total
                result.current_metrics["chroma_replacement_char_count"] = chroma_replacement_total
                result.current_metrics["chroma_min_quality_score"] = min_score
                result.current_metrics["chroma_avg_quality_score"] = avg_score

                # Emit chunk-level Chroma issues
                if chroma_glued_total > 0:
                    if result.quality_status == "ok":
                        result.quality_status = "chroma_glued_text"
                    result.add_issue(
                        IssueCode.CHROMA_GLUED_TEXT,
                        IssueSeverity.ERROR,
                        f"Chroma chunk'larında yapışık sözcükler tespit edildi (toplam: {chroma_glued_total})",
                        {"glued_token_count": chroma_glued_total, "chunk_count": chunk_count},
                    )

                if chroma_mojibake_total > 0:
                    if result.quality_status == "ok":
                        result.quality_status = "chroma_mojibake"
                    result.add_issue(
                        IssueCode.CHROMA_MOJIBAKE,
                        IssueSeverity.ERROR,
                        f"Chroma chunk'larında mojibake/bozuk karakter dizisi tespit edildi (toplam: {chroma_mojibake_total})",
                        {"mojibake_sequence_count": chroma_mojibake_total, "chunk_count": chunk_count},
                    )

                if chroma_replacement_total > 0:
                    if result.quality_status == "ok":
                        result.quality_status = "chroma_replacement_char"
                    result.add_issue(
                        IssueCode.CHROMA_REPLACEMENT_CHAR,
                        IssueSeverity.ERROR,
                        f"Chroma chunk'larında replacement karakter (\\ufffd) tespit edildi (toplam: {chroma_replacement_total})",
                        {"replacement_char_count": chroma_replacement_total, "chunk_count": chunk_count},
                    )

                if min_score is not None and min_score < quality_threshold and chunk_count > 0:
                    if result.quality_status == "ok":
                        result.quality_status = "chroma_low_score"
                    result.add_issue(
                        IssueCode.CHROMA_LOW_SCORE,
                        IssueSeverity.WARNING,
                        f"Chroma'da minimum chunk kalite puanı eşiğin altında: {min_score:.3f} < {quality_threshold}",
                        {"min_quality_score": min_score, "threshold": quality_threshold, "chunk_count": chunk_count},
                    )

                result.chunk_summary["retrievable_count"] = retrievable_count
                result.chunk_summary["empty_chunk_count"] = empty_chunk_count
                result.chunk_summary["stale_schema_count"] = stale_schema_count
                result.chunk_summary["superseded_count"] = superseded_count
                result.chunk_summary["duplicate_count"] = duplicate_count

                if empty_chunk_count > 0:
                    result.add_issue(
                        IssueCode.CHROMA_EMPTY_CHUNK,
                        IssueSeverity.ERROR,
                        f"Chroma'da boş metin içeren {empty_chunk_count} chunk bulundu.",
                        {"empty_chunk_count": empty_chunk_count},
                    )

                if duplicate_count > 0:
                    result.add_issue(
                        IssueCode.STRUCTURE_DUPLICATE_CHUNK,
                        IssueSeverity.WARNING,
                        f"Chroma'da {duplicate_count} yinelenen chunk bulundu.",
                        {"duplicate_count": duplicate_count},
                    )

                if stale_schema_count > 0:
                    result.schema_status = "stale"
                    result.add_issue(
                        IssueCode.CHROMA_STALE_SCHEMA,
                        IssueSeverity.WARNING,
                        f"Chroma'da eski schema versiyonuna ait {stale_schema_count} chunk bulundu (beklenen: {CHUNK_SCHEMA_VERSION}).",
                        {"stale_schema_count": stale_schema_count, "expected": CHUNK_SCHEMA_VERSION},
                    )

                if superseded_count == chunk_count and chunk_count > 0:
                    result.add_issue(
                        IssueCode.CHROMA_SUPERSEDED_ONLY,
                        IssueSeverity.INFO,
                        "Belgenin tüm chunk'ları superseded (mülga/yürürlükten kalkmış) olarak işaretli.",
                    )

                if retrievable_count == 0 and chunk_count > 0 and superseded_count < chunk_count:
                    result.retrievability_status = "not_retrievable"
                    result.add_issue(
                        IssueCode.RETRIEVAL_NOT_RETRIEVABLE,
                        IssueSeverity.ERROR,
                        "Belgenin aktif chunk'larından hiçbiri retrievable kriterini sağlamıyor.",
                        {"chunk_count": chunk_count, "retrievable_count": retrievable_count},
                    )

    except Exception as unhandled:
        logger.exception("Scanner beklenmeyen bir istisna ile karşılaştı: %s", unhandled)
        result.add_issue(
            IssueCode.UNKNOWN_INTEGRITY_STATE,
            IssueSeverity.CRITICAL,
            f"Beklenmeyen tarama hatası: {unhandled}",
            {"error": str(unhandled)},
        )

    return result


def scan_production(
    index: Any = None,
    rag: Any = None,
    source_ids: Optional[List[str]] = None,
    quality_threshold: float = 0.88,
) -> ProductionScanReport:
    """Tüm production katalog ve Chroma collection'ı salt-okunur tarar.

    Asla Chroma veya source cache üzerine yazma işlemi yapmaz.
    """
    from repair.classifier import classify_document

    report = ProductionScanReport(dry_run=True, production_write_enabled=False)

    indexed_items: Dict[str, Any] = {}
    if index is not None:
        if hasattr(index, "data") and isinstance(index.data, dict):
            indexed_items = index.data.get("indexed_items", {})
        elif isinstance(index, dict):
            indexed_items = index.get("indexed_items", {})

    canonical_filter = None
    if source_ids:
        canonical_filter = {str(x).strip().lower() for x in source_ids if str(x).strip()}

    issues_summary: Dict[str, int] = {}

    for file_hash, meta in sorted(
        indexed_items.items(),
        key=lambda item: (
            str(item[1].get("source_id", "") if isinstance(item[1], dict) else ""),
            str(item[1].get("title", "") if isinstance(item[1], dict) else ""),
            str(item[0]),
        ),
    ):
        meta_dict = dict(meta or {})
        sid = str(meta_dict.get("source_id", "")).strip().lower()
        if canonical_filter is not None and sid not in canonical_filter:
            continue

        scan_res = scan_document(
            file_hash=file_hash,
            meta=meta_dict,
            index=index,
            rag=rag,
            quality_threshold=quality_threshold,
        )

        classification = classify_document(scan_res)
        report.classifications[scan_res.document_id] = classification.status

        for code in scan_res.issue_codes:
            issues_summary[code] = issues_summary.get(code, 0) + 1

        report.documents.append(scan_res)

        if classification.status == "SAFE":
            report.healthy_documents += 1
        elif classification.status == "REVIEW":
            report.review_documents += 1
            report.repair_candidates += 1
        elif classification.status == "BLOCKED":
            report.blocked_documents += 1

    report.scanned_documents = len(report.documents)
    report.issues_summary = issues_summary
    report.summary = {
        "total_scanned": report.scanned_documents,
        "healthy": report.healthy_documents,
        "review": report.review_documents,
        "blocked": report.blocked_documents,
        "repair_candidates": report.repair_candidates,
        "quality_threshold": quality_threshold,
        "dry_run": True,
        "production_write_enabled": False,
    }

    return report
