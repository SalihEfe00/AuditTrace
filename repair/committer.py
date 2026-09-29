# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 12: Safe Commit / Atomic Staging / Controlled Reindex.
The single write authority for committing verified extraction candidates.
"""
from __future__ import annotations

import binascii
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mufettis.config import (
    CHROMA_COLLECTION,
    CHROMA_UPSERT_BATCH_SIZE,
    CHUNK_SCHEMA_VERSION,
    DATA_DIR,
    INGEST_INDEX_FILE,
    SOURCE_CACHE_DIR,
)
from mufettis.common.utils import mask_sensitive, now_iso, safe_json, source_profile
from mufettis.corpus import with_canonical
from mufettis.corpus.identity import content_fingerprint
from mufettis.extraction.text_repair import repair_extracted_spacing
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex, store_path
from repair.models import (
    BeforeSnapshot,
    CommitResult,
    CommitStatus,
    ComparisonStatus,
    StagingManifest,
)

# In-process concurrency guard
_GLOBAL_COMMIT_LOCK = threading.Lock()
_ACTIVE_COMMIT_TARGETS: Dict[str, threading.Lock] = {}
_THREAD_LOCAL = threading.local()


@contextmanager
def acquire_commit_lock(document_id: str, source_sha256: str):
    """Acquires a concurrency lock scoped to the specific document and source hash."""
    target_key = f"{document_id}:{source_sha256}"
    held = getattr(_THREAD_LOCAL, "held_locks", None)
    if held is None:
        held = set()
        _THREAD_LOCAL.held_locks = held

    if target_key in held:
        yield
        return

    with _GLOBAL_COMMIT_LOCK:
        if target_key not in _ACTIVE_COMMIT_TARGETS:
            _ACTIVE_COMMIT_TARGETS[target_key] = threading.Lock()
        lock = _ACTIVE_COMMIT_TARGETS[target_key]

    acquired = lock.acquire(blocking=True, timeout=10.0)
    if not acquired:
        raise TimeoutError(f"Hedef doküman için eşzamanlı commit kilitli: {target_key}")

    held.add(target_key)
    try:
        yield
    finally:
        held.discard(target_key)
        lock.release()
        with _GLOBAL_COMMIT_LOCK:
            # Clean up lock reference if not locked
            if not lock.locked():
                _ACTIVE_COMMIT_TARGETS.pop(target_key, None)


@dataclass
class CommitPolicy:
    """Configurable safety policy for commit execution."""
    staging_base_dir: Path = field(default_factory=lambda: DATA_DIR / "repair_staging")
    require_final_ready: bool = True
    allow_empty_chunks: bool = False
    expected_schema_version: str = CHUNK_SCHEMA_VERSION


def validate_pre_commit(
    candidate: Any,
    decision: Any,
    target_rag: Optional[LocalRAG] = None,
    target_cache_dir: Optional[Path] = None,
    policy: Optional[CommitPolicy] = None,
) -> Tuple[bool, List[str]]:
    """12-point deterministic pre-commit safety checklist.

    Returns (is_valid, issues).
    """
    if policy is None:
        policy = CommitPolicy()

    issues: List[str] = []

    # 1. Candidate presence and type
    if candidate is None:
        return False, ["Candidate nesnesi None."]

    doc_id = str(getattr(candidate, "document_id", "") or "")
    src_doc_id = str(getattr(candidate, "source_document_id", "") or doc_id)
    sha256 = str(getattr(candidate, "source_sha256", "") or "").strip().lower()

    if not doc_id:
        issues.append("Candidate document_id boş.")
    if not sha256 or len(sha256) != 64:
        issues.append(f"Candidate source_sha256 geçersiz veya eksik: '{sha256}'.")

    # 2. Candidate extraction status
    status = str(getattr(candidate, "status", "") or "")
    if status.upper() in {"FAILED", "EXTRACTION_FAILED", "BLOCKED", "UNREADABLE", "MISSING", "HASH_MISMATCH"}:
        issues.append(f"Candidate durumu başarısız ({status}).")

    # 3. Decision presence and status
    if decision is None:
        issues.append("FinalGateDecision nesnesi eksik.")
    else:
        final_status = str(getattr(decision, "final_status", "") or getattr(decision, "status", "") or "")
        if policy.require_final_ready and final_status != ComparisonStatus.READY_TO_COMMIT.value:
            issues.append(
                f"Commit için final karar READY_TO_COMMIT olmalıdır; mevcut={final_status}."
            )

    # 4. Candidate chunks validation
    chunks = getattr(candidate, "legal_chunks", []) or []
    if not chunks and not policy.allow_empty_chunks:
        issues.append("Candidate legal chunk listesi boş.")

    for i, ch in enumerate(chunks):
        if not isinstance(ch, dict):
            issues.append(f"Chunk {i} geçerli bir sözlük değil.")
            break
        text = str(ch.get("text", "")).strip()
        if not text:
            issues.append(f"Chunk {i} boş metin içeriyor.")
            break

    # 5. Schema version validation
    cand_schema = str(
        getattr(candidate, "metadata", {}).get("schema_version")
        or getattr(candidate, "chunk_schema_version", policy.expected_schema_version)
    )
    if cand_schema != policy.expected_schema_version:
        issues.append(
            f"Schema version uyumsuzluğu: beklenen={policy.expected_schema_version}, aday={cand_schema}."
        )

    # 6. Source cache candidate text
    norm_text = str(getattr(candidate, "normalized_text", "") or getattr(candidate, "raw_text", "") or "")
    if not norm_text.strip():
        issues.append("Candidate metni (normalized_text) boş.")

    # 7. Target Chroma collection accessibility
    if target_rag is not None:
        if getattr(target_rag, "collection", None) is None:
            issues.append("Hedef Chroma collection erişilemez.")
        else:
            # 8. Embedding model compatibility
            cand_model = getattr(candidate, "metadata", {}).get("embedding_model")
            if cand_model and cand_model != target_rag.model_name:
                issues.append(
                    f"Embedding model uyumsuzluğu: hedef={target_rag.model_name}, aday={cand_model}."
                )

    # 9. Target cache directory
    if target_cache_dir is not None:
        try:
            target_cache_dir = Path(target_cache_dir)
            target_cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            issues.append(f"Hedef cache dizini oluşturulamadı/erişilemez: {exc}")

    return len(issues) == 0, issues


def stage_candidate(
    candidate: Any,
    decision: Any,
    staging_dir: Path,
    dry_run: bool = False,
) -> StagingManifest:
    """Stages candidate files and creates a manifest in the staging directory."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    random_hex = binascii.hexlify(os.urandom(6)).decode()
    commit_id = staging_dir.name if staging_dir.name.startswith("commit_") else f"commit_{random_hex}"
    candidate_id = str(getattr(candidate, "document_id", "") or "candidate")
    doc_id = str(getattr(candidate, "document_id", "") or "")
    src_doc_id = str(getattr(candidate, "source_document_id", "") or doc_id)
    sha256 = str(getattr(candidate, "source_sha256", "") or "")
    chunks = getattr(candidate, "legal_chunks", []) or []

    manifest = StagingManifest(
        commit_id=commit_id,
        candidate_id=candidate_id,
        document_id=doc_id,
        source_document_id=src_doc_id,
        source_sha256=sha256,
        chunk_schema_version=CHUNK_SCHEMA_VERSION,
        candidate_chunk_count=len(chunks),
        final_decision=str(getattr(decision, "final_status", "") or getattr(decision, "status", "")),
        target_collection=CHROMA_COLLECTION,
        created_at=now_iso(),
        staging_dir=str(staging_dir),
        dry_run=dry_run,
    )

    # Write staging files
    manifest_file = staging_dir / "manifest.json"
    manifest_file.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    candidate_file = staging_dir / "candidate.json"
    cand_dict = getattr(candidate, "to_dict", lambda: safe_json(candidate))()
    candidate_file.write_text(json.dumps(safe_json(cand_dict), ensure_ascii=False, indent=2), encoding="utf-8")

    chunks_file = staging_dir / "chunks.json"
    chunks_file.write_text(json.dumps(safe_json(chunks), ensure_ascii=False, indent=2), encoding="utf-8")

    cache_tmp = staging_dir / "cache.tmp"
    norm_text = str(getattr(candidate, "normalized_text", "") or getattr(candidate, "raw_text", "") or "")
    cache_tmp.write_text(norm_text, encoding="utf-8")

    return manifest


def capture_before_snapshot(
    document_id: str,
    source_sha256: str,
    staging_dir: Path,
    cache_dir: Optional[Path] = None,
    rag: Optional[LocalRAG] = None,
    index_path: Optional[Path] = None,
    commit_id: str = "",
    source_document_id: str = "",
) -> Dict[str, Any]:
    """Captures deterministic snapshot of cache, index, and Chroma state before mutation."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    c_dir = cache_dir or SOURCE_CACHE_DIR
    idx_p = index_path or INGEST_INDEX_FILE

    # 1. Cache snapshot
    cache_file = c_dir / f"{source_sha256}.txt"
    cache_existed = cache_file.exists()
    cache_content = ""
    cache_hash = ""
    cache_size = 0
    if cache_existed:
        try:
            cache_content = cache_file.read_text(encoding="utf-8")
            cache_size = len(cache_content.encode("utf-8"))
            cache_hash = hashlib.sha256(cache_content.encode("utf-8")).hexdigest()
            # Save copy in staging
            (staging_dir / "before_cache.txt").write_text(cache_content, encoding="utf-8")
        except Exception as exc:
            # Sessizce geçmek "önbellek vardı ama boştu" kaydı üretir; geri alma dosyayı boşaltırdı.
            raise RuntimeError(f"Önceki durum anlık görüntüsü alınamadı (source cache): {exc}") from exc

    cache_state = {
        "existed": cache_existed,
        "path": str(cache_file),
        "sha256": cache_hash,
        "size": cache_size,
        "content": cache_content,
    }

    # 2. Index snapshot
    index_existed = False
    index_record = None
    if store_path(idx_p).exists() or Path(idx_p).exists():
        try:
            index_record = IngestIndex(path=idx_p).get(source_sha256)
            index_existed = index_record is not None
        except Exception as exc:
            # Sessizce geçmek "kayıt yoktu" demektir; geri alma mevcut indeks kaydını silerdi.
            raise RuntimeError(f"Önceki durum anlık görüntüsü alınamadı (ingest index): {exc}") from exc

    index_state = {
        "existed": index_existed,
        "path": str(idx_p),
        "file_hash": source_sha256,
        "record": index_record,
    }

    # 3. Chroma snapshot
    chroma_existed = False
    chroma_ids = []
    chroma_docs = []
    chroma_metas = []
    if rag is not None and getattr(rag, "collection", None) is not None:
        try:
            got = rag.collection.get(
                where={"file_hash": source_sha256},
                include=["documents", "metadatas"],
            )
            if got and got.get("ids"):
                chroma_ids = list(got.get("ids", []))
                chroma_docs = list(got.get("documents", []) or [])
                chroma_metas = list(got.get("metadatas", []) or [])
                chroma_existed = len(chroma_ids) > 0
        except Exception as exc:
            # Sessizce geçmek "parça yoktu" demektir; commit eski parçaları silip geri getiremezdi.
            raise RuntimeError(f"Önceki durum anlık görüntüsü alınamadı (Chroma): {exc}") from exc

    chroma_state = {
        "existed": chroma_existed,
        "chunk_count": len(chroma_ids),
        "ids": chroma_ids,
        "documents": chroma_docs,
        "metadatas": chroma_metas,
    }

    # 4. Fingerprints
    idx_hash = (
        hashlib.sha256(json.dumps(index_record, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if index_record is not None
        else ""
    )
    chroma_data_str = "|".join(
        f"{cid}:{cdoc}:{json.dumps(cm, sort_keys=True, ensure_ascii=False)}"
        for cid, cdoc, cm in zip(chroma_ids, chroma_docs, chroma_metas)
    )
    chroma_hash = hashlib.sha256(chroma_data_str.encode("utf-8")).hexdigest() if chroma_ids else ""

    fingerprints = {
        "cache_hash": cache_hash,
        "index_hash": idx_hash,
        "chroma_hash": chroma_hash,
        "chunk_count": len(chroma_ids),
    }

    snapshot = BeforeSnapshot(
        commit_id=commit_id,
        document_id=document_id,
        source_document_id=source_document_id or document_id,
        source_sha256=source_sha256,
        timestamp=now_iso(),
        chunk_schema_version=CHUNK_SCHEMA_VERSION,
        cache_state=cache_state,
        index_state=index_state,
        chroma_state=chroma_state,
        fingerprints=fingerprints,
    )

    snapshot_file = staging_dir / "snapshot.json"
    snapshot_file.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return snapshot.to_dict()


def atomic_commit_source_cache(candidate: Any, cache_dir: Path) -> Path:
    """Atomically commits candidate text to the target source cache directory."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    sha256 = str(getattr(candidate, "source_sha256", "")).strip().lower()
    target_file = cache_dir / f"{sha256}.txt"
    random_hex = binascii.hexlify(os.urandom(4)).decode()
    tmp_file = cache_dir / f"{sha256}.txt.tmp_{random_hex}"

    payload = str(getattr(candidate, "normalized_text", "") or getattr(candidate, "raw_text", "") or "")
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    tmp_file.replace(target_file)
    return target_file


def cleanup_same_hash_chroma(
    rag: LocalRAG,
    document_id: str,
    source_sha256: str,
) -> int:
    """Deletes ONLY old active records matching the EXACT SAME source hash and document id.

    Strictly preserves other documents and other source hashes.
    Returns count of deleted chunks.
    """
    if rag is None or getattr(rag, "collection", None) is None:
        return 0

    sha256 = str(source_sha256).strip().lower()
    try:
        found = rag.collection.get(where={"file_hash": sha256}, include=["metadatas"])
    except Exception as exc:
        raise RuntimeError(f"Eski kayıtlar Chroma'dan okunamadı: {exc}") from exc

    ids = found.get("ids", []) or []
    metadatas = found.get("metadatas", []) or []

    target_ids: List[str] = []
    for cid, meta in zip(ids, metadatas):
        meta = meta or {}
        # Strict same-hash check
        if str(meta.get("file_hash", "")).lower() != sha256:
            continue
        # Document identity check: matches if document_id or source_document_id matches,
        # or if existing record has no document_id set
        m_doc = str(meta.get("document_id", "") or "")
        m_src = str(meta.get("source_document_id", "") or "")
        if not m_doc and not m_src:
            target_ids.append(cid)
        elif m_doc == document_id or m_src == document_id:
            target_ids.append(cid)

    if target_ids:
        try:
            rag.collection.delete(ids=target_ids)
        except Exception as exc:
            raise RuntimeError(f"Eski same-hash kayıtlar silinemedi ({len(target_ids)} adet): {exc}") from exc

    return len(target_ids)


def upsert_candidate_chroma(
    rag: LocalRAG,
    candidate: Any,
) -> int:
    """Batch embeds and upserts candidate chunks into the target Chroma collection."""
    if rag is None or getattr(rag, "collection", None) is None:
        raise RuntimeError("Hedef LocalRAG collection hazır değil.")

    chunks = getattr(candidate, "legal_chunks", []) or []
    if not chunks:
        return 0

    sha256 = str(getattr(candidate, "source_sha256", "")).strip().lower()
    doc_id = str(getattr(candidate, "document_id", "") or "")
    src_doc_id = str(getattr(candidate, "source_document_id", "") or doc_id)
    cand_meta = getattr(candidate, "metadata", {}) or {}
    title = str(cand_meta.get("title", "") or "")
    doc_type = str(cand_meta.get("document_type", "") or "")
    source_id = str(cand_meta.get("source_id") or doc_id)
    # Eski güven alanları uydurma varsayılanla ("secondary"/40) doldurulmaz: kaynak kaydından
    # gelir. Retrieval güveni zaten kanonik modelden (yayıncı niteliği + doğrulama) okunur.
    profile = source_profile(source_id)
    fingerprint = str(cand_meta.get("content_fingerprint") or "") or content_fingerprint(
        getattr(candidate, "normalized_text", "") or getattr(candidate, "raw_text", "")
    )

    docs: List[str] = []
    metadatas: List[Dict[str, str]] = []
    ids: List[str] = []

    for ch in chunks:
        chunk_text = repair_extracted_spacing(ch.get("text", ""), title, doc_type)
        chunk_text = mask_sensitive(chunk_text)
        record_meta = {
            **cand_meta,
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "file_hash": sha256,
            "document_id": doc_id,
            "source_document_id": src_doc_id,
            "source_id": source_id,
            "source": cand_meta.get("source") or profile.get("name") or doc_id.replace("_", "."),
            "source_type": str(cand_meta.get("source_type") or profile.get("source_type") or ""),
            "authority_rank": str(cand_meta.get("authority_rank") or profile.get("authority_rank") or 0),
            "content_fingerprint": fingerprint,
            "status": "active",
            "article_prefix": ch.get("article_prefix") or "",
            "article_no": ch.get("article_no") or "",
            "article_title": ch.get("article_title") or "",
            "paragraph_no": ch.get("paragraph_no") or "",
            "item_no": ch.get("item_no") or "",
            "subitem_no": ch.get("subitem_no") or "",
            "section_no": ch.get("section_no") or "",
            "account_code": ch.get("account_code") or "",
            "chunk_type": ch.get("chunk_type") or "document",
            "structure_type": ch.get("structure_type") or cand_meta.get("structure_type", "document"),
            "subchunk_no": int(ch.get("subchunk_no", 0) or 0),
            "chunk_no": int(ch.get("chunk_no", 0) or 0),
            "document_indexable": "True",
            "evidence_indexable": "True",
        }
        # ingest_text ile aynı kanonik alanlar (kimlik, kaynak, normatif rol).
        record_meta = with_canonical(record_meta)
        docs.append(chunk_text)
        metadatas.append({
            k: str(v) if v is not None else ""
            for k, v in record_meta.items()
            if k != "content_size" or isinstance(v, (str, int, float))
        })
        ids.append(f"{sha256[:24]}-{ch.get('chunk_no', 0):06d}")

    total = len(docs)
    for start in range(0, total, CHROMA_UPSERT_BATCH_SIZE):
        end = min(total, start + CHROMA_UPSERT_BATCH_SIZE)
        batch_docs = docs[start:end]
        batch_embeddings = rag.embed(batch_docs)
        rag.collection.upsert(
            ids=ids[start:end],
            documents=batch_docs,
            embeddings=batch_embeddings,
            metadatas=metadatas[start:end],
        )

    return total


def commit_candidate(
    candidate: Any,
    decision: Any,
    *,
    allow_commit: bool = False,
    rag: Optional[LocalRAG] = None,
    target_cache_dir: Optional[Path] = None,
    staging_base_dir: Optional[Path] = None,
    policy: Optional[CommitPolicy] = None,
    dry_run: bool = False,
) -> CommitResult:
    """Single Write Authority: Safely commits an ExtractionCandidate to cache and Chroma.

    Pre-conditions:
    - allow_commit must be True (default False = dry-run / safe mode)
    - decision must be READY_TO_COMMIT
    - pre-commit checklist must pass 100%
    """
    if policy is None:
        policy = CommitPolicy()
    if staging_base_dir is not None:
        policy.staging_base_dir = Path(staging_base_dir)

    is_dry_run = dry_run or (not allow_commit)

    doc_id = str(getattr(candidate, "document_id", "") or "unknown")
    sha256 = str(getattr(candidate, "source_sha256", "") or "unknown").strip().lower()

    # 1. PRE-COMMIT VALIDATION
    valid, issues = validate_pre_commit(
        candidate=candidate,
        decision=decision,
        target_rag=rag,
        target_cache_dir=target_cache_dir,
        policy=policy,
    )
    if not valid:
        return CommitResult(
            status=CommitStatus.ABORTED.value,
            commit_id="",
            candidate_id=doc_id,
            document_id=doc_id,
            source_sha256=sha256,
            dry_run=is_dry_run,
            reasons=issues,
            error="; ".join(issues),
        )

    # 2. CONCURRENCY LOCK
    try:
        with acquire_commit_lock(doc_id, sha256):
            random_hex = binascii.hexlify(os.urandom(6)).decode()
            commit_id = f"commit_{random_hex}"
            staging_dir = policy.staging_base_dir / commit_id

            # 3. ATOMIC STAGING
            try:
                manifest = stage_candidate(candidate, decision, staging_dir, dry_run=is_dry_run)
                capture_before_snapshot(
                    document_id=doc_id,
                    source_sha256=sha256,
                    staging_dir=staging_dir,
                    cache_dir=target_cache_dir or SOURCE_CACHE_DIR,
                    rag=rag,
                    commit_id=manifest.commit_id,
                    source_document_id=str(getattr(candidate, "source_document_id", "") or doc_id),
                )
            except Exception as exc:
                return CommitResult(
                    status=CommitStatus.FAILED.value,
                    commit_id=commit_id,
                    candidate_id=doc_id,
                    document_id=doc_id,
                    source_sha256=sha256,
                    dry_run=is_dry_run,
                    reasons=[f"Aday staging başarısız: {exc}"],
                    error=str(exc),
                )

            # 4. DRY RUN GUARD
            if is_dry_run:
                # In dry run, check old chunk count without modifying anything
                old_count = 0
                if rag is not None and getattr(rag, "collection", None) is not None:
                    try:
                        found = rag.collection.get(where={"file_hash": sha256})
                        old_count = len(found.get("ids", []) or [])
                    except Exception:
                        pass
                return CommitResult(
                    status=CommitStatus.NO_OP.value if not allow_commit else CommitStatus.SUCCESS.value,
                    commit_id=commit_id,
                    candidate_id=doc_id,
                    document_id=doc_id,
                    source_sha256=sha256,
                    cache_commit=False,
                    chroma_cleanup=False,
                    chroma_upsert=False,
                    old_chunk_count=old_count,
                    new_chunk_count=len(getattr(candidate, "legal_chunks", []) or []),
                    dry_run=True,
                    reasons=["Dry-run modu: hiçbir dosya veya Chroma kaydı değiştirilmedi."],
                    manifest=manifest.to_dict(),
                )

            # 5. IDEMPOTENCY CHECK
            # If already committed and active in Chroma with same chunk count
            cache_dir = target_cache_dir or SOURCE_CACHE_DIR
            cache_file = cache_dir / f"{sha256}.txt"
            old_chunks_count = 0
            if rag is not None and getattr(rag, "collection", None) is not None:
                try:
                    existing = rag.collection.get(where={"file_hash": sha256})
                    old_chunks_count = len(existing.get("ids", []) or [])
                    new_chunks = getattr(candidate, "legal_chunks", []) or []
                    if old_chunks_count == len(new_chunks) and cache_file.exists():
                        norm_text = str(getattr(candidate, "normalized_text", "") or "")
                        if cache_file.read_text(encoding="utf-8") == norm_text:
                            return CommitResult(
                                status=CommitStatus.NO_OP.value,
                                commit_id=commit_id,
                                candidate_id=doc_id,
                                document_id=doc_id,
                                source_sha256=sha256,
                                cache_commit=True,
                                chroma_cleanup=False,
                                chroma_upsert=False,
                                old_chunk_count=old_chunks_count,
                                new_chunk_count=len(new_chunks),
                                dry_run=False,
                                reasons=["İdempotent çağrı: aday zaten hedef ortamda tam eşleşmeyle güncel."],
                                manifest=manifest.to_dict(),
                            )
                except Exception:
                    pass

            # 6. ATOMIC SOURCE CACHE COMMIT
            cache_committed = False
            try:
                atomic_commit_source_cache(candidate, cache_dir)
                cache_committed = True
            except Exception as exc:
                return CommitResult(
                    status=CommitStatus.FAILED.value,
                    commit_id=commit_id,
                    candidate_id=doc_id,
                    document_id=doc_id,
                    source_sha256=sha256,
                    cache_commit=False,
                    chroma_cleanup=False,
                    chroma_upsert=False,
                    reasons=[f"Source cache atomic yazma hatası: {exc}"],
                    error=str(exc),
                    manifest=manifest.to_dict(),
                )

            # 7. CHROMA SAME-HASH CLEANUP
            chroma_cleaned = False
            cleaned_count = 0
            if rag is not None:
                try:
                    cleaned_count = cleanup_same_hash_chroma(rag, doc_id, sha256)
                    chroma_cleaned = True
                except Exception as exc:
                    return CommitResult(
                        status=CommitStatus.PARTIAL_FAILURE.value,
                        commit_id=commit_id,
                        candidate_id=doc_id,
                        document_id=doc_id,
                        source_sha256=sha256,
                        cache_commit=True,
                        chroma_cleanup=False,
                        chroma_upsert=False,
                        old_chunk_count=old_chunks_count,
                        new_chunk_count=0,
                        reasons=[f"Cache güncellendi fakat Chroma eski kayıt temizliği başarısız: {exc}"],
                        error=str(exc),
                        manifest=manifest.to_dict(),
                    )

            # 8. CHROMA CANDIDATE UPSERT
            chroma_upserted = False
            upserted_count = 0
            if rag is not None:
                try:
                    upserted_count = upsert_candidate_chroma(rag, candidate)
                    chroma_upserted = True
                except Exception as exc:
                    return CommitResult(
                        status=CommitStatus.PARTIAL_FAILURE.value,
                        commit_id=commit_id,
                        candidate_id=doc_id,
                        document_id=doc_id,
                        source_sha256=sha256,
                        cache_commit=True,
                        chroma_cleanup=chroma_cleaned,
                        chroma_upsert=False,
                        old_chunk_count=cleaned_count,
                        new_chunk_count=0,
                        reasons=[f"Cache güncellendi fakat Chroma candidate upsert başarısız: {exc}"],
                        error=str(exc),
                        manifest=manifest.to_dict(),
                    )

            return CommitResult(
                status=CommitStatus.SUCCESS.value,
                commit_id=commit_id,
                candidate_id=doc_id,
                document_id=doc_id,
                source_sha256=sha256,
                cache_commit=cache_committed,
                chroma_cleanup=chroma_cleaned,
                chroma_upsert=chroma_upserted,
                old_chunk_count=cleaned_count or old_chunks_count,
                new_chunk_count=upserted_count,
                dry_run=False,
                reasons=[
                    f"Aday başarıyla commit edildi: {upserted_count} chunk upsert edildi, "
                    f"{cleaned_count} eski chunk temizlendi, source cache güncellendi."
                ],
                manifest=manifest.to_dict(),
            )

    except TimeoutError as exc:
        return CommitResult(
            status=CommitStatus.ABORTED.value,
            commit_id="",
            candidate_id=doc_id,
            document_id=doc_id,
            source_sha256=sha256,
            reasons=[str(exc)],
            error=str(exc),
        )
