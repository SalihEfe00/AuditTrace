# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19: Operational Quarantine & Telemetry Test Suite.

Covers:
A. quarantine source metadata
B. quarantine payload hash preservation
C. retry identity immutability
D. retry audit event + history preservation
E. multiple retry history
F. event vs unique-document telemetry
G. missing source metadata safe handling
H. quarantine read path zero Chroma mutation
I. retry passes through versioned_ingest_text
J. retry cannot bypass VERIFY/RESCAN
K. hash mismatch blocks retry
L. quarantine_cache is excluded from production corpus counts
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from mufettis.common.utils import sha256_bytes
from mufettis.rag.index import IngestIndex
from mufettis.rag.maintenance import versioned_ingest_text
from repair.audit import RepairAuditLogger
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


class MockCollection:
    """In-memory mock for Chroma collection providing isolated state."""

    def __init__(self):
        self.records: Dict[str, Dict[str, Any]] = {}

    def upsert(
        self,
        ids: List[str],
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        embeddings: Optional[Any] = None,
    ):
        metas = metadatas or [{}] * len(ids)
        for chunk_id, doc, meta in zip(ids, documents, metas):
            self.records[chunk_id] = {
                "id": chunk_id,
                "document": doc,
                "metadata": dict(meta),
            }

    def get(
        self,
        where: Optional[Dict[str, Any]] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        matched_ids = []
        matched_docs = []
        matched_metas = []

        where_clause = where or {}
        for chunk_id, rec in self.records.items():
            match = True
            for k, v in where_clause.items():
                if rec["metadata"].get(k) != v:
                    match = False
                    break
            if match:
                matched_ids.append(chunk_id)
                matched_docs.append(rec["document"])
                matched_metas.append(rec["metadata"])

        res = {"ids": matched_ids}
        if include is None or "documents" in include:
            res["documents"] = matched_docs
        if include is None or "metadatas" in include:
            res["metadatas"] = matched_metas
        return res

    def delete(self, where: Optional[Dict[str, Any]] = None):
        if not where:
            self.records.clear()
            return
        to_delete = []
        for chunk_id, rec in self.records.items():
            match = True
            for k, v in where.items():
                if rec["metadata"].get(k) != v:
                    match = False
                    break
            if match:
                to_delete.append(chunk_id)
        for cid in to_delete:
            self.records.pop(cid, None)

    def count(self) -> int:
        return len(self.records)


class MockLocalRAG:
    """Mock RAG engine simulating LocalRAG without loading neural models."""

    def __init__(self):
        self.collection = MockCollection()
        self.simulate_rescan_corruption = False

    def delete_hash(self, file_hash: str):
        self.collection.delete(where={"file_hash": file_hash})

    def ingest_text(self, text: str, meta: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
        file_hash = meta["file_hash"]
        if force:
            self.delete_hash(file_hash)

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text]

        chunk_ids = []
        docs = []
        metas = []
        for i, p in enumerate(paragraphs):
            cid = f"{file_hash}_chunk_{i}"
            chunk_ids.append(cid)
            doc_text = p
            if self.simulate_rescan_corruption:
                doc_text = "MADDE 1 \x00\x01\x02 BozukKarakterler"

            docs.append(doc_text)
            c_meta = dict(meta)
            c_meta["chunk_id"] = cid
            c_meta["chunk_index"] = i
            c_meta["file_hash"] = file_hash
            c_meta["document_id"] = meta.get("document_id") or file_hash
            c_meta["source_id"] = meta.get("source_id") or "test_source"
            c_meta["title"] = meta.get("title") or "Test Document"
            metas.append(c_meta)

        self.collection.upsert(ids=chunk_ids, documents=docs, metadatas=metas)
        return {"status": "ok", "count": len(chunk_ids)}

    def count(self) -> int:
        return self.collection.count()


class TestQuarantineOperational(unittest.TestCase):
    """Test suite for FAZ 19 Operational Quarantine and Telemetry."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.index_path = self.temp_dir / "test_ingest_index.json"
        self.index = IngestIndex(self.index_path)
        self.audit_path = self.temp_dir / "test_repair_audit.jsonl"
        self.audit_logger = RepairAuditLogger(self.audit_path)
        self.cache_dir = self.temp_dir / "quarantine_cache"
        self.rag = MockLocalRAG()
        import unittest.mock
        self.save_cache_patcher = unittest.mock.patch("mufettis.rag.maintenance.save_source_cache")
        self.save_cache_patcher.start()

    def tearDown(self):
        self.save_cache_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_a_quarantine_record_contains_source_metadata(self):
        """Test A: Quarantine record contains full source metadata and default operational fields."""
        raw_bytes = b"Unrecoverable corrupt binary content \x00\x01"
        file_hash = sha256_bytes(raw_bytes)
        corrupted_text = "Bozuk \ufffd\ufffd\ufffd icerik score 0.0"
        meta = {
            "file_hash": file_hash,
            "document_id": "bddk_corrupt_01",
            "document_key": "kanun_corrupt",
            "source_id": "bddk_official",
            "source_type": "official_web_source",
            "url": "https://www.bddk.org.tr/mevzuat/corrupt_01.pdf",
            "title": "BDDK Corrupt Test Mevzuati",
            "is_pdf": True,
        }

        res = versioned_ingest_text(
            rag=self.rag,
            index=self.index,
            text=corrupted_text,
            meta=meta,
            force=True,
            raw_bytes=raw_bytes,
        )

        self.assertEqual(res["status"], "quarantined")
        rec = self.index.get(file_hash)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "quarantined")
        self.assertEqual(rec["document_id"], "bddk_corrupt_01")
        self.assertTrue(rec.get("document_key"))
        self.assertEqual(rec["source_id"], "bddk_official")
        self.assertEqual(rec["url"], "https://www.bddk.org.tr/mevzuat/corrupt_01.pdf")
        self.assertEqual(rec["title"], "BDDK Corrupt Test Mevzuati")
        self.assertEqual(rec["retry_count"], 0)
        self.assertFalse(rec["reviewed"])
        self.assertIsInstance(rec["quarantine_history"], list)
        self.assertEqual(len(rec["quarantine_history"]), 1)

    def test_b_quarantine_payload_hash_preservation(self):
        """Test B: Quarantine payload binary and text are preserved and hash is verifiable."""
        payload_bytes = b"Sample legal text raw bytes 12345"
        f_hash = sha256_bytes(payload_bytes)
        text = "Sample legal text extracted"

        save_res = save_quarantine_payload(
            f_hash,
            text=text,
            raw_bytes=payload_bytes,
            cache_dir=self.cache_dir,
        )

        self.assertTrue(save_res["saved_bytes"])
        self.assertTrue(save_res["saved_text"])

        loaded_text, loaded_bytes = load_quarantine_payload(f_hash, cache_dir=self.cache_dir)
        self.assertEqual(loaded_bytes, payload_bytes)
        self.assertEqual(loaded_text, text)

        valid, msg = verify_quarantine_payload_integrity(f_hash, cache_dir=self.cache_dir)
        self.assertTrue(valid)
        self.assertIn("verified", msg)

    def test_c_retry_identity_immutability(self):
        """Test C: Retry maintains exact file_hash and document_id identity."""
        raw_bytes = b"Legal content for identity test"
        f_hash = sha256_bytes(raw_bytes)
        text = "MADDE 1 - Temiz hukuk maddesi."
        meta = {
            "file_hash": f_hash,
            "document_id": "doc_identity_test",
            "document_key": "kanun_test",
            "source_id": "bddk_official",
            "title": "Identity Test Kanunu",
        }

        # First quarantine it manually
        save_quarantine_payload(f_hash, text=text, raw_bytes=raw_bytes, cache_dir=self.cache_dir)
        meta["status"] = "quarantined"
        meta["quarantine_reason"] = "Manual quarantine for test"
        self.index.upsert(f_hash, meta)

        # Retry through retry_quarantined_document
        res = retry_quarantined_document(
            rag=self.rag,
            index=self.index,
            file_hash=f_hash,
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )

        self.assertEqual(res["status"], "indexed")
        updated = self.index.get(f_hash)
        self.assertEqual(updated["file_hash"], f_hash)
        self.assertEqual(updated["document_id"], "doc_identity_test")
        self.assertEqual(updated["status"], "active")

    def test_d_retry_audit_event_and_history_preservation(self):
        """Test D: Retry emits INGEST_RETRY_STARTED and preserves history in index and audit."""
        raw_bytes = b"Corrupted retry test bytes"
        f_hash = sha256_bytes(raw_bytes)
        text = "Corrupted \ufffd\ufffd\ufffd text"
        meta = {
            "file_hash": f_hash,
            "document_id": "retry_audit_doc",
            "source_id": "test_src",
            "title": "Retry Audit Test",
            "status": "quarantined",
            "quarantine_reason": "Initial corrupt quality",
            "quarantine_history": [{"attempt": 0, "reason": "Initial corrupt quality"}],
        }
        save_quarantine_payload(f_hash, text=text, raw_bytes=raw_bytes, cache_dir=self.cache_dir)
        self.index.upsert(f_hash, meta)

        self.rag.simulate_rescan_corruption = True
        retry_quarantined_document(
            rag=self.rag,
            index=self.index,
            file_hash=f_hash,
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )

        # Audit events check
        events = self.audit_logger.get_events()
        event_types = [e.event_type for e in events]
        self.assertIn(AuditEventType.INGEST_RETRY_STARTED.value, event_types)

        # Index check
        rec = self.index.get(f_hash)
        self.assertEqual(rec["retry_count"], 1)
        self.assertGreaterEqual(len(rec["quarantine_history"]), 2)

    def test_e_multiple_retry_history(self):
        """Test E: Multiple retries monotonically increment retry_count and append history."""
        raw_bytes = b"Multiple retry unrecoverable"
        f_hash = sha256_bytes(raw_bytes)
        text = "Bozuk \ufffd\ufffd\ufffd metin"
        save_quarantine_payload(f_hash, text=text, raw_bytes=raw_bytes, cache_dir=self.cache_dir)

        meta = {
            "file_hash": f_hash,
            "document_id": "multi_retry_doc",
            "source_id": "bddk",
            "status": "quarantined",
            "quarantine_reason": "Failed initially",
        }
        self.index.upsert(f_hash, meta)

        self.rag.simulate_rescan_corruption = True
        for attempt in range(1, 4):
            retry_quarantined_document(
                rag=self.rag,
                index=self.index,
                file_hash=f_hash,
                cache_dir=self.cache_dir,
                audit_logger=self.audit_logger,
            )
            rec = self.index.get(f_hash)
            self.assertEqual(rec["retry_count"], attempt)
            self.assertEqual(len(rec["quarantine_history"]), attempt)

    def test_f_event_vs_unique_document_telemetry(self):
        """Test F: Distinguishes between event count and unique document count."""
        raw_1 = b"Doc 1 bytes"
        h1 = sha256_bytes(raw_1)

        # Emit 3 quarantine events for the SAME document h1
        for i in range(3):
            self.audit_logger.emit(
                event_type=AuditEventType.INGEST_QUARANTINED.value,
                repair_id=f"quarantine_{h1[:12]}_{i}",
                document_id="doc_1",
                source_sha256=h1,
                reason="Verification failed",
                status="QUARANTINED",
                details={"issues": ["VERIFY_FAIL"], "is_retry": i > 0},
            )

        self.index.upsert(h1, {"status": "quarantined", "file_hash": h1, "document_id": "doc_1"})

        telemetry = compute_operational_telemetry(
            log_path=self.audit_path,
            index=self.index,
        )

        ev_m = telemetry["event_metrics"]
        uq_m = telemetry["unique_document_metrics"]

        # 3 quarantine events occurred
        self.assertEqual(ev_m["quarantined_events"], 3)
        self.assertEqual(ev_m["verification_failure_events"], 3)

        # But only 1 unique document is in quarantine and processed!
        self.assertEqual(uq_m["unique_documents_processed"], 1)
        self.assertEqual(uq_m["unique_documents_in_quarantine"], 1)

    def test_g_missing_source_metadata_safe_handling(self):
        """Test G: Handles missing source payload safely without unhandled exception."""
        f_hash = "non_existent_hash_1234567890abcdef1234567890abcdef1234567890abcdef"
        self.index.upsert(f_hash, {
            "file_hash": f_hash,
            "document_id": "ghost_doc",
            "status": "quarantined",
            "quarantine_reason": "Lost payload",
        })

        res = retry_quarantined_document(
            rag=self.rag,
            index=self.index,
            file_hash=f_hash,
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )

        self.assertEqual(res["status"], "error")
        self.assertIn("No cached payload or readable source found", res["error"])

    def test_h_quarantine_read_path_zero_chroma_mutation(self):
        """Test H: Listing and querying quarantine causes zero Chroma calls or mutations."""
        initial_chroma_count = self.rag.count()

        # Add 2 quarantined docs in index
        for i in range(2):
            h = f"hash_read_test_{i:04d}" + "0" * 48
            self.index.upsert(h, {
                "file_hash": h,
                "document_id": f"read_test_{i}",
                "status": "quarantined",
                "title": f"Doc {i}",
            })

        # Call read-only query APIs
        items = list_quarantined_documents(index=self.index, cache_dir=self.cache_dir)
        self.assertEqual(len(items), 2)

        detail = get_quarantine_detail(items[0]["file_hash"], index=self.index, cache_dir=self.cache_dir)
        self.assertIsNotNone(detail)

        telemetry = compute_operational_telemetry(log_path=self.audit_path, index=self.index)
        self.assertIsNotNone(telemetry)

        # Chroma count remains identical
        self.assertEqual(self.rag.count(), initial_chroma_count)

    def test_i_retry_passes_through_versioned_ingest_text(self):
        """Test I: Successful retry routes through versioned_ingest_text and becomes indexed."""
        raw_bytes = b"Valid legal text for successful retry"
        f_hash = sha256_bytes(raw_bytes)
        clean_text = "MADDE 1 - Bankalar risk yonetimi sistemlerini kurar.\n\nMADDE 2 - Denetim uygulanir."

        save_quarantine_payload(f_hash, text=clean_text, raw_bytes=raw_bytes, cache_dir=self.cache_dir)
        self.index.upsert(f_hash, {
            "file_hash": f_hash,
            "document_id": "retry_success_doc",
            "source_id": "bddk_official",
            "title": "Risk Yonetimi Tebligi",
            "status": "quarantined",
            "quarantine_reason": "Initial failure",
        })

        res = retry_quarantined_document(
            rag=self.rag,
            index=self.index,
            file_hash=f_hash,
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )

        self.assertEqual(res["status"], "indexed")
        self.assertGreater(res["count"], 0)
        self.assertGreater(self.rag.count(), 0)

        updated = self.index.get(f_hash)
        self.assertEqual(updated["status"], "active")
        self.assertEqual(updated["retry_count"], 1)

    def test_j_retry_cannot_bypass_verify_rescan(self):
        """Test J: Retry cannot bypass VERIFY or RESCAN; failure triggers rollback to quarantine."""
        raw_bytes = b"Rescan corrupt retry bytes"
        f_hash = sha256_bytes(raw_bytes)
        text = "MADDE 1 - Normal gibi gorunen madde."

        save_quarantine_payload(f_hash, text=text, raw_bytes=raw_bytes, cache_dir=self.cache_dir)
        self.index.upsert(f_hash, {
            "file_hash": f_hash,
            "document_id": "rescan_fail_retry_doc",
            "source_id": "bddk_official",
            "title": "Rescan Failure Doc",
            "status": "quarantined",
            "quarantine_reason": "Initial quarantine",
        })

        # Simulate rescan failure inside mock rag
        self.rag.simulate_rescan_corruption = True

        res = retry_quarantined_document(
            rag=self.rag,
            index=self.index,
            file_hash=f_hash,
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )

        self.assertEqual(res["status"], "quarantined")
        # Ensure Chroma was rolled back and count is 0!
        self.assertEqual(self.rag.count(), 0)
        rec = self.index.get(f_hash)
        self.assertEqual(rec["status"], "quarantined")
        self.assertEqual(rec["retry_count"], 1)

    def test_k_hash_mismatch_blocks_retry(self):
        """Test K: Corrupted payload with mismatched SHA256 blocks retry."""
        real_bytes = b"Real content bytes"
        f_hash = sha256_bytes(real_bytes)

        # Write wrong bytes into cache
        tampered_bytes = b"Tampered different bytes"
        cdir = self.cache_dir
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{f_hash}.bin").write_bytes(tampered_bytes)

        self.index.upsert(f_hash, {
            "file_hash": f_hash,
            "document_id": "tampered_doc",
            "status": "quarantined",
            "quarantine_reason": "Initial",
        })

        res = retry_quarantined_document(
            rag=self.rag,
            index=self.index,
            file_hash=f_hash,
            cache_dir=self.cache_dir,
            audit_logger=self.audit_logger,
        )

        self.assertEqual(res["status"], "blocked")
        self.assertIn("integrity check failed", res["error"])

    def test_l_quarantine_cache_excluded_from_production_corpus(self):
        """Test L: Quarantine cache directory is completely distinct from source_cache."""
        from mufettis.config import SOURCE_CACHE_DIR, QUARANTINE_CACHE_DIR

        self.assertNotEqual(SOURCE_CACHE_DIR.resolve(), QUARANTINE_CACHE_DIR.resolve())
        self.assertFalse(str(QUARANTINE_CACHE_DIR).startswith(str(SOURCE_CACHE_DIR)))

    def test_m_review_quarantine_document_workflow(self):
        """Test M: Review/acknowledge workflow updates quarantine metadata without Chroma mutation."""
        f_hash = "review_test_hash_1234567890abcdef1234567890abcdef12345678"
        self.index.upsert(f_hash, {
            "file_hash": f_hash,
            "document_id": "review_doc",
            "status": "quarantined",
            "quarantine_reason": "Low score",
        })

        res = review_quarantine_document(
            file_hash=f_hash,
            reviewer="chief_inspector",
            notes="Reviewed: awaiting upstream fix from BDDK IT",
            index=self.index,
            audit_logger=self.audit_logger,
        )

        self.assertEqual(res["status"], "reviewed")
        self.assertEqual(res["reviewed_by"], "chief_inspector")

        updated = self.index.get(f_hash)
        self.assertTrue(updated["reviewed"])
        self.assertEqual(updated["review_notes"], "Reviewed: awaiting upstream fix from BDDK IT")

        # Verify audit event emitted
        events = self.audit_logger.get_events()
        review_events = [e for e in events if e.event_type == AuditEventType.INGEST_QUARANTINE_REVIEWED.value]
        self.assertEqual(len(review_events), 1)
        self.assertEqual(review_events[0].status, "REVIEWED")


if __name__ == "__main__":
    unittest.main()
