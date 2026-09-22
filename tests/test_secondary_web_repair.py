# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 17: Secondary Web Source Repair Regression Tests.

Covers:
A. Valid UTF-8 raw response -> valid candidate (web_harvester_utf8_soup)
B. Hash mismatch -> BLOCKED / HASH_MISMATCH
C. Mojibake reduction (738 -> 0, quality 0.7861 -> 0.9461) -> READY_TO_COMMIT
D. Fetch failure -> FETCH_FAILED, safe non-crashing handling
E. Zero Production Write Guarantee across all test scenarios
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mufettis.config import (
    CHUNK_SCHEMA_VERSION,
    DB_PATH,
    INGEST_INDEX_FILE,
    SOURCE_CACHE_DIR,
)
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex
import mufettis.harvesting.web_harvester

from repair.classifier import classify_document
from repair.comparator import (
    ComparisonStatus,
    build_snapshot,
    compare_quality,
)
from repair.retrieval_comparator import evaluate_final_gate
from repair.extractor import extract_candidate, extract_web_candidate
from repair.models import (
    ClassificationType,
    DocumentScanResult,
    ExtractionCandidate,
    Issue,
    IssueCode,
    IssueSeverity,
    RetrievalComparisonResult,
    WaveConfig,
    WaveDocumentStatus,
    WaveScope,
    WaveStatus,
)
from repair.wave import WaveOrchestrator


SAMPLE_WEB_URL = "https://www.mevzuat.org/"
SAMPLE_DOC_ID = "mevzuat_org"
SAMPLE_DOC_KEY = "s1"
SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>mevzuat.org – Mali Mevzuat ve Muhasebe Platformu</title>
</head>
<body>
    <header>
        <h1>Mevzuat ve Muhasebe Rehberi</h1>
    </header>
    <main>
        <article>
            <h2>Madde 1: Genel Esaslar</h2>
            <p>Bu platform Türkiye Cumhuriyeti mali mevzuatı, vergi kanunları ve muhasebe standartlarını derler.</p>
        </article>
        <article>
            <h2>Madde 2: Yürürlük ve Uygulama</h2>
            <p>Yürürlükteki kanun ve yönetmelikler bankacılık ve finans kuruluşları tarafından titizlikle uygulanır.</p>
        </article>
    </main>
</body>
</html>"""

SAMPLE_RAW_BYTES = SAMPLE_HTML.encode("utf-8")
SAMPLE_HASH = hashlib.sha256(SAMPLE_RAW_BYTES).hexdigest()


def _take_production_snapshot():
    """Captures an exact snapshot of production files to enforce Zero Production Write Guarantee."""
    paths_to_monitor = [DB_PATH, INGEST_INDEX_FILE, SOURCE_CACHE_DIR]
    snap = {}
    for p in paths_to_monitor:
        path_obj = Path(p)
        if not path_obj.exists():
            snap[str(path_obj)] = None
            continue
        if path_obj.is_file():
            st = path_obj.stat()
            snap[str(path_obj)] = {
                "type": "file",
                "size": st.st_size,
                "mtime": st.st_mtime_ns,
            }
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


class TestSecondaryWebSourceRepair(unittest.TestCase):
    """Regression test suite for secondary web source repair pipeline."""

    def setUp(self):
        self.prod_snap_before = _take_production_snapshot()

    def tearDown(self):
        # Scenario E: Zero Production Write Guarantee
        prod_snap_after = _take_production_snapshot()
        self.assertEqual(
            self.prod_snap_before,
            prod_snap_after,
            "Production corpus was mutated during test! Zero write guarantee violated!",
        )

    # -----------------------------------------------------------------------
    # Scenario A: Valid UTF-8 raw response -> valid candidate
    # -----------------------------------------------------------------------
    @patch("mufettis.harvesting.web_harvester.WebSourceHarvester._get")
    def test_scenario_a_valid_utf8_response_produces_valid_candidate(self, mock_fetch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = SAMPLE_RAW_BYTES
        mock_response.text = SAMPLE_HTML
        mock_fetch.return_value = mock_response

        meta = {
            "document_id": SAMPLE_DOC_ID,
            "document_key": SAMPLE_DOC_KEY,
            "url": SAMPLE_WEB_URL,
            "source_type": "secondary",
            "is_pdf": False,
            "ingest_origin": "secondary_web_source",
        }

        cand_res = extract_candidate(
            source_path=SAMPLE_WEB_URL,
            meta=meta,
            expected_sha256=SAMPLE_HASH,
        )

        self.assertEqual(cand_res.status, "SUCCESS")
        self.assertIsNotNone(cand_res.candidate)
        cand = cand_res.candidate
        self.assertEqual(cand.document_id, SAMPLE_DOC_ID)
        self.assertEqual(cand.source_sha256, SAMPLE_HASH)
        self.assertEqual(cand.extraction_method, "web_harvester_utf8_soup")
        self.assertGreater(len(cand.legal_chunks), 0)
        self.assertEqual(cand.quality_metrics.get("mojibake_sequence_count"), 0)
        self.assertGreaterEqual(cand.quality_metrics.get("score", 0.0), 0.88)

    # -----------------------------------------------------------------------
    # Scenario B: Hash mismatch -> BLOCKED / HASH_MISMATCH
    # -----------------------------------------------------------------------
    @patch("mufettis.harvesting.web_harvester.WebSourceHarvester._get")
    def test_scenario_b_hash_mismatch_blocks_extraction(self, mock_fetch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"Tamamen farkli bir icerik - hash uyusmazligi testi"
        mock_response.text = "Tamamen farkli bir icerik - hash uyusmazligi testi"
        mock_fetch.return_value = mock_response

        meta = {
            "document_id": SAMPLE_DOC_ID,
            "url": SAMPLE_WEB_URL,
            "source_type": "secondary",
            "is_pdf": False,
        }

        cand_res = extract_candidate(
            source_path=SAMPLE_WEB_URL,
            meta=meta,
            expected_sha256=SAMPLE_HASH,  # Expected original hash
        )

        self.assertEqual(cand_res.status, "HASH_MISMATCH")
        self.assertIsNone(cand_res.candidate)
        self.assertTrue(any("uyumsuzluğu" in err.lower() or "mismatch" in err.lower() or "sha256" in err.lower() for err in cand_res.errors))

    # -----------------------------------------------------------------------
    # Scenario C: Mojibake 738 -> 0 quality improvement -> READY_TO_COMMIT
    # -----------------------------------------------------------------------
    @patch("mufettis.harvesting.web_harvester.WebSourceHarvester._get")
    def test_scenario_c_mojibake_reduction_ready_to_commit(self, mock_fetch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = SAMPLE_RAW_BYTES
        mock_response.text = SAMPLE_HTML
        mock_fetch.return_value = mock_response

        meta = {
            "document_id": SAMPLE_DOC_ID,
            "url": SAMPLE_WEB_URL,
            "source_type": "secondary",
            "is_pdf": False,
        }

        cand_res = extract_candidate(
            source_path=SAMPLE_WEB_URL,
            meta=meta,
            expected_sha256=SAMPLE_HASH,
        )
        self.assertEqual(cand_res.status, "SUCCESS")
        candidate = cand_res.candidate

        # Old baseline metrics with mojibake 738 and low score 0.7861
        old_metrics = {
            "score": 0.7861,
            "chars": len(candidate.normalized_text),
            "glued_token_count": 5,
            "very_long_token_count": 2,
            "replacement_char_count": 0,
            "mojibake_sequence_count": 738,
        }
        old_snapshot = build_snapshot(old_metrics)

        # Quality Comparison
        quality_res = compare_quality(old_snapshot, candidate)

        # Retrieval Gate
        retrieval_res = RetrievalComparisonResult(
            status=ComparisonStatus.READY_TO_COMMIT.value
        )
        final_gate = evaluate_final_gate(quality_res, retrieval_res)

        self.assertEqual(final_gate.final_status, ComparisonStatus.READY_TO_COMMIT.value)

    # -----------------------------------------------------------------------
    # Scenario D: Fetch failure -> FETCH_FAILED handled safely
    # -----------------------------------------------------------------------
    @patch("mufettis.harvesting.web_harvester.WebSourceHarvester._get")
    def test_scenario_d_fetch_failure_handled_gracefully(self, mock_fetch):
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.content = b"Service Unavailable"
        mock_fetch.return_value = mock_response

        meta = {
            "document_id": SAMPLE_DOC_ID,
            "url": SAMPLE_WEB_URL,
            "source_type": "secondary",
            "is_pdf": False,
        }

        cand_res = extract_candidate(
            source_path=SAMPLE_WEB_URL,
            meta=meta,
            expected_sha256=SAMPLE_HASH,
        )

        self.assertEqual(cand_res.status, "FETCH_FAILED")
        self.assertIsNone(cand_res.candidate)
        self.assertTrue(any("503" in err for err in cand_res.errors))

    # -----------------------------------------------------------------------
    # Scenario E: Wave Orchestrator dry-run on secondary web candidate
    # -----------------------------------------------------------------------
    @patch("mufettis.harvesting.web_harvester.WebSourceHarvester._get")
    def test_scenario_e_wave_orchestrator_dry_run_simulation(self, mock_fetch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = SAMPLE_RAW_BYTES
        mock_response.text = SAMPLE_HTML
        mock_fetch.return_value = mock_response

        temp_dir = tempfile.mkdtemp(prefix="wave_test_")
        try:
            temp_p = Path(temp_dir)
            cache_p = temp_p / "cache"
            cache_p.mkdir()
            (cache_p / f"{SAMPLE_HASH}.txt").write_text("Old cache text", encoding="utf-8")

            rag_mock = MagicMock(spec=LocalRAG)
            rag_mock.collection = MagicMock()
            rag_mock.collection.get.return_value = {
                "ids": ["c1"],
                "documents": ["Old chunk"],
                "metadatas": [{"file_hash": SAMPLE_HASH, "chunk_schema_version": CHUNK_SCHEMA_VERSION}],
            }
            rag_mock.collection.count.return_value = 1

            index_mock = MagicMock(spec=IngestIndex)
            index_mock.get.return_value = {
                "document_id": SAMPLE_DOC_ID,
                "document_key": SAMPLE_DOC_KEY,
                "url": SAMPLE_WEB_URL,
                "file_hash": SAMPLE_HASH,
                "source_type": "secondary",
                "is_pdf": False,
                "ingest_origin": "secondary_web_source",
            }
            index_mock.data = {"indexed_items": {SAMPLE_HASH: index_mock.get.return_value}}

            orch = WaveOrchestrator(
                rag=rag_mock,
                index=index_mock,
                cache_dir=cache_p,
                staging_dir=temp_p / "staging",
                audit_logger=MagicMock(),
            )

            config = WaveConfig(
                scope="PILOT",
                target_document_ids=[SAMPLE_DOC_ID],
                doc_limit=1,
                dry_run=True,
                allow_production_repair=False,
                environment="rag_lab",
            )

            targets = [{
                "document_id": SAMPLE_DOC_ID,
                "source_hash": SAMPLE_HASH,
                "url": SAMPLE_WEB_URL,
                "source_type": "secondary",
                "is_pdf": False,
                "ingest_origin": "secondary_web_source",
            }]

            summary = orch.execute_wave(config, target_items=targets)

            self.assertEqual(summary.status, WaveStatus.SUCCESS.value)
            self.assertEqual(summary.scanned_count, 1)
            self.assertEqual(summary.committed_count, 0)
            self.assertEqual(summary.production_mutation_count, 0)
            self.assertEqual(summary.documents[0].status, WaveDocumentStatus.SIMULATED.value)
            self.assertEqual(summary.documents[0].action_taken, "SIMULATE")
            self.assertIsNotNone(summary.documents[0].candidate)
            self.assertEqual(summary.documents[0].candidate["extraction_method"], "web_harvester_utf8_soup")
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
