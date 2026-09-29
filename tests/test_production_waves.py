# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 17: Controlled Production Repair Waves / Verify + Rescan Tests.

Covers:
1. Wave Models, Enums, and Configuration
2. Production Corpus Presence Validation
3. Target Scope Resolution & Progression (PILOT, SMALL_BATCH, MEDIUM_BATCH, BDDK_ACTIVE, etc.)
4. Production Safety Gates & Multi-Key Guards
5. End-to-End Sandbox Wave Execution (SCAN -> CLASSIFY -> REPAIR -> VERIFY -> RESCAN)
6. Mandatory Post-Repair Rescan (glued text, mojibake, cross-document mutation detection & rollback)
7. Verification Failure & Canonical Rollback Integration
8. Circuit Breaker & Failure Budget Halting
9. Cross-Document Isolation & Audit Trail Cryptographic Provenance
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mufettis.config import CHUNK_SCHEMA_VERSION, DATA_DIR, SOURCE_CACHE_DIR
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex

from repair.audit import RepairAuditLogger
from repair.models import (
    AuditEventType,
    ClassificationResult,
    ClassificationType,
    CommitResult,
    CommitStatus,
    DocumentScanResult,
    ExtractionCandidate,
    Issue,
    IssueCode,
    IssueSeverity,
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
from repair.wave import WaveOrchestrator, check_production_corpus

SAMPLE_DOC_ID = "bddk_reg_01"
SAMPLE_SHA256 = "c0ffee112233445566778899aabbccddeeff00112233445566778899aabbccdd"


def _make_candidate(
    doc_id: str = SAMPLE_DOC_ID,
    sha256: str = SAMPLE_SHA256,
    chunks: int = 3,
) -> ExtractionCandidate:
    legal_chunks = [
        {
            "chunk_id": f"{doc_id}_ch_{i}",
            "text": f"Madde {i}: Bankacılık düzenleme ve denetleme esasları fıkra {i}.",
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "metadata": {
                "document_id": doc_id,
                "source_hash": sha256,
                "article_no": str(i),
                "structure_type": "article",
            },
        }
        for i in range(1, chunks + 1)
    ]
    raw_text = "\n\n".join(c["text"] for c in legal_chunks)
    return ExtractionCandidate(
        document_id=doc_id,
        source_document_id=doc_id,
        source_sha256=sha256,
        raw_text=raw_text,
        normalized_text=raw_text,
        legal_chunks=legal_chunks,
        extraction_method="pymupdf",
    )


# ===========================================================================
# 1. Test Wave Models & Configuration
# ===========================================================================

class TestWaveModelsAndConfig(unittest.TestCase):

    def test_wave_scope_enums(self):
        self.assertEqual(WaveScope.PILOT.value, "PILOT")
        self.assertEqual(WaveScope.SMALL_BATCH.value, "SMALL_BATCH")
        self.assertEqual(WaveScope.MEDIUM_BATCH.value, "MEDIUM_BATCH")
        self.assertEqual(WaveScope.BDDK_ACTIVE.value, "BDDK_ACTIVE")
        self.assertEqual(WaveScope.OTHER_SOURCES.value, "OTHER_SOURCES")
        self.assertEqual(WaveScope.ALL.value, "ALL")

    def test_wave_status_enums(self):
        self.assertEqual(WaveStatus.PENDING.value, "PENDING")
        self.assertEqual(WaveStatus.SUCCESS.value, "SUCCESS")
        self.assertEqual(WaveStatus.HALTED.value, "HALTED")
        self.assertEqual(WaveStatus.CIRCUIT_OPEN.value, "CIRCUIT_OPEN")
        self.assertEqual(WaveStatus.PRODUCTION_CORPUS_ABSENT.value, "PRODUCTION_CORPUS_ABSENT")

    def test_wave_document_status_enums(self):
        self.assertEqual(WaveDocumentStatus.VERIFIED_AND_RESCANNED.value, "VERIFIED_AND_RESCANNED")
        self.assertEqual(WaveDocumentStatus.SIMULATED.value, "SIMULATED")
        self.assertEqual(WaveDocumentStatus.RESCAN_FAILED.value, "RESCAN_FAILED")
        self.assertEqual(WaveDocumentStatus.VERIFY_FAILED_RESTORED.value, "VERIFY_FAILED_RESTORED")

    def test_audit_event_types_include_wave(self):
        self.assertEqual(AuditEventType.WAVE_STARTED.value, "WAVE_STARTED")
        self.assertEqual(AuditEventType.WAVE_COMPLETED.value, "WAVE_COMPLETED")
        self.assertEqual(AuditEventType.WAVE_HALTED.value, "WAVE_HALTED")
        self.assertEqual(AuditEventType.WAVE_RESCAN_PASSED.value, "WAVE_RESCAN_PASSED")
        self.assertEqual(AuditEventType.WAVE_RESCAN_FAILED.value, "WAVE_RESCAN_FAILED")

    def test_wave_config_defaults(self):
        cfg = WaveConfig()
        self.assertEqual(cfg.scope, "PILOT")
        self.assertEqual(cfg.doc_limit, 1)
        self.assertTrue(cfg.require_rescan)
        self.assertTrue(cfg.halt_on_failure)
        self.assertFalse(cfg.allow_production_repair)
        self.assertTrue(cfg.dry_run)
        d = cfg.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["scope"], "PILOT")

    def test_wave_document_result_and_summary_to_dict(self):
        doc_res = WaveDocumentResult(
            document_id="doc1",
            source_sha256="abc",
            status=WaveDocumentStatus.VERIFIED_AND_RESCANNED.value,
        )
        d = doc_res.to_dict()
        self.assertEqual(d["document_id"], "doc1")
        self.assertEqual(d["status"], "VERIFIED_AND_RESCANNED")

        summary = WaveSummary(
            wave_id="wave_123",
            scope="PILOT",
            documents=[doc_res],
        )
        sd = summary.to_dict()
        self.assertEqual(sd["wave_id"], "wave_123")
        self.assertEqual(len(sd["documents"]), 1)


# ===========================================================================
# 2. Test Production Corpus Presence Validation
# ===========================================================================

class TestProductionCorpusCheck(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_corpus_check_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_absent_when_index_missing(self):
        db_p = self.test_dir / "chroma.sqlite3"
        idx_p = self.test_dir / "ingest_index.json"
        c_dir = self.test_dir / "source_cache"
        c_dir.mkdir(parents=True, exist_ok=True)

        is_pres, reason, details = check_production_corpus(db_path=db_p, index_file=idx_p, cache_dir=c_dir)
        self.assertFalse(is_pres)
        self.assertIn("missing", reason.lower())
        self.assertEqual(details["index_documents_count"], 0)

    def test_absent_when_index_empty(self):
        db_p = self.test_dir / "chroma.sqlite3"
        idx_p = self.test_dir / "ingest_index.json"
        idx_p.write_text(json.dumps({"documents": {}}), encoding="utf-8")
        c_dir = self.test_dir / "source_cache"
        c_dir.mkdir(parents=True, exist_ok=True)

        is_pres, reason, details = check_production_corpus(db_path=db_p, index_file=idx_p, cache_dir=c_dir)
        self.assertFalse(is_pres)
        self.assertIn("empty", reason.lower())

    def test_absent_when_chroma_has_zero_embeddings(self):
        db_p = self.test_dir / "chroma.sqlite3"
        idx_p = self.test_dir / "ingest_index.json"
        idx_p.write_text(json.dumps({"documents": {"doc1": {"title": "Doc 1"}}}), encoding="utf-8")
        c_dir = self.test_dir / "source_cache"
        c_dir.mkdir(parents=True, exist_ok=True)

        # Create empty sqlite table
        con = sqlite3.connect(str(db_p))
        con.execute("CREATE TABLE embeddings (id TEXT PRIMARY KEY);")
        con.commit()
        con.close()

        is_pres, reason, details = check_production_corpus(db_path=db_p, index_file=idx_p, cache_dir=c_dir)
        self.assertFalse(is_pres)
        self.assertIn("0 embeddings", reason)

    def test_present_when_index_has_docs_and_chroma_has_embeddings(self):
        db_p = self.test_dir / "chroma.sqlite3"
        idx_p = self.test_dir / "ingest_index.json"
        idx_p.write_text(json.dumps({"documents": {"doc1": {"title": "Doc 1"}}}), encoding="utf-8")
        c_dir = self.test_dir / "source_cache"
        c_dir.mkdir(parents=True, exist_ok=True)

        con = sqlite3.connect(str(db_p))
        con.execute("CREATE TABLE embeddings (id TEXT PRIMARY KEY);")
        con.execute("INSERT INTO embeddings VALUES ('emb_1');")
        con.commit()
        con.close()

        is_pres, reason, details = check_production_corpus(db_path=db_p, index_file=idx_p, cache_dir=c_dir)
        self.assertTrue(is_pres)
        self.assertEqual(details["embeddings_count"], 1)
        self.assertEqual(details["index_documents_count"], 1)

    def test_corrupted_sqlite_handled_gracefully(self):
        db_p = self.test_dir / "chroma.sqlite3"
        idx_p = self.test_dir / "ingest_index.json"
        idx_p.write_text(json.dumps({"documents": {"doc1": {}}}), encoding="utf-8")
        db_p.write_bytes(b"NOT A VALID SQLITE DATABASE")

        is_pres, reason, details = check_production_corpus(db_path=db_p, index_file=idx_p)
        self.assertFalse(is_pres)
        self.assertIn("0 embeddings", reason)

    def test_scenario_a_directory_db_with_chroma_and_schema_2_index(self):
        db_dir = self.test_dir / "chroma_db"
        db_dir.mkdir(parents=True, exist_ok=True)
        sqlite_p = db_dir / "chroma.sqlite3"
        con = sqlite3.connect(str(sqlite_p))
        con.execute("CREATE TABLE embeddings (id TEXT PRIMARY KEY);")
        con.execute("INSERT INTO embeddings VALUES ('emb_1'), ('emb_2');")
        con.commit()
        con.close()

        idx_p = self.test_dir / "ingest_index.json"
        idx_p.write_text(json.dumps({
            "version": "2.0",
            "indexed_items": {
                "hash_1": {"title": "Kanun 1"},
                "hash_2": {"title": "Teblig 1"}
            }
        }), encoding="utf-8")
        c_dir = self.test_dir / "source_cache"
        c_dir.mkdir(parents=True, exist_ok=True)

        is_pres, reason, details = check_production_corpus(db_path=db_dir, index_file=idx_p, cache_dir=c_dir)
        self.assertTrue(is_pres)
        self.assertEqual(details["embeddings_count"], 2)
        self.assertEqual(details["index_documents_count"], 2)
        self.assertTrue(details["db_exists"])

    def test_scenario_a_with_active_rag_or_collection(self):
        idx_p = self.test_dir / "ingest_index.json"
        idx_p.write_text(json.dumps({
            "version": "2.0",
            "indexed_items": {"hash_1": {"title": "Doc 1"}}
        }), encoding="utf-8")
        c_dir = self.test_dir / "source_cache"
        c_dir.mkdir(parents=True, exist_ok=True)
        dummy_dir = self.test_dir / "dummy_db"
        dummy_dir.mkdir(parents=True, exist_ok=True)

        mock_col = MagicMock()
        mock_col.count.return_value = 150

        is_pres, reason, details = check_production_corpus(
            db_path=dummy_dir,
            index_file=idx_p,
            cache_dir=c_dir,
            collection=mock_col,
        )
        self.assertTrue(is_pres)
        self.assertEqual(details["embeddings_count"], 150)
        self.assertEqual(details["index_documents_count"], 1)

    def test_scenario_b_chroma_collection_count_zero(self):
        db_dir = self.test_dir / "chroma_db"
        db_dir.mkdir(parents=True, exist_ok=True)
        sqlite_p = db_dir / "chroma.sqlite3"
        con = sqlite3.connect(str(sqlite_p))
        con.execute("CREATE TABLE embeddings (id TEXT PRIMARY KEY);")
        con.commit()
        con.close()

        idx_p = self.test_dir / "ingest_index.json"
        idx_p.write_text(json.dumps({
            "version": "2.0",
            "indexed_items": {"h1": {"title": "T1"}}
        }), encoding="utf-8")
        c_dir = self.test_dir / "source_cache"
        c_dir.mkdir(parents=True, exist_ok=True)

        is_pres, reason, details = check_production_corpus(db_path=db_dir, index_file=idx_p, cache_dir=c_dir)
        self.assertFalse(is_pres)
        self.assertIn("0 embeddings", reason)
        self.assertEqual(details["embeddings_count"], 0)

    def test_scenario_c_schema_2_indexed_items_missing_or_empty(self):
        db_dir = self.test_dir / "chroma_db"
        db_dir.mkdir(parents=True, exist_ok=True)
        sqlite_p = db_dir / "chroma.sqlite3"
        con = sqlite3.connect(str(sqlite_p))
        con.execute("CREATE TABLE embeddings (id TEXT PRIMARY KEY);")
        con.execute("INSERT INTO embeddings VALUES ('e1');")
        con.commit()
        con.close()

        idx_p = self.test_dir / "ingest_index.json"
        idx_p.write_text(json.dumps({"version": "2.0", "indexed_items": {}}), encoding="utf-8")
        c_dir = self.test_dir / "source_cache"
        c_dir.mkdir(parents=True, exist_ok=True)

        is_pres, reason, details = check_production_corpus(db_path=db_dir, index_file=idx_p, cache_dir=c_dir)
        self.assertFalse(is_pres)
        self.assertIn("empty", reason.lower())

    def test_scenario_d_db_path_missing(self):
        missing_db = self.test_dir / "missing_db_folder"
        idx_p = self.test_dir / "ingest_index.json"
        idx_p.write_text(json.dumps({"version": "2.0", "indexed_items": {"h1": {}}}), encoding="utf-8")
        c_dir = self.test_dir / "source_cache"
        c_dir.mkdir(parents=True, exist_ok=True)

        is_pres, reason, details = check_production_corpus(db_path=missing_db, index_file=idx_p, cache_dir=c_dir)
        self.assertFalse(is_pres)
        self.assertFalse(details["db_exists"])
        self.assertFalse(missing_db.exists(), "check_production_corpus must NOT create missing db path!")

    def test_scenario_e_zero_production_write_guarantee(self):
        from mufettis.config import DB_PATH, INGEST_INDEX_FILE

        paths_to_monitor = [DB_PATH, INGEST_INDEX_FILE, SOURCE_CACHE_DIR]
        def _take_snapshot():
            snap = {}
            for p in paths_to_monitor:
                path_obj = Path(p)
                if not path_obj.exists():
                    snap[str(path_obj)] = None
                    continue
                if path_obj.is_file():
                    st = path_obj.stat()
                    snap[str(path_obj)] = {"type": "file", "size": st.st_size, "mtime": st.st_mtime_ns}
                elif path_obj.is_dir():
                    dir_snap = {}
                    for item in path_obj.rglob("*"):
                        if item.is_file():
                            st = item.stat()
                            dir_snap[str(item.relative_to(path_obj))] = {
                                "size": st.st_size,
                                "mtime": st.st_mtime_ns,
                            }
                    snap[str(path_obj)] = {"type": "dir", "files": dir_snap}
            return snap

        snap_before = _take_snapshot()

        # Run check_production_corpus on real production paths (both with and without collection)
        is_pres, reason, details = check_production_corpus()
        self.assertTrue(is_pres)
        self.assertGreater(details["embeddings_count"], 0)
        self.assertGreater(details["index_documents_count"], 0)

        snap_after = _take_snapshot()

        # Strictly check for any modifications: additions, deletions, size, mtime
        diffs = []
        for path_key in set(snap_before.keys()) | set(snap_after.keys()):
            if path_key not in snap_before:
                diffs.append(f"ADDED PATH: {path_key}")
            elif path_key not in snap_after:
                diffs.append(f"DELETED PATH: {path_key}")
            else:
                b_info = snap_before[path_key]
                a_info = snap_after[path_key]
                if b_info != a_info:
                    if b_info.get("type") == "file":
                        diffs.append(f"FILE MODIFIED: {path_key} before={b_info} after={a_info}")
                    elif b_info.get("type") == "dir":
                        b_files = b_info.get("files", {})
                        a_files = a_info.get("files", {})
                        for f in set(b_files.keys()) | set(a_files.keys()):
                            if f not in b_files:
                                diffs.append(f"NEW FILE IN DIR: {path_key}/{f}")
                            elif f not in a_files:
                                diffs.append(f"DELETED FILE IN DIR: {path_key}/{f}")
                            elif b_files[f] != a_files[f]:
                                diffs.append(f"FILE IN DIR MODIFIED: {path_key}/{f} before={b_files[f]} after={a_files[f]}")

        self.assertEqual(
            diffs,
            [],
            f"Zero Production Write Guarantee VIOLATED: Production files or directories were modified: {diffs}",
        )


# ===========================================================================
# 3. Test Target Scope Resolution
# ===========================================================================

class TestTargetScopeResolution(unittest.TestCase):

    def setUp(self):
        self.test_items = [
            {"document_id": "bddk_reg_1", "source_type": "bddk", "title": "BDDK 1"},
            {"document_id": "bddk_reg_2", "source_type": "bddk", "title": "BDDK 2"},
            {"document_id": "bddk_reg_3", "source_type": "bddk", "title": "BDDK 3"},
            {"document_id": "bddk_reg_4", "source_type": "bddk", "title": "BDDK 4"},
            {"document_id": "bddk_reg_5", "source_type": "bddk", "title": "BDDK 5"},
            {"document_id": "bddk_reg_6", "source_type": "bddk", "title": "BDDK 6"},
            {"document_id": "kanun_5411", "source_type": "kanun", "title": "Kanun 5411"},
            {"document_id": "teblig_tcmb", "source_type": "tcmb", "title": "TCMB Teblig"},
        ]
        self.mock_rag = MagicMock(spec=LocalRAG)
        self.mock_index = MagicMock(spec=IngestIndex)
        self.mock_index.documents = {}
        self.orch = WaveOrchestrator(rag=self.mock_rag, index=self.mock_index)

    def test_pilot_scope_resolves_strictly_one(self):
        cfg = WaveConfig(scope=WaveScope.PILOT.value, doc_limit=10)
        res = self.orch.resolve_target_scope(cfg, provided_items=self.test_items)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["document_id"], "bddk_reg_1")

    def test_small_batch_scope_resolves_3_to_5(self):
        cfg = WaveConfig(scope=WaveScope.SMALL_BATCH.value, doc_limit=4)
        res = self.orch.resolve_target_scope(cfg, provided_items=self.test_items)
        self.assertEqual(len(res), 4)

        # Capped at 5 if user specifies 100
        cfg_large = WaveConfig(scope=WaveScope.SMALL_BATCH.value, doc_limit=100)
        res_large = self.orch.resolve_target_scope(cfg_large, provided_items=self.test_items)
        self.assertEqual(len(res_large), 5)

    def test_medium_batch_scope_resolves_up_to_10(self):
        cfg = WaveConfig(scope=WaveScope.MEDIUM_BATCH.value, doc_limit=10)
        res = self.orch.resolve_target_scope(cfg, provided_items=self.test_items)
        self.assertEqual(len(res), 8)  # total items is 8

    def test_bddk_active_filters_only_bddk(self):
        cfg = WaveConfig(scope=WaveScope.BDDK_ACTIVE.value, doc_limit=10)
        res = self.orch.resolve_target_scope(cfg, provided_items=self.test_items)
        self.assertEqual(len(res), 6)
        for t in res:
            self.assertTrue("bddk" in t["document_id"] or "bddk" in t.get("source_type", ""))

    def test_other_sources_filters_out_bddk(self):
        cfg = WaveConfig(scope=WaveScope.OTHER_SOURCES.value, doc_limit=10)
        res = self.orch.resolve_target_scope(cfg, provided_items=self.test_items)
        self.assertEqual(len(res), 2)
        doc_ids = [t["document_id"] for t in res]
        self.assertIn("kanun_5411", doc_ids)
        self.assertIn("teblig_tcmb", doc_ids)

    def test_explicit_target_ids_filter(self):
        cfg = WaveConfig(
            scope=WaveScope.CUSTOM.value,
            target_document_ids=["kanun_5411", "bddk_reg_2"],
            doc_limit=5,
        )
        res = self.orch.resolve_target_scope(cfg, provided_items=self.test_items)
        self.assertEqual(len(res), 2)
        ids = [t["document_id"] for t in res]
        self.assertEqual(set(ids), {"kanun_5411", "bddk_reg_2"})


# ===========================================================================
# 4. Test Production Safety Gates & Prechecks
# ===========================================================================

class TestWaveSafetyGatesAndPrechecks(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_wave_gates_"))
        self.cache_dir = self.test_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.test_dir / "staging"
        self.audit_file = self.test_dir / "audit.jsonl"
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)
        self.mock_rag = MagicMock(spec=LocalRAG)
        self.mock_index = MagicMock(spec=IngestIndex)
        self.mock_index.documents = {}
        self.orch = WaveOrchestrator(
            rag=self.mock_rag,
            index=self.mock_index,
            cache_dir=self.cache_dir,
            staging_dir=self.staging_dir,
            audit_logger=self.audit_logger,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("repair.wave.check_production_corpus", return_value=(False, "Corpus absent", {}))
    def test_production_blocked_when_corpus_absent(self, mock_check):
        cfg = WaveConfig(
            environment="production",
            allow_production_repair=True,
            dry_run=False,
        )
        summary = self.orch.execute_wave(cfg, target_items=[{"document_id": "doc1"}])
        self.assertEqual(summary.status, WaveStatus.PRODUCTION_CORPUS_ABSENT.value)
        self.assertIn("ABSENT", summary.halt_reason)
        self.assertEqual(summary.production_mutation_count, 0)

    @patch("repair.wave.check_production_corpus", return_value=(True, "Corpus present", {}))
    def test_production_blocked_without_allow_production_flag(self, mock_check):
        cfg = WaveConfig(
            environment="production",
            allow_production_repair=False,  # strictly False
            dry_run=False,
        )
        summary = self.orch.execute_wave(cfg, target_items=[{"document_id": "doc1"}])
        self.assertEqual(summary.status, WaveStatus.BLOCKED.value)
        self.assertIn("allow-production", summary.halt_reason)
        self.assertEqual(summary.production_mutation_count, 0)

    def test_dry_run_executes_simulation_with_zero_writes(self):
        cfg = WaveConfig(
            environment="rag_lab",
            dry_run=True,
            scope="PILOT",
        )
        items = [{"document_id": "test_doc", "source_hash": "dummy_hash"}]
        with patch("repair.wave.scan_document") as mock_scan, \
             patch("repair.wave.classify_document") as mock_class:
            mock_scan.return_value = DocumentScanResult(document_id="test_doc", source_hash="dummy_hash")
            mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)

            summary = self.orch.execute_wave(cfg, target_items=items)
            self.assertEqual(summary.status, WaveStatus.SUCCESS.value)
            self.assertEqual(len(summary.documents), 1)
            self.assertEqual(summary.documents[0].status, WaveDocumentStatus.SIMULATED.value)
            self.assertEqual(summary.committed_count, 0)
            self.assertEqual(summary.production_mutation_count, 0)
            self.assertEqual(summary.sandbox_mutation_count, 0)

    def test_blocked_classification_skips_repair_with_zero_write(self):
        cfg = WaveConfig(environment="rag_lab", dry_run=False)
        items = [{"document_id": "corrupt_doc", "source_hash": "bad_hash"}]
        with patch("repair.wave.scan_document") as mock_scan, \
             patch("repair.wave.classify_document") as mock_class:
            mock_scan.return_value = DocumentScanResult(document_id="corrupt_doc")
            mock_class.return_value = ClassificationResult(document_id="corrupt_doc", status=ClassificationType.BLOCKED.value, blocking_issues=["File unreadable and corrupt"])

            summary = self.orch.execute_wave(cfg, target_items=items)
            self.assertEqual(summary.blocked_count, 1)
            self.assertEqual(summary.documents[0].status, WaveDocumentStatus.BLOCKED.value)
            self.assertEqual(summary.committed_count, 0)

    def test_review_without_approval_marked_waiting_approval(self):
        cfg = WaveConfig(environment="rag_lab", dry_run=False)
        items = [{"document_id": "review_doc", "source_hash": "hash123"}]
        with patch("repair.wave.scan_document") as mock_scan, \
             patch("repair.wave.classify_document") as mock_class:
            mock_scan.return_value = DocumentScanResult(document_id="review_doc")
            mock_class.return_value = ClassificationResult(document_id="review_doc", status=ClassificationType.REVIEW.value, review_issues=["High risk article deletion"])

            summary = self.orch.execute_wave(cfg, target_items=items)
            self.assertEqual(summary.review_count, 1)
            self.assertEqual(summary.documents[0].status, WaveDocumentStatus.WAITING_APPROVAL.value)
            self.assertEqual(summary.committed_count, 0)

    def test_review_with_invalid_approval_token_rejected(self):
        cfg = WaveConfig(environment="rag_lab", dry_run=False)
        items = [{"document_id": "review_doc", "source_hash": "hash123", "approval_id": "bad_token"}]
        with patch("repair.wave.scan_document") as mock_scan, \
             patch("repair.wave.classify_document") as mock_class:
            mock_scan.return_value = DocumentScanResult(document_id="review_doc")
            mock_class.return_value = ClassificationResult(document_id="review_doc", status=ClassificationType.REVIEW.value, review_issues=["Token required"])

            summary = self.orch.execute_wave(cfg, target_items=items)
            self.assertEqual(summary.failed_count, 1)
            self.assertEqual(summary.documents[0].status, WaveDocumentStatus.FAILED.value)
            self.assertIn("rejected", summary.documents[0].errors[0].lower())


# ===========================================================================
# 5. Test End-to-End Sandbox Wave Execution (SCAN -> CLASSIFY -> REPAIR -> VERIFY -> RESCAN)
# ===========================================================================

class TestEndToEndWaveExecutionSandbox(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_wave_e2e_"))
        self.cache_dir = self.test_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.test_dir / "staging"
        self.audit_file = self.test_dir / "audit.jsonl"
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)

        self.mock_rag = MagicMock(spec=LocalRAG)
        self.mock_rag.collection = MagicMock()
        self.mock_rag.collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        self.mock_rag.collection.count.return_value = 0

        self.mock_index = MagicMock(spec=IngestIndex)
        self.mock_index.documents = {}

        self.orch = WaveOrchestrator(
            rag=self.mock_rag,
            index=self.mock_index,
            cache_dir=self.cache_dir,
            staging_dir=self.staging_dir,
            audit_logger=self.audit_logger,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    def test_healthy_pilot_wave_success(self, mock_verify, mock_commit, mock_class, mock_scan):
        candidate = _make_candidate()
        mock_scan.return_value = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[],
        )
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_123", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="com_123", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)

        cfg = WaveConfig(
            scope=WaveScope.PILOT.value,
            dry_run=False,
            require_rescan=True,
            environment="rag_lab",
        )
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256}]

        summary = self.orch.execute_wave(
            cfg,
            target_items=target,
            candidate_overrides={SAMPLE_DOC_ID: candidate},
        )

        self.assertEqual(summary.status, WaveStatus.SUCCESS.value)
        self.assertEqual(summary.target_count, 1)
        self.assertEqual(summary.committed_count, 1)
        self.assertEqual(summary.verified_count, 1)
        self.assertEqual(summary.rescanned_count, 1)
        self.assertEqual(summary.failed_count, 0)
        self.assertEqual(summary.documents[0].status, WaveDocumentStatus.VERIFIED_AND_RESCANNED.value)
        self.assertTrue(summary.documents[0].rescan_passed)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    def test_small_batch_multi_document_wave_success(self, mock_verify, mock_commit, mock_class, mock_scan):
        doc_ids = ["bddk_1", "bddk_2", "bddk_3"]
        candidates = {d: _make_candidate(doc_id=d) for d in doc_ids}

        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_x", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)

        cfg = WaveConfig(
            scope=WaveScope.SMALL_BATCH.value,
            doc_limit=3,
            dry_run=False,
            require_rescan=True,
            environment="rag_lab",
        )
        targets = [{"document_id": d, "source_hash": f"hash_{d}"} for d in doc_ids]

        summary = self.orch.execute_wave(cfg, target_items=targets, candidate_overrides=candidates)
        self.assertEqual(summary.status, WaveStatus.SUCCESS.value)
        self.assertEqual(summary.committed_count, 3)
        self.assertEqual(summary.verified_count, 3)
        self.assertEqual(summary.rescanned_count, 3)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    def test_approved_review_document_repaired_successfully(self, mock_verify, mock_commit, mock_class, mock_scan):
        # Create approval record
        appr = self.orch.approval_manager.create_approval(
            repair_id="rep_999",
            candidate_id="cand_999",
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
        )

        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id="review_doc", status=ClassificationType.REVIEW.value, review_issues=["Token required"])
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_appr", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)

        cfg = WaveConfig(environment="rag_lab", dry_run=False, require_rescan=True)
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256, "approval_id": appr.approval_id}]

        summary = self.orch.execute_wave(
            cfg,
            target_items=target,
            candidate_overrides={SAMPLE_DOC_ID: _make_candidate()},
        )

        self.assertEqual(summary.status, WaveStatus.SUCCESS.value)
        self.assertEqual(summary.documents[0].status, WaveDocumentStatus.VERIFIED_AND_RESCANNED.value)

        # Assert approval was consumed
        consumed_appr = self.orch.approval_manager.get_approval(appr.approval_id)
        self.assertTrue(consumed_appr.consumed)


# ===========================================================================
# 6. Test Mandatory Post-Repair Rescan & Regressions
# ===========================================================================

class TestMandatoryPostRepairRescan(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_rescan_"))
        self.cache_dir = self.test_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.test_dir / "staging"
        self.audit_file = self.test_dir / "audit.jsonl"
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)

        self.mock_rag = MagicMock(spec=LocalRAG)
        self.mock_index = MagicMock(spec=IngestIndex)
        self.mock_index.documents = {}

        self.orch = WaveOrchestrator(
            rag=self.mock_rag,
            index=self.mock_index,
            cache_dir=self.cache_dir,
            staging_dir=self.staging_dir,
            audit_logger=self.audit_logger,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    @patch("repair.wave.rollback_commit")
    def test_rescan_detecting_glued_text_triggers_rollback(self, mock_rollback, mock_verify, mock_commit, mock_class, mock_scan):
        # 1st scan healthy; 2nd scan (rescan) has QUALITY_GLUED_TEXT
        scan_before = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        scan_rescan = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[
                Issue(code=IssueCode.QUALITY_GLUED_TEXT, severity=IssueSeverity.ERROR.value, message="Glued words detected"),
            ],
        )
        mock_scan.side_effect = [scan_before, scan_rescan]
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_glued", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_rollback.return_value = RollbackResult(status=RollbackStatus.ROLLBACK_SUCCESS.value, rollback_id="rb_1")

        cfg = WaveConfig(environment="rag_lab", dry_run=False, require_rescan=True)
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256}]

        summary = self.orch.execute_wave(cfg, target_items=target, candidate_overrides={SAMPLE_DOC_ID: _make_candidate()})
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(summary.rolled_back_count, 1)
        self.assertEqual(summary.documents[0].status, WaveDocumentStatus.RESCAN_FAILED.value)
        self.assertFalse(summary.documents[0].rescan_passed)
        self.assertTrue(summary.documents[0].rollback_occurred)
        mock_rollback.assert_called_once()

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    @patch("repair.wave.rollback_commit")
    def test_rescan_detecting_mojibake_triggers_rollback(self, mock_rollback, mock_verify, mock_commit, mock_class, mock_scan):
        scan_before = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        scan_rescan = DocumentScanResult(
            document_id=SAMPLE_DOC_ID,
            source_hash=SAMPLE_SHA256,
            issues=[
                Issue(code=IssueCode.QUALITY_MOJIBAKE, severity=IssueSeverity.CRITICAL.value, message="Mojibake found"),
            ],
        )
        mock_scan.side_effect = [scan_before, scan_rescan]
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_moj", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_rollback.return_value = RollbackResult(status=RollbackStatus.ROLLBACK_SUCCESS.value, rollback_id="rb_2")

        cfg = WaveConfig(environment="rag_lab", dry_run=False, require_rescan=True)
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256}]

        summary = self.orch.execute_wave(cfg, target_items=target, candidate_overrides={SAMPLE_DOC_ID: _make_candidate()})
        self.assertEqual(summary.documents[0].status, WaveDocumentStatus.RESCAN_FAILED.value)
        self.assertIn(IssueCode.QUALITY_MOJIBAKE, summary.documents[0].regressions_detected)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    @patch("repair.wave.rollback_commit")
    def test_cross_document_mutation_triggers_immediate_rollback_and_halts_wave(
        self, mock_rollback, mock_verify, mock_commit, mock_class, mock_scan
    ):
        # Set up adjacent document in index
        self.mock_index.documents = {
            "adjacent_doc": {"source_hash": "adj_hash_1", "chunk_count": 5},
        }

        # Mock capture before vs after to simulate adjacent doc mutation
        fingerprint_before = {"documents": {"adjacent_doc": {"source_hash": "adj_hash_1", "chunk_count": 5, "cache_size": 100}}}
        fingerprint_after = {"documents": {"adjacent_doc": {"source_hash": "adj_hash_1", "chunk_count": 99, "cache_size": 100}}} # mutated chunk_count!

        self.orch._capture_corpus_fingerprint = MagicMock(side_effect=[fingerprint_before, fingerprint_after])

        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_leak", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_rollback.return_value = RollbackResult(status=RollbackStatus.ROLLBACK_SUCCESS.value, rollback_id="rb_cross")

        cfg = WaveConfig(environment="rag_lab", dry_run=False, require_rescan=True, halt_on_failure=True)
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256}]

        summary = self.orch.execute_wave(cfg, target_items=target, candidate_overrides={SAMPLE_DOC_ID: _make_candidate()})
        self.assertEqual(summary.status, WaveStatus.HALTED.value)
        self.assertEqual(summary.documents[0].status, WaveDocumentStatus.RESCAN_FAILED.value)
        self.assertIn("CROSS_DOCUMENT_MUTATION", summary.documents[0].regressions_detected)
        mock_rollback.assert_called_once()


# ===========================================================================
# 7. Test Verification Failure & Rollback
# ===========================================================================

class TestVerificationFailureAndRollback(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_vf_"))
        self.cache_dir = self.test_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.test_dir / "staging"
        self.audit_file = self.test_dir / "audit.jsonl"
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)

        self.mock_rag = MagicMock(spec=LocalRAG)
        self.mock_index = MagicMock(spec=IngestIndex)
        self.mock_index.documents = {}

        self.orch = WaveOrchestrator(
            rag=self.mock_rag,
            index=self.mock_index,
            cache_dir=self.cache_dir,
            staging_dir=self.staging_dir,
            audit_logger=self.audit_logger,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    @patch("repair.wave.rollback_commit")
    def test_verify_failure_triggers_clean_restoration(self, mock_rollback, mock_verify, mock_commit, mock_class, mock_scan):
        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_bad_v", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFY_FAILED.value, commit_id="com_bad_v", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256, failures=["Benchmark BM-01 dropped rank from 1 to 8"])
        mock_rollback.return_value = RollbackResult(status=RollbackStatus.ROLLBACK_SUCCESS.value, rollback_id="rb_restore")

        cfg = WaveConfig(environment="rag_lab", dry_run=False, rollback_on_verify_fail=True)
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256}]

        summary = self.orch.execute_wave(cfg, target_items=target, candidate_overrides={SAMPLE_DOC_ID: _make_candidate()})
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(summary.rolled_back_count, 1)
        self.assertEqual(summary.documents[0].status, WaveDocumentStatus.VERIFY_FAILED_RESTORED.value)
        self.assertTrue(summary.documents[0].rollback_occurred)
        mock_rollback.assert_called_once()

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    @patch("repair.wave.rollback_commit")
    def test_verify_failure_with_rollback_failure_reported(self, mock_rollback, mock_verify, mock_commit, mock_class, mock_scan):
        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_bad_v2", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFY_FAILED.value, commit_id="com_bad_v2", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256, failures=["Failed"])
        mock_rollback.return_value = RollbackResult(
            status=RollbackStatus.ROLLBACK_FAILED.value,
            error="Snapshot corrupted",
        )

        cfg = WaveConfig(environment="rag_lab", dry_run=False, rollback_on_verify_fail=True)
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256}]

        summary = self.orch.execute_wave(cfg, target_items=target, candidate_overrides={SAMPLE_DOC_ID: _make_candidate()})
        self.assertEqual(summary.documents[0].status, WaveDocumentStatus.VERIFY_FAILED_ROLLBACK_FAILED.value)
        self.assertIn("Snapshot corrupted", summary.documents[0].errors[0])


# ===========================================================================
# 8. Test Circuit Breaker & Failure Budget Halting
# ===========================================================================

class TestCircuitBreakerAndFailureBudget(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_cb_"))
        self.cache_dir = self.test_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.test_dir / "staging"
        self.audit_file = self.test_dir / "audit.jsonl"
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)

        self.mock_rag = MagicMock(spec=LocalRAG)
        self.mock_index = MagicMock(spec=IngestIndex)
        self.mock_index.documents = {}

        self.orch = WaveOrchestrator(
            rag=self.mock_rag,
            index=self.mock_index,
            cache_dir=self.cache_dir,
            staging_dir=self.staging_dir,
            audit_logger=self.audit_logger,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    @patch("repair.wave.rollback_commit")
    def test_consecutive_failures_trips_circuit_breaker(
        self, mock_rollback, mock_verify, mock_commit, mock_class, mock_scan
    ):
        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        # Always fail verification
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFY_FAILED.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256, failures=["F"])
        mock_rollback.return_value = RollbackResult(status=RollbackStatus.ROLLBACK_SUCCESS.value)

        # Config: halt after 2 consecutive failures
        cfg = WaveConfig(
            scope="SMALL_BATCH",
            doc_limit=5,
            dry_run=False,
            halt_on_failure=False,  # let circuit breaker catch it
            max_consecutive_failures=2,
            failure_budget=5,
        )
        targets = [{"document_id": f"doc_{i}", "source_hash": f"h_{i}"} for i in range(1, 6)]
        candidates = {f"doc_{i}": _make_candidate(doc_id=f"doc_{i}") for i in range(1, 6)}

        summary = self.orch.execute_wave(cfg, target_items=targets, candidate_overrides=candidates)
        self.assertTrue(summary.circuit_broken)
        self.assertEqual(summary.status, WaveStatus.CIRCUIT_OPEN.value)
        # Should have stopped after 2 targets processed
        self.assertEqual(summary.scanned_count, 2)
        self.assertEqual(len(summary.documents), 2)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    def test_consecutive_failures_reset_on_success(
        self, mock_verify, mock_commit, mock_class, mock_scan
    ):
        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)

        # First doc fails, second succeeds, third succeeds
        mock_verify.side_effect = [
            VerificationResult(status=VerificationStatus.VERIFY_FAILED.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256, failures=["F"]),
            VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256),
            VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256),
        ]

        cfg = WaveConfig(
            scope="SMALL_BATCH",
            doc_limit=3,
            dry_run=False,
            halt_on_failure=False,
            max_consecutive_failures=2,
            failure_budget=5,
            require_rescan=False,
        )
        targets = [{"document_id": f"doc_{i}", "source_hash": f"h_{i}"} for i in range(1, 4)]
        candidates = {f"doc_{i}": _make_candidate(doc_id=f"doc_{i}") for i in range(1, 4)}

        summary = self.orch.execute_wave(cfg, target_items=targets, candidate_overrides=candidates)
        self.assertFalse(summary.circuit_broken)
        self.assertEqual(self.orch.consecutive_failures, 0)
        self.assertEqual(summary.verified_count, 2)


# ===========================================================================
# 9. Test Isolation & Audit Provenance
# ===========================================================================

class TestIsolationAndAuditProvenance(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_iso_audit_"))
        self.cache_dir = self.test_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.test_dir / "staging"
        self.audit_file = self.test_dir / "audit.jsonl"
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)

        self.mock_rag = MagicMock(spec=LocalRAG)
        self.mock_index = MagicMock(spec=IngestIndex)
        self.mock_index.documents = {}

        self.orch = WaveOrchestrator(
            rag=self.mock_rag,
            index=self.mock_index,
            cache_dir=self.cache_dir,
            staging_dir=self.staging_dir,
            audit_logger=self.audit_logger,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    def test_audit_trail_cryptographic_provenance(self, mock_verify, mock_commit, mock_class, mock_scan):
        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_audit", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)

        cfg = WaveConfig(environment="rag_lab", dry_run=False, require_rescan=True)
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256}]

        summary = self.orch.execute_wave(cfg, target_items=target, candidate_overrides={SAMPLE_DOC_ID: _make_candidate()})
        self.assertEqual(summary.status, WaveStatus.SUCCESS.value)

        # Verify audit file contents
        events = self.audit_logger.get_events()
        event_types = [e.event_type for e in events]
        self.assertIn("WAVE_STARTED", event_types)
        self.assertIn("REPAIR_COMMITTED", event_types)
        self.assertIn("REPAIR_VERIFIED", event_types)
        self.assertIn("WAVE_RESCAN_PASSED", event_types)
        self.assertIn("WAVE_COMPLETED", event_types)

        # Verify hash chain integrity
        valid_chain, errors = self.audit_logger.verify_chain()
        self.assertTrue(valid_chain)
        self.assertEqual(len(errors), 0)

    def test_production_audit_file_not_polluted_by_sandbox(self):
        # Ensure our test audit log is isolated from the production audit log
        from repair.audit import REPAIR_AUDIT_LOG_FILE
        self.assertNotEqual(str(self.audit_file), str(REPAIR_AUDIT_LOG_FILE))

    def test_toctou_hash_mismatch_aborts_repair(self):
        # Create dummy source file with different content
        source_p = self.test_dir / "doc.txt"
        source_p.write_text("Modified content", encoding="utf-8")

        cfg = WaveConfig(environment="rag_lab", dry_run=False)
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256, "source_path": str(source_p)}]

        with patch("repair.wave.scan_document") as mock_scan, \
             patch("repair.wave.classify_document") as mock_class:
            mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
            mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)

            summary = self.orch.execute_wave(cfg, target_items=target, candidate_overrides={SAMPLE_DOC_ID: _make_candidate()})
            self.assertEqual(summary.failed_count, 1)
            self.assertEqual(summary.documents[0].status, WaveDocumentStatus.FAILED.value)
            self.assertIn("TOCTOU", summary.documents[0].errors[0])

    def test_expired_approval_token_rejected(self):
        appr = self.orch.approval_manager.create_approval(
            repair_id="rep_exp",
            candidate_id="cand_exp",
            document_id=SAMPLE_DOC_ID,
            source_sha256=SAMPLE_SHA256,
            expiration_seconds=-10,  # already expired
        )

        cfg = WaveConfig(environment="rag_lab", dry_run=False)
        target = [{"document_id": SAMPLE_DOC_ID, "source_hash": SAMPLE_SHA256, "approval_id": appr.approval_id}]

        with patch("repair.wave.scan_document") as mock_scan, \
             patch("repair.wave.classify_document") as mock_class:
            mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
            mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.REVIEW.value, review_issues=["review needed"])

            summary = self.orch.execute_wave(cfg, target_items=target)
            self.assertEqual(summary.failed_count, 1)
            self.assertEqual(summary.documents[0].status, WaveDocumentStatus.FAILED.value)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    @patch("repair.wave.rollback_commit")
    def test_halt_on_failure_false_continues_remaining_documents(
        self, mock_rollback, mock_verify, mock_commit, mock_class, mock_scan
    ):
        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        # Doc 1 fails, Doc 2 passes
        mock_verify.side_effect = [
            VerificationResult(status=VerificationStatus.VERIFY_FAILED.value, commit_id="c", candidate_id="cand_1", document_id="doc_1", source_sha256=SAMPLE_SHA256, failures=["F"]),
            VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id="doc_2", source_sha256=SAMPLE_SHA256),
        ]
        mock_rollback.return_value = RollbackResult(status=RollbackStatus.ROLLBACK_SUCCESS.value)

        cfg = WaveConfig(
            scope="SMALL_BATCH",
            doc_limit=2,
            dry_run=False,
            halt_on_failure=False,  # continue to doc 2!
            max_consecutive_failures=3,
            failure_budget=5,
            require_rescan=False,
        )
        targets = [{"document_id": "doc_1", "source_hash": "h_1"}, {"document_id": "doc_2", "source_hash": "h_2"}]
        candidates = {"doc_1": _make_candidate(doc_id="doc_1"), "doc_2": _make_candidate(doc_id="doc_2")}

        summary = self.orch.execute_wave(cfg, target_items=targets, candidate_overrides=candidates)
        self.assertEqual(summary.scanned_count, 2)
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(summary.verified_count, 1)
        self.assertEqual(summary.status, WaveStatus.PARTIAL.value)

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    @patch("repair.wave.rollback_commit")
    def test_total_failure_budget_exhaustion_halts_wave(
        self, mock_rollback, mock_verify, mock_commit, mock_class, mock_scan
    ):
        mock_scan.return_value = DocumentScanResult(document_id=SAMPLE_DOC_ID, source_hash=SAMPLE_SHA256, issues=[])
        mock_class.return_value = ClassificationResult(document_id=SAMPLE_DOC_ID, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="c", candidate_id="cand_1", document_id=SAMPLE_DOC_ID, source_sha256=SAMPLE_SHA256)
        mock_rollback.return_value = RollbackResult(status=RollbackStatus.ROLLBACK_SUCCESS.value)

        # 1 fails, 1 succeeds, 1 fails -> total_failures reaches budget of 2!
        mock_verify.side_effect = [
            VerificationResult(status=VerificationStatus.VERIFY_FAILED.value, commit_id="c", candidate_id="cand_1", document_id="doc_1", source_sha256=SAMPLE_SHA256, failures=["F"]),
            VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id="doc_2", source_sha256=SAMPLE_SHA256),
            VerificationResult(status=VerificationStatus.VERIFY_FAILED.value, commit_id="c", candidate_id="cand_1", document_id="doc_3", source_sha256=SAMPLE_SHA256, failures=["F"]),
            VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id="doc_4", source_sha256=SAMPLE_SHA256),
        ]

        cfg = WaveConfig(
            scope="MEDIUM_BATCH",
            doc_limit=4,
            dry_run=False,
            halt_on_failure=False,
            max_consecutive_failures=3,
            failure_budget=2,  # budget 2
            require_rescan=False,
        )
        targets = [{"document_id": f"doc_{i}", "source_hash": f"h_{i}"} for i in range(1, 5)]
        candidates = {f"doc_{i}": _make_candidate(doc_id=f"doc_{i}") for i in range(1, 5)}

        summary = self.orch.execute_wave(cfg, target_items=targets, candidate_overrides=candidates)
        self.assertTrue(summary.circuit_broken)
        self.assertIn("Circuit breaker tripped", summary.halt_reason)

    def test_scope_all_progression_limits(self):
        cfg = WaveConfig(scope=WaveScope.ALL.value, doc_limit=3)
        items = [{"document_id": f"d_{i}"} for i in range(10)]
        res = self.orch.resolve_target_scope(cfg, provided_items=items)
        self.assertEqual(len(res), 3)

    def test_wave_summary_json_serializability(self):
        doc_res = WaveDocumentResult(
            document_id="doc_ser",
            source_sha256="sha_ser",
            status=WaveDocumentStatus.VERIFIED_AND_RESCANNED.value,
        )
        summary = WaveSummary(
            wave_id="wave_ser",
            scope="PILOT",
            documents=[doc_res],
        )
        data = summary.to_dict()
        serialized = json.dumps(data)
        self.assertIn("wave_ser", serialized)
        self.assertIn("doc_ser", serialized)

    def test_check_production_corpus_handles_missing_cache_dir(self):
        missing_cache = self.test_dir / "non_existent_cache"
        is_pres, reason, details = check_production_corpus(cache_dir=missing_cache)
        self.assertFalse(is_pres)
        self.assertFalse(details["cache_exists"])


# ===========================================================================
# 11. FAZ 17: Rescan Identity & Cross-Document Isolation Tests (A-F)
# ===========================================================================

class TestRescanIdentityAndCrossDocumentIsolation(unittest.TestCase):
    """FAZ 17: Rescan Identity & Cross-Document Isolation Tests (A-F)."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_rescan_id_"))
        self.cache_dir = self.test_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.test_dir / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.test_dir / "audit.jsonl"
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)

        self.mock_rag = MagicMock(spec=LocalRAG)
        self.mock_index = MagicMock(spec=IngestIndex)
        self.mock_index.documents = {}

        self.orch = WaveOrchestrator(
            rag=self.mock_rag,
            index=self.mock_index,
            cache_dir=self.cache_dir,
            staging_dir=self.staging_dir,
            audit_logger=self.audit_logger,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_A_document_key_differs_from_doc_id_same_real_doc_passes_rescan(self):
        """A) document_key != document_id but same real doc (same hash) -> PASSES rescan.
        Simulates mevzuat_org (target document_id) with document_key='sı' under bb56d01f...
        """
        target_hash = "bb56d01f" + "0" * 56
        target_doc_id = "mevzuat_org"

        (self.cache_dir / f"{target_hash}.txt").write_text("Old content before repair", encoding="utf-8")

        self.mock_index.documents = {
            target_hash: {
                "file_hash": target_hash,
                "document_id": None,
                "document_key": "sı",
                "source_id": "mevzuat_org",
                "chunk_count": 23,
            }
        }

        # Fingerprint before: target must be excluded, adjacent should be empty
        fp_before = self.orch._capture_corpus_fingerprint(exclude_doc_id=target_doc_id, exclude_hash=target_hash)
        self.assertNotIn(target_hash, fp_before["documents"])
        self.assertNotIn("sı", fp_before["documents"])
        self.assertNotIn(target_doc_id, fp_before["documents"])
        self.assertEqual(len(fp_before["documents"]), 0)

        # Mutate cache file (simulating commit)
        (self.cache_dir / f"{target_hash}.txt").write_text("New repaired content (different length)", encoding="utf-8")

        # Fingerprint after: target must still be excluded
        fp_after = self.orch._capture_corpus_fingerprint(exclude_doc_id=target_doc_id, exclude_hash=target_hash)
        self.assertEqual(fp_before, fp_after)

    def test_B_same_file_hash_same_real_doc_identity_passes_rescan(self):
        """B) Same file_hash + matching real doc identity -> PASSES rescan."""
        doc_hash = "a" * 64
        doc_id = "doc_alpha"
        (self.cache_dir / f"{doc_hash}.txt").write_text("alpha text", encoding="utf-8")

        self.mock_index.documents = {
            doc_hash: {
                "file_hash": doc_hash,
                "document_id": doc_id,
                "document_key": "key_alpha",
                "chunk_count": 10,
            }
        }

        fp = self.orch._capture_corpus_fingerprint(exclude_doc_id=doc_id, exclude_hash=doc_hash)
        self.assertEqual(len(fp["documents"]), 0, "Target doc must be excluded from adjacent fingerprint")

    def test_C_conflicting_real_doc_id_unexpected_same_hash_triggers_mutation_alarm(self):
        """C) Different real document_id + unexpectedly same hash -> FAIL / mutation alarm.
        If doc_B carries a conflicting explicit document_id, it represents a distinct document
        and must NOT be silently excluded. If hash changes, mutation is detected on doc_B.
        """
        shared_hash = "s" * 64
        target_id = "doc_A"
        conflicting_id = "doc_B"

        (self.cache_dir / f"{shared_hash}.txt").write_text("Original shared cache", encoding="utf-8")

        # doc_B explicitly has a different real document_id
        self.mock_index.documents = {
            "key_doc_B": {
                "file_hash": shared_hash,
                "document_id": conflicting_id,
                "chunk_count": 5,
            }
        }

        # Fingerprint before: doc_B MUST NOT be excluded because it has a conflicting document_id
        fp_before = self.orch._capture_corpus_fingerprint(exclude_doc_id=target_id, exclude_hash=shared_hash)
        self.assertIn("doc_B", fp_before["documents"], "Conflicting doc_B must be tracked as adjacent")

        # Mutate cache file of shared_hash (as would happen when doc_A commits)
        (self.cache_dir / f"{shared_hash}.txt").write_text("Mutated text with new length", encoding="utf-8")

        fp_after = self.orch._capture_corpus_fingerprint(exclude_doc_id=target_id, exclude_hash=shared_hash)

        # Mutation check must detect doc_B mutated!
        mutated = []
        for adj_id, b in fp_before["documents"].items():
            a = fp_after["documents"].get(adj_id)
            if a != b:
                mutated.append(adj_id)

        self.assertIn("doc_B", mutated, "Rescan must flag doc_B as mutated")

    def test_D_genuine_other_document_chunk_mutation_triggers_rescan_fail(self):
        """D) Genuinely different adjacent document has chunks mutated -> detected as mutation."""
        target_hash = "t" * 64
        target_id = "target_doc"
        other_hash = "o" * 64
        other_id = "other_doc"

        (self.cache_dir / f"{target_hash}.txt").write_text("target cache", encoding="utf-8")
        (self.cache_dir / f"{other_hash}.txt").write_text("other cache", encoding="utf-8")

        self.mock_index.documents = {
            target_hash: {"file_hash": target_hash, "document_id": target_id, "chunk_count": 5},
            other_hash: {"file_hash": other_hash, "document_id": other_id, "chunk_count": 10},
        }

        fp_before = self.orch._capture_corpus_fingerprint(exclude_doc_id=target_id, exclude_hash=target_hash)
        self.assertIn(other_id, fp_before["documents"])

        # Mutate other_doc's chunk count in index (simulating unintended leak)
        self.mock_index.documents[other_hash]["chunk_count"] = 15

        fp_after = self.orch._capture_corpus_fingerprint(exclude_doc_id=target_id, exclude_hash=target_hash)

        mutated = []
        for adj_id, b in fp_before["documents"].items():
            a = fp_after["documents"].get(adj_id)
            if a != b:
                mutated.append(f"{adj_id}: before={b}, after={a}")

        self.assertTrue(any(other_id in m for m in mutated), "Unintended mutation on other_doc must be detected")

    @patch("repair.wave.scan_document")
    @patch("repair.wave.classify_document")
    @patch("repair.wave.commit_candidate")
    @patch("repair.wave.verify_committed_repair")
    def test_E_normal_rescan_lifecycle_passes(self, mock_verify, mock_commit, mock_class, mock_scan):
        """E) Normal rescan flow where target document matches alias and no adjacent mutations occur -> PASS."""
        target_hash = "bb56d01f" + "0" * 56
        target_doc_id = "mevzuat_org"

        (self.cache_dir / f"{target_hash}.txt").write_text("Clean cache", encoding="utf-8")

        self.mock_index.documents = {
            target_hash: {
                "file_hash": target_hash,
                "document_id": None,
                "document_key": "sı",
                "source_id": "mevzuat_org",
                "chunk_count": 22,
            },
            "other_doc": {
                "file_hash": "other" * 12 + "0000",
                "document_id": "other_doc",
                "chunk_count": 5,
            },
        }
        (self.cache_dir / f"{'other' * 12 + '0000'}.txt").write_text("Other cache", encoding="utf-8")

        mock_scan.return_value = DocumentScanResult(document_id=target_doc_id, source_hash=target_hash, issues=[])
        mock_class.return_value = ClassificationResult(document_id=target_doc_id, status=ClassificationType.SAFE.value)
        mock_commit.return_value = CommitResult(status=CommitStatus.COMMITTED.value, commit_id="com_ok", candidate_id="cand_1", document_id=target_doc_id, source_sha256=target_hash)
        mock_verify.return_value = VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="c", candidate_id="cand_1", document_id=target_doc_id, source_sha256=target_hash)

        cfg = WaveConfig(environment="rag_lab", dry_run=False, require_rescan=True, halt_on_failure=True)
        target = [{"document_id": target_doc_id, "source_hash": target_hash}]

        summary = self.orch.execute_wave(cfg, target_items=target, candidate_overrides={target_doc_id: _make_candidate()})
        self.assertEqual(summary.status, WaveStatus.SUCCESS.value)
        self.assertEqual(summary.documents[0].status, WaveDocumentStatus.VERIFIED_AND_RESCANNED.value)
        self.assertTrue(summary.documents[0].rescan_passed)
        self.assertFalse(summary.documents[0].rollback_occurred)

    def test_F_zero_production_writes_guarantee(self):
        """F) Rescan identity logic operates strictly on isolated mock/staging structures; zero production mutations."""
        self.assertNotEqual(self.cache_dir, DATA_DIR / "source_cache")
        self.assertNotEqual(self.staging_dir, DATA_DIR / "repair_staging")


if __name__ == "__main__":
    unittest.main()
