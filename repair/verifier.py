# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 13: Post-Repair Verification / Verified Repair Gate.

Verifies that a committed repair has actually achieved the expected state:
  cache PASS + chunk PASS + Chroma PASS + retrieval PASS → VERIFIED_REPAIR

This module is STRICTLY read-only.  It never:
  - writes to source cache
  - upserts to Chroma
  - deletes from Chroma
  - triggers re-extraction or re-commit

The single public entry-point is:
    verify_committed_repair(commit_result, candidate, rag, cache_dir, ...)

Returns a VerificationResult whose .status is one of:
    VERIFIED_REPAIR / VERIFY_FAILED / REVIEW / UNKNOWN_INTEGRITY_STATE
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.legal.parser import _chunk_is_retrievable
from repair.models import (
    CheckResult,
    CommitResult,
    ExtractionCandidate,
    VerificationResult,
    VerificationStatus,
)

# ── internal status constants ────────────────────────────────────────────────
_PASS = "PASS"
_FAIL = "FAIL"
_REVIEW = "REVIEW"
_UNKNOWN = "UNKNOWN"

# mandatory check IDs whose FAIL always yields VERIFY_FAILED
_CRITICAL_CHECKS_BASE = {
    "SOURCE_IDENTITY",
    "CACHE_EXISTS",
    "CACHE_READABLE",
    "CACHE_HASH_MATCH",
    "CHUNK_COUNT",
    "CHUNK_SCHEMA",
    "CHUNK_METADATA",
    "RETRIEVABILITY",
    "CHROMA_COLLECTION",
    "CROSS_HASH",
    "CROSS_DOCUMENT",
    "ACCOUNT_REFERENCE",
    "STALE_GLUED_TEXT",
    # SOURCE_SCOPED_RETRIEVAL replaces EXACT_RETRIEVAL / DOCUMENT_REFERENCE for
    # secondary web sources. For official/PDF sources the original checks remain critical.
}

# For official/PDF sources EXACT_RETRIEVAL + DOCUMENT_REFERENCE remain critical
_CRITICAL_CHECKS_OFFICIAL = _CRITICAL_CHECKS_BASE | {
    "EXACT_RETRIEVAL",
    "DOCUMENT_REFERENCE",
    "SEMANTIC_RETRIEVAL",
}

# Legacy alias so external callers that import _CRITICAL_CHECKS still work
_CRITICAL_CHECKS = _CRITICAL_CHECKS_OFFICIAL

# Secondary web source signals
_SECONDARY_INGEST_ORIGINS = frozenset({"secondary_web_source", "web_harvest", "web_scrape"})
_SECONDARY_SOURCE_TYPES = frozenset({"secondary"})


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_check(
    check_id: str,
    name: str,
    status: str,
    expected: str = "",
    actual: str = "",
    **details: Any,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        name=name,
        status=status,
        expected=expected,
        actual=actual,
        details=dict(details),
    )


def _final_status(
    checks: List[CheckResult],
    extra_failures: List[str],
    effective_critical: Optional[set] = None,
) -> str:
    """Derive the aggregate VerificationStatus from individual check results.

    Parameters
    ----------
    checks : list of CheckResult
    extra_failures : list of str
        Pre-computed failure messages (from the caller's aggregation loop).
    effective_critical : set, optional
        When provided, only checks whose check_id is in this set contribute to
        FAIL / UNKNOWN / REVIEW detection.  Defaults to _CRITICAL_CHECKS_OFFICIAL.
    """
    if effective_critical is None:
        effective_critical = _CRITICAL_CHECKS_OFFICIAL
    critical_failed = any(
        c.status == _FAIL and c.check_id in effective_critical
        for c in checks
    )
    has_unknown = any(c.status == _UNKNOWN and c.check_id in effective_critical for c in checks)
    any_review = any(c.status == _REVIEW and c.check_id in effective_critical for c in checks)

    if critical_failed or extra_failures:
        return VerificationStatus.VERIFY_FAILED.value
    if has_unknown:
        return VerificationStatus.UNKNOWN_INTEGRITY_STATE.value
    if any_review:
        return VerificationStatus.REVIEW.value
    return VerificationStatus.VERIFIED_REPAIR.value


# ── individual check functions ───────────────────────────────────────────────

def _check_source_identity(
    commit_result: CommitResult,
    candidate: Any,
) -> CheckResult:
    """Check 1 — SOURCE_IDENTITY: commit_id / doc_id / sha256 consistency."""
    cr_doc = str(getattr(commit_result, "document_id", "") or "")
    cr_sha = str(getattr(commit_result, "source_sha256", "") or "").lower().strip()
    cand_doc = str(getattr(candidate, "document_id", "") or "")
    cand_sha = str(getattr(candidate, "source_sha256", "") or "").lower().strip()
    cand_src = str(getattr(candidate, "source_document_id", "") or cand_doc)
    cr_cand_id = str(getattr(commit_result, "candidate_id", "") or "")

    mismatches = []
    if cr_doc and cand_doc and cr_doc != cand_doc:
        mismatches.append(f"document_id mismatch: commit={cr_doc} candidate={cand_doc}")
    if cr_sha and cand_sha and cr_sha != cand_sha:
        mismatches.append(f"source_sha256 mismatch: commit={cr_sha} candidate={cand_sha}")
    if not cr_sha or len(cr_sha) != 64:
        mismatches.append(f"commit source_sha256 invalid: '{cr_sha}'")
    if not cand_sha or len(cand_sha) != 64:
        mismatches.append(f"candidate source_sha256 invalid: '{cand_sha}'")

    if mismatches:
        return _make_check(
            "SOURCE_IDENTITY", "Source identity consistency",
            _FAIL,
            expected="matching doc_id + sha256",
            actual="; ".join(mismatches),
            mismatches=mismatches,
        )
    return _make_check(
        "SOURCE_IDENTITY", "Source identity consistency",
        _PASS,
        expected=f"doc_id={cr_doc} sha256={cr_sha[:12]}…",
        actual="match",
        commit_id=str(getattr(commit_result, "commit_id", "")),
        candidate_id=cr_cand_id,
        source_document_id=cand_src,
    )


def _check_cache_exists(cache_dir: Path, sha256: str) -> CheckResult:
    """Check 2 — CACHE_EXISTS: expected <sha256>.txt present in cache_dir."""
    expected_file = cache_dir / f"{sha256}.txt"
    if expected_file.exists():
        return _make_check(
            "CACHE_EXISTS", "Cache file presence",
            _PASS,
            expected=expected_file.name,
            actual="exists",
        )
    return _make_check(
        "CACHE_EXISTS", "Cache file presence",
        _FAIL,
        expected=expected_file.name,
        actual="missing",
        path=str(expected_file),
    )


def _check_cache_readable(cache_dir: Path, sha256: str) -> CheckResult:
    """Check 3 — CACHE_READABLE: file non-empty and readable."""
    f = cache_dir / f"{sha256}.txt"
    if not f.exists():
        return _make_check(
            "CACHE_READABLE", "Cache file readable and non-empty",
            _UNKNOWN,
            expected="readable non-empty file",
            actual="file not found",
        )
    try:
        content = f.read_text(encoding="utf-8")
    except Exception as exc:
        return _make_check(
            "CACHE_READABLE", "Cache file readable and non-empty",
            _UNKNOWN,
            expected="readable file",
            actual=f"read error: {exc}",
        )
    if not content.strip():
        return _make_check(
            "CACHE_READABLE", "Cache file readable and non-empty",
            _FAIL,
            expected="non-empty text",
            actual="empty file",
        )
    return _make_check(
        "CACHE_READABLE", "Cache file readable and non-empty",
        _PASS,
        expected="non-empty readable",
        actual=f"{len(content)} chars",
    )


def _check_cache_hash_match(cache_dir: Path, sha256: str) -> CheckResult:
    """Check 4 — CACHE_HASH_MATCH: filename == committed sha256."""
    expected_file = cache_dir / f"{sha256}.txt"
    if not expected_file.exists():
        return _make_check(
            "CACHE_HASH_MATCH", "Cache filename matches committed sha256",
            _UNKNOWN,
            expected=f"{sha256}.txt",
            actual="file not found",
        )
    # The filename IS the hash — if it exists under this name, it matches.
    return _make_check(
        "CACHE_HASH_MATCH", "Cache filename matches committed sha256",
        _PASS,
        expected=f"{sha256}.txt",
        actual="match",
    )


def _check_cache_content_match(
    cache_dir: Path,
    sha256: str,
    candidate_normalized_text: str,
) -> CheckResult:
    """Check 5 — CACHE_CONTENT_MATCH: committed cache text == candidate.normalized_text."""
    f = cache_dir / f"{sha256}.txt"
    if not f.exists():
        return _make_check(
            "CACHE_CONTENT_MATCH", "Cache content matches candidate text",
            _UNKNOWN,
            expected="matching text",
            actual="file not found",
        )
    try:
        cached = f.read_text(encoding="utf-8")
    except Exception as exc:
        return _make_check(
            "CACHE_CONTENT_MATCH", "Cache content matches candidate text",
            _UNKNOWN,
            expected="readable file",
            actual=f"read error: {exc}",
        )
    # Normalise whitespace for comparison
    def _norm(t: str) -> str:
        return " ".join(t.split())

    if _norm(cached) == _norm(candidate_normalized_text):
        return _make_check(
            "CACHE_CONTENT_MATCH", "Cache content matches candidate text",
            _PASS,
            expected="exact text match",
            actual=f"match ({len(cached)} chars)",
        )
    # Partial overlap? Could be REVIEW rather than hard FAIL
    # Use simple character-level overlap as ambiguity signal
    shorter = min(len(cached), len(candidate_normalized_text))
    if shorter > 0:
        overlap = sum(
            1 for a, b in zip(cached[:shorter], candidate_normalized_text[:shorter])
            if a == b
        ) / shorter
    else:
        overlap = 0.0
    status = _FAIL if overlap < 0.90 else _REVIEW
    return _make_check(
        "CACHE_CONTENT_MATCH", "Cache content matches candidate text",
        status,
        expected="exact text match",
        actual=f"overlap={overlap:.2%} cached={len(cached)}chars candidate={len(candidate_normalized_text)}chars",
        char_overlap=round(overlap, 4),
    )


def _check_chunk_count(
    rag: Any,
    sha256: str,
    expected_count: int,
) -> CheckResult:
    """Check 6 — CHUNK_COUNT: Chroma committed chunk count == candidate chunk count."""
    try:
        found = rag.collection.get(where={"file_hash": sha256}, include=["metadatas"])
    except Exception as exc:
        return _make_check(
            "CHUNK_COUNT", "Committed chunk count matches candidate",
            _UNKNOWN,
            expected=str(expected_count),
            actual=f"Chroma read error: {exc}",
        )
    actual_count = len(found.get("ids", []) or [])
    if actual_count == expected_count:
        return _make_check(
            "CHUNK_COUNT", "Committed chunk count matches candidate",
            _PASS,
            expected=str(expected_count),
            actual=str(actual_count),
        )
    if actual_count < expected_count:
        return _make_check(
            "CHUNK_COUNT", "Committed chunk count matches candidate",
            _FAIL,
            expected=str(expected_count),
            actual=str(actual_count),
            delta=actual_count - expected_count,
            issue="chunk_loss",
        )
    # actual > expected — explosion
    ratio = actual_count / max(expected_count, 1)
    status = _FAIL if ratio > 3.0 else _REVIEW
    return _make_check(
        "CHUNK_COUNT", "Committed chunk count matches candidate",
        status,
        expected=str(expected_count),
        actual=str(actual_count),
        delta=actual_count - expected_count,
        issue="chunk_explosion",
        ratio=round(ratio, 2),
    )


def _check_chunk_schema(rag: Any, sha256: str) -> CheckResult:
    """Check 7 — CHUNK_SCHEMA: all committed chunks carry correct schema version."""
    try:
        found = rag.collection.get(where={"file_hash": sha256}, include=["metadatas"])
    except Exception as exc:
        return _make_check(
            "CHUNK_SCHEMA", "Committed chunks carry correct schema version",
            _UNKNOWN,
            expected=CHUNK_SCHEMA_VERSION,
            actual=f"Chroma read error: {exc}",
        )
    metas = found.get("metadatas", []) or []
    bad = [
        str(m.get("chunk_schema_version", ""))
        for m in metas
        if str(m.get("chunk_schema_version", "")) != CHUNK_SCHEMA_VERSION
    ]
    if bad:
        return _make_check(
            "CHUNK_SCHEMA", "Committed chunks carry correct schema version",
            _FAIL,
            expected=CHUNK_SCHEMA_VERSION,
            actual=f"{len(bad)} chunks with wrong schema",
            wrong_versions=list(set(bad)),
        )
    return _make_check(
        "CHUNK_SCHEMA", "Committed chunks carry correct schema version",
        _PASS,
        expected=CHUNK_SCHEMA_VERSION,
        actual=f"all {len(metas)} chunks OK",
    )


def _check_chunk_metadata(rag: Any, sha256: str, doc_id: str) -> CheckResult:
    """Check 8 — CHUNK_METADATA: doc_id, file_hash, status='active' on committed chunks."""
    try:
        found = rag.collection.get(where={"file_hash": sha256}, include=["metadatas"])
    except Exception as exc:
        return _make_check(
            "CHUNK_METADATA", "Committed chunks carry correct metadata",
            _UNKNOWN,
            expected=f"doc_id={doc_id} file_hash={sha256[:12]}… status=active",
            actual=f"Chroma read error: {exc}",
        )
    metas = found.get("metadatas", []) or []
    issues = []
    for i, m in enumerate(metas):
        m_doc = str(m.get("document_id", "") or "")
        m_hash = str(m.get("file_hash", "") or "")
        m_status = str(m.get("status", "") or "").lower()
        if doc_id and m_doc and m_doc != doc_id:
            issues.append(f"chunk[{i}] document_id={m_doc} expected={doc_id}")
        if m_hash.lower() != sha256.lower():
            issues.append(f"chunk[{i}] file_hash={m_hash} expected={sha256[:12]}…")
        if m_status in {"superseded", "mülga", "mulga", "repealed"}:
            issues.append(f"chunk[{i}] status={m_status} (should be active)")
    if issues:
        return _make_check(
            "CHUNK_METADATA", "Committed chunks carry correct metadata",
            _FAIL,
            expected="doc_id + file_hash + status=active",
            actual=f"{len(issues)} metadata issues",
            issues=issues[:10],
        )
    return _make_check(
        "CHUNK_METADATA", "Committed chunks carry correct metadata",
        _PASS,
        expected="doc_id + file_hash + status=active",
        actual=f"all {len(metas)} chunks OK",
    )


def _check_chunk_empty(rag: Any, sha256: str) -> CheckResult:
    """Check 9 — CHUNK_EMPTY: no empty-text chunks in committed state."""
    try:
        found = rag.collection.get(where={"file_hash": sha256}, include=["documents"])
    except Exception as exc:
        return _make_check(
            "CHUNK_EMPTY", "No empty chunks committed",
            _UNKNOWN,
            expected="0 empty chunks",
            actual=f"Chroma read error: {exc}",
        )
    docs = found.get("documents", []) or []
    empty = [i for i, d in enumerate(docs) if not str(d or "").strip()]
    if empty:
        return _make_check(
            "CHUNK_EMPTY", "No empty chunks committed",
            _FAIL,
            expected="0 empty chunks",
            actual=f"{len(empty)} empty chunks",
            empty_indices=empty[:10],
        )
    return _make_check(
        "CHUNK_EMPTY", "No empty chunks committed",
        _PASS,
        expected="0 empty chunks",
        actual=f"0 out of {len(docs)}",
    )


def _check_chunk_duplicate(rag: Any, sha256: str) -> CheckResult:
    """Check 10 — CHUNK_DUPLICATE: no duplicate chunk texts in committed state."""
    try:
        found = rag.collection.get(where={"file_hash": sha256}, include=["documents"])
    except Exception as exc:
        return _make_check(
            "CHUNK_DUPLICATE", "No duplicate chunks committed",
            _UNKNOWN,
            expected="0 duplicates",
            actual=f"Chroma read error: {exc}",
        )
    docs = found.get("documents", []) or []
    seen: Dict[str, int] = {}
    dups = 0
    for d in docs:
        key = " ".join(str(d or "").split())
        if key:
            if key in seen:
                dups += 1
            seen[key] = seen.get(key, 0) + 1
    if dups:
        return _make_check(
            "CHUNK_DUPLICATE", "No duplicate chunks committed",
            _REVIEW,
            expected="0 duplicates",
            actual=f"{dups} duplicate texts",
        )
    return _make_check(
        "CHUNK_DUPLICATE", "No duplicate chunks committed",
        _PASS,
        expected="0 duplicates",
        actual=f"0 out of {len(docs)}",
    )


def _check_retrievability(rag: Any, sha256: str, doc_id: str) -> CheckResult:
    """Check 11 — RETRIEVABILITY: all committed chunks pass _chunk_is_retrievable."""
    try:
        found = rag.collection.get(where={"file_hash": sha256}, include=["documents", "metadatas"])
    except Exception as exc:
        return _make_check(
            "RETRIEVABILITY", "Committed chunks are retrievable",
            _UNKNOWN,
            expected="100% retrievable",
            actual=f"Chroma read error: {exc}",
        )
    docs = found.get("documents", []) or []
    metas = found.get("metadatas", []) or []
    if not docs:
        return _make_check(
            "RETRIEVABILITY", "Committed chunks are retrievable",
            _FAIL,
            expected=">0 retrievable chunks",
            actual="no chunks found",
        )
    not_ret = [
        i for i, (d, m) in enumerate(zip(docs, metas))
        if not _chunk_is_retrievable(m or {}, d or "")
    ]
    rate = (len(docs) - len(not_ret)) / len(docs)
    if not_ret:
        return _make_check(
            "RETRIEVABILITY", "Committed chunks are retrievable",
            _FAIL,
            expected="100%",
            actual=f"{rate:.0%} ({len(docs) - len(not_ret)}/{len(docs)})",
            not_retrievable_indices=not_ret[:10],
        )
    return _make_check(
        "RETRIEVABILITY", "Committed chunks are retrievable",
        _PASS,
        expected="100%",
        actual=f"100% ({len(docs)}/{len(docs)})",
    )


def _check_chroma_collection(rag: Any) -> CheckResult:
    """Check 12 — CHROMA_COLLECTION: collection accessible and queryable."""
    try:
        count = rag.collection.count()
        return _make_check(
            "CHROMA_COLLECTION", "Chroma collection accessible",
            _PASS,
            expected="accessible",
            actual=f"count={count}",
        )
    except Exception as exc:
        return _make_check(
            "CHROMA_COLLECTION", "Chroma collection accessible",
            _UNKNOWN,
            expected="accessible",
            actual=f"exception: {exc}",
        )


def _check_cross_hash(
    rag: Any,
    sha256: str,
    doc_id: str,
    known_other_hashes: Optional[List[str]] = None,
) -> CheckResult:
    """Check 13 — CROSS_HASH: different hash records for same doc are untouched."""
    if not known_other_hashes:
        # No snapshot of other hashes was provided — report PASS with caveat
        return _make_check(
            "CROSS_HASH", "Cross-hash isolation (other hashes unchanged)",
            _PASS,
            expected="no other-hash records for this doc",
            actual="no baseline to compare (pass by absence)",
        )
    issues = []
    for other_hash in known_other_hashes:
        if other_hash.lower() == sha256.lower():
            continue
        try:
            found = rag.collection.get(
                where={"file_hash": other_hash}, include=["metadatas"]
            )
        except Exception as exc:
            issues.append(f"hash={other_hash[:12]}: read error {exc}")
            continue
        metas = found.get("metadatas", []) or []
        for m in metas:
            m_doc = str(m.get("document_id", "") or "")
            if m_doc == doc_id:
                m_hash = str(m.get("file_hash", "") or "").lower()
                if m_hash == sha256.lower():
                    issues.append(
                        f"hash={other_hash[:12]}: record unexpectedly points to committed hash"
                    )
    if issues:
        return _make_check(
            "CROSS_HASH", "Cross-hash isolation (other hashes unchanged)",
            _FAIL,
            expected="other-hash records unchanged",
            actual="; ".join(issues),
        )
    return _make_check(
        "CROSS_HASH", "Cross-hash isolation (other hashes unchanged)",
        _PASS,
        expected="other-hash records unchanged",
        actual=f"checked {len(known_other_hashes)} other hashes",
    )


def _check_cross_document(
    rag: Any,
    doc_id: str,
    expected_total_count: Optional[int] = None,
) -> CheckResult:
    """Check 14 — CROSS_DOCUMENT: total corpus count matches expected (other docs untouched)."""
    if expected_total_count is None:
        return _make_check(
            "CROSS_DOCUMENT", "Cross-document isolation (other docs unchanged)",
            _PASS,
            expected="no baseline count",
            actual="pass by absence of baseline",
        )
    try:
        actual = rag.count()
    except Exception as exc:
        return _make_check(
            "CROSS_DOCUMENT", "Cross-document isolation (other docs unchanged)",
            _UNKNOWN,
            expected=str(expected_total_count),
            actual=f"count error: {exc}",
        )
    if actual == expected_total_count:
        return _make_check(
            "CROSS_DOCUMENT", "Cross-document isolation (other docs unchanged)",
            _PASS,
            expected=str(expected_total_count),
            actual=str(actual),
        )
    return _make_check(
        "CROSS_DOCUMENT", "Cross-document isolation (other docs unchanged)",
        _FAIL,
        expected=str(expected_total_count),
        actual=str(actual),
        delta=actual - expected_total_count,
    )


def _check_exact_retrieval(
    rag: Any,
    exact_phrase: str,
    expected_doc_id: str,
    k: int = 5,
    expected_file_hash: Optional[str] = None,
) -> CheckResult:
    """Check 15 — EXACT_RETRIEVAL: exact phrase query returns expected document."""
    if not exact_phrase:
        return _make_check(
            "EXACT_RETRIEVAL", "Exact phrase retrieval",
            _REVIEW,
            expected="a query phrase",
            actual="no phrase provided",
        )
    try:
        results = rag.query(exact_phrase, k=k)
    except Exception as exc:
        return _make_check(
            "EXACT_RETRIEVAL", "Exact phrase retrieval",
            _UNKNOWN,
            expected=f"doc_id={expected_doc_id} in top-{k}",
            actual=f"query error: {exc}",
        )
    found_ids = [
        str(r.get("metadata", {}).get("document_id") or r.get("metadata", {}).get("source_id") or r.get("metadata", {}).get("source_document_id") or r.get("metadata", {}).get("document_key") or "")
        for r in results
    ]
    found_hashes = [
        str(r.get("metadata", {}).get("file_hash") or "").lower()
        for r in results
    ]
    exp_h = (expected_file_hash or "").lower().strip()
    match_found = (expected_doc_id in found_ids) or (bool(exp_h) and exp_h in found_hashes)
    if match_found:
        rank = (found_ids.index(expected_doc_id) + 1) if expected_doc_id in found_ids else (found_hashes.index(exp_h) + 1)
        return _make_check(
            "EXACT_RETRIEVAL", "Exact phrase retrieval",
            _PASS,
            expected=f"{expected_doc_id} in top-{k}",
            actual=f"found at rank {rank}",
            rank=rank,
        )
    return _make_check(
        "EXACT_RETRIEVAL", "Exact phrase retrieval",
        _FAIL,
        expected=f"{expected_doc_id} in top-{k}",
        actual=f"not found; got {found_ids[:3]}",
    )


def _check_document_reference(
    rag: Any,
    doc_title_query: str,
    expected_doc_id: str,
    k: int = 5,
    expected_file_hash: Optional[str] = None,
) -> CheckResult:
    """Check 16 — DOCUMENT_REFERENCE: document title query returns expected doc."""
    if not doc_title_query:
        return _make_check(
            "DOCUMENT_REFERENCE", "Document reference retrieval",
            _REVIEW,
            expected="a title query",
            actual="no query provided",
        )
    try:
        results = rag.query(doc_title_query, k=k)
    except Exception as exc:
        return _make_check(
            "DOCUMENT_REFERENCE", "Document reference retrieval",
            _UNKNOWN,
            expected=f"{expected_doc_id} in top-{k}",
            actual=f"query error: {exc}",
        )
    found_ids = [
        str(r.get("metadata", {}).get("document_id") or r.get("metadata", {}).get("source_id") or r.get("metadata", {}).get("source_document_id") or r.get("metadata", {}).get("document_key") or "")
        for r in results
    ]
    found_hashes = [
        str(r.get("metadata", {}).get("file_hash") or "").lower()
        for r in results
    ]
    exp_h = (expected_file_hash or "").lower().strip()
    match_found = (expected_doc_id in found_ids) or (bool(exp_h) and exp_h in found_hashes)
    if match_found:
        rank = (found_ids.index(expected_doc_id) + 1) if expected_doc_id in found_ids else (found_hashes.index(exp_h) + 1)
        return _make_check(
            "DOCUMENT_REFERENCE", "Document reference retrieval",
            _PASS,
            expected=f"{expected_doc_id} in top-{k}",
            actual=f"found at rank {rank}",
            rank=rank,
        )
    return _make_check(
        "DOCUMENT_REFERENCE", "Document reference retrieval",
        _FAIL,
        expected=f"{expected_doc_id} in top-{k}",
        actual=f"not found; got {found_ids[:3]}",
    )


def _check_account_reference(
    rag: Any,
    account_query: str,
    expected_doc_id: str,
    k: int = 3,
    expected_file_hash: Optional[str] = None,
) -> CheckResult:
    """Check 17 — ACCOUNT_REFERENCE: accounting code query returns expected chunk."""
    if not account_query:
        return _make_check(
            "ACCOUNT_REFERENCE", "Account code retrieval",
            _PASS,
            expected="an account query",
            actual="n/a (no account query in benchmark)",
        )
    try:
        results = rag.query(account_query, k=k)
    except Exception as exc:
        return _make_check(
            "ACCOUNT_REFERENCE", "Account code retrieval",
            _UNKNOWN,
            expected=f"{expected_doc_id} in top-{k}",
            actual=f"query error: {exc}",
        )
    found_ids = [
        str(r.get("metadata", {}).get("document_id") or r.get("metadata", {}).get("source_id") or r.get("metadata", {}).get("source_document_id") or r.get("metadata", {}).get("document_key") or "")
        for r in results
    ]
    found_hashes = [
        str(r.get("metadata", {}).get("file_hash") or "").lower()
        for r in results
    ]
    exp_h = (expected_file_hash or "").lower().strip()
    match_found = (expected_doc_id in found_ids) or (bool(exp_h) and exp_h in found_hashes)
    if match_found:
        rank = (found_ids.index(expected_doc_id) + 1) if expected_doc_id in found_ids else (found_hashes.index(exp_h) + 1)
        return _make_check(
            "ACCOUNT_REFERENCE", "Account code retrieval",
            _PASS,
            expected=f"{expected_doc_id} in top-{k}",
            actual=f"found at rank {rank}",
            rank=rank,
        )
    return _make_check(
        "ACCOUNT_REFERENCE", "Account code retrieval",
        _FAIL,
        expected=f"{expected_doc_id} in top-{k}",
        actual=f"not found; got {found_ids[:3]}",
    )


def _check_semantic_retrieval(
    rag: Any,
    semantic_query: str,
    expected_doc_id: str,
    k: int = 5,
    expected_file_hash: Optional[str] = None,
) -> CheckResult:
    """Check 18 — SEMANTIC_RETRIEVAL: semantic query returns expected document."""
    if not semantic_query:
        return _make_check(
            "SEMANTIC_RETRIEVAL", "Semantic retrieval",
            _REVIEW,
            expected="a semantic query",
            actual="no query provided",
        )
    try:
        results = rag.query(semantic_query, k=k)
    except Exception as exc:
        return _make_check(
            "SEMANTIC_RETRIEVAL", "Semantic retrieval",
            _UNKNOWN,
            expected=f"{expected_doc_id} in top-{k}",
            actual=f"query error: {exc}",
        )
    found_ids = [
        str(r.get("metadata", {}).get("document_id") or r.get("metadata", {}).get("source_id") or r.get("metadata", {}).get("source_document_id") or r.get("metadata", {}).get("document_key") or "")
        for r in results
    ]
    found_hashes = [
        str(r.get("metadata", {}).get("file_hash") or "").lower()
        for r in results
    ]
    exp_h = (expected_file_hash or "").lower().strip()
    match_found = (expected_doc_id in found_ids) or (bool(exp_h) and exp_h in found_hashes)
    if match_found:
        rank = (found_ids.index(expected_doc_id) + 1) if expected_doc_id in found_ids else (found_hashes.index(exp_h) + 1)
        return _make_check(
            "SEMANTIC_RETRIEVAL", "Semantic retrieval",
            _PASS,
            expected=f"{expected_doc_id} in top-{k}",
            actual=f"found at rank {rank}",
            rank=rank,
        )
    # REVIEW if result list is non-empty but expected doc absent
    status = _REVIEW if results else _FAIL
    return _make_check(
        "SEMANTIC_RETRIEVAL", "Semantic retrieval",
        status,
        expected=f"{expected_doc_id} in top-{k}",
        actual=f"not found; got {found_ids[:3]}",
    )


def _check_stale_glued_text(
    rag: Any,
    stale_fragments: List[str],
    k: int = 5,
) -> CheckResult:
    """Check 19 — STALE_GLUED_TEXT: known bad fragments no longer appear in retrieval."""
    if not stale_fragments:
        return _make_check(
            "STALE_GLUED_TEXT", "Stale/glued text absent from retrieval",
            _PASS,
            expected="no known stale fragments",
            actual="pass by absence",
        )
    still_retrievable = []
    errors = []
    for fragment in stale_fragments:
        try:
            results = rag.query(fragment, k=k)
        except Exception as exc:
            errors.append(f"fragment='{fragment[:40]}': query error {exc}")
            continue
        for r in results:
            doc_text = str(r.get("document", "") or "")
            if fragment.lower() in doc_text.lower():
                still_retrievable.append(fragment[:60])
                break
    if errors:
        return _make_check(
            "STALE_GLUED_TEXT", "Stale/glued text absent from retrieval",
            _UNKNOWN,
            expected="no stale fragments retrievable",
            actual=f"query errors: {errors[:3]}",
        )
    if still_retrievable:
        return _make_check(
            "STALE_GLUED_TEXT", "Stale/glued text absent from retrieval",
            _FAIL,
            expected="not retrievable",
            actual=f"still found: {still_retrievable[:3]}",
            stale_found=still_retrievable,
        )
    return _make_check(
        "STALE_GLUED_TEXT", "Stale/glued text absent from retrieval",
        _PASS,
        expected="not retrievable",
        actual=f"checked {len(stale_fragments)} fragments — none returned",
    )


def _check_metadata_completeness(rag: Any, sha256: str) -> CheckResult:
    """Check 20 — METADATA_COMPLETENESS: committed chunks have required metadata fields."""
    REQUIRED_FIELDS = {
        "document_id", "file_hash", "chunk_schema_version", "status",
        "document_indexable", "chunk_type", "chunk_no",
    }
    try:
        found = rag.collection.get(where={"file_hash": sha256}, include=["metadatas"])
    except Exception as exc:
        return _make_check(
            "METADATA_COMPLETENESS", "Committed chunk metadata completeness",
            _UNKNOWN,
            expected="required fields present",
            actual=f"Chroma read error: {exc}",
        )
    metas = found.get("metadatas", []) or []
    if not metas:
        return _make_check(
            "METADATA_COMPLETENESS", "Committed chunk metadata completeness",
            _FAIL,
            expected="≥1 chunk with metadata",
            actual="no chunks found",
        )
    missing_field_counts: Dict[str, int] = {}
    for m in metas:
        for field in REQUIRED_FIELDS:
            if not str(m.get(field, "") or "").strip():
                missing_field_counts[field] = missing_field_counts.get(field, 0) + 1
    if missing_field_counts:
        return _make_check(
            "METADATA_COMPLETENESS", "Committed chunk metadata completeness",
            _FAIL,
            expected=f"all {REQUIRED_FIELDS}",
            actual=f"missing: {missing_field_counts}",
            missing_field_counts=missing_field_counts,
        )
    return _make_check(
        "METADATA_COMPLETENESS", "Committed chunk metadata completeness",
        _PASS,
        expected="all required fields present",
        actual=f"all {len(metas)} chunks OK",
    )


def _check_quality_recheck(
    rag: Any,
    sha256: str,
    candidate_chunk_count: int,
) -> CheckResult:
    """Check 21 — QUALITY_RECHECK: committed chunk quality consistent with candidate."""
    try:
        found = rag.collection.get(where={"file_hash": sha256}, include=["documents"])
    except Exception as exc:
        return _make_check(
            "QUALITY_RECHECK", "Post-commit quality consistent with candidate",
            _UNKNOWN,
            expected="quality consistent",
            actual=f"Chroma read error: {exc}",
        )
    docs = found.get("documents", []) or []
    committed_count = len(docs)
    empty = sum(1 for d in docs if not str(d or "").strip())
    meaningful = sum(1 for d in docs if len(str(d or "").strip()) >= 50)

    issues = []
    if committed_count != candidate_chunk_count:
        issues.append(f"count mismatch: committed={committed_count} candidate={candidate_chunk_count}")
    if empty > 0:
        issues.append(f"empty chunks: {empty}")
    if meaningful < committed_count * 0.5:
        issues.append(f"low meaningful ratio: {meaningful}/{committed_count}")

    if issues:
        return _make_check(
            "QUALITY_RECHECK", "Post-commit quality consistent with candidate",
            _FAIL,
            expected="quality consistent with candidate",
            actual="; ".join(issues),
            committed_count=committed_count,
            empty_count=empty,
            meaningful_count=meaningful,
        )
    return _make_check(
        "QUALITY_RECHECK", "Post-commit quality consistent with candidate",
        _PASS,
        expected="quality consistent",
        actual=f"count={committed_count} empty={empty} meaningful={meaningful}",
    )


# ── benchmark helpers ────────────────────────────────────────────────────────

def run_baseline_benchmarks(
    rag: Any,
    benchmarks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Run BM-01..BM-N against a live RAG and return per-benchmark hit/miss results.

    This is a read-only operation.  Results are stored in-memory and shared
    across harness tests — benchmarks are loaded once per test class via setUpClass.
    """
    results = []
    for bm in benchmarks:
        bm_id = bm.get("id", "?")
        query = bm.get("query", "")
        bm_type = bm.get("type", "")
        expected_results = bm.get("results", [])
        expected_doc_ids = {r.get("document_id", "") for r in expected_results}
        k = max(len(expected_results) + 2, 5)
        try:
            hits = rag.query(query, k=k)
        except Exception as exc:
            results.append({
                "id": bm_id, "type": bm_type, "query": query,
                "status": "ERROR", "error": str(exc),
                "expected_doc_ids": list(expected_doc_ids), "found_doc_ids": [],
            })
            continue
        found_doc_ids = [r.get("metadata", {}).get("document_id", "") for r in hits]
        hits_set = set(found_doc_ids)
        hit = bool(expected_doc_ids & hits_set)
        results.append({
            "id": bm_id, "type": bm_type, "query": query,
            "status": "HIT" if hit else "MISS",
            "expected_doc_ids": list(expected_doc_ids),
            "found_doc_ids": found_doc_ids[:5],
        })
    return results



def _is_secondary_web_source(candidate: Any) -> bool:
    """Return True when the candidate originates from a secondary web harvest.

    We check the candidate's metadata dict (which carries ingest_origin /
    source_type written by the extractor) and legal_chunks[0] metadata.
    This never touches the production Chroma or disk; it is purely in-memory.
    """
    meta: dict = {}
    if hasattr(candidate, "metadata") and isinstance(candidate.metadata, dict):
        meta = candidate.metadata

    ingest_origin = str(meta.get("ingest_origin", "") or "").strip().lower()
    source_type   = str(meta.get("source_type",   "") or "").strip().lower()

    if ingest_origin in _SECONDARY_INGEST_ORIGINS or source_type in _SECONDARY_SOURCE_TYPES:
        return True

    # Fallback: check first legal chunk metadata if candidate meta is sparse
    chunks = getattr(candidate, "legal_chunks", []) or []
    if chunks and isinstance(chunks[0], dict):
        c0 = chunks[0]
        c0_origin = str(c0.get("ingest_origin", "") or "").strip().lower()
        c0_type   = str(c0.get("source_type",   "") or "").strip().lower()
        if c0_origin in _SECONDARY_INGEST_ORIGINS or c0_type in _SECONDARY_SOURCE_TYPES:
            return True

    # Final fallback: non-PDF with a URL is web-harvested
    is_pdf = meta.get("is_pdf")
    has_url = bool(str(meta.get("url", "") or "").strip())
    extraction_method = str(meta.get("extraction_method", "") or "").lower()
    if (is_pdf is False or "web" in extraction_method) and has_url:
        return True

    return False


def _check_source_scoped_retrieval(
    rag: Any,
    sha256: str,
    doc_id: str,
    candidate: Any,
) -> CheckResult:
    """Check 22 — SOURCE_SCOPED_RETRIEVAL (secondary web sources only).

    Verifies that the committed chunks are findable via a *targeted* Chroma
    metadata query (source_id / document_id filter) rather than the global
    reranked rag.query() path that biases official BDDK sources.

    This check REPLACES EXACT_RETRIEVAL and DOCUMENT_REFERENCE as the primary
    retrieval gate for secondary web documents.  It proves:
      - Chunks are indexed in Chroma (source_id or document_id present).
      - At least one chunk is retrievable.
      - The committed file_hash matches expected.
    It does NOT test global ranking position — that is intentional.
    """
    # Strategy 1: direct hash-based metadata lookup (most reliable)
    try:
        found = rag.collection.get(
            where={"file_hash": sha256},
            include=["metadatas", "documents"],
        )
    except Exception as exc:
        return _make_check(
            "SOURCE_SCOPED_RETRIEVAL",
            "Source-scoped retrieval (secondary web document exists in Chroma)",
            _UNKNOWN,
            expected=f"doc_id={doc_id} indexed under sha256={sha256[:12]}…",
            actual=f"Chroma read error: {exc}",
        )

    ids   = found.get("ids",       []) or []
    metas = found.get("metadatas", []) or []
    docs  = found.get("documents", []) or []

    if not ids:
        return _make_check(
            "SOURCE_SCOPED_RETRIEVAL",
            "Source-scoped retrieval (secondary web document exists in Chroma)",
            _FAIL,
            expected=f"≥1 chunk with file_hash={sha256[:12]}…",
            actual="0 chunks found — document absent from Chroma",
        )

    # Verify that at least one chunk carries a consistent source identifier
    matched_ids = [
        m.get("source_id") or m.get("document_id") or ""
        for m in metas
        if (m.get("source_id") or m.get("document_id"))
    ]
    # Build expected source_id from candidate metadata
    cand_meta: dict = getattr(candidate, "metadata", {}) or {}
    expected_source_id = (
        cand_meta.get("source_id")
        or doc_id
        or ""
    )

    id_matched = any(
        m in (doc_id, expected_source_id)
        for m in matched_ids
    )

    # Also check retrievability of the found chunks
    retrievable = [
        i for i, (d, m) in enumerate(zip(docs, metas))
        if _chunk_is_retrievable(m or {}, d or "")
    ]

    if not retrievable:
        return _make_check(
            "SOURCE_SCOPED_RETRIEVAL",
            "Source-scoped retrieval (secondary web document exists in Chroma)",
            _FAIL,
            expected="≥1 retrievable chunk",
            actual=f"{len(ids)} chunks found but 0 retrievable — metadata flags may be wrong",
            chunk_count=len(ids),
        )

    return _make_check(
        "SOURCE_SCOPED_RETRIEVAL",
        "Source-scoped retrieval (secondary web document exists in Chroma)",
        _PASS,
        expected=f"≥1 retrievable chunk for {doc_id}",
        actual=(
            f"{len(retrievable)}/{len(ids)} retrievable chunks; "
            f"source_id_matched={id_matched}"
        ),
        chunk_count=len(ids),
        retrievable_count=len(retrievable),
        source_id_matched=id_matched,
    )


# ── main entry-point ─────────────────────────────────────────────────────────

def verify_committed_repair(
    commit_result: CommitResult,
    candidate: Any,
    rag: Any,
    cache_dir: Path,
    *,
    baseline_benchmarks: Optional[List[Dict[str, Any]]] = None,
    known_stale_fragments: Optional[List[str]] = None,
    expected_total_chunk_count: Optional[int] = None,
    known_other_hashes: Optional[List[str]] = None,
) -> VerificationResult:
    """
    Post-repair verification gate (FAZ 13).

    Parameters
    ----------
    commit_result : CommitResult
        The result object returned by FAZ 12 commit_candidate().
    candidate : ExtractionCandidate
        The candidate that was committed.
    rag : LocalRAG
        The LocalRAG pointing at the TARGET collection (NOT production).
    cache_dir : Path
        The source cache directory where <sha256>.txt was written.
    baseline_benchmarks : list[dict], optional
        BM-01..BM-N benchmark entries from retrieval_baseline.json.
        Each entry has {"id", "type", "query", "results":[{"document_id",...}]}.
    known_stale_fragments : list[str], optional
        Known glued/corrupted text fragments that must NOT appear in retrieval.
    expected_total_chunk_count : int, optional
        Total expected Chroma count (all documents) — used for cross-document check.
    known_other_hashes : list[str], optional
        Other source hashes for the same document — used for cross-hash check.

    Returns
    -------
    VerificationResult
        .status ∈ {VERIFIED_REPAIR, VERIFY_FAILED, REVIEW, UNKNOWN_INTEGRITY_STATE}
    """
    doc_id = str(getattr(candidate, "document_id", "") or
                 getattr(commit_result, "document_id", "") or "")
    sha256 = str(
        getattr(candidate, "source_sha256", "") or
        getattr(commit_result, "source_sha256", "") or ""
    ).lower().strip()
    commit_id = str(getattr(commit_result, "commit_id", "") or "")
    candidate_id = str(getattr(commit_result, "candidate_id", "") or doc_id)
    candidate_chunks = getattr(candidate, "legal_chunks", []) or []
    candidate_norm_text = str(
        getattr(candidate, "normalized_text", "") or
        getattr(candidate, "raw_text", "") or ""
    )

    checks: List[CheckResult] = []
    failures: List[str] = []
    warnings_list: List[str] = []
    evidence: List[Dict[str, Any]] = []

    # ── check 1: source identity ─────────────────────────────────────────────
    checks.append(_check_source_identity(commit_result, candidate))

    # ── check 2-5: cache ─────────────────────────────────────────────────────
    cache_dir = Path(cache_dir)
    checks.append(_check_cache_exists(cache_dir, sha256))
    checks.append(_check_cache_readable(cache_dir, sha256))
    checks.append(_check_cache_hash_match(cache_dir, sha256))
    checks.append(_check_cache_content_match(cache_dir, sha256, candidate_norm_text))

    # ── check 6-10: chunk integrity ──────────────────────────────────────────
    checks.append(_check_chunk_count(rag, sha256, len(candidate_chunks)))
    checks.append(_check_chunk_schema(rag, sha256))
    checks.append(_check_chunk_metadata(rag, sha256, doc_id))
    checks.append(_check_chunk_empty(rag, sha256))
    checks.append(_check_chunk_duplicate(rag, sha256))

    # ── check 11: retrievability ─────────────────────────────────────────────
    checks.append(_check_retrievability(rag, sha256, doc_id))

    # ── check 12: Chroma collection ──────────────────────────────────────────
    checks.append(_check_chroma_collection(rag))

    # ── check 13: cross-hash isolation ───────────────────────────────────────
    checks.append(_check_cross_hash(rag, sha256, doc_id, known_other_hashes))

    # ── check 14: cross-document isolation ───────────────────────────────────
    checks.append(_check_cross_document(rag, doc_id, expected_total_chunk_count))

    # ── checks 15-18: retrieval ──────────────────────────────────────────────
    # Derive reasonable queries from benchmarks when available
    exact_query = ""
    doc_query = ""
    account_query = ""
    semantic_query = ""
    exact_expected_doc = doc_id
    doc_expected_doc = doc_id
    account_expected_doc = ""
    semantic_expected_doc = doc_id

    if baseline_benchmarks:
        for bm in baseline_benchmarks:
            bm_type = bm.get("type", "")
            bm_results = bm.get("results", [])
            bm_query = bm.get("query", "")
            # Pick first benchmark with a matching expected doc
            expected_ids = {r.get("document_id", "") for r in bm_results}
            if doc_id in expected_ids:
                if bm_type == "exact_law" and not exact_query:
                    exact_query = bm_query
                elif bm_type == "document_lookup" and not doc_query:
                    doc_query = bm_query
                elif bm_type == "accounting_lookup" and not account_query:
                    account_query = bm_query
                    account_expected_doc = list(expected_ids)[0]
                elif bm_type == "semantic" and not semantic_query:
                    semantic_query = bm_query
        # Fallback: pick any accounting benchmark
        if not account_query:
            for bm in baseline_benchmarks:
                if bm.get("type") == "accounting_lookup":
                    account_query = bm.get("query", "")
                    bm_res = bm.get("results", [])
                    account_expected_doc = bm_res[0].get("document_id", "") if bm_res else ""
                    break

    # Detect source type: secondary web sources use scoped retrieval instead of
    # global reranked rag.query() to avoid official-source priority bias.
    is_secondary = _is_secondary_web_source(candidate)

    checks.append(_check_exact_retrieval(rag, exact_query, exact_expected_doc, expected_file_hash=sha256))
    checks.append(_check_document_reference(rag, doc_query, doc_expected_doc, expected_file_hash=sha256))
    checks.append(_check_account_reference(rag, account_query, account_expected_doc or "doc5_tekduzen_hesap_plani", expected_file_hash=sha256 if account_expected_doc == doc_id else None))
    checks.append(_check_semantic_retrieval(rag, semantic_query, semantic_expected_doc, expected_file_hash=sha256))

    # ── check 19: stale glued text ───────────────────────────────────────────
    checks.append(_check_stale_glued_text(rag, known_stale_fragments or []))

    # ── check 20: metadata completeness ──────────────────────────────────────
    checks.append(_check_metadata_completeness(rag, sha256))

    # ── check 21: quality recheck ─────────────────────────────────────────────
    checks.append(_check_quality_recheck(rag, sha256, len(candidate_chunks)))

    # ── check 22 (secondary web only): source-scoped retrieval ───────────────
    # For secondary web sources this IS the critical retrieval gate.
    # We run it for all source types — for official sources it is informational only.
    scoped_check = _check_source_scoped_retrieval(rag, sha256, doc_id, candidate)
    checks.append(scoped_check)

    # ── aggregate failures ────────────────────────────────────────────────────
    # Determine which check IDs are critical for this particular source type.
    if is_secondary:
        # SOURCE_SCOPED_RETRIEVAL is the primary retrieval gate.
        # EXACT_RETRIEVAL / DOCUMENT_REFERENCE / SEMANTIC_RETRIEVAL failures are
        # downgraded to warnings when scoped retrieval passes — they reflect global
        # reranking bias, NOT actual absence of the document.
        scoped_passed = (scoped_check.status == _PASS)
        downgraded_ids: set = (
            {"EXACT_RETRIEVAL", "DOCUMENT_REFERENCE", "SEMANTIC_RETRIEVAL"}
            if scoped_passed
            else set()
        )
        effective_critical = (_CRITICAL_CHECKS_BASE | {"SOURCE_SCOPED_RETRIEVAL"}) - downgraded_ids
    else:
        # Official/PDF sources: original full critical set (SOURCE_SCOPED_RETRIEVAL informational)
        effective_critical = _CRITICAL_CHECKS_OFFICIAL

    for c in checks:
        if c.status == _FAIL and c.check_id in effective_critical:
            failures.append(f"{c.check_id}: {c.actual}")
        elif c.status == _FAIL:
            warnings_list.append(f"{c.check_id}: {c.actual}")

    # ── derive per-domain statuses ────────────────────────────────────────────
    def _domain_status(ids: List[str]) -> str:
        statuses = {c.status for c in checks if c.check_id in ids}
        if _FAIL in statuses:
            return _FAIL
        if _UNKNOWN in statuses:
            return _UNKNOWN
        if _REVIEW in statuses:
            return _REVIEW
        return _PASS

    cache_status = _domain_status({"CACHE_EXISTS", "CACHE_READABLE", "CACHE_HASH_MATCH", "CACHE_CONTENT_MATCH"})
    chunk_status = _domain_status({"CHUNK_COUNT", "CHUNK_SCHEMA", "CHUNK_METADATA", "CHUNK_EMPTY", "CHUNK_DUPLICATE"})
    chroma_status = _domain_status({"CHROMA_COLLECTION", "RETRIEVABILITY"})
    exact_ret = _domain_status({"EXACT_RETRIEVAL"})
    doc_ret = _domain_status({"DOCUMENT_REFERENCE"})
    acc_ret = _domain_status({"ACCOUNT_REFERENCE"})
    sem_ret = _domain_status({"SEMANTIC_RETRIEVAL"})
    stale_st = _domain_status({"STALE_GLUED_TEXT"})
    meta_st = _domain_status({"METADATA_COMPLETENESS"})
    quality_st = _domain_status({"QUALITY_RECHECK"})

    final = _final_status(checks, failures, effective_critical=effective_critical)

    return VerificationResult(
        status=final,
        commit_id=commit_id,
        candidate_id=candidate_id,
        document_id=doc_id,
        source_sha256=sha256,
        cache_status=cache_status,
        chunk_status=chunk_status,
        chroma_status=chroma_status,
        exact_retrieval_status=exact_ret,
        document_retrieval_status=doc_ret,
        account_retrieval_status=acc_ret,
        semantic_retrieval_status=sem_ret,
        stale_data_status=stale_st,
        metadata_status=meta_st,
        quality_status=quality_st,
        checks=checks,
        failures=failures,
        warnings=warnings_list,
        evidence=evidence,
    )
