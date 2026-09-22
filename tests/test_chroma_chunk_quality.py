# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 17: Chroma Chunk Quality Layer Tests.

Comprehensive validation for chunk-level Chroma quality inspection:
A. Cache clean + Chroma glued -> REVIEW (IssueCode.CHROMA_GLUED_TEXT)
B. Cache clean + Chroma mojibake -> REVIEW (IssueCode.CHROMA_MOJIBAKE)
C. Cache clean + Chroma clean -> SAFE
D. Cache corrupt + Chroma clean -> REVIEW (IssueCode.QUALITY_GLUED_TEXT retained)
E. Mixed corruption (cache + chroma) -> REVIEW (both issues recorded)
F. Metadata file_hash / source_hash fallback mapping in Chroma collection query
G. Previously repaired s1 and l1 remain healthy and do not trigger false positives
H. Zero production mutation guarantee during scanning
I. Candidate detection aligns with --rag-scan criteria
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mufettis.config import CHUNK_SCHEMA_VERSION
from repair.classifier import classify_document
from repair.comparator import build_snapshot, compare_quality
from repair.models import (
    ClassificationType,
    ComparisonStatus,
    DocumentScanResult,
    ExtractionCandidate,
    IssueCode,
)
from repair.scanner import scan_document

GLUED_SAMPLE_TOKEN = "bankadenetimvegozetimusulveesaslari"  # >= 24 alpha chars


class TestChromaChunkQualityLayer(unittest.TestCase):
    """Test suite for Chroma chunk-level quality integration in self-healing scanner."""

    def _create_mock_rag(self, chunks: list[dict], query_by: str = "file_hash") -> MagicMock:
        """Helper to create mock RAG with specific Chroma chunks."""
        mock_rag = MagicMock()
        mock_collection = MagicMock()

        def mock_get(where=None, include=None):
            where = where or {}
            match_val = where.get("file_hash") or where.get("source_hash")
            if match_val == "test_hash_123":
                if query_by == "source_hash" and "file_hash" in where:
                    # Return empty to test fallback to source_hash
                    return {"ids": [], "documents": [], "metadatas": []}
                docs = [c["text"] for c in chunks]
                metas = [
                    c.get("meta", {
                        "chunk_schema_version": str(CHUNK_SCHEMA_VERSION),
                        "status": "active",
                        "document_indexable": True,
                    }) for c in chunks
                ]
                ids = [f"c_{i}" for i in range(len(chunks))]
                return {"ids": ids, "documents": docs, "metadatas": metas}
            return {"ids": [], "documents": [], "metadatas": []}

        mock_collection.get.side_effect = mock_get
        mock_rag.collection = mock_collection
        return mock_rag

    @patch("repair.scanner.load_source_cache")
    def test_a_cache_clean_chroma_glued(self, mock_load_cache):
        """Test A: Cache clean + Chroma glued -> classified as REVIEW with CHROMA_GLUED_TEXT."""
        mock_load_cache.return_value = "Bu tamamen temiz bir mevzuat metnidir. Hicbir yapisik kelime yoktur."
        # Glued token >= 24 chars in chunk
        glued_chunk_text = f"MADDE 1 - {GLUED_SAMPLE_TOKEN} cercevesinde bankacilik islemleri yurutulur."
        rag = self._create_mock_rag([{"text": glued_chunk_text}])

        result = scan_document(
            file_hash="test_hash_123",
            meta={"document_id": "doc_test_a"},
            rag=rag,
        )

        self.assertIn(IssueCode.CHROMA_GLUED_TEXT, result.issue_codes)
        self.assertNotIn(IssueCode.QUALITY_GLUED_TEXT, result.issue_codes)
        self.assertTrue(result.chroma_quality.get("glued_token_count", 0) > 0)
        
        classification = classify_document(result)
        self.assertEqual(classification.classification, ClassificationType.REVIEW.value)

    @patch("repair.scanner.load_source_cache")
    def test_b_cache_clean_chroma_mojibake(self, mock_load_cache):
        """Test B: Cache clean + Chroma mojibake -> classified as REVIEW with CHROMA_MOJIBAKE."""
        mock_load_cache.return_value = "Temiz metin örneği. Türkiye Bankalar Birliği düzenlemesi."
        # Mojibake text in Chroma chunk
        mojibake_chunk_text = "TÃ¼rkiye Ä± ÅŸ â€“ Bankacılık Düzenleme ve Denetleme Kurumu."
        rag = self._create_mock_rag([{"text": mojibake_chunk_text}])

        result = scan_document(
            file_hash="test_hash_123",
            meta={"document_id": "doc_test_b"},
            rag=rag,
        )

        self.assertIn(IssueCode.CHROMA_MOJIBAKE, result.issue_codes)
        self.assertNotIn(IssueCode.QUALITY_MOJIBAKE, result.issue_codes)
        self.assertTrue(result.chroma_quality.get("mojibake_sequence_count", 0) > 0)

        classification = classify_document(result)
        self.assertEqual(classification.classification, ClassificationType.REVIEW.value)

    @patch("repair.scanner.load_source_cache")
    def test_c_cache_clean_chroma_clean(self, mock_load_cache):
        """Test C: Cache clean + Chroma clean -> classified as SAFE."""
        clean_text = "MADDE 1 - (1) Bu Yönetmeliğin amacı bankaların iç denetim sistemlerini düzenlemektir."
        mock_load_cache.return_value = clean_text
        rag = self._create_mock_rag([{"text": clean_text}])

        result = scan_document(
            file_hash="test_hash_123",
            meta={"document_id": "doc_test_c"},
            rag=rag,
        )

        self.assertNotIn(IssueCode.CHROMA_GLUED_TEXT, result.issue_codes)
        self.assertNotIn(IssueCode.CHROMA_MOJIBAKE, result.issue_codes)
        self.assertNotIn(IssueCode.CHROMA_LOW_SCORE, result.issue_codes)
        self.assertEqual(result.chroma_quality.get("glued_token_count", 0), 0)

        classification = classify_document(result)
        self.assertEqual(classification.classification, ClassificationType.SAFE.value)

    @patch("repair.scanner.load_source_cache")
    def test_d_cache_corrupt_chroma_clean(self, mock_load_cache):
        """Test D: Cache corrupt + Chroma clean -> retains QUALITY_GLUED_TEXT detection."""
        corrupt_cache = f"MADDE 1 - {GLUED_SAMPLE_TOKEN} cercevesinde yapilacaktir."
        clean_chunk = "MADDE 1 - (1) Temiz chunk metni burada yer almaktadır."
        mock_load_cache.return_value = corrupt_cache
        rag = self._create_mock_rag([{"text": clean_chunk}])

        result = scan_document(
            file_hash="test_hash_123",
            meta={"document_id": "doc_test_d"},
            rag=rag,
        )

        self.assertIn(IssueCode.QUALITY_GLUED_TEXT, result.issue_codes)
        self.assertNotIn(IssueCode.CHROMA_GLUED_TEXT, result.issue_codes)

        classification = classify_document(result)
        self.assertEqual(classification.classification, ClassificationType.REVIEW.value)

    @patch("repair.scanner.load_source_cache")
    def test_e_mixed_corruption(self, mock_load_cache):
        """Test E: Mixed corruption (cache mojibake + chroma glued) -> both recorded."""
        cache_mojibake = "TÃ¼rkiye Ä± ÅŸ â€“ Denetim ve Gözetim Raporu."
        chroma_glued = f"MADDE 1 - {GLUED_SAMPLE_TOKEN} uygulanacaktır."
        mock_load_cache.return_value = cache_mojibake
        rag = self._create_mock_rag([{"text": chroma_glued}])

        result = scan_document(
            file_hash="test_hash_123",
            meta={"document_id": "doc_test_e"},
            rag=rag,
        )

        self.assertIn(IssueCode.QUALITY_MOJIBAKE, result.issue_codes)
        self.assertIn(IssueCode.CHROMA_GLUED_TEXT, result.issue_codes)

        classification = classify_document(result)
        self.assertEqual(classification.classification, ClassificationType.REVIEW.value)

    @patch("repair.scanner.load_source_cache")
    def test_f_source_hash_identity_fallback(self, mock_load_cache):
        """Test F: Chroma metadata fallback from file_hash to source_hash."""
        mock_load_cache.return_value = "Temiz mevzuat metni."
        glued_chunk = f"MADDE 2 - {GLUED_SAMPLE_TOKEN} cercevesinde yapilacaktir."
        # Querying by source_hash, file_hash will return empty
        rag = self._create_mock_rag([{"text": glued_chunk}], query_by="source_hash")

        result = scan_document(
            file_hash="test_hash_123",
            meta={"document_id": "doc_test_f"},
            rag=rag,
        )

        self.assertEqual(result.chunk_summary.get("chunk_count", 0), 1)
        self.assertIn(IssueCode.CHROMA_GLUED_TEXT, result.issue_codes)
        self.assertTrue(result.chroma_quality.get("glued_token_count", 0) > 0)

    def test_g_repaired_documents_clean_in_production(self):
        """Test G: Previously repaired s1 and l1 do not trigger false positive candidate issues."""
        from mufettis.rag.engine import LocalRAG
        from mufettis.rag.index import IngestIndex

        try:
            rag = LocalRAG(allow_download=False)
            idx = IngestIndex()
        except Exception:
            self.skipTest("Production corpus unavailable in current test environment.")

        # Hashes for s1 and l1
        h_s1 = "bb56d01f3658ebc46c31bf2a24911f32a764d8a1491cf0eb318f700ca7244eb6"
        h_l1 = "cc02fecc2e426cbe88c051d7d4aba13c38e69ac7b2455810e26a5cfd6c1e1a1"

        for h, label in [(h_s1, "s1"), (h_l1, "l1")]:
            meta = idx.data.get("indexed_items", {}).get(h)
            if not meta:
                continue
            res = scan_document(file_hash=h, meta=meta, index=idx, rag=rag)
            self.assertNotIn(
                IssueCode.CHROMA_GLUED_TEXT,
                res.issue_codes,
                f"Repaired document {label} unexpectedly triggered CHROMA_GLUED_TEXT",
            )
            self.assertNotIn(
                IssueCode.CHROMA_MOJIBAKE,
                res.issue_codes,
                f"Repaired document {label} unexpectedly triggered CHROMA_MOJIBAKE",
            )
            # Must not be blocked or degraded
            self.assertNotEqual(res.quality_status, "chroma_glued_text")
            self.assertNotEqual(res.quality_status, "chroma_mojibake")

    @patch("repair.scanner.load_source_cache")
    def test_h_zero_production_write(self, mock_load_cache):
        """Test H: Scanning with Chroma layer performs zero writes to collection or index."""
        mock_load_cache.return_value = "Temiz metin."
        rag = self._create_mock_rag([{"text": "MADDE 1 - temiz metin."}])

        scan_document(
            file_hash="test_hash_123",
            meta={"document_id": "doc_test_h"},
            rag=rag,
        )

        # Verify collection modifying methods were NEVER called
        self.assertFalse(rag.collection.upsert.called)
        self.assertFalse(rag.collection.add.called)
        self.assertFalse(rag.collection.delete.called)
        self.assertFalse(rag.collection.update.called)

    @patch("repair.scanner.load_source_cache")
    def test_i_comparator_snapshot_reflects_chroma_degradation(self, mock_load_cache):
        """Test I: build_snapshot and compare_quality recognize Chroma glued text improvement."""
        mock_load_cache.return_value = "Temiz metin."
        glued_chunk = f"MADDE 1 - {GLUED_SAMPLE_TOKEN} cercevesinde bankacilik."
        rag = self._create_mock_rag([{"text": glued_chunk}])

        scan_res = scan_document(
            file_hash="test_hash_123",
            meta={"document_id": "doc_test_i"},
            rag=rag,
        )

        # Baseline snapshot should capture Chroma glued token count
        snapshot = build_snapshot(scan_res)
        self.assertTrue(snapshot.glued_token_count > 0)

        # Candidate with clean chunks should register improvement
        clean_candidate = ExtractionCandidate(
            document_id="doc_test_i",
            source_sha256="test_hash_123",
            raw_text="MADDE 1 - Risk yönetimi usul ve esasları ve banka denetimi mevzuat hukuku.",
            normalized_text="MADDE 1 - Risk yönetimi usul ve esasları ve banka denetimi mevzuat hukuku.",
            quality_metrics={"score": 0.95, "glued_token_count": 0, "mojibake_sequence_count": 0, "replacement_char_count": 0, "chars": 100},
            legal_chunks=[{"text": "MADDE 1 - Risk yönetimi usul ve esasları ve banka denetimi mevzuat hukuku.", "status": "active"}],
            status="SUCCESS",
        )

        comparison = compare_quality(snapshot, clean_candidate)
        self.assertEqual(comparison.status, ComparisonStatus.READY_TO_COMMIT.value)
        self.assertIn("glued_token_count", comparison.metric_deltas)
        self.assertTrue(comparison.metric_deltas["glued_token_count"]["delta"] < 0)


if __name__ == "__main__":
    unittest.main()
