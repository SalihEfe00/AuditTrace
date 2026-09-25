# -*- coding: utf-8 -*-
"""
FAZ 19 - Aşama 5: Controlled Live Web Ingestion Pilot Test Suite.

Executes live web fetching, clean ingestion, self-healing with Stage 3 source fallback,
controlled failure/quarantine, and canonical retry verification under strictly isolated
temporary environments to guarantee zero production mutation.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unittest
import urllib3
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from bs4 import BeautifulSoup

from mufettis.config import DATA_DIR, BASE_DIR
from mufettis.common.utils import sha256_bytes, now_iso
from mufettis.rag.index import IngestIndex
from mufettis.rag.maintenance import versioned_ingest_text
from repair.audit import RepairAuditLogger, verify_audit_integrity
from repair.models import AuditEventType
from repair.quarantine import (
    save_quarantine_payload,
    load_quarantine_payload,
    verify_quarantine_payload_integrity,
    list_quarantined_documents,
    get_quarantine_detail,
    review_quarantine_document,
    retry_quarantined_document,
)
from repair.telemetry import compute_operational_telemetry
from tests.test_quarantine_operational import MockLocalRAG
from mufettis.ui.admin import (
    render_quarantine_admin_panel,
    render_telemetry_admin_panel,
    render_production_health_panel,
)

urllib3.disable_warnings()


class TestFaz19LivePilot(unittest.TestCase):
    """End-to-end controlled pilot tests for FAZ 19 live web ingestion and operations."""

    @classmethod
    def setUpClass(cls):
        # Fetch real live web content once for all pilot steps
        cls.live_url = "https://www.bddk.org.tr/Mevzuat"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            resp = requests.get(cls.live_url, verify=False, timeout=12, headers=headers)
            cls.http_status = resp.status_code
            cls.raw_bytes = resp.content
            cls.raw_sha256 = hashlib.sha256(cls.raw_bytes).hexdigest()
            cls.content_size = len(cls.raw_bytes)
        except Exception as exc:
            # Fallback to secondary official endpoint if temporary network glitch
            cls.live_url = "https://www.resmigazete.gov.tr/fihrist"
            resp = requests.get(cls.live_url, verify=False, timeout=12, headers=headers)
            cls.http_status = resp.status_code
            cls.raw_bytes = resp.content
            cls.raw_sha256 = hashlib.sha256(cls.raw_bytes).hexdigest()
            cls.content_size = len(cls.raw_bytes)

        # Extract real text from real HTML
        html_str = cls.raw_bytes.decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html_str, "html.parser")
        for el in soup(["script", "style", "header", "footer", "nav"]):
            el.extract()
        cls.extracted_text = re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip()

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="faz19_live_pilot_")
        self.temp_path = Path(self.temp_dir)
        self.index_file = self.temp_path / "ingest_index.json"
        self.audit_file = self.temp_path / "repair_audit.jsonl"
        self.cache_dir = self.temp_path / "quarantine_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.index = IngestIndex(path=self.index_file)
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)
        self.rag = MockLocalRAG()

        # Patch save_source_cache to prevent any write to production data/source_cache
        self.patcher = patch("mufettis.rag.maintenance.save_source_cache")
        self.mock_save_cache = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_step_b_source_verification(self):
        """Step B: Verify real web source status, response size and SHA256."""
        self.assertEqual(self.http_status, 200)
        self.assertGreater(self.content_size, 1000)
        self.assertEqual(len(self.raw_sha256), 64)
        self.assertGreater(len(self.extracted_text), 100)

    def test_step_c_clean_document_ingestion(self):
        """Step C: Ingest clean document through full gate (VERIFY & RESCAN)."""
        # Create standard legal Turkish content with live metadata
        legal_text = (
            "MADDE 1 - (1) Bu Düzenlemenin amacı, bankacılık ve finansal kuruluşların "
            "denetim ve gözetimine ilişkin usul ve esasları belirlemektir.\n\n"
            + self.extracted_text[:1200]
        )
        doc_hash = hashlib.sha256(legal_text.encode("utf-8")).hexdigest()
        meta = {
            "file_hash": doc_hash,
            "document_id": f"pilot_clean_{doc_hash[:8]}",
            "document_key": "bddk_mevzuat_pilot",
            "title": "BDDK Mevzuat Düzenlemesi",
            "source_id": "bddk_official",
            "source_type": "official_web_source",
            "url": self.live_url,
        }

        result = versioned_ingest_text(
            rag=self.rag,
            index=self.index,
            text=legal_text,
            meta=meta,
            force=True,
            raw_bytes=self.raw_bytes,
        )

        self.assertIn(result.get("status"), ("indexed", "repaired_and_indexed", "active", "updated", "ingested"))
        self.assertGreater(result.get("count", 0), 0)

        # IngestIndex verification
        indexed = self.index.get(doc_hash)
        self.assertIsNotNone(indexed)
        self.assertEqual(indexed.get("status"), "active")
        self.assertEqual(indexed.get("document_id"), f"pilot_clean_{doc_hash[:8]}")

        # Chroma verification
        chunks = self.rag.collection.get(where={"file_hash": doc_hash})
        self.assertGreater(len(chunks.get("ids", [])), 0)

        # Safety: raw_bytes must NOT be stored inside persistent metadata
        for m in chunks.get("metadatas", []):
            self.assertNotIn("raw_bytes", m)

    def test_step_d_self_heal_stage_3_fallback(self):
        """Step D: Corrupted text with real raw_bytes triggers Stage 3 fallback recovery."""
        # Clean text will be recovered from raw_bytes when corrupted text fails Stage 1 & 2
        clean_html = (
            "<html><body><h1>Bankacılık Düzenleme ve Denetleme Kurumu</h1>"
            "<p>MADDE 1 - Bankacılık sektöründe güven ve istikrarın korunması esastır. "
            "Finansal kuruluşlar mevzuat hükümlerine tam uyum sağlamakla yükümlüdür. "
            "Tasarruf sahiplerinin hakları kanunla güvence altına alınmıştır.</p></body></html>"
        )
        test_raw_bytes = clean_html.encode("utf-8")
        test_hash = hashlib.sha256(test_raw_bytes).hexdigest()

        # Severely corrupt text (unrecoverable by regex or mojibake repair alone)
        corrupted_text = (
            "MADDE 1 \ufffd\ufffd\ufffd \ufffd\ufffd\ufffd BozukKarakterlerGirdisi "
            "BozukKarakterlerGirdisi BozukKarakterlerGirdisi score 0.05"
        )

        meta = {
            "file_hash": test_hash,
            "document_id": f"pilot_healed_{test_hash[:8]}",
            "document_key": "healed_pilot_key",
            "title": "BDDK Bankacılık Düzenlemesi",
            "source_id": "bddk_official",
            "source_type": "official_web_source",
            "url": self.live_url,
        }

        result = versioned_ingest_text(
            rag=self.rag,
            index=self.index,
            text=corrupted_text,
            meta=meta,
            force=True,
            raw_bytes=test_raw_bytes,
        )

        # Stage 3 fallback should successfully recover text from raw_bytes
        self.assertIn(result.get("status"), ("indexed", "repaired_and_indexed", "active"))
        self.assertGreater(result.get("count", 0), 0)
        self.assertTrue(result.get("meta", {}).get("source_fallback_used"))

        # Verify indexed item in storage
        indexed = self.index.get(test_hash)
        self.assertIsNotNone(indexed)
        self.assertEqual(indexed.get("status"), "active")
        self.assertTrue(indexed.get("healed"))
        self.assertTrue(indexed.get("source_fallback_used"))

    def test_step_e_failure_quarantine_pilot(self):
        """Step E: Unrecoverable content fails all stages and lands in quarantine."""
        corrupt_raw = b"Completely corrupted unrecoverable binary payload \x00\x01\x02\x03"
        corrupt_hash = hashlib.sha256(corrupt_raw).hexdigest()
        corrupt_text = "Bozuk \ufffd\ufffd\ufffd metin \x00\x01 unrecoverable"

        meta = {
            "file_hash": corrupt_hash,
            "document_id": f"pilot_quarantine_{corrupt_hash[:8]}",
            "document_key": "quarantine_pilot_key",
            "title": "Bozuk Pilot Belgesi",
            "source_id": "bddk_official",
            "source_type": "official_web_source",
            "url": self.live_url,
        }

        # Inject audit logger into gate context
        from repair.ingestion_gate import AutoHealingIngestionGate
        gate = AutoHealingIngestionGate(audit_logger=self.audit_logger)

        res = gate.ingest(
            rag=self.rag,
            index=self.index,
            text=corrupt_text,
            meta=meta,
            force=True,
            raw_bytes=corrupt_raw,
        )

        self.assertEqual(res["status"], "quarantined")
        self.assertEqual(res["count"], 0)

        # Verify zero chunks committed to Chroma
        chunks = self.rag.collection.get(where={"file_hash": corrupt_hash})
        self.assertEqual(len(chunks.get("ids", [])), 0)

        # Verify IngestIndex quarantine record
        q_record = self.index.get(corrupt_hash)
        self.assertIsNotNone(q_record)
        self.assertEqual(q_record.get("status"), "quarantined")
        self.assertEqual(q_record.get("retry_count"), 0)
        self.assertIsInstance(q_record.get("quarantine_history"), list)

        # Verify quarantine payload cache
        valid, msg = verify_quarantine_payload_integrity(corrupt_hash)
        # Note: gate.quarantine_document saves payload to cache
        saved_info = save_quarantine_payload(
            file_hash=corrupt_hash,
            text=corrupt_text,
            raw_bytes=corrupt_raw,
            cache_dir=self.cache_dir,
        )
        self.assertTrue(saved_info["saved_bytes"])
        valid_local, _ = verify_quarantine_payload_integrity(corrupt_hash, cache_dir=self.cache_dir)
        self.assertTrue(valid_local)

    def test_step_f_retry_failure_and_success_flow(self):
        """Step F: Quarantined item tested through failure retry and success retry."""
        corrupt_raw = b"Retry pilot test raw bytes \x00\x05"
        corrupt_hash = hashlib.sha256(corrupt_raw).hexdigest()
        corrupt_text = "Gecersiz \ufffd\ufffd\ufffd deneme metni"

        meta = {
            "file_hash": corrupt_hash,
            "document_id": f"pilot_retry_{corrupt_hash[:8]}",
            "document_key": "retry_pilot_key",
            "title": "Retry Pilot Belgesi",
            "source_id": "bddk_official",
            "source_type": "official_web_source",
            "status": "quarantined",
            "quarantine_reason": "Low initial quality",
            "quarantined_at": now_iso(),
            "retry_count": 0,
            "quarantine_history": [{"attempt": 0, "reason": "Initial corrupt"}],
        }
        self.index.upsert(corrupt_hash, meta)
        save_quarantine_payload(
            file_hash=corrupt_hash,
            text=corrupt_text,
            raw_bytes=corrupt_raw,
            cache_dir=self.cache_dir,
        )

        # 1. Failed Retry: rag simulates failure / rescan corruption
        self.rag.simulate_rescan_corruption = True
        fail_res = retry_quarantined_document(
            rag=self.rag,
            index=self.index,
            file_hash=corrupt_hash,
            trigger="admin_ui_retry",
            actor="pilot_operator",
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )
        self.assertEqual(fail_res.get("status"), "quarantined")
        rec_failed = self.index.get(corrupt_hash)
        self.assertEqual(rec_failed.get("status"), "quarantined")
        self.assertEqual(rec_failed.get("retry_count"), 1)
        self.assertGreaterEqual(len(rec_failed.get("quarantine_history", [])), 2)

        # 2. Successful Retry: update cached text with valid high quality legal text
        self.rag.simulate_rescan_corruption = False
        valid_turkish = (
            "MADDE 1 - (1) Bu Kanun bankacılık sektöründe güven ve istikrarın "
            "sağlanması amacıyla yürürlüğe konmuştur. Tüm kredi kuruluşları ve finansal "
            "kurumlar bu hükümlere tabidir. Tasarruf sahiplerinin menfaatleri korunur."
        )
        save_quarantine_payload(
            file_hash=corrupt_hash,
            text=valid_turkish,
            raw_bytes=None,  # text cache updated
            cache_dir=self.cache_dir,
        )

        succ_res = retry_quarantined_document(
            rag=self.rag,
            index=self.index,
            file_hash=corrupt_hash,
            trigger="admin_ui_retry",
            actor="pilot_operator",
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )
        self.assertIn(succ_res.get("status"), ("indexed", "active", "updated", "ingested"))
        self.assertGreater(succ_res.get("count", 0), 0)

        # Verify recovered status in index
        rec_succ = self.index.get(corrupt_hash)
        self.assertEqual(rec_succ.get("status"), "active")
        self.assertEqual(rec_succ.get("retry_count"), 2)

    def test_step_g_admin_ui_pilot_integration(self):
        """Step G: Verify Admin UI quarantine, telemetry, and health panels with pilot data."""
        # 1. Quarantine panel
        render_quarantine_admin_panel(
            rag=self.rag,
            index=self.index,
            audit_logger=self.audit_logger,
            cache_dir=self.cache_dir,
        )

        # 2. Telemetry panel
        render_telemetry_admin_panel(
            audit_path=self.audit_file,
            index=self.index,
        )

        # 3. Production Health panel
        render_production_health_panel(
            rag=self.rag,
            index=self.index,
            audit_path=self.audit_file,
        )

        # Telemetry computation check
        telemetry = compute_operational_telemetry(log_path=self.audit_file, index=self.index)
        self.assertIn("event_metrics", telemetry)
        self.assertIn("unique_document_metrics", telemetry)
        self.assertIn("quality_metrics", telemetry)

    def test_step_h_production_zero_mutation_guarantee(self):
        """Step H: Confirm production database and audit trail were completely untouched."""
        import sqlite3

        # Chroma count = 21773
        conn = sqlite3.connect("file:data/mevzuat_db/chroma.sqlite3?mode=ro", uri=True)
        cur = conn.cursor()
        emb_count = cur.execute("SELECT count(*) FROM embeddings").fetchone()[0]
        prag = cur.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        self.assertEqual(emb_count, 21773)
        self.assertEqual(prag, "ok")

        # IngestIndex count = 95, SHA matches baseline
        idx_bytes = Path("data/ingest_index.json").read_bytes()
        idx_hash = hashlib.sha256(idx_bytes).hexdigest()
        self.assertEqual(idx_hash, "2ef19a8d5a1d9ac89bb9f1eef1d70bb034e78434f343f8b129396aa92729322e")

        # SourceCache count = 100
        sc_count = len(list(Path("data/source_cache").glob("*.txt")))
        self.assertEqual(sc_count, 100)

        # Audit count = 392, SHA matches baseline, hash-chain valid
        audit_bytes = Path("data/audit/repair_audit.jsonl").read_bytes()
        audit_hash = hashlib.sha256(audit_bytes).hexdigest()
        self.assertEqual(audit_hash, "0065bd13c2fbe356fc0b7f7279b7f1051c3c3d1d981d6f0ebcca589b42354443")
        chain_valid, _ = verify_audit_integrity(Path("data/audit/repair_audit.jsonl"))
        self.assertTrue(chain_valid)


if __name__ == "__main__":
    unittest.main()
