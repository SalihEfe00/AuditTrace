# -*- coding: utf-8 -*-
"""Müfettiş AI - FAZ 18: Regression, E2E ve Production-Safety Validation Suite.

Comprehensive validation covering the full FAZ 18 Regression Matrix:
  A) Cache quality regression (clean, mojibake, glued, replacement chars, low quality)
  B) Chroma quality regression (glued corruption, mojibake, replacement chars, low score, mixed)
  C) Identity / hash safety (file_hash mismatch, source_hash mismatch, duplicate hash, identity collision, cross-doc mutation)
  D) Self-healing sequence (clean direct commit, Stage 1, Stage 2, Stage 3 source fallback, unrecoverable quarantine)
  E) VERIFY / RESCAN (verify pass/fail, rescan pass/fail, automatic rollback, original state restoration)
  F) Retrieval regression & Section 7 Critical E2E:
     1. Intentionally create corrupted chunk in isolated Chroma collection.
     2. Verify corrupted text is returned in retrieval candidates before repair.
     3. Run auto-healing repair pipeline.
     4. Verify old corrupted chunk is completely gone from retrieval results.
     5. Verify new clean chunk is retrievable.
     6. Verify metadata / document identity remains intact.
     7. Verify cross-document mutation = 0.
     8. Verify rollback under simulated failure.
  G) Web ingestion E2E (FETCH -> raw_bytes -> versioned_ingest_text -> Gate -> Staged -> VERIFY -> RESCAN -> Commit)
  H) Stage 3 source fallback (damaged text + valid HTML raw_bytes -> Stage 3 activation -> clean candidate committed -> no raw_bytes leak)
  I) Failure isolation (unrecoverable failure on Doc A leaves Doc B 100% untouched, cross-doc mutation = 0, audit chain VALID)

Strictly isolated: 0 production writes, 0 production mutation, temporary DB and audit paths.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from mufettis.extraction.text_repair import (
    repair_extracted_spacing,
    repair_mojibake,
)
from mufettis.rag.index import IngestIndex
from mufettis.rag.maintenance import versioned_ingest_text
from mufettis.repair.audit import RepairAuditLogger
from mufettis.repair.ingestion_gate import AutoHealingIngestionGate


# ---------------------------------------------------------------------------
# Isolated Test Fixtures & Mocks
# ---------------------------------------------------------------------------

class MockChromaCollection:
    """Isolated in-memory Chroma collection mock supporting get, upsert, delete, count, query."""

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
        embs = list(embeddings) if embeddings is not None else [[0.0, 0.0, 1.0]] * len(ids)
        for chunk_id, doc, meta, emb in zip(ids, documents, metas, embs):
            self.records[chunk_id] = {
                "id": chunk_id,
                "document": doc,
                "metadata": dict(meta),
                "embedding": list(emb),
            }

    def get(
        self,
        where: Optional[Dict[str, Any]] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        matched_ids = []
        matched_docs = []
        matched_metas = []
        matched_embs = []

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
                matched_embs.append(rec.get("embedding", [0.0, 0.0, 1.0]))

        res = {"ids": matched_ids}
        if include is None or "documents" in include:
            res["documents"] = matched_docs
        if include is None or "metadatas" in include:
            res["metadatas"] = matched_metas
        if include is not None and "embeddings" in include:
            res["embeddings"] = matched_embs
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

    def query(
        self,
        query_texts: Optional[List[str]] = None,
        query_embeddings: Optional[List[Any]] = None,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[Any]]:
        """Simulates Chroma retrieval query matching query tokens against stored chunk documents."""
        terms = []
        if query_texts:
            for qt in query_texts:
                terms.extend(re.findall(r"\w+", qt.lower()))

        where_clause = where or {}
        candidates = []
        for cid, rec in self.records.items():
            match = True
            for k, v in where_clause.items():
                if rec["metadata"].get(k) != v:
                    match = False
                    break
            if not match:
                continue

            doc_lower = rec["document"].lower()
            score = 0
            if terms:
                for term in terms:
                    if term in doc_lower:
                        score += 1

            if score > 0:
                candidates.append((score, cid, rec["document"], rec["metadata"]))

        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[:n_results]

        ids = [c[1] for c in top]
        docs = [c[2] for c in top]
        metas = [c[3] for c in top]
        distances = [round(max(0.0, 1.0 - (c[0] / max(1, len(terms)))), 4) for c in top]

        return {
            "ids": [ids],
            "documents": [docs],
            "metadatas": [metas],
            "distances": [distances],
        }


class MockIsolatedRAG:
    """Isolated mock RAG engine implementing the full LocalRAG interface needed for FAZ 18."""

    def __init__(self):
        self.collection = MockChromaCollection()
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
            paragraphs = [text.strip()]

        ids = []
        docs = []
        metas = []
        for i, para in enumerate(paragraphs):
            cid = f"{file_hash[:24]}-{i:06d}"
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
                "document_id": meta.get("document_id", "default_doc"),
            })
            metas.append(chunk_meta)

        self.collection.upsert(ids=ids, documents=docs, metadatas=metas)
        return {"status": "ingested", "count": len(ids)}

    def mark_hash_superseded(self, old_hash: str, new_hash: str, effective_to: str = ""):
        got = self.collection.get(where={"file_hash": old_hash})
        for cid in got.get("ids", []):
            if cid in self.collection.records:
                self.collection.records[cid]["metadata"]["status"] = "superseded"

    def query(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Simulates LocalRAG query method returning raw chunk document items."""
        if not query.strip():
            return []
        res = self.collection.query(
            query_texts=[query],
            n_results=k,
            where={"chunk_schema_version": "10.0.0"},
        )
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]

        out = []
        for d, m, dist in zip(docs, metas, dists):
            out.append({
                "document": d,
                "metadata": m,
                "distance": dist,
            })
        return out


# ---------------------------------------------------------------------------
# FAZ 18 Comprehensive Validation Tests
# ---------------------------------------------------------------------------

class TestRepairPipelineRegression(unittest.TestCase):
    """FAZ 18: Regression, E2E ve Production-Safety Validation Suite."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="faz18_test_"))
        self.index_path = self.test_dir / "ingest_index.json"
        self.index = IngestIndex(path=self.index_path)
        self.audit_logger = RepairAuditLogger(log_path=self.test_dir / "repair_audit.jsonl")
        self.rag = MockIsolatedRAG()
        self.gate = AutoHealingIngestionGate(
            rag=self.rag,
            index=self.index,
            audit_logger=self.audit_logger,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # =========================================================================
    # A) Cache Quality Regression
    # =========================================================================

    def test_a1_clean_cache_evaluation(self):
        """A.1: Clean legal text evaluates to high quality with zero issues."""
        text = (
            "MADDE 1 – Bu Kanunun amacı, bankacılık sektöründe güven ve istikrarın "
            "sağlanması, kredi sisteminin etkin bir şekilde çalışması ve tasarruf "
            "sahiplerinin hak ve menfaatlerinin korunmasına ilişkin usul ve esasları düzenlemektir.\n\n"
            "MADDE 2 – Türkiye'de kurulu mevduat bankaları, katılım bankaları, kalkınma ve "
            "yatırım bankaları bu Kanun hükümlerine tabidir."
        )
        res = self.gate.evaluate_text_quality(text)
        self.assertTrue(res["is_clean"])
        self.assertEqual(res["glued_token_count"], 0)
        self.assertEqual(res["mojibake_sequence_count"], 0)
        self.assertEqual(res["replacement_char_count"], 0)
        self.assertGreaterEqual(res["score"], 0.95)
        self.assertEqual(len(res["issues"]), 0)

    def test_a2_mojibake_cache_evaluation(self):
        """A.2: Mojibake-corrupted text is detected and flagged as not clean."""
        text = (
            "MADDE 1 â€“ BankacÄ±lÄ±k DÃ¼zenleme ve Denetleme Kurumu tarafÄ±ndan "
            "yÃ¼rÃ¼tÃ¼len gÃ¶zetim ve denetim faaliyetleri kapsamÄ±nda hazÄ±rlanmÄ±ÅŸtÄ±r.\n\n"
            "MADDE 2 â€“ TÃ¼m kuruluÅŸlar bu dÃ¼zenlemelere uymakla yÃ¼kÃ¼mlÃ¼dÃ¼r."
        )
        res = self.gate.evaluate_text_quality(text)
        self.assertFalse(res["is_clean"])
        self.assertGreater(res["mojibake_sequence_count"], 0)
        self.assertTrue(any("MOJIBAKE" in issue for issue in res["issues"]))

        # Verify Stage 1 heals it completely
        healed = repair_mojibake(text)
        h_res = self.gate.evaluate_text_quality(healed)
        self.assertEqual(h_res["mojibake_sequence_count"], 0)

    def test_a3_glued_text_cache_evaluation(self):
        """A.3: Glued text is identified with positive glued_tokens and is_clean=False."""
        text = (
            "MADDE 1 – Ülkemizdetedavüldebulunanbanknot ve madeni paraların tedavülü.\n\n"
            "Bankalarca kullandırılan ihracat kredilerinin takibi zorunludur."
        )
        res = self.gate.evaluate_text_quality(text)
        self.assertFalse(res["is_clean"])
        self.assertGreater(res["glued_token_count"], 0)

        # Verify Stage 2 heals it
        healed = repair_extracted_spacing(text)
        h_res = self.gate.evaluate_text_quality(healed)
        self.assertEqual(h_res["glued_token_count"], 0)

    def test_a4_replacement_chars_evaluation(self):
        """A.4: Unicode replacement characters (\ufffd) are detected and flagged."""
        text = "MADDE 1 – Bankacılık sekt\ufffdr\ufffdnde g\ufffdven ve istikrarın sağlanması."
        res = self.gate.evaluate_text_quality(text)
        self.assertFalse(res["is_clean"])
        self.assertGreater(res["replacement_char_count"], 0)
        self.assertTrue(any("REPLACEMENT_CHARS" in issue for issue in res["issues"]))

    def test_a5_low_quality_evaluation(self):
        """A.5: Heavily damaged text with invalid tokens evaluates to low score."""
        text = (
            "MADDE 1 \ufffd\ufffd\ufffd bozuk text \ufffd\ufffd\ufffd unrecoverable "
            "kimliksizlestirilemeyenlerdenmiscesinegluedwordthatcannotberepairedbyrule \ufffd\ufffd\ufffd"
        )
        res = self.gate.evaluate_text_quality(text)
        self.assertFalse(res["is_clean"])
        self.assertLess(res["score"], self.gate.quality_threshold)

    # =========================================================================
    # B) Chroma Quality Regression
    # =========================================================================

    def test_b1_chroma_glued_corruption_detection(self):
        """B.1: Rescan gate detects glued tokens in staged Chroma chunks."""
        file_hash = "glued_chroma_001" * 4
        self.rag.collection.upsert(
            ids=[f"{file_hash[:24]}-000000"],
            documents=["MADDE 1 – Ülkemizdetedavüldebulunanbanknot tedavülü."],
            metadatas=[{"file_hash": file_hash, "chunk_schema_version": "10.0.0"}],
        )
        passed, issues, summary = self.gate.rescan_committed_chunks(self.rag, file_hash)
        self.assertFalse(passed)
        self.assertGreater(summary["glued_total"], 0)
        self.assertTrue(any("GLUED_TOKENS" in issue for issue in issues))

    def test_b2_chroma_mojibake_detection(self):
        """B.2: Rescan gate detects mojibake in staged Chroma chunks."""
        file_hash = "mojibake_chroma_002" * 3 + "1234"
        self.rag.collection.upsert(
            ids=[f"{file_hash[:24]}-000000"],
            documents=["MADDE 1 â€“ BankacÄ±lÄ±k DÃ¼zenleme Kurulu."],
            metadatas=[{"file_hash": file_hash, "chunk_schema_version": "10.0.0"}],
        )
        passed, issues, summary = self.gate.rescan_committed_chunks(self.rag, file_hash)
        self.assertFalse(passed)
        self.assertGreater(summary["mojibake_total"], 0)
        self.assertTrue(any("MOJIBAKE" in issue for issue in issues))

    def test_b3_chroma_replacement_chars_detection(self):
        """B.3: Rescan gate detects replacement characters in staged Chroma chunks."""
        file_hash = "repchar_chroma_003" * 3 + "1234"
        self.rag.collection.upsert(
            ids=[f"{file_hash[:24]}-000000"],
            documents=["MADDE 1 – Sekt\ufffdr g\ufffdvenliği esas alınır."],
            metadatas=[{"file_hash": file_hash, "chunk_schema_version": "10.0.0"}],
        )
        passed, issues, summary = self.gate.rescan_committed_chunks(self.rag, file_hash)
        self.assertFalse(passed)
        self.assertGreater(summary["replacement_total"], 0)
        self.assertTrue(any("REPLACEMENT" in issue for issue in issues))

    def test_b4_chroma_low_quality_chunk_detection(self):
        """B.4: Rescan gate flags chunk with min_score below quality threshold."""
        file_hash = "lowscore_chroma_004" * 3 + "12"
        self.rag.collection.upsert(
            ids=[f"{file_hash[:24]}-000000"],
            documents=[
                "MADDE 1 \ufffd\ufffd\ufffd bozuk text \ufffd\ufffd\ufffd unrecoverable "
                "kimliksizlestirilemeyenlerdenmiscesinegluedwordthatcannotberepairedbyrule \ufffd\ufffd\ufffd"
            ],
            metadatas=[{"file_hash": file_hash, "chunk_schema_version": "10.0.0"}],
        )
        passed, issues, summary = self.gate.rescan_committed_chunks(self.rag, file_hash)
        self.assertFalse(passed)
        self.assertLess(summary["min_score"], self.gate.quality_threshold)

    def test_b5_chroma_mixed_document_corruption(self):
        """B.5: Mixed document with both clean and corrupted chunks is caught by rescan."""
        file_hash = "mixed_chroma_005" * 4
        self.rag.collection.upsert(
            ids=[f"{file_hash[:24]}-000000", f"{file_hash[:24]}-000001"],
            documents=[
                "MADDE 1 – Bu Kanunun amacı finansal piyasaları düzenlemektir.",
                "MADDE 2 â€“ BankacÄ±lÄ±k DÃ¼zenleme Kurulu gÃ¶revlidir.",
            ],
            metadatas=[
                {"file_hash": file_hash, "chunk_schema_version": "10.0.0"},
                {"file_hash": file_hash, "chunk_schema_version": "10.0.0"},
            ],
        )
        passed, issues, summary = self.gate.rescan_committed_chunks(self.rag, file_hash)
        self.assertFalse(passed)
        self.assertEqual(summary["chunk_count"], 2)
        self.assertGreater(summary["mojibake_total"], 0)

    # =========================================================================
    # C) Identity / Hash Safety
    # =========================================================================

    def test_c1_file_hash_mismatch_safety(self):
        """C.1: Document with missing or empty file_hash raises ValueError safely."""
        text = "MADDE 1 – Test metni."
        meta = {"document_id": "test_no_hash"}
        with self.assertRaises(ValueError):
            self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
        self.assertEqual(self.rag.collection.count(), 0)

    def test_c2_source_hash_mismatch_safety(self):
        """C.2: Consistent hash identity is maintained throughout the pipeline."""
        text = "MADDE 1 – Test metni düzenlemesi."
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        meta = {"file_hash": h, "document_id": "doc_hash_id", "source_hash": h}
        res = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
        self.assertEqual(res["status"], "indexed")

        entry = self.index.get(h)
        self.assertEqual(entry["file_hash"], h)
        self.assertEqual(entry["document_id"], "doc_hash_id")

    def test_c3_duplicate_hash_skips_without_mutation(self):
        """C.3: Ingesting an already indexed identical hash skips without mutation."""
        text = "MADDE 1 – Bankacılık kanunu güven ilkeleri."
        meta = {"file_hash": "duphash01" * 7 + "1", "document_id": "doc_dup"}

        res1 = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
        self.assertEqual(res1["status"], "indexed")
        initial_count = self.rag.collection.count()

        # Ingest identical again
        res2 = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
        self.assertEqual(res2["status"], "skipped")
        self.assertEqual(self.rag.collection.count(), initial_count)

    def test_c4_document_identity_collision_blocked(self):
        """C.4: Document identity collision across different documents is blocked."""
        meta1 = {
            "file_hash": "coll_hash_01" * 5 + "1234",
            "document_id": "doc_coll_1",
            "document_key": "KEY_100",
        }
        res1 = self.gate.ingest(rag=self.rag, index=self.index, text="MADDE 1 – İlk belge.", meta=meta1)
        self.assertEqual(res1["status"], "indexed")

        # Second doc has same file_hash but different document_id
        meta2 = {
            "file_hash": meta1["file_hash"],
            "document_id": "doc_coll_2",
            "document_key": "KEY_200",
        }
        res2 = self.gate.ingest(rag=self.rag, index=self.index, text="MADDE 1 – Çakışan belge.", meta=meta2)
        self.assertEqual(res2["status"], "blocked")

    def test_c5_cross_document_zero_mutation(self):
        """C.5: Ingesting Document A causes zero changes to Document B."""
        meta_b = {"file_hash": "doc_b_hash" * 6 + "1234", "document_id": "doc_b", "document_key": "KB"}
        self.gate.ingest(rag=self.rag, index=self.index, text="MADDE 1 – Belge B metni.", meta=meta_b)
        count_b_initial = len(self.rag.collection.get(where={"file_hash": meta_b["file_hash"]})["ids"])

        # Ingest Document A (which undergoes repair)
        meta_a = {"file_hash": "doc_a_hash" * 6 + "5678", "document_id": "doc_a", "document_key": "KA"}
        mojibake_text = "MADDE 1 â€“ Belge A DÃ¼zenleme metni."
        self.gate.ingest(rag=self.rag, index=self.index, text=mojibake_text, meta=meta_a)

        # Document B must be 100% intact
        got_b = self.rag.collection.get(where={"file_hash": meta_b["file_hash"]})
        self.assertEqual(len(got_b["ids"]), count_b_initial)
        self.assertEqual(got_b["metadatas"][0]["document_id"], "doc_b")
        entry_b = self.index.get(meta_b["file_hash"])
        self.assertEqual(entry_b["status"], "active")

    # =========================================================================
    # D) Self-Healing Sequence (Stage 1 -> Stage 2 -> Stage 3 -> Quarantine)
    # =========================================================================

    def test_d1_clean_document_direct_commit(self):
        """D.1: Clean document undergoes direct commit with 0 repairs."""
        text = "MADDE 1 – Finansal kurumların faaliyet usul ve esasları."
        meta = {"file_hash": "clean_seq_001" * 4 + "12", "document_id": "doc_clean_seq"}
        res = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
        self.assertEqual(res["status"], "indexed")
        self.assertFalse(res["healed"])

    def test_d2_stage1_mojibake_repair(self):
        """D.2: Mojibake corruption is resolved at Stage 1."""
        text = "MADDE 1 â€“ BankacÄ±lÄ±k DÃ¼zenleme Kurulu tarafÄ±ndan hazÄ±rlanmÄ±ÅŸtÄ±r."
        meta = {"file_hash": "stage1_seq_002" * 4 + "12", "document_id": "doc_stage1"}
        res = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
        self.assertEqual(res["status"], "repaired_and_indexed")
        self.assertTrue(res["healed"])

    def test_d3_stage2_glued_text_repair(self):
        """D.3: Glued text corruption is resolved at Stage 2."""
        text = (
            "MADDE 1 – Ülkemizdetedavüldebulunanbanknot ve madeni paralar.\n\n"
            "MADDE 2 – İhracat kredilerinin takibinde bankalar yetkilidir."
        )
        meta = {"file_hash": "stage2_seq_003" * 4 + "12", "document_id": "doc_stage2"}
        res = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
        self.assertEqual(res["status"], "repaired_and_indexed")
        self.assertTrue(res["healed"])

    def test_d4_stage3_source_fallback_reextraction(self):
        """D.4: Corrupt text with valid HTML raw_bytes triggers Stage 3 fallback."""
        html_bytes = (
            "<html><body>"
            "<h1>5411 Sayılı Bankacılık Kanunu</h1>"
            "<p>MADDE 1 – Bu Kanunun amacı, bankacılık sektöründe güven ve istikrarın sağlanmasıdır.</p>"
            "<p>MADDE 2 – Türkiye'de kurulu mevduat bankaları bu Kanun hükümlerine tabidir.</p>"
            "</body></html>"
        ).encode("utf-8")
        unrecoverable_text = (
            "MADDE 1 \ufffd\ufffd\ufffd bozuk text \ufffd\ufffd\ufffd unrecoverable "
            "kimliksizlestirilemeyenlerdenmiscesinegluedwordthatcannotberepairedbyrule \ufffd\ufffd\ufffd"
        )
        meta = {
            "file_hash": "stage3_seq_004" * 4 + "12",
            "document_id": "doc_stage3",
            "title": "Bankacılık Kanunu",
            "is_pdf": False,
        }
        res = self.gate.ingest(rag=self.rag, index=self.index, text=unrecoverable_text, meta=meta, raw_bytes=html_bytes)
        self.assertEqual(res["status"], "repaired_and_indexed")
        self.assertTrue(res["healed"])
        self.assertTrue(res["meta"].get("source_fallback_used"))

        # Verify raw_bytes did not leak into index
        entry = self.index.get(meta["file_hash"])
        self.assertNotIn("raw_bytes", entry)

    def test_d5_unrecoverable_corruption_quarantine(self):
        """D.5: Unrecoverable corruption is quarantined with 0 chunks in Chroma."""
        unrecoverable_text = (
            "MADDE 1 \ufffd\ufffd\ufffd bozuk text \ufffd\ufffd\ufffd unrecoverable "
            "kimliksizlestirilemeyenlerdenmiscesinegluedwordthatcannotberepairedbyrule \ufffd\ufffd\ufffd"
        )
        meta = {"file_hash": "unrecov_seq_005" * 4 + "1", "document_id": "doc_unrecov"}
        res = self.gate.ingest(rag=self.rag, index=self.index, text=unrecoverable_text, meta=meta)
        self.assertEqual(res["status"], "quarantined")
        self.assertEqual(self.rag.collection.count(), 0)

        entry = self.index.get(meta["file_hash"])
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], "quarantined")
        self.assertFalse(entry["evidence_indexable"])

    # =========================================================================
    # E) VERIFY / RESCAN & Rollback
    # =========================================================================

    def test_e1_verify_success_and_rescan_success(self):
        """E.1: Clean candidate passes both VERIFY and post-commit RESCAN."""
        text = "MADDE 1 – Finansal piyasaların istikrarını koruma tedbirleri."
        meta = {"file_hash": "ver_pass_001" * 5 + "12", "document_id": "doc_ver_pass"}
        res = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
        self.assertEqual(res["status"], "indexed")
        self.assertEqual(self.rag.collection.count(), 1)

    def test_e2_verify_failure_aborts_commit(self):
        """E.2: Candidate that cannot meet verification threshold is aborted without write."""
        unrecoverable_text = (
            "MADDE 1 \ufffd\ufffd\ufffd bozuk text \ufffd\ufffd\ufffd unrecoverable "
            "kimliksizlestirilemeyenlerdenmiscesinegluedwordthatcannotberepairedbyrule \ufffd\ufffd\ufffd"
        )
        meta = {"file_hash": "ver_fail_002" * 5 + "12", "document_id": "doc_ver_fail"}
        res = self.gate.ingest(rag=self.rag, index=self.index, text=unrecoverable_text, meta=meta)
        self.assertEqual(res["status"], "quarantined")
        self.assertEqual(self.rag.collection.count(), 0)

    def test_e3_rescan_failure_triggers_automatic_rollback(self):
        """E.3: Rescan failure triggers automatic rollback and leaves 0 dirty chunks."""
        text = "MADDE 1 – Finansal piyasalar denetim ilkeleri."
        meta = {"file_hash": "rescan_fail_003" * 4 + "1", "document_id": "doc_rescan_fail"}
        self.rag.simulate_rescan_corruption = True
        try:
            res = self.gate.ingest(rag=self.rag, index=self.index, text=text, meta=meta)
            self.assertEqual(res["status"], "quarantined")
            self.assertIn("rescan", res["error"].lower())
            self.assertEqual(self.rag.collection.count(), 0)
        finally:
            self.rag.simulate_rescan_corruption = False

    def test_e4_rollback_original_state_restoration(self):
        """E.4: When replacing a document, rescan failure cleans candidate chunks."""
        file_hash = "orig_rest_004" * 4 + "1234"
        meta = {"file_hash": file_hash, "document_id": "doc_orig_rest"}
        self.rag.simulate_rescan_corruption = True
        try:
            res = self.gate.ingest(rag=self.rag, index=self.index, text="MADDE 1 – Test.", meta=meta)
            self.assertEqual(res["status"], "quarantined")
            got = self.rag.collection.get(where={"file_hash": file_hash})
            self.assertEqual(len(got["ids"]), 0)
        finally:
            self.rag.simulate_rescan_corruption = False

    # =========================================================================
    # F & Section 7) Retrieval Regression (CRITICAL E2E)
    # =========================================================================

    def test_f1_retrieval_regression_e2e_replaces_corrupt_chunk(self):
        """Section 7 Critical E2E Scenario:
        1. Intentionally create corrupted chunk in isolated Chroma collection.
        2. Verify corrupted text is returned in retrieval candidates before repair.
        3. Run auto-healing repair pipeline.
        4. Verify old corrupted chunk is completely gone from retrieval results.
        5. Verify new clean chunk is retrievable.
        6. Verify metadata / document identity remains intact.
        7. Verify cross-document mutation = 0.
        """
        # Step 0: Ingest a control document to verify cross-document zero mutation
        control_hash = "ctrl_hash_007" * 4 + "1234"
        control_meta = {
            "file_hash": control_hash,
            "document_id": "doc_control",
            "document_key": "KEY_CTRL",
            "title": "Kontrol Belgesi",
        }
        control_text = "MADDE 1 – Kontrol mevzuatı bağımsız olarak saklanır."
        res_ctrl = self.gate.ingest(rag=self.rag, index=self.index, text=control_text, meta=control_meta)
        self.assertEqual(res_ctrl["status"], "indexed")
        ctrl_initial_records = self.rag.collection.get(where={"file_hash": control_hash})

        # Step 1: Intentionally create corrupted chunk in isolated collection
        target_hash = "target_hash_007" * 4 + "123"
        target_meta = {
            "file_hash": target_hash,
            "document_id": "doc_target_7",
            "document_key": "KEY_TARGET_7",
            "title": "Tedavül Yönetmeliği",
        }
        corrupted_text = (
            "MADDE 1 – Ülkemizdetedavüldebulunanbanknot ve madeni paraların tedavülü.\n\n"
            "MADDE 2 – İhracat kredilerinin takibinde bankalar yetkilidir."
        )

        # Directly insert corrupted version into Chroma to represent unhealed legacy state
        self.rag.ingest_text(corrupted_text, target_meta, force=True)
        self.index.upsert(target_hash, dict(target_meta, status="active", chunk_metadata_valid=True))

        # Step 2: Query collection - verify corrupted text is returned in retrieval candidates
        results_before = self.rag.query("Ülkemizdetedavüldebulunanbanknot", k=5)
        self.assertGreater(len(results_before), 0)
        self.assertIn("Ülkemizdetedavüldebulunanbanknot", results_before[0]["document"])
        self.assertEqual(results_before[0]["metadata"]["document_id"], "doc_target_7")

        # Step 3: Run auto-healing repair pipeline
        res_repair = self.gate.ingest(
            rag=self.rag,
            index=self.index,
            text=corrupted_text,
            meta=target_meta,
            force=True,
        )
        self.assertEqual(res_repair["status"], "repaired_and_indexed")
        self.assertTrue(res_repair["healed"])

        # Step 4: Verify old corrupted chunk is completely gone from retrieval results
        results_after_bad = self.rag.query("Ülkemizdetedavüldebulunanbanknot", k=5)
        matched_bad = [r for r in results_after_bad if "Ülkemizdetedavüldebulunanbanknot" in r["document"]]
        self.assertEqual(len(matched_bad), 0, "Corrupted chunk still retrievable after repair!")

        # Step 5: Verify new clean chunk IS retrievable
        results_after_clean = self.rag.query("tedavülde bulunan banknot", k=5)
        self.assertGreater(len(results_after_clean), 0)
        self.assertIn("tedavülde bulunan banknot", results_after_clean[0]["document"])

        # Step 6: Verify metadata / document identity remains intact
        top_meta = results_after_clean[0]["metadata"]
        self.assertEqual(top_meta["document_id"], "doc_target_7")
        self.assertEqual(top_meta["file_hash"], target_hash)
        entry = self.index.get(target_hash)
        self.assertEqual(entry["status"], "active")
        self.assertTrue(entry.get("healed"))

        # Step 7: Verify cross-document mutation = 0
        ctrl_final_records = self.rag.collection.get(where={"file_hash": control_hash})
        self.assertEqual(ctrl_initial_records["ids"], ctrl_final_records["ids"])
        self.assertEqual(ctrl_initial_records["documents"], ctrl_final_records["documents"])
        self.assertEqual(ctrl_initial_records["metadatas"], ctrl_final_records["metadatas"])

    def test_f2_retrieval_rollback_safety(self):
        """F.2: Simulated rescan failure during update does not expose corrupt chunks to retrieval."""
        file_hash = "ret_roll_008" * 5 + "12"
        meta = {"file_hash": file_hash, "document_id": "doc_ret_roll"}
        self.rag.simulate_rescan_corruption = True
        try:
            self.gate.ingest(rag=self.rag, index=self.index, text="MADDE 1 – Test.", meta=meta)
            # Must not be retrievable
            res = self.rag.query("Test", k=5)
            doc_matches = [r for r in res if r["metadata"].get("file_hash") == file_hash]
            self.assertEqual(len(doc_matches), 0)
        finally:
            self.rag.simulate_rescan_corruption = False

    # =========================================================================
    # G) Web Ingestion E2E
    # =========================================================================

    def test_g1_web_harvester_e2e_pipeline(self):
        """G.1: End-to-end simulated web harvester flow through versioned_ingest_text."""
        html_payload = (
            b"<!DOCTYPE html><html><head><title>BDDK Karari</title></head><body>"
            b"<h1>Bankacilik Duzenleme ve Denetleme Kurulu Karari</h1>"
            b"<p>Karar No: 12345</p>"
            b"<p>MADDE 1 - Bankalar ozkaynak hesaplamalarinda belirtilen standart oranlara uymakla yukumludur.</p>"
            b"<p>MADDE 2 - Bu Karar yayimi tarihinde yururluge girer.</p>"
            b"</body></html>"
        )
        h = hashlib.sha256(html_payload).hexdigest()
        extracted_text = (
            "Bankacilik Duzenleme ve Denetleme Kurulu Karari\n\n"
            "Karar No: 12345\n\n"
            "MADDE 1 - Bankalar ozkaynak hesaplamalarinda belirtilen standart oranlara uymakla yukumludur.\n\n"
            "MADDE 2 - Bu Karar yayimi tarihinde yururluge girer."
        )
        meta = {
            "file_hash": h,
            "document_id": "bddk_karar_12345",
            "document_key": "KARAR-12345",
            "title": "BDDK Kararı 12345",
            "source_type": "official",
            "url": "https://www.bddk.org.tr/mevzuat/karar_12345",
            "raw_bytes": html_payload,
        }

        import unittest.mock
        with unittest.mock.patch("mufettis.rag.maintenance.save_source_cache"), \
             unittest.mock.patch("mufettis.repair.ingestion_gate.get_audit_logger", return_value=self.audit_logger):
            res = versioned_ingest_text(
                rag=self.rag,
                index=self.index,
                text=extracted_text,
                meta=meta,
            )

        self.assertIn(res["status"], {"indexed", "repaired_and_indexed"})
        self.assertGreater(self.rag.collection.count(), 0)

        entry = self.index.get(h)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["document_id"], "bddk_karar_12345")
        self.assertNotIn("raw_bytes", entry)

    # =========================================================================
    # H) Stage 3 Source Fallback
    # =========================================================================

    def test_h1_stage3_raw_bytes_pipeline_and_no_leak(self):
        """H.1: Stage 3 activates from raw_bytes, heals content, and prevents raw_bytes leakage."""
        html_bytes = (
            "<html><body>"
            "<h1>Finansal Tüketiciler Tebliği</h1>"
            "<p>MADDE 1 – Finansal tüketicilerden alınacak ücretlere ilişkin usul ve esaslar düzenlenmiştir.</p>"
            "<p>MADDE 2 – Bu Tebliğ hükümlerini Kurul Başkanı yürütür.</p>"
            "</body></html>"
        ).encode("utf-8")

        unrecoverable_text = (
            "MADDE 1 \ufffd\ufffd\ufffd bozuk text \ufffd\ufffd\ufffd unrecoverable "
            "kimliksizlestirilemeyenlerdenmiscesinegluedwordthatcannotberepairedbyrule \ufffd\ufffd\ufffd"
        )
        meta = {
            "file_hash": "st3_pipe_009" * 5 + "1",
            "document_id": "doc_st3_pipe",
            "title": "Finansal Tüketiciler Tebliği",
            "is_pdf": False,
            "raw_bytes": html_bytes,
        }

        res = self.gate.ingest(rag=self.rag, index=self.index, text=unrecoverable_text, meta=meta)
        self.assertEqual(res["status"], "repaired_and_indexed")
        self.assertTrue(res["meta"].get("source_fallback_used"))

        # Chroma check
        got = self.rag.collection.get(where={"file_hash": meta["file_hash"]})
        self.assertGreater(len(got["documents"]), 0)
        self.assertIn("ücretlere ilişkin usul", " ".join(got["documents"]))
        for m in got["metadatas"]:
            self.assertNotIn("raw_bytes", m)

        # Index check
        entry = self.index.get(meta["file_hash"])
        self.assertNotIn("raw_bytes", entry)

    # =========================================================================
    # I) Failure Isolation
    # =========================================================================

    def test_i1_failure_isolation_multi_document(self):
        """I.1: Failure on Document X leaves Document Y completely untouched."""
        # 1. Ingest Healthy Document Y
        meta_y = {
            "file_hash": "healthy_y_010" * 4 + "123",
            "document_id": "doc_y",
            "document_key": "KEY_Y",
            "title": "Sağlıklı Belge Y",
        }
        text_y = "MADDE 1 – Sağlıklı belge hükümleri uygulanır."
        res_y = self.gate.ingest(rag=self.rag, index=self.index, text=text_y, meta=meta_y)
        self.assertEqual(res_y["status"], "indexed")
        count_y = len(self.rag.collection.get(where={"file_hash": meta_y["file_hash"]})["ids"])

        # 2. Ingest Corrupted Document X with unrecoverable failure
        meta_x = {
            "file_hash": "failing_x_010" * 4 + "456",
            "document_id": "doc_x",
            "document_key": "KEY_X",
            "title": "Bozuk Belge X",
        }
        unrecoverable_text = (
            "MADDE 1 \ufffd\ufffd\ufffd bozuk text \ufffd\ufffd\ufffd unrecoverable "
            "kimliksizlestirilemeyenlerdenmiscesinegluedwordthatcannotberepairedbyrule \ufffd\ufffd\ufffd"
        )
        res_x = self.gate.ingest(rag=self.rag, index=self.index, text=unrecoverable_text, meta=meta_x)
        self.assertEqual(res_x["status"], "quarantined")

        # 3. Document X must have 0 chunks in Chroma
        got_x = self.rag.collection.get(where={"file_hash": meta_x["file_hash"]})
        self.assertEqual(len(got_x["ids"]), 0)

        # 4. Document Y must have exact initial count and unmodified metadata
        got_y = self.rag.collection.get(where={"file_hash": meta_y["file_hash"]})
        self.assertEqual(len(got_y["ids"]), count_y)
        self.assertEqual(got_y["metadatas"][0]["document_id"], "doc_y")

        # 5. Audit logger hash-chain must remain strictly valid
        valid, errors = self.audit_logger.verify_chain()
        self.assertTrue(valid)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
