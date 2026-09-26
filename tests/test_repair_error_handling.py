# -*- coding: utf-8 -*-
"""
E6: onarım/geri alma yolunda sessizce yutulan hatalar.

Önceden okuma hataları `except Exception: pass` ile geçiliyordu ve sonuç yanlış
bir "önceki durum" ya da yanlışlıkla geçen bir doğrulama oluyordu:
- anlık görüntü: okunamayan cache/indeks/Chroma "yoktu/boştu" diye kaydediliyordu,
- geri yükleme: okunamayan yedek boş içerikle "başarılı" yazılıyordu,
- parmak izi: okuma hatası "veri yok" hash'iyle eşleşebiliyordu,
- tekrar koruması: okunamayan rollback işareti korumayı atlatıyordu.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from repair.committer import capture_before_snapshot
from repair.models import RollbackStatus
from repair.rollback import calculate_state_fingerprint, restore_source_cache, rollback_commit

HASH = "a" * 64


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="repair_err_"))
        self.cache_dir = self.tmp / "cache"
        self.cache_dir.mkdir()
        self.staging = self.tmp / "staging"
        self.index_path = self.tmp / "ingest_index.json"
        self.index_path.write_text('{"version": "2.0", "indexed_items": {}}', encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _failing_rag():
        rag = MagicMock()
        rag.collection.get.side_effect = RuntimeError("chroma okunamadı")
        return rag

    def _unreadable_cache(self):
        # Dizin olarak var olan "dosya": exists() True, read_text() hata verir.
        (self.cache_dir / f"{HASH}.txt").mkdir()


class TestSnapshotCaptureFailsLoudly(_Base):
    def _capture(self, rag=None):
        return capture_before_snapshot(
            document_id="doc", source_sha256=HASH, staging_dir=self.staging,
            cache_dir=self.cache_dir, rag=rag, index_path=self.index_path,
        )

    def test_healthy_state_is_captured(self):
        (self.cache_dir / f"{HASH}.txt").write_text("içerik", encoding="utf-8")
        rag = MagicMock()
        rag.collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        self.assertIsNotNone(self._capture(rag))

    def test_unreadable_cache_aborts(self):
        self._unreadable_cache()
        with self.assertRaisesRegex(RuntimeError, "source cache"):
            self._capture()

    def test_corrupt_index_aborts(self):
        self.index_path.write_text("{bozuk json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "ingest index"):
            self._capture()

    def test_chroma_error_aborts(self):
        with self.assertRaisesRegex(RuntimeError, "Chroma"):
            self._capture(rag=self._failing_rag())


class TestRestoreAndFingerprint(_Base):
    def test_unreadable_backup_is_not_restored_as_empty(self):
        (self.staging).mkdir()
        (self.staging / "before_cache.txt").mkdir()  # okunamaz
        target = self.cache_dir / f"{HASH}.txt"
        target.write_text("güncel içerik", encoding="utf-8")
        ok, message = restore_source_cache({"existed": True, "content": ""}, self.cache_dir, HASH, self.staging)
        self.assertFalse(ok, message)
        self.assertEqual(target.read_text(encoding="utf-8"), "güncel içerik")

    def test_fingerprint_read_errors_never_match_empty_state(self):
        empty = calculate_state_fingerprint(HASH, self.cache_dir, None, None)
        self._unreadable_cache()
        broken = calculate_state_fingerprint(HASH, self.cache_dir, self._failing_rag(), None)
        self.assertEqual(len(broken["errors"]), 2)
        self.assertNotEqual(broken["cache_hash"], empty["cache_hash"])
        self.assertEqual(broken["chunk_count"], -1)
        self.assertEqual(empty["errors"], [])


class TestRollbackMarkerGuard(_Base):
    def test_unreadable_marker_blocks_repeat_rollback_unless_forced(self):
        commit_dir = self.staging / "commit_x"
        commit_dir.mkdir(parents=True)
        (commit_dir / "rollback.json").write_text("{bozuk", encoding="utf-8")

        blocked = rollback_commit("commit_x", staging_base_dir=self.staging, cache_dir=self.cache_dir)
        self.assertEqual(blocked.status, RollbackStatus.ROLLBACK_BLOCKED.value)
        self.assertIn("işaret dosyası", blocked.failures[0])

        # force işaret engelini aşar; işlem bir sonraki güvenlik adımında (anlık görüntü yok) durur.
        forced = rollback_commit("commit_x", staging_base_dir=self.staging, cache_dir=self.cache_dir, force=True)
        self.assertNotIn("işaret dosyası", " ".join(forced.failures))
        self.assertIn("Snapshot", " ".join(forced.failures))


class TestResetChromaCollection(unittest.TestCase):
    def test_failed_delete_of_existing_collection_raises(self):
        from mufettis.rag.maintenance import reset_chroma_collection

        rag = MagicMock()
        rag.client.delete_collection.side_effect = RuntimeError("kilitli")
        rag.client.get_collection.return_value = MagicMock()  # hâlâ duruyor
        with self.assertRaisesRegex(RuntimeError, "sıfırlanamadı"):
            reset_chroma_collection(rag)
        rag.client.get_or_create_collection.assert_not_called()

    def test_missing_collection_is_created(self):
        from mufettis.rag.maintenance import reset_chroma_collection

        rag = MagicMock()
        rag.client.delete_collection.side_effect = ValueError("yok")
        rag.client.get_collection.side_effect = ValueError("yok")
        reset_chroma_collection(rag)
        rag.client.get_or_create_collection.assert_called_once()


class TestAuditChainHead(_Base):
    def test_unreadable_log_rejects_append_instead_of_restarting_chain(self):
        from repair.audit import RepairAuditLogger
        from repair.models import AuditEvent

        log_path = self.tmp / "repair_audit.jsonl"
        log_path.mkdir()  # exists() True, open() OSError verir
        logger = RepairAuditLogger(log_path=log_path)
        ok, _event, error = logger.log_event(AuditEvent(event_id="e1", event_type="TEST", timestamp="2026-09-26 00:00:00"))
        self.assertFalse(ok)
        self.assertIn("zincir", error)

    def test_healthy_log_links_events(self):
        from repair.audit import RepairAuditLogger
        from repair.models import AuditEvent

        logger = RepairAuditLogger(log_path=self.tmp / "repair_audit.jsonl")
        ok1, first, _ = logger.log_event(AuditEvent(event_id="e1", event_type="TEST", timestamp="2026-09-26 00:00:00"))
        ok2, second, _ = logger.log_event(AuditEvent(event_id="e2", event_type="TEST", timestamp="2026-09-26 00:00:01"))
        self.assertTrue(ok1 and ok2)
        self.assertEqual(second.previous_event_hash, first.event_hash)
        self.assertEqual(second.sequence_no, first.sequence_no + 1)


if __name__ == "__main__":
    unittest.main()
