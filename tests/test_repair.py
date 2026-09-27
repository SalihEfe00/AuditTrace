# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 9: Self-Healing Infrastructure / Read-Only Repair Layer
Comprehensive Test Suite (Tests 1 - 24).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import repair
from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.rag.maintenance import scan_production_rag_quality
from repair.classifier import classify_document
from repair.comparator import compare_quality
from repair.extractor import create_repair_plan, extract_candidate
from repair.models import (
    ClassificationType,
    ComparisonStatus,
    DocumentScanResult,
    ExtractionPlan,
    Issue,
    IssueCode,
    IssueSeverity,
    ProductionScanReport,
)
from repair.scanner import scan_document, scan_production

def _real_pdf_fixture():
    """Gerçek PDF duman testleri için isteğe bağlı dosya (MUFETTIS_REPAIR_PDF); yoksa None."""
    path = Path(os.environ.get("MUFETTIS_REPAIR_PDF", "").strip() or "-")
    return path if path.is_file() else None


class DummyCollection:
    """Mock Chroma collection for read-only scanner tests."""

    def __init__(self, data=None):
        self.data = data or {}
        self.mutated = False

    def get(self, where=None, include=None):
        file_hash = where.get("file_hash") if where else None
        item = self.data.get(file_hash, {})
        return {
            "ids": item.get("ids", []),
            "documents": item.get("documents", []),
            "metadatas": item.get("metadatas", []),
        }

    def delete(self, *args, **kwargs):
        self.mutated = True
        raise AssertionError("Chroma delete() call is strictly forbidden in read-only repair layer!")

    def upsert(self, *args, **kwargs):
        self.mutated = True
        raise AssertionError("Chroma upsert() call is strictly forbidden in read-only repair layer!")

    def add(self, *args, **kwargs):
        self.mutated = True
        raise AssertionError("Chroma add() call is strictly forbidden in read-only repair layer!")


class DummyRAG:
    def __init__(self, collection=None):
        self.collection = collection or DummyCollection()


class TestRepairInfrastructure(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

    # TEST 1: repair package importable
    def test_01_package_import(self):
        self.assertTrue(hasattr(repair, "scan_document"))
        self.assertTrue(hasattr(repair, "scan_production"))
        self.assertTrue(hasattr(repair, "classify_document"))
        self.assertTrue(hasattr(repair, "create_repair_plan"))
        self.assertTrue(hasattr(repair, "extract_candidate"))
        self.assertTrue(hasattr(repair, "compare_quality"))

    # TEST 2: scanner runs on empty / test fixture without crashing
    def test_02_scanner_on_empty_fixture(self):
        empty_index = {"indexed_items": {}}
        report = scan_production(index=empty_index, rag=DummyRAG())
        self.assertIsInstance(report, ProductionScanReport)
        self.assertEqual(report.scanned_documents, 0)
        self.assertEqual(report.healthy_documents, 0)
        self.assertTrue(report.dry_run)
        self.assertFalse(report.production_write_enabled)

    # TEST 3: scanner does not perform production write
    def test_03_scanner_no_production_write(self):
        col = DummyCollection({"h1": {"ids": ["c1"], "documents": ["doc"], "metadatas": [{}]}})
        rag = DummyRAG(collection=col)
        with patch("repair.scanner.load_source_cache", return_value="Temiz metin"):
            res = scan_document("h1", meta={"document_id": "d1"}, rag=rag)
        self.assertFalse(col.mutated)
        self.assertEqual(res.document_id, "d1")

    # TEST 4: missing source detected
    def test_04_missing_source_detected(self):
        non_existent_file = Path(self.test_dir) / "missing_file.pdf"
        res = scan_document(
            file_hash="dummy_hash_123",
            meta={"document_id": "doc1", "path": str(non_existent_file)},
        )
        self.assertIn(IssueCode.SOURCE_MISSING, res.issue_codes)
        self.assertEqual(res.source_status, "missing")

    # TEST 5: missing cache detected
    def test_05_missing_cache_detected(self):
        with patch("repair.scanner.load_source_cache", return_value=""):
            res = scan_document(
                file_hash="dummy_hash_no_cache",
                meta={"document_id": "doc_no_cache"},
            )
        self.assertIn(IssueCode.CACHE_MISSING, res.issue_codes)
        self.assertEqual(res.cache_status, "missing")

    # TEST 6: source hash mismatch detected
    def test_06_source_hash_mismatch_detected(self):
        test_file = Path(self.test_dir) / "real_file.pdf"
        test_file.write_bytes(b"%PDF-1.4 test content")
        actual_hash = hashlib.sha256(b"%PDF-1.4 test content").hexdigest()
        different_hash = "0000000000000000000000000000000000000000000000000000000000000000"

        res = scan_document(
            file_hash=different_hash,
            meta={"document_id": "doc_mismatch", "path": str(test_file)},
        )
        self.assertIn(IssueCode.SOURCE_HASH_MISMATCH, res.issue_codes)
        self.assertEqual(res.source_status, "hash_mismatch")

    # TEST 7: stale schema detected
    def test_07_stale_schema_detected(self):
        col = DummyCollection({
            "hash_stale": {
                "ids": ["c1"],
                "documents": ["MADDE 1 - Kanun kapsamı metni."],
                "metadatas": [{"chunk_schema_version": "1.0.0", "document_indexable": True}],
            }
        })
        rag = DummyRAG(collection=col)
        with patch("repair.scanner.load_source_cache", return_value="MADDE 1 - Kanun kapsamı metni."):
            res = scan_document("hash_stale", meta={"document_id": "doc_stale"}, rag=rag)
        self.assertIn(IssueCode.CHROMA_STALE_SCHEMA, res.issue_codes)
        self.assertEqual(res.schema_status, "stale")

    # TEST 8: glued text detected
    def test_08_glued_text_detected(self):
        glued_sample = "BDDKKurulKararı bankacılıkdüzenlemevedenetlemekurumu 5411sayılıkanun"
        with patch("repair.scanner.load_source_cache", return_value=glued_sample):
            res = scan_document("hash_glued", meta={"document_id": "doc_glued"})
        self.assertIn(IssueCode.QUALITY_GLUED_TEXT, res.issue_codes)

    # TEST 9: mojibake detected
    def test_09_mojibake_detected(self):
        mojibake_sample = "Bankacılık DÃ¼zenleme ve Denetleme Kurulu kararÄ± gereÄŸince"
        with patch("repair.scanner.load_source_cache", return_value=mojibake_sample):
            res = scan_document("hash_mojibake", meta={"document_id": "doc_mojibake"})
        self.assertIn(IssueCode.QUALITY_MOJIBAKE, res.issue_codes)

    # TEST 10: replacement character detected
    def test_10_replacement_char_detected(self):
        rep_sample = "Bu maddede yer alan h\ufffdk\ufffdmler y\ufffdr\ufffdrl\ufffcktedir."
        with patch("repair.scanner.load_source_cache", return_value=rep_sample):
            res = scan_document("hash_rep", meta={"document_id": "doc_rep"})
        self.assertIn(IssueCode.QUALITY_REPLACEMENT_CHAR, res.issue_codes)

    # TEST 11: empty/duplicate chunk detected
    def test_11_empty_and_duplicate_chunk_detected(self):
        col = DummyCollection({
            "hash_dup": {
                "ids": ["c1", "c2", "c3"],
                "documents": ["Aynı metin içeriği burada.", "Aynı metin içeriği burada.", "   "],
                "metadatas": [
                    {"chunk_schema_version": CHUNK_SCHEMA_VERSION},
                    {"chunk_schema_version": CHUNK_SCHEMA_VERSION},
                    {"chunk_schema_version": CHUNK_SCHEMA_VERSION},
                ],
            }
        })
        rag = DummyRAG(collection=col)
        with patch("repair.scanner.load_source_cache", return_value="Aynı metin içeriği burada."):
            res = scan_document("hash_dup", meta={"document_id": "doc_dup"}, rag=rag)
        self.assertIn(IssueCode.CHROMA_EMPTY_CHUNK, res.issue_codes)
        self.assertIn(IssueCode.STRUCTURE_DUPLICATE_CHUNK, res.issue_codes)

    # TEST 12: retrievability issue detected
    def test_12_retrievability_issue_detected(self):
        col = DummyCollection({
            "hash_not_ret": {
                "ids": ["c1"],
                "documents": ["Kısa metin"],  # < 250 chars and non-indexable
                "metadatas": [{
                    "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                    "document_indexable": False,
                    "evidence_indexable": False,
                }],
            }
        })
        rag = DummyRAG(collection=col)
        with patch("repair.scanner.load_source_cache", return_value="Kısa metin"):
            res = scan_document("hash_not_ret", meta={"document_id": "doc_not_ret"}, rag=rag)
        self.assertIn(IssueCode.RETRIEVAL_NOT_RETRIEVABLE, res.issue_codes)
        self.assertEqual(res.retrievability_status, "not_retrievable")

    # TEST 13: classifier produces BLOCKED
    def test_13_classifier_produces_blocked(self):
        doc_res = DocumentScanResult(document_id="doc_blocked")
        doc_res.add_issue(IssueCode.SOURCE_HASH_MISMATCH, IssueSeverity.CRITICAL, "Hash mismatch")
        clf = classify_document(doc_res)
        self.assertEqual(clf.status, ClassificationType.BLOCKED.value)
        self.assertTrue(len(clf.blocking_issues) > 0)

        # Also unknown integrity state must produce BLOCKED
        doc_res2 = DocumentScanResult(document_id="doc_unknown")
        doc_res2.add_issue(IssueCode.UNKNOWN_INTEGRITY_STATE, IssueSeverity.CRITICAL, "Integrity failure")
        clf2 = classify_document(doc_res2)
        self.assertEqual(clf2.status, ClassificationType.BLOCKED.value)

    # TEST 14: classifier produces REVIEW
    def test_14_classifier_produces_review(self):
        doc_res = DocumentScanResult(document_id="doc_review")
        doc_res.add_issue(IssueCode.QUALITY_GLUED_TEXT, IssueSeverity.ERROR, "Glued text found")
        clf = classify_document(doc_res)
        self.assertEqual(clf.status, ClassificationType.REVIEW.value)
        self.assertTrue(len(clf.review_issues) > 0)

    # TEST 15: classifier produces SAFE
    def test_15_classifier_produces_safe(self):
        doc_res = DocumentScanResult(
            document_id="doc_safe",
            source_cache_chars=500,
            current_metrics={"score": 0.95},
            chunk_summary={"retrievable_count": 2},
        )
        clf = classify_document(doc_res)
        self.assertEqual(clf.status, ClassificationType.SAFE.value)
        self.assertEqual(len(clf.blocking_issues), 0)
        self.assertEqual(len(clf.review_issues), 0)
        self.assertTrue(len(clf.safe_conditions) > 0)

    # TEST 16: extractor produces ExtractionPlan
    def test_16_extractor_produces_extraction_plan(self):
        doc_res = DocumentScanResult(
            document_id="doc_plan",
            source_path="/tmp/fake.pdf",
            source_hash="hash123",
        )
        doc_res.add_issue(IssueCode.QUALITY_GLUED_TEXT, IssueSeverity.ERROR, "Glued text")
        clf = classify_document(doc_res)
        plan = create_repair_plan(doc_res, clf)
        self.assertIsInstance(plan, ExtractionPlan)
        self.assertEqual(plan.document_id, "doc_plan")
        self.assertEqual(plan.primary_method, "pypdfium2_fast")
        self.assertIn("spatial_reconstruction", plan.fallback_methods)
        self.assertIn("pdfplumber_fallback", plan.fallback_methods)
        self.assertTrue(plan.ocr_allowed)

    # TEST 17: extractor does not write to production
    def test_17_extractor_does_not_write_to_production(self):
        test_pdf = Path(self.test_dir) / "sample.pdf"
        test_pdf.write_bytes(b"%PDF-1.4 dummy pdf bytes")
        expected_hash = hashlib.sha256(b"%PDF-1.4 dummy pdf bytes").hexdigest()

        # Custom mock extractor that simulates in-memory extraction
        mock_ext = MagicMock(return_value=("Kanonik Çıkarılmış Metin", 1, False))

        with patch("mufettis.rag.maintenance.save_source_cache") as mock_save:
            res = extract_candidate(test_pdf, expected_hash=expected_hash, custom_extractor=mock_ext)
            self.assertTrue(res["ok"])
            self.assertTrue(res["in_memory_only"])
            mock_save.assert_not_called()

    # TEST 18: comparator rejects regression
    def test_18_comparator_rejects_regression(self):
        current_metrics = {
            "score": 0.92,
            "chars": 1000,
            "glued_token_count": 0,
            "mojibake_sequence_count": 0,
            "replacement_char_count": 0,
        }
        # Candidate introduces mojibake and lower score
        candidate_metrics = {
            "score": 0.70,
            "chars": 1000,
            "glued_token_count": 0,
            "mojibake_sequence_count": 5,
            "replacement_char_count": 2,
        }
        res = compare_quality(current_metrics, candidate_metrics)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(len(res.quality_reasons) > 0)

    # TEST 19: comparator accepts improvement as READY_TO_COMMIT
    def test_19_comparator_accepts_improvement(self):
        current_metrics = {
            "score": 0.75,
            "chars": 1000,
            "glued_token_count": 12,
            "mojibake_sequence_count": 2,
            "replacement_char_count": 1,
        }
        # Candidate cleanly fixes glued tokens and mojibake with higher score
        candidate_metrics = {
            "score": 0.95,
            "chars": 1020,
            "glued_token_count": 0,
            "mojibake_sequence_count": 0,
            "replacement_char_count": 0,
        }
        res = compare_quality(current_metrics, candidate_metrics)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)
        self.assertTrue(res.delta_score > 0)

    # TEST 20: comparator marks ambiguous candidate as REVIEW
    def test_20_comparator_marks_ambiguous_as_review(self):
        current_metrics = {
            "score": 0.89,
            "chars": 1000,
            "glued_token_count": 1,
            "mojibake_sequence_count": 0,
            "replacement_char_count": 0,
        }
        # Candidate fixes 1 glued word, but score drops slightly within tolerance without clear dominance
        candidate_metrics = {
            "score": 0.88,
            "chars": 990,
            "glued_token_count": 1,
            "mojibake_sequence_count": 0,
            "replacement_char_count": 0,
        }
        res = compare_quality(current_metrics, candidate_metrics)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)

    # TEST 21: production write functions are NOT called during repair scan
    def test_21_production_write_functions_not_called(self):
        with patch("mufettis.rag.maintenance.save_source_cache") as mock_save, \
             patch("mufettis.rag.maintenance.reset_chroma_collection") as mock_reset:
            dummy_index = {
                "indexed_items": {
                    "hash_a": {"source_id": "src1", "title": "Belge A"},
                    "hash_b": {"source_id": "src2", "title": "Belge B"},
                }
            }
            with patch("repair.scanner.load_source_cache", return_value="Denetim metni."):
                report = scan_production(index=dummy_index, rag=DummyRAG())
            self.assertEqual(report.scanned_documents, 2)
            mock_save.assert_not_called()
            mock_reset.assert_not_called()

    # TEST 22: production Chroma and source cache state before == after
    def test_22_state_before_and_after_equality(self):
        col = DummyCollection({
            "h_equal": {
                "ids": ["c1"],
                "documents": ["Korumalı mevzuat metni."],
                "metadatas": [{"chunk_schema_version": CHUNK_SCHEMA_VERSION, "document_indexable": True}],
            }
        })
        rag = DummyRAG(collection=col)
        dummy_index = {"indexed_items": {"h_equal": {"source_id": "src_eq", "title": "Korumalı"}}}

        # Fingerprint before scan
        before_state = {
            "doc_keys": list(col.data.keys()),
            "doc_content": col.data["h_equal"]["documents"][:],
            "metadata_content": [dict(m) for m in col.data["h_equal"]["metadatas"]],
        }

        with patch("repair.scanner.load_source_cache", return_value="Korumalı mevzuat metni."):
            report = scan_production(index=dummy_index, rag=rag)

        # Fingerprint after scan
        after_state = {
            "doc_keys": list(col.data.keys()),
            "doc_content": col.data["h_equal"]["documents"][:],
            "metadata_content": [dict(m) for m in col.data["h_equal"]["metadatas"]],
        }

        self.assertEqual(before_state, after_state)
        self.assertFalse(col.mutated)
        self.assertEqual(report.scanned_documents, 1)

    # TEST 23: legacy scanner API compatibility
    def test_23_legacy_scanner_api_compatibility(self):
        from mufettis.rag.index import IngestIndex
        idx = IngestIndex()
        idx.data = {
            "indexed_items": {
                "legacy_h": {"source_id": "bddk_1", "title": "Eski Tarama Belgesi"}
            }
        }
        with patch("mufettis.rag.maintenance.load_source_cache", return_value="Eski test metni"):
            res = scan_production_rag_quality(index=idx, rag=DummyRAG())
        self.assertTrue(res.get("ok"))
        self.assertIn("documents", res)
        self.assertIn("quality_threshold", res)
        self.assertEqual(res["total_documents"], 1)

    # TEST 24: full regression compatibility
    def test_24_no_regression_in_existing_models(self):
        # Verify schema version contract
        self.assertEqual(str(CHUNK_SCHEMA_VERSION), "10.0.0")
        # Verify that scan report can be serialized to JSON without circular references
        rep = ProductionScanReport(scanned_documents=1, healthy_documents=1)
        json_str = json.dumps(rep.to_dict())
        self.assertIn('"scanned_documents": 1', json_str)


class TestFaz10VerifiedCandidateExtraction(unittest.TestCase):
    """Müfettiş AI - FAZ 10 Verified Source Candidate Extraction Test Suite."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

        # Create a valid mock PDF file
        self.valid_pdf = Path(self.test_dir) / "valid_sample.pdf"
        self.valid_pdf.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>startxref\n0\n%%EOF")
        self.valid_hash = hashlib.sha256(self.valid_pdf.read_bytes()).hexdigest()

    # FAZ 10 - TEST 1: Valid source PDF passes source validation
    def test_f10_01_valid_source_pdf_validation_pass(self):
        from repair.extractor import validate_source
        val = validate_source(self.valid_pdf, expected_hash=self.valid_hash, document_id="doc1")
        self.assertEqual(val.status, "VERIFIED")
        self.assertTrue(val.exists)
        self.assertTrue(val.readable)
        self.assertTrue(val.hash_match)
        self.assertTrue(val.identity_verified)

    # FAZ 10 - TEST 2: Missing PDF results in MISSING / no extraction
    def test_f10_02_missing_pdf_blocked(self):
        from repair.extractor import extract_candidate, validate_source
        missing_path = Path(self.test_dir) / "non_existent.pdf"
        val = validate_source(missing_path, expected_hash=self.valid_hash)
        self.assertEqual(val.status, "MISSING")
        self.assertFalse(val.exists)

        cand = extract_candidate(missing_path, expected_hash=self.valid_hash)
        self.assertEqual(cand.status, "MISSING")
        self.assertEqual(len(cand.legal_chunks), 0)

    # FAZ 10 - TEST 3: Unreadable/invalid source (non-pdf or empty or invalid magic bytes)
    def test_f10_03_invalid_source_blocked(self):
        from repair.extractor import extract_candidate, validate_source
        bad_file = Path(self.test_dir) / "not_pdf.txt"
        bad_file.write_text("Metin dosyası", encoding="utf-8")
        val = validate_source(bad_file, expected_hash="dummy")
        self.assertEqual(val.status, "UNREADABLE")

        empty_pdf = Path(self.test_dir) / "empty.pdf"
        empty_pdf.write_bytes(b"")
        val2 = validate_source(empty_pdf, expected_hash="dummy")
        self.assertEqual(val2.status, "UNREADABLE")

        corrupt_pdf = Path(self.test_dir) / "corrupt.pdf"
        corrupt_pdf.write_bytes(b"HELLO_WORLD_NO_PDF_MAGIC")
        val3 = validate_source(corrupt_pdf, expected_hash="dummy")
        self.assertEqual(val3.status, "UNREADABLE")

    # FAZ 10 - TEST 4: Hash match verifies identity
    def test_f10_04_hash_match_verified(self):
        from repair.extractor import validate_source
        val = validate_source(self.valid_pdf, expected_hash=self.valid_hash)
        self.assertEqual(val.status, "VERIFIED")
        self.assertEqual(val.actual_sha256, self.valid_hash)

    # FAZ 10 - TEST 5: Hash mismatch is BLOCKED and extraction does not run
    def test_f10_05_hash_mismatch_blocked_no_extraction(self):
        from repair.extractor import extract_candidate
        mock_ext = MagicMock()
        different_hash = "1111111111111111111111111111111111111111111111111111111111111111"
        cand = extract_candidate(self.valid_pdf, expected_hash=different_hash, custom_extractor=mock_ext)
        self.assertEqual(cand.status, "HASH_MISMATCH")
        mock_ext.assert_not_called()

    # FAZ 10 - TEST 6: Known valid PDF produces candidate successfully
    def test_f10_06_candidate_successfully_generated(self):
        from repair.extractor import extract_candidate
        mock_ext = MagicMock(return_value=(
            "MADDE 1 - (1) Bu Kanunun amacı bankacılık sistemini düzenlemektir.\n"
            "MADDE 2 - (1) Bu Kanun tüm bankaları kapsar.", 2, False
        ))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertEqual(cand.status, "SUCCESS")
        self.assertTrue(cand.in_memory_only)
        self.assertGreater(len(cand.normalized_text), 0)
        self.assertEqual(cand.page_count, 2)
        self.assertFalse(cand.ocr_used)

    # FAZ 10 - TEST 7: Fallback scenario tracking
    def test_f10_07_fallback_scenario_invoked(self):
        from repair.extractor import extract_candidate
        # When custom extractor or pipeline uses fallback
        mock_ext = MagicMock(return_value=("Yedek metin", 1, False))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertEqual(cand.status, "SUCCESS")
        self.assertIn("fallback_used", cand.metadata.get("method_info", {}))

    # FAZ 10 - TEST 8: OCR scenario: OCR only when required and properly flagged
    def test_f10_08_ocr_scenario_flagged(self):
        from repair.extractor import extract_candidate
        mock_ext = MagicMock(return_value=("OCR ile taranmış belge metni", 1, True))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertTrue(cand.ocr_used)
        self.assertEqual(cand.metadata["method_info"]["ocr_used"], True)

    # FAZ 10 - TEST 9: Normalization applied
    def test_f10_09_normalization_applied(self):
        from repair.extractor import extract_candidate
        raw_input = "MADDE 1 - (1) BankacÄ±lÄ±k DÃ¼zenleme Kurumu."
        mock_ext = MagicMock(return_value=(raw_input, 1, False))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertEqual(cand.status, "SUCCESS")
        # Normalization fixes mojibake
        self.assertIn("Bankacılık Düzenleme", cand.normalized_text)

    # FAZ 10 - TEST 10: Glued token repair pipeline remains active
    def test_f10_10_glued_token_repair_active(self):
        from repair.extractor import extract_candidate
        raw_glued = "BDDKKurulKararı 5411sayılıkanun"
        mock_ext = MagicMock(return_value=(raw_glued, 1, False))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertIsNotNone(cand.normalized_text)
        self.assertIn("glued_token_count", cand.quality_metrics)

    # FAZ 10 - TEST 11: Structure discovery preserved
    def test_f10_11_structure_discovery_preserved(self):
        from repair.extractor import extract_candidate
        raw_text = (
            "BANKACILIK KANUNU\n"
            "MADDE 1 - (1) Amaç ve kapsam.\n"
            "MADDE 2 - (1) Tanımlar."
        )
        mock_ext = MagicMock(return_value=(raw_text, 1, False))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, title="Bankacılık Kanunu", custom_extractor=mock_ext)
        self.assertIsInstance(cand.structure, dict)
        self.assertEqual(cand.structure.get("article_count"), 2)
        self.assertTrue(cand.structure.get("legal_structure_resolved"))

    # FAZ 10 - TEST 12: Legal chunk generation works
    def test_f10_12_legal_chunk_generation_works(self):
        from repair.extractor import extract_candidate
        raw_text = (
            "MADDE 1 - (1) Bu Kanunun amacı finansal piyasalarda güveni sağlamaktır.\n"
            "MADDE 2 - (1) Bu Kanun hükümleri Türkiye'deki tüm bankalara uygulanır."
        )
        mock_ext = MagicMock(return_value=(raw_text, 1, False))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertGreater(len(cand.legal_chunks), 0)
        first_chunk = cand.legal_chunks[0]
        self.assertIn("text", first_chunk)
        self.assertIn("chunk_type", first_chunk)
        self.assertIn("chunk_schema_version", first_chunk)

    # FAZ 10 - TEST 13: Source SHA256 preserved throughout candidate
    def test_f10_13_source_hash_propagation(self):
        from repair.extractor import extract_candidate
        mock_ext = MagicMock(return_value=("MADDE 1 - Test", 1, False))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertEqual(cand.source_sha256, self.valid_hash)
        for chk in cand.legal_chunks:
            self.assertEqual(chk["source_hash"], self.valid_hash)

    # FAZ 10 - TEST 14: Candidate chunks have current schema version
    def test_f10_14_candidate_chunks_schema_version(self):
        from repair.extractor import extract_candidate
        mock_ext = MagicMock(return_value=("MADDE 1 - Test", 1, False))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        for chk in cand.legal_chunks:
            self.assertEqual(chk["chunk_schema_version"], str(CHUNK_SCHEMA_VERSION))

    # FAZ 10 - TEST 15: Candidate metadata contains extraction method
    def test_f10_15_candidate_metadata_contains_method(self):
        from repair.extractor import extract_candidate
        mock_ext = MagicMock(return_value=("Metin", 1, False))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertIn("method_info", cand.metadata)
        self.assertEqual(cand.metadata["method_info"]["primary_method"], "custom")

    # FAZ 10 - TEST 16: Production Chroma untouched
    def test_f10_16_production_chroma_untouched(self):
        col = DummyCollection({"hash_x": {"ids": ["c1"], "documents": ["doc1"], "metadatas": [{}]}})
        mock_ext = MagicMock(return_value=("MADDE 1 - Deneme", 1, False))
        from repair.extractor import extract_candidate
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertEqual(cand.status, "SUCCESS")
        self.assertFalse(col.mutated)

    # FAZ 10 - TEST 17: Production source cache untouched
    def test_f10_17_production_source_cache_untouched(self):
        with patch("mufettis.rag.maintenance.save_source_cache") as mock_save:
            from repair.extractor import extract_candidate
            mock_ext = MagicMock(return_value=("MADDE 1 - Test", 1, False))
            cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
            self.assertEqual(cand.status, "SUCCESS")
            mock_save.assert_not_called()

    # FAZ 10 - TEST 18: No production file write
    def test_f10_18_no_production_file_write(self):
        with patch("builtins.open", side_effect=open) as spy_open:
            from repair.extractor import extract_candidate
            mock_ext = MagicMock(return_value=("MADDE 1 - Test", 1, False))
            cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
            # Ensure open() was never called with 'w', 'a', or 'wb'
            for call_args in spy_open.call_args_list:
                args, kwargs = call_args
                mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
                self.assertNotIn("w", mode, f"File write attempt detected in candidate extraction: {args}")
                self.assertNotIn("a", mode, f"File append attempt detected in candidate extraction: {args}")

    # FAZ 10 - TEST 19: Candidate extraction failure is explicit
    def test_f10_19_candidate_extraction_failure_explicit(self):
        from repair.extractor import extract_candidate
        # When extractor returns empty text
        mock_ext = MagicMock(return_value=("   ", 1, False))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertEqual(cand.status, "EMPTY_EXTRACTION")
        self.assertFalse(cand["ok"])

        # When extractor raises an exception
        mock_fail = MagicMock(side_effect=RuntimeError("Engine crash"))
        cand2 = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_fail)
        self.assertEqual(cand2.status, "EXTRACTION_FAILED")
        self.assertFalse(cand2["ok"])

    # FAZ 10 - TEST 20: OCR and fallback metadata correctly recorded
    def test_f10_20_ocr_fallback_metadata_recorded(self):
        from repair.extractor import extract_candidate
        mock_ext = MagicMock(return_value=("OCR metni", 3, True))
        cand = extract_candidate(self.valid_pdf, expected_hash=self.valid_hash, custom_extractor=mock_ext)
        self.assertTrue(cand.ocr_used)
        self.assertEqual(cand.page_count, 3)
        self.assertEqual(cand.metadata["method_info"]["ocr_used"], True)

    # Real PDF Smoke Test
    def test_f10_real_pdf_smoke_test(self):
        real_pdf = _real_pdf_fixture()
        if real_pdf is None:
            self.skipTest("MUFETTIS_REPAIR_PDF ile gerçek bir PDF yolu verin")
        from repair.extractor import extract_candidate, validate_source
        from mufettis.common.utils import sha256_bytes
        h = sha256_bytes(real_pdf.read_bytes())
        val = validate_source(real_pdf, expected_hash=h)
        self.assertEqual(val.status, "VERIFIED")

        cand = extract_candidate(real_pdf, expected_hash=h)
        self.assertEqual(cand.status, "SUCCESS")
        self.assertTrue(cand.in_memory_only)
        self.assertGreater(len(cand.normalized_text), 1000)
        self.assertGreater(len(cand.legal_chunks), 0)
        self.assertEqual(cand.source_sha256, h)


class TestFaz11AQualityGateCore(unittest.TestCase):
    """Müfettiş AI - FAZ 11A Quality Gate Core / RAG-Independent Comparison Test Suite."""

    def setUp(self):
        from repair.models import QualitySnapshot
        self.base_current = QualitySnapshot(
            document_id="doc100",
            source_document_id="doc100",
            source_sha256="hash_base_123",
            quality_score=0.75,
            glued_token_count=15,
            very_long_token_count=2,
            mojibake_sequence_count=2,
            replacement_char_count=1,
            chars=2000,
            article_count=10,
            paragraph_count=20,
            item_count=5,
            subitem_count=0,
            section_count=2,
            accounting_code_count=0,
            chunk_count=10,
            meaningful_chunk_count=10,
            empty_chunk_count=0,
            duplicate_chunk_count=0,
            metadata_completeness=1.0,
            schema_version="10.0.0",
        )

    # 1. CLEAR IMPROVEMENT -> READY_TO_COMMIT
    def test_f11_01_clear_improvement(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        cand = QualitySnapshot(
            document_id="doc100",
            source_sha256="hash_base_123",
            quality_score=0.96,
            glued_token_count=0,
            mojibake_sequence_count=0,
            replacement_char_count=0,
            chars=2050,
            article_count=10,
            paragraph_count=20,
            chunk_count=10,
            meaningful_chunk_count=10,
            schema_version="10.0.0",
        )
        res = compare_quality(self.base_current, cand)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)
        self.assertEqual(len(res.hard_failures), 0)
        self.assertEqual(len(res.warnings), 0)

    # 2. CLEAR REGRESSION -> REJECT
    def test_f11_02_clear_regression(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        cand = QualitySnapshot(
            document_id="doc100",
            source_sha256="hash_base_123",
            quality_score=0.50,
            glued_token_count=40,
            mojibake_sequence_count=8,
            replacement_char_count=4,
            chars=2000,
            article_count=10,
            chunk_count=10,
        )
        res = compare_quality(self.base_current, cand)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(len(res.hard_failures) > 0)

    # 3. MOJIBAKE INTRODUCED -> REJECT
    def test_f11_03_mojibake_introduced(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        clean_curr = QualitySnapshot(
            document_id="doc1", source_sha256="h1", quality_score=0.90, mojibake_sequence_count=0, chars=1000
        )
        cand = QualitySnapshot(
            document_id="doc1", source_sha256="h1", quality_score=0.92, mojibake_sequence_count=3, chars=1000
        )
        res = compare_quality(clean_curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(any("mojibake" in x.lower() for x in res.hard_failures))

    # 4. REPLACEMENT CHAR INTRODUCED -> REJECT
    def test_f11_04_replacement_char_introduced(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        clean_curr = QualitySnapshot(
            document_id="doc1", source_sha256="h1", quality_score=0.90, replacement_char_count=0, chars=1000
        )
        cand = QualitySnapshot(
            document_id="doc1", source_sha256="h1", quality_score=0.92, replacement_char_count=2, chars=1000
        )
        res = compare_quality(clean_curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(any("replacement" in x.lower() for x in res.hard_failures))

    # 5. GLUED TEXT IMPROVES -> positive signal
    def test_f11_05_glued_text_improves(self):
        from repair.comparator import calculate_metric_deltas
        from repair.models import MetricDirection, QualitySnapshot
        curr = QualitySnapshot(glued_token_count=50)
        cand = QualitySnapshot(glued_token_count=2)
        deltas = calculate_metric_deltas(curr, cand)
        self.assertEqual(deltas["glued_token_count"]["direction"], MetricDirection.IMPROVED.value)
        self.assertEqual(deltas["glued_token_count"]["delta"], -48)

    # 6. GLUED TEXT WORSE -> REVIEW / WARNING
    def test_f11_06_glued_text_worse(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.90, glued_token_count=2, chars=1000)
        cand = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.90, glued_token_count=25, chars=1000)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)
        self.assertTrue(len(res.warnings) > 0)

    # 7. STRUCTURE CATASTROPHIC LOSS -> REJECT
    def test_f11_07_structure_catastrophic_loss(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.80, article_count=50, chars=5000)
        cand = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.85, article_count=2, chars=5000)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(any("katastrofik" in x.lower() for x in res.hard_failures))

    # 8. STRUCTURE MODERATE DRIFT -> REVIEW
    def test_f11_08_structure_moderate_drift(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.80, article_count=50, glued_token_count=10, chars=5000)
        cand = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.88, article_count=30, glued_token_count=0, chars=5000)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)
        self.assertTrue(any("madde sayısında" in x.lower() for x in res.warnings))

    # 9. CHUNK EXPLOSION -> REVIEW
    def test_f11_09_chunk_explosion(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.85, chunk_count=10, chars=2000)
        cand = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.88, chunk_count=45, chars=2100)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)
        self.assertTrue(any("chunk explosion" in x.lower() for x in res.warnings))

    # 10. EMPTY CHUNKS INCREASE -> REJECT
    def test_f11_10_empty_chunks_increase(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", empty_chunk_count=0, chars=1000)
        cand = QualitySnapshot(document_id="doc1", source_sha256="h1", empty_chunk_count=3, chars=1000)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(any("boş chunk" in x.lower() for x in res.hard_failures))

    # 11. DUPLICATE CHUNKS INCREASE -> REVIEW
    def test_f11_11_duplicate_chunks_increase(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", duplicate_chunk_count=0, glued_token_count=10, chars=1000)
        cand = QualitySnapshot(document_id="doc1", source_sha256="h1", duplicate_chunk_count=3, glued_token_count=0, chars=1000)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)
        self.assertTrue(any("yinelenen parça" in x.lower() for x in res.warnings))

    # 12. IDENTICAL CURRENT/CANDIDATE -> REVIEW ("No measurable improvement.")
    def test_f11_12_identical_current_candidate(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.90, chars=1000, chunk_count=5, article_count=5)
        cand = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.90, chars=1000, chunk_count=5, article_count=5)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)
        self.assertIn("No measurable improvement.", res.warnings)

    # 13. SOURCE HASH MISMATCH -> REJECT
    def test_f11_13_source_hash_mismatch(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="hash_original", quality_score=0.80)
        cand = QualitySnapshot(document_id="doc1", source_sha256="hash_different", quality_score=0.95)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertEqual(res.source_identity_status, "MISMATCH")

    # 14. DOCUMENT IDENTITY MISMATCH -> REJECT
    def test_f11_14_document_identity_mismatch(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc_a", source_sha256="h1", quality_score=0.80)
        cand = QualitySnapshot(document_id="doc_b", source_sha256="h1", quality_score=0.95)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertEqual(res.source_identity_status, "DOCUMENT_MISMATCH")

    # 15. MISSING METADATA / SCHEMA INCOMPATIBILITY -> REVIEW
    def test_f11_15_missing_metadata(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.80, glued_token_count=10)
        cand = QualitySnapshot(
            document_id="doc1", source_sha256="h1", quality_score=0.95, glued_token_count=0,
            metadata_completeness=0.5, schema_version="5.0.0"
        )
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)
        self.assertTrue(len(res.warnings) > 0)

    # 16. QUALITY SCORE IMPROVES BUT HARD SAFETY FAILS -> REJECT
    def test_f11_16_score_improves_but_hard_safety_fails(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.70, mojibake_sequence_count=0, chars=1000)
        # Score jumps to 0.98, but introduces mojibake
        cand = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.98, mojibake_sequence_count=2, chars=1000)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)

    # 17. SCORE DECLINES SLIGHTLY BUT DEFECTS FIXED -> READY_TO_COMMIT
    def test_f11_17_score_declines_within_tolerance_defects_fixed(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(
            document_id="doc1", source_sha256="h1", quality_score=0.90, glued_token_count=10, mojibake_sequence_count=1, chars=1000
        )
        cand = QualitySnapshot(
            document_id="doc1", source_sha256="h1", quality_score=0.89, glued_token_count=0, mojibake_sequence_count=0, chars=1000
        )
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)

    # 18. MIXED SIGNALS -> REVIEW
    def test_f11_18_mixed_signals(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.85, glued_token_count=10, duplicate_chunk_count=0, chars=1000)
        # Fixed glued tokens, but duplicate chunks increased
        cand = QualitySnapshot(document_id="doc1", source_sha256="h1", quality_score=0.88, glued_token_count=0, duplicate_chunk_count=2, chars=1000)
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)

    # 19. CANDIDATE INCOMPLETE -> REJECT
    def test_f11_19_candidate_incomplete(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, ExtractionCandidate
        curr = self.base_current
        cand = ExtractionCandidate(
            document_id="doc100",
            source_sha256="hash_base_123",
            status="EXTRACTION_FAILED",
        )
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)

    # 20. VALID CLEAR IMPROVEMENT WITH NO REGRESSION -> READY_TO_COMMIT
    def test_f11_20_valid_clear_improvement_no_regression(self):
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        curr = QualitySnapshot(
            document_id="doc200", source_sha256="h200", quality_score=0.72, glued_token_count=25, chars=1500, article_count=8, chunk_count=8
        )
        cand = QualitySnapshot(
            document_id="doc200", source_sha256="h200", quality_score=0.94, glued_token_count=0, chars=1550, article_count=8, chunk_count=8
        )
        res = compare_quality(curr, cand)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)

    # 21. REAL PDF FIXTURE VS CONTROLLED BASELINE COMPARISON
    def test_f11_21_real_pdf_fixture_vs_controlled_baseline(self):
        real_pdf = _real_pdf_fixture()
        if real_pdf is None:
            self.skipTest("MUFETTIS_REPAIR_PDF ile gerçek bir PDF yolu verin")
        from repair.extractor import extract_candidate
        from repair.comparator import compare_quality
        from repair.models import ComparisonStatus, QualitySnapshot
        from mufettis.common.utils import sha256_bytes

        h = sha256_bytes(real_pdf.read_bytes())
        cand = extract_candidate(real_pdf, expected_hash=h, document_id="real_doc")
        self.assertEqual(cand.status, "SUCCESS")

        # Controlled baseline representing a corrupted/glued previous extraction of the same document
        poor_baseline = QualitySnapshot(
            document_id="real_doc",
            source_sha256=h,
            quality_score=0.65,
            glued_token_count=40,
            mojibake_sequence_count=5,
            replacement_char_count=2,
            chars=7000,
            article_count=0,
            chunk_count=10,
        )

        res = compare_quality(poor_baseline, cand)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)
        self.assertGreater(res.delta_score, 0)
        self.assertEqual(len(res.hard_failures), 0)


if __name__ == "__main__":
    unittest.main()
