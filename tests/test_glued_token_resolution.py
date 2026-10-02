# -*- coding: utf-8 -*-
"""
Tests for Glued Token Resolution & RESCAN False-Positive Mitigation.

Covers Tests A through I:
- Test A: 'kimliksizleştirildiğinde' -> NOT_GLUE
- Test B: 'Kurumunpaylaşımöncesindeuygungörüşününalınmışolması' -> GLUE
- Test C: 282 -> 1 residual valid Turkish word -> RESCAN PASS
- Test D: 282 -> 20 real glued corruption -> RESCAN FAIL
- Test E: 282 -> 0 -> RESCAN PASS
- Test F: Short normal Turkish words -> NOT_GLUE
- Test G: Mixed document -> real corruption caught
- Test H: Target 1 & Target 2 behavior preserved
- Test I: Zero production writes
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from mufettis.extraction.pdf import (
    _is_true_glued_token,
    _pdf_generic_quality_metrics,
    is_glued_token,
)
from repair.models import (
    CommitResult,
    CommitStatus,
    ComparisonStatus,
    DocumentScanResult,
    ExtractionCandidate,
    IssueCode,
    IssueSeverity,
    ComparisonResult,
    VerificationResult,
    VerificationStatus,
    WaveConfig,
    WaveDocumentStatus,
)
from repair.wave import WaveOrchestrator
from mufettis.config import DATA_DIR


class TestGluedTokenResolution(unittest.TestCase):
    """Unit tests for distinguishing legitimate Turkish words from glued tokens and RESCAN evaluation."""

    def test_a_natural_long_turkish_word_not_glued(self):
        """Test A: 'kimliksizleştirildiğinde' (24 chars) is recognized as a valid Turkish word, NOT glued corruption."""
        token = "kimliksizleştirildiğinde"
        self.assertFalse(
            _is_true_glued_token(token),
            f"'{token}' should NOT be classified as glued text.",
        )
        self.assertFalse(
            is_glued_token(token),
            f"'{token}' alias should also return False.",
        )
        # Verify in generic quality metrics: glued count should be 0
        metrics = _pdf_generic_quality_metrics(f"Veriler {token} saklanır.")
        self.assertEqual(metrics["glued_token_count"], 0)

    def test_b_real_concatenated_phrase_is_glued(self):
        """Test B: 'Kurumunpaylaşımöncesindeuygungörüşününalınmışolması' is recognized as glued corruption."""
        token = "Kurumunpaylaşımöncesindeuygungörüşününalınmışolması"
        self.assertTrue(
            _is_true_glued_token(token),
            f"'{token}' must be classified as glued text corruption.",
        )
        metrics = _pdf_generic_quality_metrics(f"Metin {token} içermektedir.")
        self.assertGreaterEqual(metrics["glued_token_count"], 1)

    def test_c_rescan_passes_on_residual_valid_word_reduction(self):
        """Test C: Baseline = 282 glued tokens -> 1 residual valid word (reduction >= 95%) -> RESCAN PASS."""
        appr_mgr = MagicMock()
        appr_mgr.validate_and_consume.return_value = (True, "", MagicMock())

        orchestrator = WaveOrchestrator(
            rag=MagicMock(),
            index=MagicMock(),
            cache_dir=Path("tests/mock_cache"),
            staging_dir=Path("tests/mock_staging"),
            audit_logger=MagicMock(),
            approval_manager=appr_mgr,
        )

        # Candidate with clean metrics
        candidate = ExtractionCandidate(
            document_id="test_doc_c",
            source_document_id="test_doc_c",
            source_sha256="test_c_hash",
            raw_text="temiz metin kimliksizleştirildiğinde",
            normalized_text="temiz metin kimliksizleştirildiğinde",
            quality_metrics={"score": 0.9922, "glued_token_count": 0, "very_long_token_count": 0},
            legal_chunks=[{"chunk_index": 0, "chunk_id": "c0", "text": "temiz metin"}],
            extraction_method="pymupdf",
        )

        config = WaveConfig(
            environment="test",
            actor="test_runner",
            dry_run=False,
            require_rescan=True,
            rollback_on_rescan_fail=True,
        )

        # Mock initial scan
        mock_init_scan = DocumentScanResult(
            document_id="test_doc_c",
            source_hash="test_c_hash",
            current_metrics={"score": 0.595, "glued_token_count": 282},
        )
        mock_init_scan.add_issue(
            IssueCode.QUALITY_GLUED_TEXT,
            IssueSeverity.ERROR,
            "Chroma chunklarında yapışık metin tespit edildi (adet: 282)",
            {"glued_token_count": 282},
        )

        # Mock rescan with only 1 residual glued token (e.g. 282 -> 1)
        mock_rescan = DocumentScanResult(
            document_id="test_doc_c",
            source_hash="test_c_hash",
            current_metrics={"score": 0.9922, "glued_token_count": 1},
        )
        mock_rescan.add_issue(
            IssueCode.QUALITY_GLUED_TEXT,
            IssueSeverity.ERROR,
            "Cache metninde yapışık sözcükler tespit edildi (adet: 1)",
            {"glued_token_count": 1},
        )

        with patch("repair.wave.scan_document", side_effect=[mock_init_scan, mock_rescan]), \
             patch("repair.wave.compare_quality", return_value=ComparisonResult(document_id="test_doc_c", status=ComparisonStatus.READY_TO_COMMIT.value)), \
             patch("repair.wave.commit_candidate", return_value=CommitResult(commit_id="com_c", document_id="test_doc_c", source_sha256="test_c_hash", status=CommitStatus.COMMITTED.value, candidate_id="cand_c")), \
             patch("repair.wave.verify_committed_repair", return_value=VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="com_c", candidate_id="cand_c", document_id="test_doc_c", source_sha256="test_c_hash")), \
             patch.object(orchestrator, "_capture_corpus_fingerprint", return_value={"total_chunks": 100, "documents": {}}):

            doc_res = orchestrator.execute_document(
                item={"document_id": "test_doc_c", "source_hash": "test_c_hash", "approval_id": "appr_c"},
                config=config,
                candidate_override=candidate,
            )

            self.assertTrue(doc_res.rescan_passed, "RESCAN should pass when residual is reduced 282 -> 1.")
            self.assertFalse(doc_res.rollback_occurred, "Rollback should NOT occur.")
            self.assertEqual(doc_res.status, WaveDocumentStatus.VERIFIED_AND_RESCANNED.value)

    def test_d_rescan_fails_on_excessive_residual_corruption(self):
        """Test D: Baseline = 282 glued tokens -> 20 residual glued corruptions -> RESCAN FAIL & ROLLBACK."""
        appr_mgr = MagicMock()
        appr_mgr.validate_and_consume.return_value = (True, "", MagicMock())

        orchestrator = WaveOrchestrator(
            rag=MagicMock(),
            index=MagicMock(),
            cache_dir=Path("tests/mock_cache"),
            staging_dir=Path("tests/mock_staging"),
            audit_logger=MagicMock(),
            approval_manager=appr_mgr,
        )

        candidate = ExtractionCandidate(
            document_id="test_doc_d",
            source_document_id="test_doc_d",
            source_sha256="test_d_hash",
            raw_text="hala bozuk metin",
            normalized_text="hala bozuk metin",
            quality_metrics={"score": 0.85, "glued_token_count": 20, "very_long_token_count": 5},
            legal_chunks=[{"chunk_index": 0, "chunk_id": "c0", "text": "hala bozuk metin"}],
            extraction_method="pymupdf",
        )

        config = WaveConfig(
            environment="test",
            actor="test_runner",
            dry_run=False,
            require_rescan=True,
            rollback_on_rescan_fail=True,
        )

        mock_init_scan = DocumentScanResult(
            document_id="test_doc_d",
            source_hash="test_d_hash",
            current_metrics={"score": 0.595, "glued_token_count": 282},
        )
        mock_init_scan.add_issue(
            IssueCode.QUALITY_GLUED_TEXT,
            IssueSeverity.ERROR,
            "Chroma chunklarında yapışık metin tespit edildi (adet: 282)",
            {"glued_token_count": 282},
        )

        # Mock rescan with 20 remaining glued tokens
        mock_rescan = DocumentScanResult(
            document_id="test_doc_d",
            source_hash="test_d_hash",
            current_metrics={"score": 0.85, "glued_token_count": 20},
        )
        mock_rescan.add_issue(
            IssueCode.QUALITY_GLUED_TEXT,
            IssueSeverity.ERROR,
            "Cache metninde yapışık sözcükler tespit edildi (adet: 20)",
            {"glued_token_count": 20},
        )

        with patch("repair.wave.scan_document", side_effect=[mock_init_scan, mock_rescan]), \
             patch("repair.wave.compare_quality", return_value=ComparisonResult(document_id="test_doc_d", status=ComparisonStatus.READY_TO_COMMIT.value)), \
             patch("repair.wave.commit_candidate", return_value=CommitResult(commit_id="com_d", document_id="test_doc_d", source_sha256="test_d_hash", status=CommitStatus.COMMITTED.value, candidate_id="cand_d")), \
             patch("repair.wave.verify_committed_repair", return_value=VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="com_d", candidate_id="cand_d", document_id="test_doc_d", source_sha256="test_d_hash")), \
             patch("repair.wave.rollback_commit", return_value=MagicMock(status="ROLLBACK_SUCCESS", rollback_id="rb_d")), \
             patch.object(orchestrator, "_capture_corpus_fingerprint", return_value={"total_chunks": 100, "documents": {}}):

            doc_res = orchestrator.execute_document(
                item={"document_id": "test_doc_d", "source_hash": "test_d_hash", "approval_id": "appr_d"},
                config=config,
                candidate_override=candidate,
            )

            self.assertFalse(doc_res.rescan_passed, "RESCAN must fail when 20 corruptions remain.")
            self.assertTrue(doc_res.rollback_occurred, "Rollback must occur.")
            self.assertEqual(doc_res.status, WaveDocumentStatus.RESCAN_FAILED.value)

    def test_e_rescan_passes_on_complete_elimination(self):
        """Test E: Baseline = 282 glued tokens -> 0 residual -> RESCAN PASS."""
        appr_mgr = MagicMock()
        appr_mgr.validate_and_consume.return_value = (True, "", MagicMock())

        orchestrator = WaveOrchestrator(
            rag=MagicMock(),
            index=MagicMock(),
            cache_dir=Path("tests/mock_cache"),
            staging_dir=Path("tests/mock_staging"),
            audit_logger=MagicMock(),
            approval_manager=appr_mgr,
        )

        candidate = ExtractionCandidate(
            document_id="test_doc_e",
            source_document_id="test_doc_e",
            source_sha256="test_e_hash",
            raw_text="kusursuz metin",
            normalized_text="kusursuz metin",
            quality_metrics={"score": 0.995, "glued_token_count": 0, "very_long_token_count": 0},
            legal_chunks=[{"chunk_index": 0, "chunk_id": "c0", "text": "kusursuz metin"}],
            extraction_method="pymupdf",
        )

        config = WaveConfig(
            environment="test",
            actor="test_runner",
            dry_run=False,
            require_rescan=True,
            rollback_on_rescan_fail=True,
        )

        mock_init_scan = DocumentScanResult(
            document_id="test_doc_e",
            source_hash="test_e_hash",
            current_metrics={"score": 0.595, "glued_token_count": 282},
        )
        mock_init_scan.add_issue(
            IssueCode.QUALITY_GLUED_TEXT,
            IssueSeverity.ERROR,
            "Chroma chunklarında yapışık metin tespit edildi (adet: 282)",
            {"glued_token_count": 282},
        )

        # Mock rescan completely clean
        mock_rescan = DocumentScanResult(
            document_id="test_doc_e",
            source_hash="test_e_hash",
            current_metrics={"score": 0.995, "glued_token_count": 0},
        )

        with patch("repair.wave.scan_document", side_effect=[mock_init_scan, mock_rescan]), \
             patch("repair.wave.compare_quality", return_value=ComparisonResult(document_id="test_doc_e", status=ComparisonStatus.READY_TO_COMMIT.value)), \
             patch("repair.wave.commit_candidate", return_value=CommitResult(commit_id="com_e", document_id="test_doc_e", source_sha256="test_e_hash", status=CommitStatus.COMMITTED.value, candidate_id="cand_e")), \
             patch("repair.wave.verify_committed_repair", return_value=VerificationResult(status=VerificationStatus.VERIFIED_REPAIR.value, commit_id="com_e", candidate_id="cand_e", document_id="test_doc_e", source_sha256="test_e_hash")), \
             patch.object(orchestrator, "_capture_corpus_fingerprint", return_value={"total_chunks": 100, "documents": {}}):

            doc_res = orchestrator.execute_document(
                item={"document_id": "test_doc_e", "source_hash": "test_e_hash", "approval_id": "appr_e"},
                config=config,
                candidate_override=candidate,
            )

            self.assertTrue(doc_res.rescan_passed)
            self.assertFalse(doc_res.rollback_occurred)
            self.assertEqual(doc_res.status, WaveDocumentStatus.VERIFIED_AND_RESCANNED.value)

    def test_f_short_normal_turkish_words_not_glued(self):
        """Test F: Short normal Turkish words return NOT_GLUE."""
        normal_words = ["banka", "mevzuat", "denetim", "kanun", "rapor", "kurum", "madde", "karar"]
        for w in normal_words:
            self.assertFalse(_is_true_glued_token(w), f"Short word '{w}' should NOT be glued.")
            self.assertFalse(is_glued_token(w))

    def test_g_mixed_document_real_corruption_caught(self):
        """Test G: Mixed document with normal words, natural long word, and true glued corruption."""
        mixed_text = (
            "Bankacılık Düzenleme ve Denetleme Kurumu mevzuatı uyarınca "
            "verilerin kimliksizleştirildiğinde korunması esastır. Ancak "
            "bankacılıkfaaliyetlerineözgüolarakbankaylamüşteriilişkisikurulduktansonraoluşanveriler "
            "şeklinde bir bozulma ayrıştırılmalıdır."
        )
        metrics = _pdf_generic_quality_metrics(mixed_text)
        # Exactly 1 true glued token should be detected (the 88-char concatenation)
        # 'kimliksizleştirildiğinde' should NOT be counted as glued
        self.assertEqual(metrics["glued_token_count"], 1)

    def test_h_target_1_and_2_behavior_preserved(self):
        """Test H: Ensures clean repaired texts from Target 1 and Target 2 continue to be evaluated as clean."""
        sample_target_1 = "GELİR İDARESİ BAŞKANLIĞI MÜKELLEF HİZMETLERİ DAİRE BAŞKANLIĞI"
        sample_target_2 = "Bankacılık Düzenleme ve Denetleme Kurulunun 24/04/2020 tarihli Kararı"
        m1 = _pdf_generic_quality_metrics(sample_target_1)
        m2 = _pdf_generic_quality_metrics(sample_target_2)
        self.assertEqual(m1["glued_token_count"], 0)
        self.assertEqual(m2["glued_token_count"], 0)
        self.assertGreaterEqual(m1["score"], 0.90)
        self.assertGreaterEqual(m2["score"], 0.90)

    def test_i_zero_production_writes_during_unit_tests(self):
        """Test I: Confirms test operations do not mutate production audit, store, or cache."""
        audit_file = (DATA_DIR / "audit/repair_audit.jsonl")
        self.assertTrue(audit_file.exists())
        # Check that importing and testing glued resolution did not append any live events
        before_size = audit_file.stat().st_size
        _ = _is_true_glued_token("kimliksizleştirildiğinde")
        _ = _is_true_glued_token("Kurumunpaylaşımöncesindeuygungörüşününalınmışolması")
        after_size = audit_file.stat().st_size
        self.assertEqual(before_size, after_size, "Audit log size must not change during inspection.")


if __name__ == "__main__":
    unittest.main()


class TestNumericRunsAreNotCorruption(unittest.TestCase):
    def test_article_number_lists_do_not_lower_quality(self):
        # "… 4,5,24,25,26,27,33,… maddeleri" (TTK önceki metni): madde listesi bozuk çıkarım değildir.
        from mufettis.extraction.pdf import _is_very_long_token, _pdf_generic_quality_metrics

        numbers = "4,5,24,25,26,27,33,35,38,39,51,64,65,84,88,138,139,145,147,"
        self.assertFalse(_is_very_long_token(numbers))
        self.assertTrue(_is_very_long_token("bankacılıkdüzenlemeveDenetlemeKurumunun" + "x" * 10))
        text = ("MADDE 1 – Bu Kanunun uygulanmasında aşağıdaki maddeler " + numbers + " yürürlüğe girer. ") * 30
        self.assertEqual(_pdf_generic_quality_metrics(text)["very_long_token_count"], 0)

    def test_addresses_do_not_lower_quality(self):
        # K5.3b: e-imza doğrulama adresi her sayfanın altında; TCMB talimatları bu yüzden karantinaya düşüyordu
        from mufettis.extraction.pdf import _is_very_long_token, _pdf_generic_quality_metrics

        for token in ("Adresi:https://www.turkiye.gov.tr/tcmb-ebys-belge-dogrulama",
                      "www.resmigazete.gov.tr/eskiler/2025/12/20251230-26.pdf",
                      "bilgi.edinme.birimi.uzun.adres@kvkk.gov.tr"):
            self.assertFalse(_is_very_long_token(token), token)
        self.assertTrue(_is_very_long_token("(cid:4)(cid:12)(cid:7)(cid:6)(cid:10)(cid:11)(cid:3)(cid:5)"))
        page = "Kurum talimatı uyarınca işlem yapılır. Belge Doğrulama Adresi:https://www.turkiye.gov.tr/tcmb-ebys\n"
        self.assertEqual(_pdf_generic_quality_metrics(page * 20)["very_long_token_count"], 0)

    def test_leader_dots_are_separators(self):
        # K5.3d: içindekiler ve form noktaları uzun öğe sayılmaz; noktayla bölünmüş gerçek bozukluk yine sayılır
        from mufettis.extraction.pdf import _is_very_long_token

        for token in ("Kapsamı" + "." * 60 + "46", "……….……………………" + "." * 40 + "…………", "Kefil" + "_" * 50,
                      "(Anonimleştirme)" + "." * 39 + "46"):
            self.assertFalse(_is_very_long_token(token), token)
        self.assertTrue(_is_very_long_token("Gelirler" + "." * 10 + "x" * 45))
        self.assertTrue(_is_very_long_token("TÜRKİYECUMHURİYETMERKEZBANKASINAKİTYÖNETİMSİSTEMİ"))
