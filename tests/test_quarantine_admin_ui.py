# -*- coding: utf-8 -*-
"""
Tests for FAZ 19 - Aşama 4: Quarantine & Operations Admin UI.

Verifies:
1. Empty quarantine list rendering
2. Single quarantined doc render
3. Multiple quarantined docs & filtering
4. Item detail view
5. Quarantine review workflow
6. Quarantine review persistence
7. Canonical retry success routing
8. Canonical retry failure / re-quarantine routing
9. Telemetry panel rendering
10. Production health panel rendering
11. Invalid payload handling (missing cache)
12. Integrity mismatch handling (corrupted cache)
13. Zero direct Chroma mutation from UI actions
14. Compatibility with existing admin UI
15. Production filesystem untouched (zero production mutation)
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mufettis.config import DATA_DIR, BASE_DIR
from mufettis.common.utils import sha256_bytes, now_iso
from mufettis.rag.index import IngestIndex
from repair.audit import RepairAuditLogger, verify_audit_integrity
from repair.models import AuditEventType
from repair.quarantine import (
    save_quarantine_payload,
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
    render_mevzuat_admin,
)


class TestQuarantineAdminUI(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_admin_ui_")
        self.temp_path = Path(self.temp_dir)
        self.index_file = self.temp_path / "ingest_index.json"
        self.audit_file = self.temp_path / "repair_audit.jsonl"
        self.cache_dir = self.temp_path / "quarantine_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.index = IngestIndex(path=self.index_file)
        self.audit_logger = RepairAuditLogger(log_path=self.audit_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_quarantined_entry(self, title: str, text: str, source_id: str = "bddk_official", reviewed: bool = False):
        raw_bytes = text.encode("utf-8")
        f_hash = sha256_bytes(raw_bytes)
        meta = {
            "file_hash": f_hash,
            "document_id": f"doc_{f_hash[:8]}",
            "document_key": f"key_{f_hash[:8]}",
            "title": title,
            "source_id": source_id,
            "source_type": "web",
            "status": "quarantined",
            "quarantine_reason": "Low quality extraction score",
            "quarantine_issues": ["Low dictionary word ratio: 0.15", "glued tokens detected"],
            "quarantined_at": now_iso(),
            "retry_count": 0,
            "reviewed": reviewed,
            "reviewed_at": now_iso() if reviewed else None,
            "reviewed_by": "auditor_1" if reviewed else None,
            "review_notes": "Previous note" if reviewed else None,
        }
        self.index.upsert(f_hash, meta)
        save_quarantine_payload(
            file_hash=f_hash,
            text=text,
            raw_bytes=raw_bytes,
            cache_dir=self.cache_dir,
        )
        return f_hash, meta

    def test_1_empty_quarantine_panel_renders(self):
        """Empty quarantine panel should render without exception and show zero metrics."""
        rag_mock = MagicMock()
        rag_mock.count.return_value = 0
        render_quarantine_admin_panel(
            rag=rag_mock,
            index=self.index,
            audit_logger=self.audit_logger,
            cache_dir=self.cache_dir,
        )
        docs = list_quarantined_documents(index=self.index, cache_dir=self.cache_dir)
        self.assertEqual(len(docs), 0)

    def test_2_single_quarantined_doc_render(self):
        """Single quarantined document renders with details and valid payload integrity."""
        f_hash, meta = self._create_quarantined_entry("Bankacılık Düzenleme Kararı", "Örnek metin içeriği")
        docs = list_quarantined_documents(index=self.index, cache_dir=self.cache_dir)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["file_hash"], f_hash)
        self.assertTrue(docs[0]["has_cached_payload"])

        detail = get_quarantine_detail(f_hash, index=self.index, cache_dir=self.cache_dir)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["title"], "Bankacılık Düzenleme Kararı")
        self.assertTrue(detail["payload_integrity_valid"])

        rag_mock = MagicMock()
        render_quarantine_admin_panel(
            rag=rag_mock,
            index=self.index,
            audit_logger=self.audit_logger,
            cache_dir=self.cache_dir,
        )

    def test_3_multiple_quarantined_docs_filtering(self):
        """Multiple documents can be filtered by reviewed status."""
        h1, _ = self._create_quarantined_entry("Belge 1", "İçerik 1", reviewed=False)
        h2, _ = self._create_quarantined_entry("Belge 2", "İçerik 2", reviewed=False)
        h3, _ = self._create_quarantined_entry("Belge 3", "İçerik 3", reviewed=True)

        all_docs = list_quarantined_documents(index=self.index, cache_dir=self.cache_dir)
        self.assertEqual(len(all_docs), 3)

        unreviewed = list_quarantined_documents(index=self.index, filter_reviewed=False, cache_dir=self.cache_dir)
        self.assertEqual(len(unreviewed), 2)
        self.assertNotIn(h3, [d["file_hash"] for d in unreviewed])

        reviewed = list_quarantined_documents(index=self.index, filter_reviewed=True, cache_dir=self.cache_dir)
        self.assertEqual(len(reviewed), 1)
        self.assertEqual(reviewed[0]["file_hash"], h3)

    def test_4_item_detail_view(self):
        """Item detail view exposes required metadata and issue details."""
        f_hash, _ = self._create_quarantined_entry("Kredi Risk Raporu", "Kredi risk hesaplamaları")
        detail = get_quarantine_detail(f_hash, index=self.index, cache_dir=self.cache_dir)
        self.assertIn("quarantine_issues", detail)
        self.assertEqual(len(detail["quarantine_issues"]), 2)
        self.assertEqual(detail["retry_count"], 0)
        self.assertTrue(detail["has_binary_cache"])
        self.assertTrue(detail["has_text_cache"])

    def test_5_quarantine_review_workflow(self):
        """Review workflow correctly records reviewer and notes."""
        f_hash, _ = self._create_quarantined_entry("İncelenecek Belge", "Deneme içerik")
        res = review_quarantine_document(
            file_hash=f_hash,
            reviewer="kullanici_ali",
            notes="Kaynak incelendi, retry denenecek.",
            index=self.index,
            audit_logger=self.audit_logger,
        )
        self.assertEqual(res["status"], "reviewed")
        self.assertEqual(res["reviewed_by"], "kullanici_ali")
        self.assertEqual(res["notes"], "Kaynak incelendi, retry denenecek.")

    def test_6_quarantine_review_persistence(self):
        """Review update persists in IngestIndex and emits tamper-evident audit event."""
        f_hash, _ = self._create_quarantined_entry("Kalıcı İnceleme", "Kalıcılık testi")
        review_quarantine_document(
            file_hash=f_hash,
            reviewer="kullanici_veli",
            notes="Audit kaydı doğrulanıyor",
            index=self.index,
            audit_logger=self.audit_logger,
        )
        # Check index persistence
        saved_meta = self.index.get(f_hash)
        self.assertTrue(saved_meta["reviewed"])
        self.assertEqual(saved_meta["reviewed_by"], "kullanici_veli")
        self.assertEqual(saved_meta["review_notes"], "Audit kaydı doğrulanıyor")

        # Check audit event
        events = [json.loads(l) for l in self.audit_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        review_events = [e for e in events if e.get("event_type") == AuditEventType.INGEST_QUARANTINE_REVIEWED.value]
        self.assertEqual(len(review_events), 1)
        self.assertEqual(review_events[0]["actor"], "kullanici_veli")

        # Verify audit hash-chain integrity
        valid, errs = verify_audit_integrity(self.audit_file)
        self.assertTrue(valid, f"Audit hash-chain broken: {errs}")

    @patch("mufettis.rag.maintenance.save_source_cache")
    def test_7_canonical_retry_success(self, mock_save_source_cache):
        """Canonical retry succeeds for high quality content and indexes document."""
        high_quality_turkish = (
            "MADDE 1 - (1) Bu Kanunun amacı, bankacılık sektöründe güven ve istikrarın "
            "sağlanması, mali sektörün etkin bir şekilde çalışması ve tasarruf sahiplerinin "
            "hak ve menfaatlerinin korunmasına ilişkin usul ve esasları düzenlemektir. "
            "Bu Kanun hükümleri Türkiye'de faaliyet gösteren tüm bankaları kapsar."
        )
        f_hash, _ = self._create_quarantined_entry("Bankacılık Kanunu", high_quality_turkish)

        rag = MockLocalRAG()

        res = retry_quarantined_document(
            rag=rag,
            index=self.index,
            file_hash=f_hash,
            trigger="admin_ui_retry",
            actor="test_admin",
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )
        self.assertIn(res.get("status"), ("indexed", "active", "updated", "ingested"))
        self.assertGreater(res.get("count", 0), 0)

        # Check updated index
        doc_meta = self.index.get(f_hash)
        self.assertEqual(doc_meta.get("status"), "active")
        self.assertEqual(doc_meta.get("retry_count"), 1)

    @patch("mufettis.rag.maintenance.save_source_cache")
    def test_8_canonical_retry_failure_requarantines(self, mock_save_source_cache):
        """Canonical retry for garbage text fails verification and re-quarantines."""
        garbage_text = "asdf qwert zxcvb 12345 !@#$%^ &*()_+ ????"
        f_hash, _ = self._create_quarantined_entry("Bozuk Belge", garbage_text)

        rag = MockLocalRAG()
        rag.simulate_rescan_corruption = True

        res = retry_quarantined_document(
            rag=rag,
            index=self.index,
            file_hash=f_hash,
            trigger="admin_ui_retry",
            actor="test_admin",
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )
        self.assertEqual(res.get("status"), "quarantined")
        self.assertTrue(res.get("error") or res.get("meta", {}).get("quarantine_reason"))

        # Verify retry count incremented in index
        doc_meta = self.index.get(f_hash)
        self.assertEqual(doc_meta.get("status"), "quarantined")
        self.assertEqual(doc_meta.get("retry_count"), 1)

    def test_9_telemetry_panel_rendering(self):
        """Telemetry panel computes and renders metrics without error."""
        # Emit a few audit events
        self.audit_logger.emit(
            event_type=AuditEventType.INGEST_QUALITY_PASSED.value,
            repair_id="rep_1",
            document_id="doc_1",
            source_sha256="hash1",
            status="PASSED",
        )
        self.audit_logger.emit(
            event_type=AuditEventType.INGEST_SELF_HEALED.value,
            repair_id="rep_2",
            document_id="doc_2",
            source_sha256="hash2",
            status="HEALED",
            details={"raw_score": 0.4, "final_score": 0.8},
        )
        self.audit_logger.emit(
            event_type=AuditEventType.INGEST_QUARANTINED.value,
            repair_id="rep_3",
            document_id="doc_3",
            source_sha256="hash3",
            status="QUARANTINED",
            reason="Quality threshold failed",
        )

        telemetry = compute_operational_telemetry(log_path=self.audit_file, index=self.index)
        ev = telemetry["event_metrics"]
        self.assertEqual(ev["total_ingest_events"], 3)
        self.assertEqual(ev["quality_passed_events"], 1)
        self.assertEqual(ev["self_healed_events"], 1)
        self.assertEqual(ev["quarantined_events"], 1)

        qm = telemetry["quality_metrics"]
        self.assertAlmostEqual(qm["avg_quality_delta"], 0.4, places=2)

        # UI function render test
        render_telemetry_admin_panel(audit_path=self.audit_file, index=self.index)

    def test_10_production_health_panel_rendering(self):
        """Production health panel renders strictly read-only metrics."""
        rag_mock = MagicMock()
        rag_mock.count.return_value = 100

        render_production_health_panel(
            rag=rag_mock,
            index=self.index,
            audit_path=self.audit_file,
        )

    def test_11_invalid_payload_missing_cache(self):
        """Documents missing cache payload report integrity failure."""
        empty_hash = "0" * 64
        valid, msg = verify_quarantine_payload_integrity(empty_hash, cache_dir=self.cache_dir)
        self.assertFalse(valid)
        self.assertIn("No quarantine payload found", msg)

    def test_12_integrity_mismatch_corrupted_payload(self):
        """Tampered payload binary fails integrity check and blocks retry."""
        real_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        meta = {
            "file_hash": real_hash,
            "document_id": "doc_corrupt",
            "title": "Corrupt Doc",
            "status": "quarantined",
            "quarantined_at": now_iso(),
        }
        self.index.upsert(real_hash, meta)

        # Write wrong binary data
        corrupt_bin = self.cache_dir / f"{real_hash}.bin"
        corrupt_bin.write_bytes(b"wrong corrupted content")

        valid, msg = verify_quarantine_payload_integrity(real_hash, cache_dir=self.cache_dir)
        self.assertFalse(valid)
        self.assertIn("mismatch", msg)

        # Attempt retry should be blocked
        rag_mock = MagicMock()
        res = retry_quarantined_document(
            rag=rag_mock,
            index=self.index,
            file_hash=real_hash,
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )
        self.assertEqual(res["status"], "blocked")
        self.assertIn("integrity check failed", res["error"])

    def test_13_zero_direct_chroma_mutation_from_ui(self):
        """Quarantine UI components must not call direct Chroma collection mutations."""
        f_hash, _ = self._create_quarantined_entry("Denetim Testi", "İçerik")
        rag_mock = MagicMock()

        # Rendering panel must never call add/upsert/delete on rag
        render_quarantine_admin_panel(
            rag=rag_mock,
            index=self.index,
            audit_logger=self.audit_logger,
            cache_dir=self.cache_dir,
        )
        self.assertEqual(rag_mock.upsert.call_count, 0)
        self.assertEqual(rag_mock.delete.call_count, 0)
        self.assertEqual(rag_mock.add_documents.call_count, 0)

        # Reviewing document must never call rag
        review_quarantine_document(
            file_hash=f_hash,
            reviewer="operator",
            notes="not",
            index=self.index,
            audit_logger=self.audit_logger,
        )
        self.assertEqual(rag_mock.upsert.call_count, 0)
        self.assertEqual(rag_mock.delete.call_count, 0)

    def test_14_compatibility_with_existing_admin_ui(self):
        """Existing render_mevzuat_admin integrates new panels seamlessly."""
        rag_mock = MagicMock()
        rag_mock.count.return_value = 42

        # render_mevzuat_admin should execute without throwing exceptions
        try:
            render_mevzuat_admin(
                rag=rag_mock,
                local_only=True,
                use_browser_bddk=False,
                allow_embedding_download=False,
            )
        except Exception as exc:
            self.fail(f"render_mevzuat_admin raised unexpected exception: {exc}")

    def test_15_production_filesystem_untouched(self):
        """Strict verification that production assets were NOT modified during tests."""
        import sqlite3

        # 1. Chroma count
        conn = sqlite3.connect("file:data/mevzuat_db/chroma.sqlite3?mode=ro", uri=True)
        cur = conn.cursor()
        emb_count = cur.execute("SELECT count(*) FROM embeddings").fetchone()[0]
        prag = cur.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        self.assertEqual(emb_count, 21773)
        self.assertEqual(prag, "ok")

        # 2. IngestIndex count & hash
        idx_bytes = (DATA_DIR / "ingest_index.json").read_bytes()
        idx_hash = hashlib.sha256(idx_bytes).hexdigest()
        self.assertEqual(idx_hash, "2ef19a8d5a1d9ac89bb9f1eef1d70bb034e78434f343f8b129396aa92729322e")
        idx_data = json.loads(idx_bytes)
        self.assertEqual(len(idx_data.get("indexed_items", {})), 95)

        # 3. SourceCache count
        sc_count = len(list((DATA_DIR / "source_cache").glob("*.txt")))
        self.assertEqual(sc_count, 100)

        # 4. Audit Log lines & hash-chain
        audit_bytes = (DATA_DIR / "audit/repair_audit.jsonl").read_bytes()
        audit_hash = hashlib.sha256(audit_bytes).hexdigest()
        self.assertEqual(audit_hash, "0065bd13c2fbe356fc0b7f7279b7f1051c3c3d1d981d6f0ebcca589b42354443")
        audit_lines = len([l for l in audit_bytes.splitlines() if l.strip()])
        self.assertEqual(audit_lines, 392)

        chain_valid, chain_errs = verify_audit_integrity((DATA_DIR / "audit/repair_audit.jsonl"))
        self.assertTrue(chain_valid)


if __name__ == "__main__":
    unittest.main()
