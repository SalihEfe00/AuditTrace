# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mufettis.config import CHUNK_SCHEMA_VERSION, EMBEDDING_MODEL, DB_PATH, DATA_DIR
from mufettis.rag.engine import LocalRAG
from mufettis.repair.committer import (
    atomic_commit_source_cache,
    cleanup_same_hash_chroma,
    commit_candidate,
    stage_candidate,
    upsert_candidate_chroma,
)
from mufettis.repair.models import (
    CommitStatus,
    ComparisonStatus,
    ExtractionCandidate,
    FinalGateDecision,
)


class TestSafeCommit(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mufettis_commit_test_"))
        self.staging_dir = self.temp_dir / "staging"
        self.cache_dir = self.temp_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        self.sample_hash = "a" * 64
        self.sample_doc_id = "doc_test_12"

        self.sample_chunks = [
            {
                "chunk_no": 0,
                "text": "MADDE 1 - Bu test maddesidir.",
                "chunk_type": "article",
                "article_no": "1",
            },
            {
                "chunk_no": 1,
                "text": "MADDE 2 - Bu test kapsam maddesidir.",
                "chunk_type": "article",
                "article_no": "2",
            },
        ]

        self.valid_candidate = ExtractionCandidate(
            document_id=self.sample_doc_id,
            source_document_id=self.sample_doc_id,
            source_sha256=self.sample_hash,
            normalized_text="MADDE 1 - Bu test maddesidir.\nMADDE 2 - Bu test kapsam maddesidir.",
            raw_text="MADDE 1 - Bu test maddesidir.\nMADDE 2 - Bu test kapsam maddesidir.",
            legal_chunks=list(self.sample_chunks),
            status="SUCCESS",
            metadata={
                "title": "Test Yönetmeliği",
                "document_type": "Yönetmelik",
                "schema_version": CHUNK_SCHEMA_VERSION,
                "embedding_model": EMBEDDING_MODEL,
            },
            in_memory_only=True,
        )

        self.valid_decision = FinalGateDecision(
            faz11a_status=ComparisonStatus.READY_TO_COMMIT.value,
            faz11b_status=ComparisonStatus.READY_TO_COMMIT.value,
            final_status=ComparisonStatus.READY_TO_COMMIT.value,
            reasons=["All quality and retrieval gates passed"],
        )

        # Mock LocalRAG for isolated in-memory verification
        self.mock_rag = MagicMock()
        self.mock_rag.model_name = EMBEDDING_MODEL
        self.mock_rag.embed.return_value = [[0.1] * 384, [0.2] * 384]
        self.mock_collection = MagicMock()
        self.mock_rag.collection = self.mock_collection
        self.mock_collection.get.return_value = {"ids": [], "metadatas": []}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. valid candidate commit
    def test_01_valid_candidate_commit(self):
        res = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.SUCCESS.value)
        self.assertTrue(res.cache_commit)
        self.assertTrue(res.chroma_cleanup)
        self.assertTrue(res.chroma_upsert)
        self.assertEqual(res.new_chunk_count, 2)
        self.assertTrue((self.cache_dir / f"{self.sample_hash}.txt").exists())

    # 2. REJECT candidate commit blocked
    def test_02_reject_candidate_commit_blocked(self):
        reject_decision = FinalGateDecision(
            faz11a_status=ComparisonStatus.REJECT.value,
            faz11b_status=ComparisonStatus.READY_TO_COMMIT.value,
            final_status=ComparisonStatus.REJECT.value,
        )
        res = commit_candidate(
            self.valid_candidate,
            reject_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.ABORTED.value)
        self.assertFalse((self.cache_dir / f"{self.sample_hash}.txt").exists())

    # 3. REVIEW candidate commit blocked
    def test_03_review_candidate_commit_blocked(self):
        review_decision = FinalGateDecision(
            faz11a_status=ComparisonStatus.READY_TO_COMMIT.value,
            faz11b_status=ComparisonStatus.REVIEW.value,
            final_status=ComparisonStatus.REVIEW.value,
        )
        res = commit_candidate(
            self.valid_candidate,
            review_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.ABORTED.value)
        self.assertFalse((self.cache_dir / f"{self.sample_hash}.txt").exists())

    # 4. hash mismatch blocked
    def test_04_hash_mismatch_blocked(self):
        bad_cand = ExtractionCandidate(
            document_id=self.sample_doc_id,
            source_sha256="short_invalid_hash",
            normalized_text="Sample text",
            legal_chunks=list(self.sample_chunks),
            status="SUCCESS",
        )
        res = commit_candidate(
            bad_cand,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.ABORTED.value)
        self.assertTrue(any("source_sha256" in r for r in res.reasons))

    # 5. document identity mismatch blocked
    def test_05_document_identity_mismatch_blocked(self):
        bad_cand = ExtractionCandidate(
            document_id="",
            source_sha256=self.sample_hash,
            normalized_text="Sample text",
            legal_chunks=list(self.sample_chunks),
            status="SUCCESS",
        )
        res = commit_candidate(
            bad_cand,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.ABORTED.value)

    # 6. schema mismatch blocked
    def test_06_schema_mismatch_blocked(self):
        bad_cand = ExtractionCandidate(
            document_id=self.sample_doc_id,
            source_sha256=self.sample_hash,
            normalized_text="Sample text",
            legal_chunks=list(self.sample_chunks),
            status="SUCCESS",
            metadata={"schema_version": "9.9.9"},
        )
        res = commit_candidate(
            bad_cand,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.ABORTED.value)
        self.assertTrue(any("Schema version" in r for r in res.reasons))

    # 7. empty candidate blocked
    def test_07_empty_candidate_blocked(self):
        bad_cand = ExtractionCandidate(
            document_id=self.sample_doc_id,
            source_sha256=self.sample_hash,
            normalized_text="",
            legal_chunks=[],
            status="SUCCESS",
        )
        res = commit_candidate(
            bad_cand,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.ABORTED.value)

    # 8. invalid metadata blocked
    def test_08_invalid_metadata_blocked(self):
        bad_cand = ExtractionCandidate(
            document_id=self.sample_doc_id,
            source_sha256=self.sample_hash,
            normalized_text="Valid text",
            legal_chunks=[{"chunk_no": 0, "text": ""}],  # Empty chunk text
            status="SUCCESS",
        )
        res = commit_candidate(
            bad_cand,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.ABORTED.value)

    # 9. embedding config mismatch blocked
    def test_09_embedding_config_mismatch_blocked(self):
        bad_cand = ExtractionCandidate(
            document_id=self.sample_doc_id,
            source_sha256=self.sample_hash,
            normalized_text="Valid text",
            legal_chunks=list(self.sample_chunks),
            status="SUCCESS",
            metadata={"embedding_model": "incompatible_model_xyz"},
        )
        res = commit_candidate(
            bad_cand,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.ABORTED.value)
        self.assertTrue(any("Embedding model" in r for r in res.reasons))

    # 10. cache write success
    def test_10_cache_write_success(self):
        p = atomic_commit_source_cache(self.valid_candidate, self.cache_dir)
        self.assertTrue(p.exists())
        self.assertEqual(p.name, f"{self.sample_hash}.txt")
        self.assertEqual(p.read_text(encoding="utf-8"), self.valid_candidate.normalized_text)

    # 11. cache atomic replacement
    def test_11_cache_atomic_replacement(self):
        target = self.cache_dir / f"{self.sample_hash}.txt"
        target.write_text("Old corrupted cache text", encoding="utf-8")
        atomic_commit_source_cache(self.valid_candidate, self.cache_dir)
        self.assertEqual(target.read_text(encoding="utf-8"), self.valid_candidate.normalized_text)

    # 12. chroma same-hash cleanup
    def test_12_chroma_same_hash_cleanup(self):
        self.mock_collection.get.return_value = {
            "ids": ["ch1", "ch2"],
            "metadatas": [
                {"file_hash": self.sample_hash, "document_id": self.sample_doc_id},
                {"file_hash": self.sample_hash, "document_id": self.sample_doc_id},
            ],
        }
        cleaned = cleanup_same_hash_chroma(self.mock_rag, self.sample_doc_id, self.sample_hash)
        self.assertEqual(cleaned, 2)
        self.mock_collection.delete.assert_called_once_with(ids=["ch1", "ch2"])

    # 13. candidate upsert
    def test_13_candidate_upsert(self):
        count = upsert_candidate_chroma(self.mock_rag, self.valid_candidate)
        self.assertEqual(count, 2)
        self.mock_collection.upsert.assert_called_once()
        kwargs = self.mock_collection.upsert.call_args[1]
        self.assertEqual(len(kwargs["ids"]), 2)
        self.assertEqual(len(kwargs["documents"]), 2)

    # 14. other hash untouched
    def test_14_other_hash_untouched(self):
        other_hash = "b" * 64
        self.mock_collection.get.return_value = {
            "ids": ["ch_other"],
            "metadatas": [{"file_hash": other_hash, "document_id": self.sample_doc_id}],
        }
        cleaned = cleanup_same_hash_chroma(self.mock_rag, self.sample_doc_id, self.sample_hash)
        self.assertEqual(cleaned, 0)
        self.mock_collection.delete.assert_not_called()

    # 15. other document untouched
    def test_15_other_document_untouched(self):
        self.mock_collection.get.return_value = {
            "ids": ["ch_diff_doc"],
            "metadatas": [{"file_hash": self.sample_hash, "document_id": "other_doc_id"}],
        }
        cleaned = cleanup_same_hash_chroma(self.mock_rag, self.sample_doc_id, self.sample_hash)
        self.assertEqual(cleaned, 0)
        self.mock_collection.delete.assert_not_called()

    # 16. dry-run mutation zero
    def test_16_dry_run_mutation_zero(self):
        res = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=False,  # default safe mode
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertTrue(res.dry_run)
        self.assertEqual(res.status, CommitStatus.NO_OP.value)
        self.assertFalse((self.cache_dir / f"{self.sample_hash}.txt").exists())
        self.mock_collection.delete.assert_not_called()
        self.mock_collection.upsert.assert_not_called()

    # 17. explicit commit mutation
    def test_17_explicit_commit_mutation(self):
        res = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertFalse(res.dry_run)
        self.assertEqual(res.status, CommitStatus.SUCCESS.value)
        self.assertTrue((self.cache_dir / f"{self.sample_hash}.txt").exists())

    # 18. cache success / Chroma failure handled
    def test_18_cache_success_chroma_failure_handled(self):
        self.mock_collection.delete.side_effect = RuntimeError("Chroma connection error")
        self.mock_collection.get.return_value = {
            "ids": ["ch1"],
            "metadatas": [{"file_hash": self.sample_hash, "document_id": self.sample_doc_id}],
        }
        res = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.PARTIAL_FAILURE.value)
        self.assertTrue(res.cache_commit)
        self.assertFalse(res.chroma_cleanup)

    # 19. Chroma cleanup / upsert failure handled
    def test_19_chroma_cleanup_upsert_failure_handled(self):
        self.mock_collection.upsert.side_effect = RuntimeError("Chroma disk full")
        res = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.PARTIAL_FAILURE.value)
        self.assertTrue(res.cache_commit)
        self.assertTrue(res.chroma_cleanup)
        self.assertFalse(res.chroma_upsert)

    # 20. repeated same candidate commit deterministic (idempotent)
    def test_20_repeated_same_candidate_deterministic(self):
        # First commit
        res1 = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res1.status, CommitStatus.SUCCESS.value)

        # Mock collection now returning the 2 newly upserted active chunks
        self.mock_collection.get.return_value = {
            "ids": ["ch0", "ch1"],
            "metadatas": [
                {"file_hash": self.sample_hash, "document_id": self.sample_doc_id},
                {"file_hash": self.sample_hash, "document_id": self.sample_doc_id},
            ],
        }

        # Second commit
        res2 = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res2.status, CommitStatus.NO_OP.value)
        self.assertIn("İdempotent", res2.reasons[0])

    # 21. already superseded old data handled safely
    def test_21_already_superseded_old_data_handled_safely(self):
        self.mock_collection.get.return_value = {
            "ids": ["ch_old"],
            "metadatas": [
                {
                    "file_hash": self.sample_hash,
                    "document_id": self.sample_doc_id,
                    "status": "superseded",
                }
            ],
        }
        res = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        self.assertEqual(res.status, CommitStatus.SUCCESS.value)
        self.mock_collection.delete.assert_called_once_with(ids=["ch_old"])

    # 22. candidate isolation in staging
    def test_22_candidate_isolation_in_staging(self):
        stage_candidate(
            self.valid_candidate,
            self.valid_decision,
            self.staging_dir / "test_stage",
        )
        self.assertTrue((self.staging_dir / "test_stage" / "manifest.json").exists())
        self.assertTrue((self.staging_dir / "test_stage" / "candidate.json").exists())
        self.assertTrue((self.staging_dir / "test_stage" / "chunks.json").exists())
        self.assertTrue((self.staging_dir / "test_stage" / "cache.tmp").exists())

    # 23. commit result accuracy
    def test_23_commit_result_accuracy(self):
        res = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.mock_rag,
            target_cache_dir=self.cache_dir,
            staging_base_dir=self.staging_dir,
        )
        d = res.to_dict()
        self.assertEqual(d["status"], CommitStatus.SUCCESS.value)
        self.assertEqual(d["document_id"], self.sample_doc_id)
        self.assertEqual(d["source_sha256"], self.sample_hash)
        self.assertTrue(d["cache_commit"])
        self.assertTrue(d["chroma_cleanup"])
        self.assertTrue(d["chroma_upsert"])
        self.assertIsNotNone(d["manifest"])

    # 24. pre-commit manifest generated
    def test_24_pre_commit_manifest_generated(self):
        manifest = stage_candidate(
            self.valid_candidate,
            self.valid_decision,
            self.staging_dir / "manifest_test",
        )
        self.assertEqual(manifest.document_id, self.sample_doc_id)
        self.assertEqual(manifest.source_sha256, self.sample_hash)
        self.assertEqual(manifest.candidate_chunk_count, 2)
        self.assertEqual(manifest.final_decision, ComparisonStatus.READY_TO_COMMIT.value)


class TestRagLabCommitHarness(unittest.TestCase):
    """FAZ 12 Hardened Isolated Test Harness for RAG Lab & Safe Commit.

    Guarantees:
    1. Zero mutation on live data/rag_lab/chroma and data/rag_lab/source_cache.
    2. All commit tests execute strictly against temporary isolated copies.
    3. Cleanup and restoration guaranteed via try/finally for PASS, FAIL, EXCEPTION.
    4. Comprehensive failure injection testing:
       - successful commit
       - cache write failure
       - Chroma cleanup failure
       - candidate upsert failure
       - duplicate ID failure
       - partial failure
    5. Production paths remain 100% unmutated under all conditions.
    """

    def setUp(self):
        self.real_rag_lab_dir = DATA_DIR / "rag_lab"
        self.real_chroma_dir = self.real_rag_lab_dir / "chroma"
        self.real_cache_dir = self.real_rag_lab_dir / "source_cache"

        if not self.real_chroma_dir.exists() or not self.real_cache_dir.exists():
            self.skipTest("data/rag_lab test corpus bulunamadı; test atlanıyor.")

        # Snapshot real state before test
        real_rag = LocalRAG(db_path=self.real_chroma_dir, allow_download=False)
        self.real_chroma_count_before = real_rag.count()
        self.real_cache_files_before = {
            p.name: p.stat().st_size for p in self.real_cache_dir.glob("*.txt")
        }
        from mufettis.rag.store import active_store_file

        # Üretimin etkin parça deposu (SQLite ya da taşınmamış Chroma).
        self.prod_chroma_file = active_store_file(DB_PATH)
        self.prod_size_before = (
            self.prod_chroma_file.stat().st_size
            if self.prod_chroma_file.exists()
            else 0
        )

        # Create completely isolated sandbox copy
        self.sandbox_dir = Path(tempfile.mkdtemp(prefix="rag_lab_commit_sandbox_"))
        self.sandbox_chroma = self.sandbox_dir / "chroma"
        self.sandbox_cache = self.sandbox_dir / "source_cache"
        self.sandbox_staging = self.sandbox_dir / "staging"

        shutil.copytree(self.real_chroma_dir, self.sandbox_chroma)
        shutil.copytree(self.real_cache_dir, self.sandbox_cache)
        self.sandbox_staging.mkdir(parents=True, exist_ok=True)

        self.isolated_rag = LocalRAG(db_path=self.sandbox_chroma, allow_download=False)

        # Target document: doc2_finansal_manipulasyon (25 chunks in baseline)
        self.target_hash = "bf956ab51a0464f272e0e2c9237a83657ea63987f7925a092bb037d574fedefa"
        self.target_doc_id = "doc2_finansal_manipulasyon"

        self.candidate_chunks = [
            {
                "chunk_id": f"isolated_rep_{i}",
                "chunk_no": i,
                "text": f"MADDE {i+1} - Onarılmış izole RAG Lab metni fıkrası {i+1}.",
                "chunk_type": "article",
                "article_no": str(i + 1),
                "retrievable": True,
            }
            for i in range(2)
        ]

        self.valid_candidate = ExtractionCandidate(
            document_id=self.target_doc_id,
            source_document_id=self.target_doc_id,
            source_path=str(self.real_rag_lab_dir / "fixtures" / "02_finansal_manipulasyon.pdf"),
            source_sha256=self.target_hash,
            raw_text="MADDE 1 - Onarılmış izole RAG Lab metni fıkrası 1.\nMADDE 2 - Onarılmış izole RAG Lab metni fıkrası 2.",
            normalized_text="MADDE 1 - Onarılmış izole RAG Lab metni fıkrası 1.\nMADDE 2 - Onarılmış izole RAG Lab metni fıkrası 2.",
            legal_chunks=list(self.candidate_chunks),
            extraction_method="pdfplumber",
            fallback_used=False,
            ocr_used=False,
            status="SUCCESS",
            metadata={
                "title": "FİNANSAL PİYASALARDA MANİPÜLASYON VE YANILTICI İŞLEMLER HAKKINDA YÖNETMELİK",
                "document_type": "Yönetmelik",
                "schema_version": CHUNK_SCHEMA_VERSION,
                "embedding_model": EMBEDDING_MODEL,
            },
            in_memory_only=True,
        )

        self.valid_decision = FinalGateDecision(
            faz11a_status=ComparisonStatus.READY_TO_COMMIT.value,
            faz11b_status=ComparisonStatus.READY_TO_COMMIT.value,
            final_status=ComparisonStatus.READY_TO_COMMIT.value,
            reasons=["All gates READY_TO_COMMIT"],
        )

    def tearDown(self):
        try:
            # Delete temporary isolated sandbox
            if hasattr(self, "sandbox_dir") and self.sandbox_dir.exists():
                shutil.rmtree(self.sandbox_dir, ignore_errors=True)
        finally:
            # Rigorous integrity check on REAL data/rag_lab and production paths
            real_rag = LocalRAG(db_path=self.real_chroma_dir, allow_download=False)
            current_chroma_count = real_rag.count()
            self.assertEqual(
                current_chroma_count,
                self.real_chroma_count_before,
                f"GÜVENLİK İHLALİ: Canlı data/rag_lab/chroma mutasyona uğradı! (önce={self.real_chroma_count_before}, sonra={current_chroma_count})",
            )

            current_cache_files = {
                p.name: p.stat().st_size for p in self.real_cache_dir.glob("*.txt")
            }
            self.assertEqual(
                current_cache_files,
                self.real_cache_files_before,
                "GÜVENLİK İHLALİ: Canlı data/rag_lab/source_cache mutasyona uğradı!",
            )

            if self.prod_chroma_file.exists():
                current_prod_size = self.prod_chroma_file.stat().st_size
                self.assertEqual(
                    current_prod_size,
                    self.prod_size_before,
                    "GÜVENLİK İHLALİ: Üretim Chroma veri tabanı mutasyona uğradı!",
                )

    def test_harness_01_successful_commit_isolated(self):
        """Valid candidate commit runs strictly on isolated copy; real state untouched."""
        res = commit_candidate(
            self.valid_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.isolated_rag,
            target_cache_dir=self.sandbox_cache,
            staging_base_dir=self.sandbox_staging,
        )
        self.assertEqual(res.status, CommitStatus.SUCCESS.value)
        self.assertTrue(res.cache_commit)
        self.assertTrue(res.chroma_cleanup)
        self.assertTrue(res.chroma_upsert)

        # Isolated copy updated: 317 - 25 + 2 = 294
        self.assertEqual(self.isolated_rag.count(), self.real_chroma_count_before - 25 + 2)

    def test_harness_02_cache_write_failure_isolated(self):
        """Simulated source cache write failure aborts commit; 0 Chroma mutation."""
        with patch("mufettis.repair.committer.atomic_commit_source_cache", side_effect=IOError("Simulated disk error")):
            res = commit_candidate(
                self.valid_candidate,
                self.valid_decision,
                allow_commit=True,
                rag=self.isolated_rag,
                target_cache_dir=self.sandbox_cache,
                staging_base_dir=self.sandbox_staging,
            )
            self.assertEqual(res.status, CommitStatus.FAILED.value)
            self.assertFalse(res.cache_commit)
            self.assertFalse(res.chroma_cleanup)
            self.assertFalse(res.chroma_upsert)
            self.assertEqual(self.isolated_rag.count(), self.real_chroma_count_before)

    def test_harness_03_chroma_cleanup_failure_isolated(self):
        """Simulated Chroma cleanup failure returns PARTIAL_FAILURE; upsert skipped."""
        with patch("mufettis.repair.committer.cleanup_same_hash_chroma", side_effect=RuntimeError("Simulated cleanup crash")):
            res = commit_candidate(
                self.valid_candidate,
                self.valid_decision,
                allow_commit=True,
                rag=self.isolated_rag,
                target_cache_dir=self.sandbox_cache,
                staging_base_dir=self.sandbox_staging,
            )
            self.assertEqual(res.status, CommitStatus.PARTIAL_FAILURE.value)
            self.assertTrue(res.cache_commit)
            self.assertFalse(res.chroma_cleanup)
            self.assertFalse(res.chroma_upsert)

    def test_harness_04_candidate_upsert_failure_isolated(self):
        """Simulated Chroma upsert failure returns PARTIAL_FAILURE explicitly."""
        with patch("mufettis.repair.committer.upsert_candidate_chroma", side_effect=RuntimeError("Simulated upsert crash")):
            res = commit_candidate(
                self.valid_candidate,
                self.valid_decision,
                allow_commit=True,
                rag=self.isolated_rag,
                target_cache_dir=self.sandbox_cache,
                staging_base_dir=self.sandbox_staging,
            )
            self.assertEqual(res.status, CommitStatus.PARTIAL_FAILURE.value)
            self.assertTrue(res.cache_commit)
            self.assertTrue(res.chroma_cleanup)
            self.assertFalse(res.chroma_upsert)

    def test_harness_05_duplicate_id_failure_isolated(self):
        """Duplicate chunk IDs in candidate trigger Chroma error; handled as PARTIAL_FAILURE without unhandled crash."""
        dup_chunks = [
            {
                "chunk_id": "dup_0",
                "chunk_no": 0,
                "text": "Fıkra A",
                "chunk_type": "article",
            },
            {
                "chunk_id": "dup_1",
                "chunk_no": 0,  # Duplicate chunk_no triggers identical Chroma ID
                "text": "Fıkra B",
                "chunk_type": "article",
            },
        ]
        dup_candidate = ExtractionCandidate(
            document_id=self.target_doc_id,
            source_document_id=self.target_doc_id,
            source_path=str(self.real_rag_lab_dir / "fixtures" / "02_finansal_manipulasyon.pdf"),
            source_sha256=self.target_hash,
            raw_text="Fıkra A\nFıkra B",
            normalized_text="Fıkra A\nFıkra B",
            legal_chunks=dup_chunks,
            extraction_method="pdfplumber",
            fallback_used=False,
            ocr_used=False,
            status="SUCCESS",
            metadata={
                "schema_version": CHUNK_SCHEMA_VERSION,
                "embedding_model": EMBEDDING_MODEL,
            },
            in_memory_only=True,
        )
        res = commit_candidate(
            dup_candidate,
            self.valid_decision,
            allow_commit=True,
            rag=self.isolated_rag,
            target_cache_dir=self.sandbox_cache,
            staging_base_dir=self.sandbox_staging,
        )
        self.assertEqual(res.status, CommitStatus.PARTIAL_FAILURE.value)
        self.assertTrue(res.cache_commit)
        self.assertTrue(res.chroma_cleanup)
        self.assertFalse(res.chroma_upsert)
        self.assertIn("upsert", res.error.lower())

    def test_harness_06_partial_failure_explicit_result_no_masking(self):
        """Partial failure is explicitly returned and never masked as SUCCESS."""
        with patch.object(self.isolated_rag.collection, "upsert", side_effect=RuntimeError("Chroma upsert failed")):
            res = commit_candidate(
                self.valid_candidate,
                self.valid_decision,
                allow_commit=True,
                rag=self.isolated_rag,
                target_cache_dir=self.sandbox_cache,
                staging_base_dir=self.sandbox_staging,
            )
            self.assertNotEqual(res.status, CommitStatus.SUCCESS.value)
            self.assertEqual(res.status, CommitStatus.PARTIAL_FAILURE.value)
            self.assertIsNotNone(res.error)

    def test_harness_07_unhandled_exception_in_caller_does_not_corrupt_real_state(self):
        """Even if caller crashes with an unexpected unhandled exception, real state is completely preserved."""
        exception_caught = False
        try:
            # Perform operations on isolated sandbox
            commit_candidate(
                self.valid_candidate,
                self.valid_decision,
                allow_commit=True,
                rag=self.isolated_rag,
                target_cache_dir=self.sandbox_cache,
                staging_base_dir=self.sandbox_staging,
            )
            # Simulate unexpected crash in caller code
            raise RuntimeError("Caller unhandled catastrophic exception")
        except RuntimeError as exc:
            if str(exc) == "Caller unhandled catastrophic exception":
                exception_caught = True

        self.assertTrue(exception_caught)
        # tearDown will execute after this and verify real_rag_lab count is still exactly 317!


if __name__ == "__main__":
    unittest.main()
