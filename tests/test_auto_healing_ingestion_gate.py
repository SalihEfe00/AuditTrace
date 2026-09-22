# -*- coding: utf-8 -*-
"""Comprehensive Test Suite for Auto-Healing Ingestion Gate.

Covers:
A. Clean new web document -> successful direct ingestion.
B. Mojibake web document -> auto-repaired & successfully committed clean.
C. Glued-text web document -> auto-repaired & successfully committed clean.
D. Unrecoverable corruption -> safely quarantined with 0 chunks in Chroma.
E. Duplicate file_hash -> skipped with 0 mutations.
F. Cross-document identity collision -> blocked with 0 mutations.
G. Post-commit rescan failure -> rolled back with 0 chunks in Chroma.
H. End-to-end integration with versioned_ingest_text.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from mufettis.common.utils import safe_json
from mufettis.extraction.text_repair import normalize_extracted_text
from mufettis.rag.index import IngestIndex
from mufettis.rag.maintenance import versioned_ingest_text
from repair.ingestion_gate import AutoHealingIngestionGate


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
    """Mock RAG engine simulating LocalRAG without loading heavy neural weights."""

    def __init__(self):
        self.collection = MockCollection()
        self.simulate_rescan_corruption = False

    def delete_hash(self, file_hash: str):
        self.collection.delete(where={"file_hash": file_hash})

    def ingest_text(self, text: str, meta: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
        file_hash = meta["file_hash"]
        if force:
            self.delete_hash(file_hash)

        # Simple legal paragraph/article splitting
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text.strip()]

        ids = []
        docs = []
        metas = []
        for i, para in enumerate(paragraphs):
            cid = f"{file_hash[:24]}-{i:06d}"
            # If simulation flag is on, inject corrupted glued token into chunk
            doc_text = para
            if self.simulate_rescan_corruption:
                doc_text += " " + ("BUBİRYAPAYBOZUKLUK" * 4)

            ids.append(cid)
            docs.append(doc_text)
            chunk_meta = dict(meta)
            chunk_meta.update({
                "chunk_no": i,
                "file_hash": file_hash,
                "chunk_schema_version": "10.0.0",
            })
            metas.append(chunk_meta)

        self.collection.upsert(ids=ids, documents=docs, metadatas=metas)
        return {"status": "ingested", "count": len(ids)}

    def mark_hash_superseded(self, old_hash: str, new_hash: str, effective_to: str = ""):
        got = self.collection.get(where={"file_hash": old_hash})
        for cid in got.get("ids", []):
            if cid in self.collection.records:
                self.collection.records[cid]["metadata"]["status"] = "superseded"


class TestAutoHealingIngestionGate(unittest.TestCase):
    """Test suite verifying auto-healing ingestion behavior across all scenarios."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_gate_"))
        self.index_path = self.test_dir / "ingest_index.json"
        self.index = IngestIndex(path=self.index_path)
        self.rag = MockLocalRAG()
        self.gate = AutoHealingIngestionGate(rag=self.rag, index=self.index)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_a_clean_web_document_ingests_successfully(self):
        """Scenario A: Clean new web document passes immediately and is committed."""
        text = (
            "MADDE 1 – Bu Kanunun amacı, bankacılık sektöründe güven ve istikrarın "
            "sağlanması, kredi sisteminin etkin bir şekilde çalışması ve tasarruf "
            "sahiplerinin hak ve menfaatlerinin korunmasına ilişkin usul ve esasları düzenlemektir.\n\n"
            "MADDE 2 – Türkiye'de kurulu mevduat bankaları, katılım bankaları, kalkınma ve "
            "yatırım bankaları bu Kanun hükümlerine tabidir."
        )
        meta = {
            "file_hash": "clean001" * 8,
            "document_id": "bddk_clean",
            "title": "5411 Sayılı Bankacılık Kanunu",
            "source_type": "official",
        }

        res = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)

        self.assertEqual(res["status"], "indexed")
        self.assertFalse(res["healed"])
        self.assertGreater(res["count"], 0)
        self.assertEqual(self.rag.collection.count(), res["count"])

        # Check index entry
        idx_entry = self.index.get(meta["file_hash"])
        self.assertIsNotNone(idx_entry)
        self.assertEqual(idx_entry["status"], "active")
        self.assertTrue(idx_entry["chunk_metadata_valid"])

    def test_b_mojibake_web_document_auto_heals_and_commits(self):
        """Scenario B: Mojibake-corrupted web document is automatically healed and committed."""
        corrupted_text = (
            "MADDE 1 â€“ BankacÄ±lÄ±k DÃ¼zenleme ve Denetleme Kurumu tarafÄ±ndan "
            "yÃ¼rÃ¼tÃ¼len gÃ¶zetim ve denetim faaliyetleri kapsamÄ±nda hazÄ±rlanmÄ±ÅŸtÄ±r.\n\n"
            "MADDE 2 â€“ TÃ¼m kuruluÅŸlar bu dÃ¼zenlemelere uymakla yÃ¼kÃ¼mlÃ¼dÃ¼r."
        )
        meta = {
            "file_hash": "mojibake002" * 5 + "1234",
            "document_id": "bddk_mojibake",
            "title": "Denetim Yönetmeliği",
            "source_type": "official",
        }

        # Verify initial text would fail raw quality check
        raw_eval = self.gate.evaluate_text_quality(corrupted_text)
        self.assertFalse(raw_eval["is_clean"])
        self.assertGreater(raw_eval["mojibake_sequence_count"], 0)

        # Ingest through Auto-Healing Gate
        res = self.gate.ingest(rag=self.rag, index=self.index, text=corrupted_text, meta=meta)

        self.assertEqual(res["status"], "repaired_and_indexed")
        self.assertTrue(res["healed"])
        self.assertGreater(res["count"], 0)

        # Verify Chroma chunks are completely clean of mojibake
        got = self.rag.collection.get(where={"file_hash": meta["file_hash"]})
        self.assertGreater(len(got["documents"]), 0)
        combined_docs = " ".join(got["documents"])
        self.assertNotIn("â€“", combined_docs)
        self.assertNotIn("Ä±", combined_docs)
        self.assertNotIn("Ã¼", combined_docs)
        self.assertIn("Düzenleme", combined_docs)

        # Check index entry records self-healing
        idx_entry = self.index.get(meta["file_hash"])
        self.assertIsNotNone(idx_entry)
        self.assertTrue(idx_entry.get("healed"))
        self.assertIn("healed_at", idx_entry)

    def test_c_glued_text_web_document_auto_heals_and_commits(self):
        """Scenario C: Glued words in web document are automatically resolved."""
        corrupted_text = (
            "MADDE 1 – Ülkemizdetedavüldebulunanbanknot ve madeni paraların tedavülü.\n\n"
            "Bankalarca kullandırılan ihracat kredilerinin takibi zorunludur."
        )
        meta = {
            "file_hash": "glued003" * 8,
            "document_id": "bddk_glued",
            "title": "İhracat Kredileri Tebliği",
            "source_type": "official",
        }

        raw_eval = self.gate.evaluate_text_quality(corrupted_text)
        self.assertFalse(raw_eval["is_clean"])
        self.assertGreater(raw_eval["glued_token_count"], 0)

        res = self.gate.ingest(rag=self.rag, index=self.index, text=corrupted_text, meta=meta)

        self.assertEqual(res["status"], "repaired_and_indexed")
        self.assertTrue(res["healed"])
        self.assertGreater(res["count"], 0)

        got = self.rag.collection.get(where={"file_hash": meta["file_hash"]})
        combined_docs = " ".join(got["documents"])
        self.assertNotIn("Ülkemizdetedavüldebulunanbanknot", combined_docs)
        self.assertIn("tedavülde bulunan banknot", combined_docs)

    def test_d_unrecoverable_corruption_quarantined_with_zero_chroma_chunks(self):
        """Scenario D: Irrecoverable garbage is quarantined with 0 chunks in Chroma."""
        garbage_text = (
            "" * 50 + "\n"
            + ("qwxkzfjvblypmrtghcdsjknqwxkzfjvblypmrtghcdsjkn" * 10) + "\n"
            + "" * 50
        )
        meta = {
            "file_hash": "garbage004" * 6 + "1234",
            "document_id": "bddk_garbage",
            "title": "Bozuk Doküman",
            "source_type": "official",
        }

        res = self.gate.ingest(rag=self.rag, index=self.index, text=garbage_text, meta=meta)

        self.assertEqual(res["status"], "quarantined")
        self.assertEqual(res["count"], 0)

        # Invariant: EXACTLY 0 chunks left in Chroma
        got = self.rag.collection.get(where={"file_hash": meta["file_hash"]})
        self.assertEqual(len(got.get("ids", [])), 0)

        # Index entry must reflect quarantine
        idx_entry = self.index.get(meta["file_hash"])
        self.assertIsNotNone(idx_entry)
        self.assertEqual(idx_entry["status"], "quarantined")
        self.assertFalse(idx_entry["evidence_indexable"])

    def test_e_duplicate_hash_skips_without_mutation(self):
        """Scenario E: Same file_hash incoming again returns skipped with 0 mutations."""
        text = "MADDE 1 – Bu genelge bankaların likidite gereksinimlerini belirler."
        meta = {
            "file_hash": "dup005" * 10 + "12",
            "document_id": "bddk_dup",
            "title": "Likidite Genelgesi",
            "source_type": "official",
        }

        # First ingestion
        res1 = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
        self.assertEqual(res1["status"], "indexed")
        initial_chroma_count = self.rag.collection.count()

        # Duplicate ingestion (not forced)
        res2 = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta, force=False)
        self.assertEqual(res2["status"], "skipped")
        self.assertEqual(self.rag.collection.count(), initial_chroma_count)

    def test_f_cross_document_identity_collision_blocked(self):
        """Scenario F: Same hash arriving with a different document_id is blocked."""
        text = "MADDE 1 – Standart hüküm."
        shared_hash = "collision006" * 5 + "1234"

        meta_a = {
            "file_hash": shared_hash,
            "document_id": "doc_original_A",
            "title": "A Dokümanı",
            "source_type": "official",
        }
        res_a = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta_a)
        self.assertEqual(res_a["status"], "indexed")

        meta_b = {
            "file_hash": shared_hash,
            "document_id": "doc_conflicting_B",
            "title": "B Dokümanı",
            "source_type": "official",
        }
        res_b = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta_b, force=False)

        self.assertEqual(res_b["status"], "blocked")
        self.assertIn("collision", res_b["error"].lower())

        # Original document remains active in index
        idx_entry = self.index.get(shared_hash)
        self.assertEqual(idx_entry["document_id"], "doc_original_A")

    def test_g_rescan_failure_triggers_rollback_and_quarantine(self):
        """Scenario G: Post-commit rescan failure triggers rollback (0 chunks in Chroma)."""
        text = "MADDE 1 – Temiz başlangıç metni ancak Chroma yazımında bozulma simüle edilir."
        meta = {
            "file_hash": "rescanfail007" * 4 + "12345678",
            "document_id": "bddk_rescan_fail",
            "title": "Rescan Testi",
            "source_type": "official",
        }

        # Enable simulated chunk corruption during Chroma upsert
        self.rag.simulate_rescan_corruption = True

        res = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)

        self.assertEqual(res["status"], "quarantined")
        self.assertIn("rescan", res["error"].lower())

        # Invariant: Rollback deleted all staged chunks from Chroma
        got = self.rag.collection.get(where={"file_hash": meta["file_hash"]})
        self.assertEqual(len(got.get("ids", [])), 0)

        # Index entry marked quarantined
        idx_entry = self.index.get(meta["file_hash"])
        self.assertEqual(idx_entry["status"], "quarantined")

    def test_h_versioned_ingest_text_end_to_end_integration(self):
        """Scenario H: versioned_ingest_text seamlessly routes through auto-healing gate."""
        mojibake_text = (
            "MADDE 1 â€“ Finansal Kiralama, Faktoring ve Finansman Åžirketleri Kanunu "
            "uyarÄ±nca dÃ¼zenlenmiÅŸtir.\n\n"
            "MADDE 2 â€“ Bu tebliÄŸ hÃ¼kÃ¼mlerini Kurul yÃ¼rÃ¼tÃ¼r."
        )
        meta = {
            "file_hash": "integration008" * 4 + "abcdefgh",
            "document_id": "bddk_integration",
            "title": "Faktoring Tebliği",
            "source_type": "official",
        }

        import unittest.mock
        with unittest.mock.patch("mufettis.rag.maintenance.save_source_cache"):
            res = versioned_ingest_text(
                rag=self.rag,
                index=self.index,
                text=mojibake_text,
                meta=meta,
                force=False,
                auto_heal=True,
            )

        self.assertEqual(res["status"], "repaired_and_indexed")
        self.assertTrue(res["healed"])
        self.assertGreater(res["count"], 0)

        # Chunks in Chroma are verified clean
        got = self.rag.collection.get(where={"file_hash": meta["file_hash"]})
        combined = " ".join(got["documents"])
        self.assertIn("Şirketleri", combined)
        self.assertIn("uyarınca", combined)
        self.assertNotIn("ÅŸ", combined)


if __name__ == "__main__":
    unittest.main()
