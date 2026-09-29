# -*- coding: utf-8 -*-
"""
E7: İçe aktarma kapısı reddettiği yeni sürüm yüzünden çalışan eski sürümü silmemeli.

Önceden kapı rag.ingest_text(force=True) ile mevcut parçaları siliyor, karantina
kararında da yeni parçaları siliyordu; sonuçta belge tamamen kayboluyordu (production
kopyasında 5411 Bankacılık Kanunu bu şekilde kayboldu). Ayrıca yeniden tarama, kısa bir
atıf kırıntısı yüzünden koca bir kanunu reddediyordu.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from mufettis.rag.index import IngestIndex
from repair.audit import RepairAuditLogger
from repair.ingestion_gate import RESCAN_MIN_SCORED_CHARS, AutoHealingIngestionGate

HASH = "c" * 64
GOOD_TEXT = (
    "MADDE 1 – Amaç\n(1) Bu Kanunun amacı, finansal piyasalarda güven ve istikrarın sağlanması, "
    "kredi sisteminin etkin bir şekilde çalışması ile tasarruf sahiplerinin hak ve menfaatlerinin "
    "korunmasına ilişkin usul ve esasları düzenlemektir.\n"
)


class EmbeddingCollection:
    def __init__(self):
        self.records: Dict[str, Dict[str, Any]] = {}

    def upsert(self, ids, documents, metadatas=None, embeddings=None):
        for i, cid in enumerate(ids):
            self.records[cid] = {
                "document": documents[i],
                "metadata": dict((metadatas or [{}] * len(ids))[i]),
                "embedding": list(embeddings[i]) if embeddings is not None else [0.0, 0.0, 1.0],
            }

    def get(self, where=None, include=None, ids=None):
        rows = [(cid, r) for cid, r in self.records.items()
                if all(r["metadata"].get(k) == v for k, v in (where or {}).items())]
        out = {"ids": [cid for cid, _ in rows]}
        inc = include or ["documents", "metadatas"]
        if "documents" in inc:
            out["documents"] = [r["document"] for _, r in rows]
        if "metadatas" in inc:
            out["metadatas"] = [r["metadata"] for _, r in rows]
        if "embeddings" in inc:
            out["embeddings"] = [r["embedding"] for _, r in rows]
        return out

    def delete(self, where=None, ids=None):
        if ids is not None:
            for cid in ids:
                self.records.pop(cid, None)
            return
        for cid in [cid for cid, r in self.records.items()
                    if all(r["metadata"].get(k) == v for k, v in (where or {}).items())]:
            self.records.pop(cid)


class FakeRag:
    def __init__(self, collection):
        self.collection = collection

    def delete_hash(self, file_hash):
        self.collection.delete(where={"file_hash": file_hash})

    def ingest_text(self, text, meta, force=False):
        if force:
            self.delete_hash(meta["file_hash"])
        parts = [p for p in text.split("\n") if p.strip()]
        self.collection.upsert(
            ids=[f"new-{i}" for i in range(len(parts))],
            documents=parts,
            metadatas=[{"file_hash": meta["file_hash"], "chunk_schema_version": "10.0.0"} for _ in parts],
            embeddings=[[0.1, 0.2, 0.3] for _ in parts],
        )
        return {"status": "ingested", "count": len(parts)}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gate_prev_"))
        self.index = IngestIndex(path=self.tmp / "ingest_index.json")
        self.gate = AutoHealingIngestionGate(audit_logger=RepairAuditLogger(log_path=self.tmp / "audit.jsonl"))
        self.collection = EmbeddingCollection()
        self.rag = FakeRag(self.collection)
        # Çalışan eski sürüm: 2 parça + aktif indeks kaydı
        self.collection.upsert(
            ids=["old-1", "old-2"], documents=["eski madde 1", "eski madde 2"],
            metadatas=[{"file_hash": HASH, "chunk_schema_version": "10.0.0"}] * 2,
            embeddings=[[0.9, 0.1, 0.0], [0.8, 0.2, 0.0]],
        )
        self.index.upsert(HASH, {"file_hash": HASH, "title": "Eski Sürüm", "status": "active", "chunk_count": 2})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestQuarantineRestoresPreviousVersion(_Base):
    def test_rescan_failure_restores_old_chunks_and_active_record(self):
        with patch.object(self.gate, "rescan_committed_chunks", return_value=(False, ["RESCAN_TEST"], {})):
            result = self.gate.ingest(self.rag, self.index, GOOD_TEXT, {"file_hash": HASH, "title": "Yeni Sürüm"}, force=True)

        self.assertEqual(result.get("status"), "quarantined")
        self.assertEqual(sorted(self.collection.records), ["old-1", "old-2"])
        self.assertEqual(self.collection.records["old-1"]["embedding"], [0.9, 0.1, 0.0])
        record = self.index.get(HASH)
        self.assertEqual(record["status"], "active")
        self.assertEqual(record["title"], "Eski Sürüm")
        self.assertEqual(record["last_rejected_ingest"]["issues"], ["RESCAN_TEST"])

    def test_first_ingest_rejection_still_quarantines_cleanly(self):
        self.collection.records.clear()
        self.index.data["indexed_items"].pop(HASH)
        with patch.object(self.gate, "rescan_committed_chunks", return_value=(False, ["RESCAN_TEST"], {})):
            self.gate.ingest(self.rag, self.index, GOOD_TEXT, {"file_hash": HASH, "title": "Yeni"}, force=True)
        self.assertEqual(self.collection.records, {})
        self.assertEqual(self.index.get(HASH)["status"], "quarantined")

    def test_unreadable_previous_state_stops_before_any_write(self):
        def broken_get(where=None, include=None, ids=None):
            raise RuntimeError("chroma kilitli")

        with patch.object(self.collection, "get", broken_get):
            with self.assertRaisesRegex(RuntimeError, "Önceki parçalar okunamadı"):
                self.gate.ingest(self.rag, self.index, GOOD_TEXT, {"file_hash": HASH, "title": "Yeni"}, force=True)
        self.assertEqual(sorted(self.collection.records), ["old-1", "old-2"])


class TestRescanScoringRules(_Base):
    def _rescan(self, docs: List[str]):
        self.collection.records.clear()
        self.collection.upsert(
            ids=[f"d{i}" for i in range(len(docs))], documents=docs,
            metadatas=[{"file_hash": HASH}] * len(docs), embeddings=[[0.0, 0.0, 1.0]] * len(docs),
        )
        return self.gate.rescan_committed_chunks(self.rag, HASH)

    def test_short_citation_fragment_does_not_fail_whole_law(self):
        fragment = "a) 657 sayılı Kanunun 48 inci maddesinin (A) fıkrasının (1),\n(4), (5),"
        self.assertLess(len(fragment), RESCAN_MIN_SCORED_CHARS)
        passed, issues, _ = self._rescan([GOOD_TEXT, fragment])
        self.assertTrue(passed, issues)

    def test_invalid_unicode_is_counted_as_corruption_even_in_short_chunks(self):
        passed, issues, _ = self._rescan([GOOD_TEXT, "Tam mükellef kurumlardan elde ed￾len kazanç"])
        self.assertFalse(passed)
        self.assertTrue(any(i.startswith("RESCAN_CHROMA_REPLACEMENT") for i in issues), issues)


class TestRescanLayoutTolerance(_Base):
    """E8: tek bir sayfa-numaralı kısa parça (0.85) yüzlerce temiz parçayı reddettiriyordu."""

    def _rescan_scores(self, scores: List[float], extra_doc: str = ""):
        from repair import ingestion_gate

        docs = [f"PARCA-{i} " + GOOD_TEXT for i in range(len(scores))] + ([extra_doc] if extra_doc else [])
        by_doc = {d: s for d, s in zip(docs, scores)}
        real = ingestion_gate._pdf_generic_quality_metrics

        def fake_metrics(text):
            q = dict(real(text))
            if text in by_doc:
                q["score"] = by_doc[text]
            return q

        self.collection.records.clear()
        self.collection.upsert(ids=[f"d{i}" for i in range(len(docs))], documents=docs,
                               metadatas=[{"file_hash": HASH}] * len(docs), embeddings=[[0.0, 0.0, 1.0]] * len(docs))
        with patch.object(ingestion_gate, "_pdf_generic_quality_metrics", fake_metrics):
            return self.gate.rescan_committed_chunks(self.rag, HASH)

    def test_single_borderline_chunk_passes(self):
        passed, issues, summary = self._rescan_scores([0.95] * 20 + [0.85])
        self.assertTrue(passed, issues)
        self.assertEqual(summary["low_score_chunks"], 1)

    def test_many_borderline_chunks_fail(self):
        passed, issues, _ = self._rescan_scores([0.95] * 20 + [0.85] * 3)
        self.assertFalse(passed)
        self.assertTrue(any(i.startswith("RESCAN_CHROMA_LOW_SCORE_CHUNKS_3_OF_23") for i in issues), issues)

    def test_chunk_below_hard_floor_fails(self):
        passed, _, _ = self._rescan_scores([0.95] * 20 + [0.5])
        self.assertFalse(passed)

    def test_control_characters_fail_regardless_of_score(self):
        passed, issues, _ = self._rescan_scores([0.95] * 5, extra_doc="MADDE 1 \x00\x01 bozuk")
        self.assertFalse(passed)
        self.assertTrue(any(i.startswith("RESCAN_CHROMA_CONTROL_CHARS") for i in issues), issues)


class TestBulkImporterQuarantineAccounting(unittest.TestCase):
    def test_quarantined_file_is_counted_and_index_record_not_overwritten(self):
        from mufettis.common.utils import sha256_bytes
        from mufettis.importers import bulk_importer

        tmp = Path(tempfile.mkdtemp(prefix="bulk_q_"))
        try:
            folder = tmp / "mevzuat"
            folder.mkdir()
            (folder / "kanun.txt").write_text(GOOD_TEXT, encoding="utf-8")
            file_hash = sha256_bytes((folder / "kanun.txt").read_bytes())
            index = IngestIndex(path=tmp / "ingest_index.json")
            preserved = {"file_hash": file_hash, "title": "korunan", "status": "active", "chunk_count": 7}

            def fake_ingest(rag, idx, text, meta, **kwargs):
                idx.upsert(file_hash, preserved)  # kapının yazdığı kayıt
                return {"status": "quarantined", "count": 0, "error": "RESCAN", "meta": preserved}

            with patch.object(bulk_importer, "versioned_ingest_text", fake_ingest), \
                    patch.object(bulk_importer.BulkRegulationImporter, "_save_run_state", lambda self, state: None):
                stats = bulk_importer.BulkRegulationImporter(folder=folder, rag=object(), index=index).import_folder()

            self.assertEqual((stats["quarantined"], stats["ingested"]), (1, 0))
            self.assertEqual(stats["files"][0]["status"], "quarantined")
            self.assertEqual(index.get(file_hash)["chunk_count"], 7)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
