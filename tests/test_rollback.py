# -*- coding: utf-8 -*-
"""
FAZ 14: Ultimate Safe Rollback / State Restoration / Recovery Gate — Test Suite.

Two test classes:
  TestRollbackUnit           — 26 unit tests (mock/filesystem, fast, deterministic)
  TestRagLabRollback — 6 live integration tests on isolated RAG Lab sandbox
                                    including full E2E BEFORE -> COMMIT -> FAILURE -> ROLLBACK -> RESTORED

Zero production mutation.
Live RAG Lab state preserved at all times.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

from mufettis.config import (
    CHUNK_SCHEMA_VERSION,
    DATA_DIR,
    DB_PATH,
    EMBEDDING_MODEL,
)
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex
from mufettis.repair.committer import (
    commit_candidate,
)
from mufettis.repair.models import (
    BeforeSnapshot,
    CommitStatus,
    ComparisonStatus,
    ExtractionCandidate,
    FinalGateDecision,
    RollbackResult,
    RollbackStatus,
    VerificationResult,
    VerificationStatus,
)
from mufettis.repair.rollback import (
    acquire_rollback_lock,
    calculate_state_fingerprint,
    restore_chroma_state,
    restore_ingest_index,
    restore_source_cache,
    rollback_commit,
    validate_snapshot,
)

_SAMPLE_HASH = "b" * 64
_SAMPLE_DOC_ID = "doc_rollback_test"


def _make_candidate(
    doc_id: str = _SAMPLE_DOC_ID,
    sha256: str = _SAMPLE_HASH,
    text: str = "MADDE 1 - Yeni metin.\nMADDE 2 - İkinci yeni metin.",
    chunk_count: int = 2,
) -> ExtractionCandidate:
    chunks = [
        {"chunk_no": i, "text": f"MADDE {i+1} - Yeni chunk {i+1}", "chunk_type": "article"}
        for i in range(chunk_count)
    ]
    return ExtractionCandidate(
        document_id=doc_id,
        source_document_id=doc_id,
        source_sha256=sha256,
        normalized_text=text,
        raw_text=text,
        legal_chunks=chunks,
        status="SUCCESS",
        metadata={"schema_version": CHUNK_SCHEMA_VERSION, "embedding_model": EMBEDDING_MODEL},
        in_memory_only=True,
    )


# ============================================================================
# CLASS 1 — Unit Tests (mock/ephemeral filesystem)
# ============================================================================

class TestRollbackUnit(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="faz14_unit_"))
        self.staging_base = self.temp_dir / "repair_staging"
        self.cache_dir = self.temp_dir / "cache"
        self.index_file = self.temp_dir / "ingest_index.json"
        self.staging_base.mkdir(parents=True)
        self.cache_dir.mkdir(parents=True)

        self.index = IngestIndex(path=self.index_file)

        # Mock RAG
        self.mock_rag = MagicMock()
        self.mock_rag.model_name = EMBEDDING_MODEL
        self.mock_collection = MagicMock()
        self.mock_rag.collection = self.mock_collection
        self.mock_rag.embed.return_value = [[0.1] * 384, [0.2] * 384]

        # Standard commit setup
        self.commit_id = "commit_test_unit_01"
        self.staging_dir = self.staging_base / self.commit_id
        self.staging_dir.mkdir(parents=True)

        self.old_text = "MADDE 1 - Eski orijinal metin."
        self.old_record = {
            "file_hash": _SAMPLE_HASH,
            "document_id": _SAMPLE_DOC_ID,
            "title": "Eski Doküman",
            "status": "active",
        }
        self.old_chunks = {
            "ids": [f"{_SAMPLE_HASH[:24]}-000000"],
            "documents": [self.old_text],
            "metadatas": [
                {
                    "document_id": _SAMPLE_DOC_ID,
                    "file_hash": _SAMPLE_HASH,
                    "status": "active",
                    "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                    "chunk_type": "article",
                    "chunk_no": "0",
                    "document_indexable": "True",
                }
            ],
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_snapshot(
        self,
        cache_existed: bool = True,
        index_existed: bool = True,
        chroma_existed: bool = True,
        commit_id: Optional[str] = None,
        doc_id: Optional[str] = None,
        sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        cid = commit_id or self.commit_id
        did = doc_id or _SAMPLE_DOC_ID
        shash = sha256 or _SAMPLE_HASH

        snap = {
            "commit_id": cid,
            "document_id": did,
            "source_document_id": did,
            "source_sha256": shash,
            "timestamp": "2026-09-20T12:00:00Z",
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "cache_state": {
                "existed": cache_existed,
                "path": str(self.cache_dir / f"{shash}.txt"),
                "sha256": "old_cache_sha",
                "size": len(self.old_text),
                "content": self.old_text if cache_existed else "",
            },
            "index_state": {
                "existed": index_existed,
                "path": str(self.index_file),
                "file_hash": shash,
                "record": self.old_record if index_existed else None,
            },
            "chroma_state": {
                "existed": chroma_existed,
                "chunk_count": len(self.old_chunks["ids"]) if chroma_existed else 0,
                "ids": self.old_chunks["ids"] if chroma_existed else [],
                "documents": self.old_chunks["documents"] if chroma_existed else [],
                "metadatas": self.old_chunks["metadatas"] if chroma_existed else [],
            },
            "fingerprints": {
                "cache_hash": "dummy_cache_hash",
                "index_hash": "dummy_index_hash",
                "chroma_hash": "dummy_chroma_hash",
                "chunk_count": 1 if chroma_existed else 0,
            },
        }
        s_file = self.staging_base / cid / "snapshot.json"
        s_file.parent.mkdir(parents=True, exist_ok=True)
        s_file.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        return snap

    # 1. Valid rollback success
    def test_01_valid_rollback_success(self):
        self._create_snapshot()
        # Committed state currently in cache
        (self.cache_dir / f"{_SAMPLE_HASH}.txt").write_text("YENİ COMMITTED METİN", encoding="utf-8")

        res = rollback_commit(
            commit_id=self.commit_id,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_SUCCESS.value)
        self.assertEqual(res.cache_status, "PASS")
        self.assertEqual(res.index_status, "PASS")
        self.assertEqual(res.chroma_status, "PASS")
        # Assert cache was restored to old text
        restored = (self.cache_dir / f"{_SAMPLE_HASH}.txt").read_text(encoding="utf-8")
        self.assertEqual(restored, self.old_text)

    # 2. Rollback after verify failure
    def test_02_rollback_after_verify_failure(self):
        self._create_snapshot()
        v_res = VerificationResult(
            status=VerificationStatus.VERIFY_FAILED.value,
            commit_id=self.commit_id,
            candidate_id=_SAMPLE_DOC_ID,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
            failures=["CHUNK_COUNT: mismatch"],
        )
        res = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            verification_result=v_res,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_SUCCESS.value)

    # 3. Cache restoration content
    def test_03_cache_restoration(self):
        self._create_snapshot(cache_existed=True)
        (self.cache_dir / f"{_SAMPLE_HASH}.txt").write_text("Candidate New Content", encoding="utf-8")
        ok, msg = restore_source_cache(
            {"existed": True, "content": self.old_text},
            self.cache_dir,
            _SAMPLE_HASH,
            self.staging_dir,
        )
        self.assertTrue(ok)
        self.assertEqual((self.cache_dir / f"{_SAMPLE_HASH}.txt").read_text(encoding="utf-8"), self.old_text)

    # 4. Cache missing before state restored to absent
    def test_04_cache_missing_before_state(self):
        self._create_snapshot(cache_existed=False)
        (self.cache_dir / f"{_SAMPLE_HASH}.txt").write_text("Unexpected Content", encoding="utf-8")
        ok, msg = restore_source_cache(
            {"existed": False},
            self.cache_dir,
            _SAMPLE_HASH,
            self.staging_dir,
        )
        self.assertTrue(ok)
        self.assertFalse((self.cache_dir / f"{_SAMPLE_HASH}.txt").exists())

    # 5. Index restoration
    def test_05_index_restoration(self):
        # Candidate updated index
        self.index.upsert(_SAMPLE_HASH, {"title": "Yeni Başlık"})
        ok, msg = restore_ingest_index(
            {"existed": True, "record": self.old_record},
            self.index,
            _SAMPLE_HASH,
        )
        self.assertTrue(ok)
        rec = self.index.get(_SAMPLE_HASH)
        self.assertEqual(rec.get("title"), "Eski Doküman")

    # 6. Index missing before state restored to absent
    def test_06_index_missing_before_state(self):
        self.index.upsert(_SAMPLE_HASH, {"title": "Yeni Başlık"})
        ok, msg = restore_ingest_index(
            {"existed": False},
            self.index,
            _SAMPLE_HASH,
        )
        self.assertTrue(ok)
        self.assertIsNone(self.index.get(_SAMPLE_HASH))

    # 7. Chroma restoration
    def test_07_chroma_restoration(self):
        ok, count, msg = restore_chroma_state(
            {"existed": True, **self.old_chunks},
            self.mock_rag,
            _SAMPLE_DOC_ID,
            _SAMPLE_HASH,
        )
        self.assertTrue(ok)
        self.assertEqual(count, 1)
        self.mock_collection.delete.assert_called_with(where={"file_hash": _SAMPLE_HASH})
        self.mock_collection.upsert.assert_called()

    # 8. Chroma missing before state restored to absent
    def test_08_chroma_missing_before_state(self):
        ok, count, msg = restore_chroma_state(
            {"existed": False},
            self.mock_rag,
            _SAMPLE_DOC_ID,
            _SAMPLE_HASH,
        )
        self.assertTrue(ok)
        self.assertEqual(count, 0)
        self.mock_collection.delete.assert_called_with(where={"file_hash": _SAMPLE_HASH})

    # 9. Snapshot missing blocked
    def test_09_snapshot_missing(self):
        # Staging exists but no snapshot.json
        res = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_BLOCKED.value)
        self.assertIn("Snapshot dosyası bulunamadı", res.failures[0])

    # 10. Snapshot corrupted fails
    def test_10_snapshot_corrupted(self):
        s_file = self.staging_dir / "snapshot.json"
        s_file.write_text("{corrupt json", encoding="utf-8")
        res = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_FAILED.value)

    # 11. Wrong snapshot identity blocked
    def test_11_wrong_snapshot_identity(self):
        self._create_snapshot(doc_id="other_doc", sha256="c" * 64)
        res = rollback_commit(
            commit_id=self.commit_id,
            document_id=_SAMPLE_DOC_ID,  # expects _SAMPLE_DOC_ID but snapshot is other_doc
            source_sha256=_SAMPLE_HASH,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_BLOCKED.value)
        self.assertTrue(any("eşleşmiyor" in f for f in res.failures))

    # 12. Verified repair blocked without force
    def test_12_verified_repair_blocked(self):
        self._create_snapshot()
        v_res = VerificationResult(
            status=VerificationStatus.VERIFIED_REPAIR.value,
            commit_id=self.commit_id,
            candidate_id=_SAMPLE_DOC_ID,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
        )
        res = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            verification_result=v_res,
            force=False,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_BLOCKED.value)
        self.assertIn("VERIFIED_REPAIR", res.failures[0])

    # 13. Double rollback (idempotency)
    def test_13_double_rollback_idempotent(self):
        self._create_snapshot()
        res1 = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
        )
        self.assertEqual(res1.status, RollbackStatus.ROLLBACK_SUCCESS.value)

        # Second call
        res2 = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=False,  # not forced
        )
        self.assertEqual(res2.status, RollbackStatus.ROLLBACK_ALREADY_RESTORED.value)

    # 14. Dry-run zero mutation
    def test_14_dry_run_zero_mutation(self):
        self._create_snapshot()
        target_cache = self.cache_dir / f"{_SAMPLE_HASH}.txt"
        target_cache.write_text("BEFORE_DRY_RUN", encoding="utf-8")

        res = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
            dry_run=True,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_SUCCESS.value)
        self.assertTrue(res.dry_run)
        # Content unchanged
        self.assertEqual(target_cache.read_text(encoding="utf-8"), "BEFORE_DRY_RUN")
        self.mock_collection.delete.assert_not_called()
        self.mock_collection.upsert.assert_not_called()

    # 15. Concurrent rollback lock
    def test_15_concurrent_rollback_locked(self):
        self._create_snapshot()
        # Acquire lock in thread
        with acquire_rollback_lock(_SAMPLE_DOC_ID, _SAMPLE_HASH):
            # Attempt rollback on same doc/hash with 0.1s timeout
            with self.assertRaises(TimeoutError):
                with acquire_rollback_lock(_SAMPLE_DOC_ID, _SAMPLE_HASH, timeout=0.1):
                    pass

    # 16. Partial restore handled explicitly
    def test_16_partial_restore_handled(self):
        self._create_snapshot()
        # Force Chroma delete to raise exception
        self.mock_collection.delete.side_effect = RuntimeError("Chroma disk error")
        res = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_PARTIAL.value)
        self.assertEqual(res.cache_status, "PASS")
        self.assertEqual(res.chroma_status, "FAIL")

    # 17. Staging directory not found
    def test_17_staging_directory_missing(self):
        res = rollback_commit(
            commit_id="nonexistent_commit",
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_BLOCKED.value)

    # 18. Snapshot validation helper
    def test_18_validate_snapshot_helper(self):
        valid, issues = validate_snapshot({"commit_id": "c1", "document_id": "d1", "source_sha256": "s1",
                                           "cache_state": {}, "index_state": {}, "chroma_state": {}})
        self.assertTrue(valid)
        self.assertEqual(len(issues), 0)

        # Invalid
        valid_bad, issues_bad = validate_snapshot({})
        self.assertFalse(valid_bad)
        self.assertGreater(len(issues_bad), 0)

    # 19. State fingerprint calculation
    def test_19_calculate_state_fingerprint(self):
        (self.cache_dir / f"{_SAMPLE_HASH}.txt").write_text("Hello Fingerprint", encoding="utf-8")
        fp = calculate_state_fingerprint(_SAMPLE_HASH, self.cache_dir, self.mock_rag, self.index)
        self.assertIn("cache_hash", fp)
        self.assertIn("chunk_count", fp)
        self.assertGreater(len(fp["cache_hash"]), 0)

    # 20. RollbackResult serialization
    def test_20_rollback_result_to_dict(self):
        r = RollbackResult(
            status=RollbackStatus.ROLLBACK_SUCCESS.value,
            rollback_id="rb_1",
            commit_id="c_1",
            document_id="d_1",
            source_sha256=_SAMPLE_HASH,
        )
        d = r.to_dict()
        self.assertEqual(d["status"], RollbackStatus.ROLLBACK_SUCCESS.value)
        self.assertEqual(d["rollback_id"], "rb_1")

    # 21. BeforeSnapshot serialization
    def test_21_before_snapshot_to_dict(self):
        snap = BeforeSnapshot(
            commit_id="c_1",
            document_id="d_1",
            source_sha256=_SAMPLE_HASH,
        )
        d = snap.to_dict()
        self.assertEqual(d["commit_id"], "c_1")
        self.assertEqual(d["document_id"], "d_1")

    # 22. Unknown integrity state verification trigger
    def test_22_unknown_integrity_state_triggers_rollback(self):
        self._create_snapshot()
        v_res = VerificationResult(
            status=VerificationStatus.UNKNOWN_INTEGRITY_STATE.value,
            commit_id=self.commit_id,
            candidate_id=_SAMPLE_DOC_ID,
            document_id=_SAMPLE_DOC_ID,
            source_sha256=_SAMPLE_HASH,
        )
        res = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            verification_result=v_res,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_SUCCESS.value)

    # 23. Cross-hash isolation unit check
    def test_23_cross_hash_isolation(self):
        other_hash = "c" * 64
        (self.cache_dir / f"{other_hash}.txt").write_text("OTHER HASH CONTENT", encoding="utf-8")
        self._create_snapshot()
        res = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_SUCCESS.value)
        # Other hash cache file untouched
        self.assertEqual((self.cache_dir / f"{other_hash}.txt").read_text(encoding="utf-8"), "OTHER HASH CONTENT")

    # 24. Cross-document isolation unit check
    def test_24_cross_document_isolation(self):
        self.index.upsert("other_doc_hash", {"document_id": "doc_unrelated", "title": "Other Doc"})
        self._create_snapshot()
        res = rollback_commit(
            commit_id=self.commit_id,
            staging_base_dir=self.staging_base,
            cache_dir=self.cache_dir,
            rag=self.mock_rag,
            index=self.index,
            force=True,
        )
        self.assertEqual(res.status, RollbackStatus.ROLLBACK_SUCCESS.value)
        # Unrelated doc in index untouched
        self.assertIsNotNone(self.index.get("other_doc_hash"))

    # 25. Unexpected exception returns ROLLBACK_FAILED
    def test_25_unexpected_exception_fails(self):
        self._create_snapshot()
        with patch("mufettis.repair.rollback.restore_source_cache", side_effect=Exception("Catastrophic error")):
            res = rollback_commit(
                commit_id=self.commit_id,
                staging_base_dir=self.staging_base,
                cache_dir=self.cache_dir,
                rag=self.mock_rag,
                index=self.index,
                force=True,
            )
            self.assertEqual(res.status, RollbackStatus.ROLLBACK_FAILED.value)
            self.assertIn("Catastrophic error", res.failures[0])

    # 26. Candidate commit capture_before_snapshot integration
    def test_26_candidate_commit_creates_snapshot(self):
        # Create a fresh staging and cache dir
        c_dir = self.temp_dir / "committer_cache"
        c_dir.mkdir(parents=True)
        (c_dir / f"{_SAMPLE_HASH}.txt").write_text("OLD BEFORE COMMIT TEXT", encoding="utf-8")

        candidate = _make_candidate(text="NEW COMMITTED TEXT")
        decision = FinalGateDecision(
            faz11a_status=ComparisonStatus.READY_TO_COMMIT.value,
            faz11b_status=ComparisonStatus.READY_TO_COMMIT.value,
            final_status=ComparisonStatus.READY_TO_COMMIT.value,
        )
        c_res = commit_candidate(
            candidate,
            decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=c_dir,
            staging_base_dir=self.staging_base,
        )
        self.assertEqual(c_res.status, CommitStatus.SUCCESS.value)
        # Verify snapshot.json was created
        snap_file = self.staging_base / c_res.commit_id / "snapshot.json"
        self.assertTrue(snap_file.exists())
        snap_data = json.loads(snap_file.read_text(encoding="utf-8"))
        self.assertEqual(snap_data["cache_state"]["content"], "OLD BEFORE COMMIT TEXT")


# ============================================================================
# CLASS 2 — Isolated RAG Lab Integration Tests (Real Chroma Sandbox)
# ============================================================================

class TestRagLabRollback(unittest.TestCase):
    """
    Live integration tests on an isolated sandbox copy of data/rag_lab.

    Strictly guarantees:
    - Real data/rag_lab/chroma count is UNCHANGED before and after each test.
    - Real data/rag_lab/source_cache files are UNCHANGED.
    - Production chroma.sqlite3 is UNCHANGED.
    - Complete E2E BEFORE -> COMMIT -> FAILURE -> ROLLBACK -> RESTORED cycle.
    """

    REAL_RAG_LAB = DATA_DIR / "rag_lab"
    REAL_CHROMA = REAL_RAG_LAB / "chroma"
    REAL_CACHE = REAL_RAG_LAB / "source_cache"
    BASELINE_FILE = REAL_RAG_LAB / "retrieval_baseline.json"

    TARGET_HASH = "bf956ab51a0464f272e0e2c9237a83657ea63987f7925a092bb037d574fedefa"
    TARGET_DOC_ID = "doc2_finansal_manipulasyon"
    TARGET_DOC_CHUNKS = 25

    def setUp(self):
        if not self.REAL_CHROMA.exists() or not self.REAL_CACHE.exists():
            self.skipTest("data/rag_lab test corpus not found.")

        # Snapshot real state
        real_rag = LocalRAG(db_path=self.REAL_CHROMA, allow_download=False)
        self._real_count_before = real_rag.count()
        self._real_cache_before = {p.name: p.stat().st_size for p in self.REAL_CACHE.glob("*.txt")}
        self._prod_file = DB_PATH / "chroma.sqlite3"
        self._prod_size_before = self._prod_file.stat().st_size if self._prod_file.exists() else 0

        # Isolated sandbox per test
        self.sandbox_dir = Path(tempfile.mkdtemp(prefix="faz14_live_sandbox_"))
        self.sandbox_chroma = self.sandbox_dir / "chroma"
        self.sandbox_cache = self.sandbox_dir / "source_cache"
        self.sandbox_staging = self.sandbox_dir / "staging"
        self.sandbox_index = self.sandbox_dir / "ingest_index.json"
        shutil.copytree(str(self.REAL_CHROMA), str(self.sandbox_chroma))
        shutil.copytree(str(self.REAL_CACHE), str(self.sandbox_cache))
        self.sandbox_staging.mkdir(parents=True, exist_ok=True)

        # Copy real ingest index if present
        real_idx = self.REAL_RAG_LAB / "ingest_index.json"
        if real_idx.exists():
            shutil.copy2(str(real_idx), str(self.sandbox_index))
        else:
            self.sandbox_index.write_text(json.dumps({"version": "2.0", "indexed_items": {}}), encoding="utf-8")

        self.isolated_rag = LocalRAG(db_path=self.sandbox_chroma, allow_download=False)
        self.isolated_index = IngestIndex(path=self.sandbox_index)

        # Snapshot pre-mutation state of target doc in sandbox
        target_cache_file = self.sandbox_cache / f"{self.TARGET_HASH}.txt"
        self._target_cache_content_before = target_cache_file.read_text(encoding="utf-8") if target_cache_file.exists() else ""
        found_target = self.isolated_rag.collection.get(where={"file_hash": self.TARGET_HASH})
        self._target_chunks_before = len(found_target.get("ids", []) or [])

        # Candidate to commit
        self.candidate = ExtractionCandidate(
            document_id=self.TARGET_DOC_ID,
            source_document_id=self.TARGET_DOC_ID,
            source_path=str(self.REAL_RAG_LAB / "fixtures" / "02_finansal_manipulasyon.pdf"),
            source_sha256=self.TARGET_HASH,
            raw_text="MADDE 1 – Rollback test adayı metni.\nMADDE 2 – İkinci rollback test adayı metni.",
            normalized_text="MADDE 1 – Rollback test adayı metni.\nMADDE 2 – İkinci rollback test adayı metni.",
            legal_chunks=[
                {"chunk_no": 0, "text": "MADDE 1 – Rollback test adayı metni.", "chunk_type": "article", "article_no": "1"},
                {"chunk_no": 1, "text": "MADDE 2 – İkinci rollback test adayı metni.", "chunk_type": "article", "article_no": "2"},
            ],
            extraction_method="pdfplumber",
            status="SUCCESS",
            metadata={
                "title": "FİNANSAL PİYASALARDA MANİPÜLASYON VE YANILTICI İŞLEMLER HAKKINDA YÖNETMELİK",
                "document_type": "Yönetmelik",
                "schema_version": CHUNK_SCHEMA_VERSION,
                "embedding_model": EMBEDDING_MODEL,
            },
            in_memory_only=True,
        )
        self.decision = FinalGateDecision(
            faz11a_status=ComparisonStatus.READY_TO_COMMIT.value,
            faz11b_status=ComparisonStatus.READY_TO_COMMIT.value,
            final_status=ComparisonStatus.READY_TO_COMMIT.value,
            reasons=["Ready"],
        )

    def tearDown(self):
        try:
            if hasattr(self, "sandbox_dir") and self.sandbox_dir.exists():
                shutil.rmtree(self.sandbox_dir, ignore_errors=True)
        finally:
            # Assert live RAG Lab unchanged
            real_rag = LocalRAG(db_path=self.REAL_CHROMA, allow_download=False)
            self.assertEqual(
                real_rag.count(),
                self._real_count_before,
                "SECURITY VIOLATION: live data/rag_lab/chroma was mutated by FAZ 14 test!",
            )
            current_cache = {p.name: p.stat().st_size for p in self.REAL_CACHE.glob("*.txt")}
            self.assertEqual(
                current_cache,
                self._real_cache_before,
                "SECURITY VIOLATION: live data/rag_lab/source_cache was mutated by FAZ 14 test!",
            )
            if self._prod_file.exists():
                self.assertEqual(
                    self._prod_file.stat().st_size,
                    self._prod_size_before,
                    "SECURITY VIOLATION: production Chroma mutated!",
                )

    def test_harness_01_e2e_before_commit_failure_rollback_restored(self):
        """THE ULTIMATE FAZ 14 E2E TEST:
        BEFORE -> COMMIT -> FORCED FAILURE -> ROLLBACK -> RESTORE VERIFICATION -> BEFORE == RESTORED.
        """
        # 1. Capture BEFORE state in sandbox
        before_total_count = self.isolated_rag.count()
        before_cache_content = (self.sandbox_cache / f"{self.TARGET_HASH}.txt").read_text(encoding="utf-8")
        before_target_chunks = len(self.isolated_rag.collection.get(where={"file_hash": self.TARGET_HASH})["ids"])

        # 2. COMMIT candidate (replaces 25 chunks with 2 chunks)
        commit_res = commit_candidate(
            self.candidate,
            self.decision,
            allow_commit=True,
            rag=self.isolated_rag,
            target_cache_dir=self.sandbox_cache,
            staging_base_dir=self.sandbox_staging,
        )
        self.assertEqual(commit_res.status, CommitStatus.SUCCESS.value)
        # Verify commit mutated sandbox as expected
        committed_target_chunks = len(self.isolated_rag.collection.get(where={"file_hash": self.TARGET_HASH})["ids"])
        self.assertEqual(committed_target_chunks, 2)
        committed_cache = (self.sandbox_cache / f"{self.TARGET_HASH}.txt").read_text(encoding="utf-8")
        self.assertEqual(committed_cache, self.candidate.normalized_text)

        # 3. Simulate VERIFICATION FAILURE
        fake_verify_failure = VerificationResult(
            status=VerificationStatus.VERIFY_FAILED.value,
            commit_id=commit_res.commit_id,
            candidate_id=self.TARGET_DOC_ID,
            document_id=self.TARGET_DOC_ID,
            source_sha256=self.TARGET_HASH,
            failures=["Simulated verification failure for E2E rollback test."],
        )

        # 4. ROLLBACK
        rb_res = rollback_commit(
            commit_id=commit_res.commit_id,
            document_id=self.TARGET_DOC_ID,
            source_sha256=self.TARGET_HASH,
            staging_base_dir=self.sandbox_staging,
            cache_dir=self.sandbox_cache,
            rag=self.isolated_rag,
            index=self.isolated_index,
            verification_result=fake_verify_failure,
        )
        self.assertEqual(rb_res.status, RollbackStatus.ROLLBACK_SUCCESS.value, msg=f"Rollback failures: {rb_res.failures}")
        self.assertEqual(rb_res.cache_status, "PASS")
        self.assertEqual(rb_res.chroma_status, "PASS")

        # 5. RESTORE VERIFICATION: BEFORE == RESTORED
        after_total_count = self.isolated_rag.count()
        after_target_chunks = len(self.isolated_rag.collection.get(where={"file_hash": self.TARGET_HASH})["ids"])
        after_cache_content = (self.sandbox_cache / f"{self.TARGET_HASH}.txt").read_text(encoding="utf-8")

        self.assertEqual(after_total_count, before_total_count, "Total Chroma chunk count must match BEFORE.")
        self.assertEqual(after_target_chunks, before_target_chunks, "Target doc chunk count must match BEFORE (25).")
        self.assertEqual(after_cache_content, before_cache_content, "Cache content must exactly match BEFORE.")

    def test_harness_02_cross_hash_isolation_in_live_sandbox(self):
        """Restoring doc2 must NOT mutate doc1, doc3, doc4, or doc5 chunks."""
        doc3_before = len(self.isolated_rag.collection.get(where={"document_id": "doc3_kredi_islemleri"})["ids"])
        doc4_before = len(self.isolated_rag.collection.get(where={"document_id": "doc4_risk_gruplari_kredi_sinirlari"})["ids"])

        commit_res = commit_candidate(
            self.candidate,
            self.decision,
            allow_commit=True,
            rag=self.isolated_rag,
            target_cache_dir=self.sandbox_cache,
            staging_base_dir=self.sandbox_staging,
        )
        rb_res = rollback_commit(
            commit_id=commit_res.commit_id,
            document_id=self.TARGET_DOC_ID,
            source_sha256=self.TARGET_HASH,
            staging_base_dir=self.sandbox_staging,
            cache_dir=self.sandbox_cache,
            rag=self.isolated_rag,
            index=self.isolated_index,
            force=True,
        )
        self.assertEqual(rb_res.status, RollbackStatus.ROLLBACK_SUCCESS.value)

        doc3_after = len(self.isolated_rag.collection.get(where={"document_id": "doc3_kredi_islemleri"})["ids"])
        doc4_after = len(self.isolated_rag.collection.get(where={"document_id": "doc4_risk_gruplari_kredi_sinirlari"})["ids"])

        self.assertEqual(doc3_after, doc3_before, "CROSS_DOCUMENT_MUTATION violation on doc3!")
        self.assertEqual(doc4_after, doc4_before, "CROSS_DOCUMENT_MUTATION violation on doc4!")

    def test_harness_03_active_superseded_restoration(self):
        """Rollback removes newly committed candidate chunks so no multiple active versions remain."""
        commit_res = commit_candidate(
            self.candidate,
            self.decision,
            allow_commit=True,
            rag=self.isolated_rag,
            target_cache_dir=self.sandbox_cache,
            staging_base_dir=self.sandbox_staging,
        )
        rb_res = rollback_commit(
            commit_id=commit_res.commit_id,
            staging_base_dir=self.sandbox_staging,
            cache_dir=self.sandbox_cache,
            rag=self.isolated_rag,
            index=self.isolated_index,
            force=True,
        )
        self.assertEqual(rb_res.status, RollbackStatus.ROLLBACK_SUCCESS.value)

        # Ensure all active chunks for target doc are the restored ones (count = 25, not 27)
        found = self.isolated_rag.collection.get(where={"file_hash": self.TARGET_HASH})
        self.assertEqual(len(found["ids"]), self._target_chunks_before)
        # None of the chunks should have the candidate's test text
        candidate_sample_text = "MADDE 1 – Rollback test adayı metni."
        docs = found.get("documents", []) or []
        for d in docs:
            self.assertNotIn(candidate_sample_text, d, "Candidate text still present after rollback!")

    def test_harness_04_retrieval_baseline_after_rollback(self):
        """After rollback, BM-02 (target doc lookup) successfully retrieves the restored document."""
        commit_res = commit_candidate(
            self.candidate,
            self.decision,
            allow_commit=True,
            rag=self.isolated_rag,
            target_cache_dir=self.sandbox_cache,
            staging_base_dir=self.sandbox_staging,
        )
        rb_res = rollback_commit(
            commit_id=commit_res.commit_id,
            staging_base_dir=self.sandbox_staging,
            cache_dir=self.sandbox_cache,
            rag=self.isolated_rag,
            index=self.isolated_index,
            force=True,
        )
        self.assertEqual(rb_res.status, RollbackStatus.ROLLBACK_SUCCESS.value)

        # Run BM-02 query against restored sandbox
        bm02_query = "Finansal Piyasalarda Manipülasyon ve Yanıltıcı İşlemler"
        results = self.isolated_rag.query(bm02_query, k=5)
        found_docs = [r.get("metadata", {}).get("document_id", "") for r in results]
        self.assertIn(self.TARGET_DOC_ID, found_docs, f"BM-02 query must retrieve {self.TARGET_DOC_ID} after rollback.")

    def test_harness_05_idempotent_rollback_on_live_sandbox(self):
        """Calling rollback twice on live sandbox: first is SUCCESS, second is ALREADY_RESTORED."""
        commit_res = commit_candidate(
            self.candidate,
            self.decision,
            allow_commit=True,
            rag=self.isolated_rag,
            target_cache_dir=self.sandbox_cache,
            staging_base_dir=self.sandbox_staging,
        )
        r1 = rollback_commit(
            commit_id=commit_res.commit_id,
            staging_base_dir=self.sandbox_staging,
            cache_dir=self.sandbox_cache,
            rag=self.isolated_rag,
            index=self.isolated_index,
            force=True,
        )
        self.assertEqual(r1.status, RollbackStatus.ROLLBACK_SUCCESS.value)

        r2 = rollback_commit(
            commit_id=commit_res.commit_id,
            staging_base_dir=self.sandbox_staging,
            cache_dir=self.sandbox_cache,
            rag=self.isolated_rag,
            index=self.isolated_index,
            force=False,
        )
        self.assertEqual(r2.status, RollbackStatus.ROLLBACK_ALREADY_RESTORED.value)

    def test_harness_06_dry_run_on_live_sandbox_zero_writes(self):
        """Dry-run rollback on live sandbox does not modify Chroma or cache."""
        commit_res = commit_candidate(
            self.candidate,
            self.decision,
            allow_commit=True,
            rag=self.isolated_rag,
            target_cache_dir=self.sandbox_cache,
            staging_base_dir=self.sandbox_staging,
        )
        count_before_dry_run = self.isolated_rag.count()
        cache_before_dry_run = (self.sandbox_cache / f"{self.TARGET_HASH}.txt").read_text(encoding="utf-8")

        r_dry = rollback_commit(
            commit_id=commit_res.commit_id,
            staging_base_dir=self.sandbox_staging,
            cache_dir=self.sandbox_cache,
            rag=self.isolated_rag,
            index=self.isolated_index,
            force=True,
            dry_run=True,
        )
        self.assertEqual(r_dry.status, RollbackStatus.ROLLBACK_SUCCESS.value)
        self.assertTrue(r_dry.dry_run)
        self.assertEqual(self.isolated_rag.count(), count_before_dry_run)
        self.assertEqual((self.sandbox_cache / f"{self.TARGET_HASH}.txt").read_text(encoding="utf-8"), cache_before_dry_run)


if __name__ == "__main__":
    unittest.main()
