# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Stage 4 Tests
Streamlit Müfettiş Analiz Ekranı (Analysis UI) Unit & Integration Tests.

Verifies the 20 required scenarios:
1. Analysis page renders safely
2. Empty or whitespace input rejected safely
3. Valid complaint input starts analysis
4. Internal correspondence input starts analysis
5. Document summary renders required fields
6. Analysis plan renders required fields
7. Evidence renders required fields
8. Findings render with distinct 5 inspector dimensions
9. Valid citation renders with verified status
10. Invalid citation is visibly rejected / not falsely validated
11. Unsupported claims render separately from findings
12. Insufficient evidence warning renders when retrieval is empty
13. Reviewer panel renders and updates review status/notes
14. Analysis exceptions handled safely without crash
15. UI does not directly mutate Chroma
16. UI does not directly modify production ingest files
17. Existing admin UI still works
18. Existing application UI still works
19. AnalysisResult serialization/display works
20. Session-state behavior is deterministic and isolated
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mufettis.analysis.analysis_presenter import AnalysisExecutionResult, AnalysisPresenter
from mufettis.analysis.citation_validator import CitationValidator
from mufettis.analysis.engine import AnalysisEngine
from mufettis.analysis.models import (
    AnalysisPlan,
    AnalysisResult,
    Citation,
    DocumentClaim,
    DocumentClassification,
    DocumentUnderstanding,
    Evidence,
    Finding,
    RetrievalPlan,
)
from mufettis.ui.analysis_view import (
    SAMPLE_COMPLAINT_TEXT,
    SAMPLE_INTERNAL_TEXT,
    SAMPLE_UNSUPPORTED_TEXT,
    render_analysis_page,
    render_evidence_text_html,
)
from mufettis.ui.app import _get_rag, render_self_test_result
from mufettis.ui.admin import normalize_display_boolean, render_mevzuat_admin
from mufettis.config import DATA_DIR


class TestAnalysisUI(unittest.TestCase):
    """Comprehensive test suite for FAZ19.9 Stage 4 Analysis UI and Presenter."""

    def setUp(self):
        self.presenter = AnalysisPresenter()

        # Deterministic Mock Evidence items for isolated UI tests
        self.mock_fee_evidence = Evidence(
            source_document="Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği",
            document_identity="mevzuat_ucretler_yonetmeligi",
            chunk_reference="Madde 13 Fıkra 1",
            text_span=(
                "MADDE 13 – (1) Kuruluşlar tarafından finansal tüketicilerin mevduat hesaplarından "
                "hesap işletim ücreti veya benzeri adlar altında herhangi bir ücret alınamaz."
            ),
            metadata={"source": "mevzuat_ucretler_yonetmeligi", "canonical_code": "ucretler_yonetmeligi"},
        )

        self.mock_credit_evidence = Evidence(
            source_document="Bankacılık Kanunu",
            document_identity="kanun_5411",
            chunk_reference="Madde 54 Fıkra 2",
            text_span=(
                "MADDE 54 – (2) Kredi açma yetkisini haiz olanlar, bu Kanunda ve ilgili mevzuatta "
                "belirtilen kredi sınırlarını ve yetki limitlerini aşamazlar."
            ),
            metadata={"source": "kanun_5411", "canonical_code": "5411"},
        )

    # ── 1. Analysis Page Renders ──────────────────────────────────────────────
    def test_01_analysis_page_renders_safely(self):
        """Analysis page renders cleanly with mock RAG and presenter without exceptions."""
        rag_mock = MagicMock()
        rag_mock.count.return_value = 100

        try:
            render_analysis_page(rag_client=rag_mock, presenter=self.presenter)
        except Exception as exc:
            self.fail(f"render_analysis_page raised unexpected exception: {exc}")

    # ── 2. Empty Input Rejected Safely ────────────────────────────────────────
    def test_02_empty_input_rejected_safely(self):
        """Empty, None, or whitespace-only inputs are rejected with clear error messages."""
        # None input
        valid, msg = self.presenter.validate_input(None)
        self.assertFalse(valid)
        self.assertIn("Lütfen analiz edilecek", msg)

        # Whitespace input
        valid, msg = self.presenter.validate_input("    \n\t   ")
        self.assertFalse(valid)
        self.assertIn("boş", msg)

        # Too short input
        valid, msg = self.presenter.validate_input("Kısa")
        self.assertFalse(valid)
        self.assertIn("kısa", msg)

        # Execution attempt with empty input
        res = self.presenter.execute_analysis(text="")
        self.assertFalse(res.success)
        self.assertIsNone(res.result)
        self.assertIsNotNone(res.error_message)

    # ── 3. Valid Complaint Input Starts Analysis ──────────────────────────────
    def test_03_valid_complaint_input_starts_analysis(self):
        """Substantive complaint text triggers full pipeline and produces validated result."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            title="Şikayet Testi",
            source="Müşteri İletişim",
            sample_evidence=[self.mock_fee_evidence],
        )

        self.assertTrue(res.success)
        self.assertIsNotNone(res.result)
        self.assertEqual(res.result.classification.document_type, "complaint")
        self.assertGreaterEqual(len(res.result.findings), 1)
        self.assertEqual(res.result.status, "completed")
        self.assertTrue(any("cret" in s or "mevduat" in s for s in res.result.document.subjects))

    # ── 4. Internal Correspondence Input Starts Analysis ──────────────────────
    def test_04_internal_correspondence_input_starts_analysis(self):
        """Internal audit correspondence text is classified and planned correctly."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_INTERNAL_TEXT,
            title="Kredi Tahsis Denetim Notu",
            source="Teftiş Kurulu",
            sample_evidence=[self.mock_credit_evidence],
        )

        self.assertTrue(res.success)
        self.assertIsNotNone(res.result)
        self.assertEqual(res.result.classification.document_type, "internal_correspondence")
        self.assertGreaterEqual(len(res.result.findings), 1)
        self.assertEqual(res.result.status, "completed")
        self.assertIn("kredi", res.result.document.subjects[0].lower())

    # ── 5. Document Summary Renders ───────────────────────────────────────────
    def test_05_document_summary_renders_required_fields(self):
        """Document understanding summary contains title, type, confidence, entities, claims."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            title="Başlık Test",
            sample_evidence=[self.mock_fee_evidence],
        )
        doc = res.result.document
        classification = res.result.classification

        self.assertEqual(doc.title, "Başlık Test")
        self.assertEqual(classification.document_type, "complaint")
        self.assertGreater(classification.confidence, 0.5)
        self.assertGreaterEqual(len(doc.entities), 1)
        self.assertGreaterEqual(len(doc.claims), 1)

    # ── 6. Analysis Plan Renders ──────────────────────────────────────────────
    def test_06_analysis_plan_renders_required_fields(self):
        """Analysis plan includes analysis_type, objective, scope, and retrieval queries."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        plan = res.result.analysis_plan
        ret_plan = res.result.retrieval_plan

        self.assertIn(plan.analysis_type, ["regulatory_relevance", "compliance_analysis"])
        self.assertTrue(len(plan.objective) > 0)
        self.assertTrue(len(plan.parameters.scope) > 0)
        self.assertGreaterEqual(len(ret_plan.queries), 1)

    # ── 7. Evidence Renders ───────────────────────────────────────────────────
    def test_07_evidence_renders_required_fields(self):
        """Retrieved evidence preserves source_document, chunk_reference, and text_span."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        ev_list = res.retrieved_evidence
        self.assertGreaterEqual(len(ev_list), 1)

        ev = ev_list[0]
        self.assertEqual(ev.source_document, self.mock_fee_evidence.source_document)
        self.assertEqual(ev.chunk_reference, self.mock_fee_evidence.chunk_reference)
        self.assertIn("hesap işletim ücreti", ev.text_span)

    # ── 8. Findings Render With 5 Distinct Dimensions ─────────────────────────
    def test_08_findings_render_with_5_distinct_dimensions(self):
        """Finding encapsulates observation, evidence, regulation, model interpretation, follow-up."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        self.assertGreaterEqual(len(res.result.findings), 1)
        finding = res.result.findings[0]

        # 1. Observation / Issue
        self.assertTrue(len(finding.issue) > 0)
        self.assertTrue(len(finding.explanation) > 0)

        # 2. Evidence
        self.assertGreaterEqual(len(finding.evidence), 1)

        # 3. Regulation Reference
        self.assertTrue(len(finding.regulation_reference) > 0)

        # 4. Model Interpretation
        self.assertIsNotNone(finding.model_interpretation)
        self.assertTrue(len(finding.model_interpretation) > 0)

        # 5. Inspector Follow-up
        self.assertTrue(len(finding.inspector_action) > 0)

    # ── 9. Valid Citation Renders ─────────────────────────────────────────────
    def test_09_valid_citation_renders_with_verified_status(self):
        """Valid citation has exact quoted text matching evidence and valid status."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        self.assertGreaterEqual(len(res.result.findings), 1)
        citations = res.result.findings[0].citations
        self.assertGreaterEqual(len(citations), 1)

        cit = citations[0]
        self.assertEqual(cit.document_identity, self.mock_fee_evidence.document_identity)
        self.assertIn(cit.text_span, self.mock_fee_evidence.text_span)
        self.assertIs(cit.metadata.get("is_valid"), True)

    # ── 10. Invalid Citation Visibly Rejected ─────────────────────────────────
    def test_10_invalid_citation_visibly_rejected(self):
        """Corrupted citation text span fails CitationValidator and is rejected from findings."""
        validator = CitationValidator()

        invalid_finding = Finding(
            issue="Uydurma Alıntı Testi",
            evidence=[self.mock_fee_evidence],
            citations=[
                Citation(
                    source="mevzuat",
                    document_identity=self.mock_fee_evidence.document_identity,
                    reference="Madde 99",
                    text_span="Bu metin mevzuat parçasında ASLA yer almayan sahte bir cümledir.",
                    metadata={},
                )
            ],
            regulation_reference="Test Yönetmeliği",
            explanation="Test açıklaması",
            inspector_action="Kontrol ediniz",
        )

        report = validator.validate_finding(invalid_finding, retrieved_evidence=[self.mock_fee_evidence])
        self.assertFalse(report.is_valid)
        self.assertIn("not grounded", report.errors[0])

    # ── 11. Unsupported Claims Render Separately ──────────────────────────────
    def test_11_unsupported_claims_render_separately(self):
        """Claims with zero matching evidence are collected into unsupported_claims."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_UNSUPPORTED_TEXT,
            sample_evidence=[self.mock_fee_evidence],  # Fee evidence irrelevant to moral damages
        )
        self.assertTrue(res.success)
        # Moral damages claim should not produce a fake fee finding
        self.assertGreaterEqual(len(res.result.unsupported_claims), 1)

    # ── 12. Insufficient Evidence Warning Renders ─────────────────────────────
    def test_12_insufficient_evidence_warning_renders(self):
        """Empty retrieval evidence returns completed_insufficient_evidence status and warning."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[],  # Zero evidence
        )
        self.assertTrue(res.success)
        self.assertEqual(res.result.status, "completed_insufficient_evidence")
        self.assertEqual(len(res.result.findings), 0)
        self.assertGreaterEqual(len(res.result.warnings), 1)
        self.assertIn("doğrulanabilir kanıt parçası bulunamadı", res.result.warnings[0])

    # ── 13. Reviewer Panel Renders and Updates Status ─────────────────────────
    def test_13_reviewer_panel_updates_review_status_and_notes(self):
        """Human reviewer can update status and notes on AnalysisResult."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        original_result = res.result
        self.assertEqual(original_result.reviewer_status, "Bekliyor")

        updated = self.presenter.apply_reviewer_action(
            result=original_result,
            status="İncelendi",
            note="Müşteri dekontu doğrulandı, banka iade yapmalıdır.",
            reviewer="Müfettiş Ahmet",
        )
        self.assertEqual(updated.reviewer_status, "İncelendi")
        self.assertEqual(updated.reviewer_note, "Müşteri dekontu doğrulandı, banka iade yapmalıdır.")
        self.assertEqual(len(updated.findings), len(original_result.findings))

    # ── 14. Analysis Exception Handled Safely ─────────────────────────────────
    def test_14_analysis_exception_handled_safely(self):
        """Presenter catches unexpected exceptions and returns clean failure without crash."""
        failing_engine = MagicMock()
        failing_engine.retrieve_evidence.side_effect = RuntimeError("Mock Chroma bağlantı hatası")

        presenter = AnalysisPresenter(engine=failing_engine)
        res = presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            rag_client=MagicMock(),
        )

        self.assertFalse(res.success)
        self.assertIsNone(res.result)
        self.assertIn("Mock Chroma bağlantı hatası", res.error_message)

    # ── 15. UI Does Not Directly Mutate Chroma ────────────────────────────────
    def test_15_ui_does_not_directly_mutate_chroma(self):
        """UI execution and presentation never invoke write/mutation methods on Chroma/RAG."""
        mock_rag = MagicMock()
        mock_rag.query.return_value = []
        mock_rag.count.return_value = 21773

        # Execute analysis
        self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            rag_client=mock_rag,
        )

        # Verify zero mutation calls
        self.assertEqual(mock_rag.add.call_count, 0)
        self.assertEqual(mock_rag.upsert.call_count, 0)
        self.assertEqual(mock_rag.delete.call_count, 0)
        self.assertEqual(mock_rag.persist.call_count, 0)

    # ── 16. UI Does Not Directly Modify Production Ingest Files ───────────────
    def test_16_ui_does_not_modify_production_files(self):
        """Ensures that ingest_index and repair_audit hashes remain completely untouched."""
        idx_p = (DATA_DIR / "ingest_index.json")
        aud_p = (DATA_DIR / "audit/repair_audit.jsonl")

        idx_hash_before = hashlib.sha256(idx_p.read_bytes()).hexdigest()
        aud_hash_before = hashlib.sha256(aud_p.read_bytes()).hexdigest()

        # Run multiple presenter executions
        self.presenter.execute_analysis(text=SAMPLE_COMPLAINT_TEXT, sample_evidence=[self.mock_fee_evidence])
        self.presenter.execute_analysis(text=SAMPLE_INTERNAL_TEXT, sample_evidence=[self.mock_credit_evidence])

        idx_hash_after = hashlib.sha256(idx_p.read_bytes()).hexdigest()
        aud_hash_after = hashlib.sha256(aud_p.read_bytes()).hexdigest()

        self.assertEqual(idx_hash_before, idx_hash_after)
        self.assertEqual(aud_hash_before, aud_hash_after)

    # ── 17. Existing Admin UI Still Works ─────────────────────────────────────
    def test_17_existing_admin_ui_still_works(self):
        """Existing admin panels execute cleanly without regression."""
        mock_rag = MagicMock()
        mock_rag.count.return_value = 21773

        try:
            render_mevzuat_admin(
                rag=mock_rag,
                local_only=True,
                use_browser_bddk=False,
                allow_embedding_download=False,
            )
        except Exception as exc:
            self.fail(f"render_mevzuat_admin failed: {exc}")

    # ── 18. Existing Application UI Still Works ───────────────────────────────
    def test_18_existing_application_ui_still_works(self):
        """Existing application UI utility functions execute cleanly."""
        rag = _get_rag(allow_download=False)
        self.assertTrue(rag is None or hasattr(rag, "query"))

        test_res = unittest.TestResult()
        test_res.testsRun = 1
        try:
            render_self_test_result(test_res)
        except Exception as exc:
            self.fail(f"render_self_test_result failed: {exc}")

    # ── 19. AnalysisResult Serialization/Display Works ────────────────────────
    def test_19_analysis_result_serialization_and_display(self):
        """AnalysisResult dumps to valid JSON and re-validates identically."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        json_repr = res.result.model_dump_json()
        self.assertIn("complaint", json_repr)
        self.assertIn("ucret_ve_komisyon", json_repr)

        restored = AnalysisResult.model_validate_json(json_repr)
        self.assertEqual(restored.status, res.result.status)
        self.assertEqual(len(restored.findings), len(res.result.findings))

    # ── 20. Session-State Behavior Deterministic & Isolated ───────────────────
    def test_20_session_state_behavior_is_deterministic(self):
        """Repeated analysis on identical text yields identical deterministic results."""
        res1 = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        res2 = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )

        self.assertEqual(res1.result.classification.document_type, res2.result.classification.document_type)
        self.assertEqual(len(res1.result.findings), len(res2.result.findings))
        self.assertEqual(res1.result.findings[0].issue, res2.result.findings[0].issue)

    # ── 21. normalize_display_boolean Contract ────────────────────────────────
    def test_21_normalize_display_boolean_contract(self):
        """Verifies clean boolean mapping for mixed truthy/falsy representations."""
        # Truthy cases
        for val in (True, "True", "true", "TRUE", 1, "1", "yes", "t", "evet"):
            self.assertTrue(normalize_display_boolean(val), f"Failed on truthy: {val!r}")

        # Falsy cases
        for val in (False, "False", "false", "FALSE", 0, "0", "no", "f", "hayır", "hayir", "", None):
            self.assertFalse(normalize_display_boolean(val), f"Failed on falsy: {val!r}")

    # ── 22. Mixed legal_structure_resolved Arrow Serialization Fix ───────────
    def test_22_mixed_legal_structure_resolved_arrow_serialization(self):
        """Proves that a DataFrame with mixed types in legal_structure_resolved renders safely to Arrow after normalization."""
        import pandas as pd
        try:
            import pyarrow as pa
        except ImportError:
            pa = None

        raw_data = {
            "title": ["Doc A", "Doc B", "Doc C", "Doc D"],
            "legal_structure_resolved": [True, "False", False, "True"],
            "document_indexable": [True, "True", 1, False],
            "evidence_indexable": [True, False, "false", None],
        }
        df = pd.DataFrame(raw_data)

        # Before normalization: pyarrow fails with ArrowInvalid on mixed types
        if pa is not None:
            with self.assertRaises(Exception):
                pa.Table.from_pandas(df[["legal_structure_resolved"]])

        # Apply data presentation layer normalization
        for col in ("legal_structure_resolved", "document_indexable", "evidence_indexable"):
            if col in df.columns:
                df[col] = df[col].apply(normalize_display_boolean).astype(bool)

        # After normalization: clean bool dtype and Arrow serialization succeeds
        self.assertEqual(df["legal_structure_resolved"].dtype, bool)
        self.assertEqual(df["legal_structure_resolved"].tolist(), [True, False, False, True])
        self.assertEqual(df["document_indexable"].tolist(), [True, True, True, False])
        self.assertEqual(df["evidence_indexable"].tolist(), [True, False, False, False])

        if pa is not None:
            table = pa.Table.from_pandas(df)
            self.assertEqual(table.num_rows, 4)
            self.assertEqual(table.schema.field("legal_structure_resolved").type, pa.bool_())

    # ── 23. AnalysisPresenter Cached in Session State ─────────────────────────
    def test_23_analysis_presenter_cached_in_session_state(self):
        """Verifies AnalysisPresenter is not repeatedly re-instantiated across reruns."""
        mock_st = MagicMock()
        mock_session_state = {}
        mock_st.session_state = mock_session_state
        mock_st.columns.side_effect = lambda n, **kwargs: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        mock_st.button.return_value = False
        mock_st.text_input.return_value = ""
        mock_st.text_area.return_value = ""

        with patch("mufettis.ui.analysis_view.st", mock_st):
            # First invocation
            render_analysis_page(presenter=None)
            self.assertIn("analysis_presenter", mock_session_state)
            cached_instance = mock_session_state["analysis_presenter"]

            # Second invocation (simulating rerun)
            render_analysis_page(presenter=None)
            self.assertIs(mock_session_state["analysis_presenter"], cached_instance)

    # ── 24. RetrievalPlan Canonical Fields UI Rendering ───────────────────────
    def test_24_retrieval_plan_ui_contract_rendering(self):
        """Verifies RetrievalPlan renders canonical fields without AttributeError for missing 'keywords'."""
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        self.assertTrue(res.success)
        # Ensure canonical contract: RetrievalPlan has NO 'keywords' attribute
        self.assertFalse(hasattr(res.result.retrieval_plan, "keywords"))
        self.assertTrue(hasattr(res.result.retrieval_plan, "queries"))
        self.assertTrue(hasattr(res.result.retrieval_plan, "source_scope"))
        self.assertTrue(hasattr(res.result.retrieval_plan, "evidence_requirements"))
        self.assertTrue(hasattr(res.result.retrieval_plan, "max_evidence_items"))

        # Render complete page with the real AnalysisExecutionResult
        mock_st = MagicMock()
        mock_st.session_state = {"analysis_exec_result": res}
        mock_st.columns.side_effect = lambda n, **kwargs: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        mock_st.button.return_value = False
        mock_st.text_input.return_value = ""
        mock_st.text_area.return_value = ""
        mock_st.selectbox.return_value = "Bekliyor"

        with patch("mufettis.ui.analysis_view.st", mock_st):
            try:
                render_analysis_page(presenter=self.presenter)
            except AttributeError as exc:
                self.fail(f"render_analysis_page crashed with AttributeError: {exc}")

    # ── 25. Native Streamlit Evidence Rendering & Integrity ───────────────────
    def test_25_native_streamlit_evidence_rendering_and_integrity(self):
        """Verifies evidence text renders through native st.code without custom HTML hacks,
        preserving exact text, Turkish characters, line breaks, and punctuation."""
        raw_text = (
            "Madde 13 — (1) Bankalar tarafından finansal tüketicilere sunulan vadesiz mevduat hesaplarından\n"
            "hesap işletim ücreti <ücret/komisyon> veya benzeri adlar altında ücret alınamaz.\n"
            "Özel karakterler: ç, ğ, ı, İ, ö, ş, ü, Ğ, Ş, Ç, Ö, Ü & 150 TL.\n"
            "   Girintili satır ve \"çift tırnak\" ile 'tek tırnak'."
        )
        sample_ev = Evidence(
            source_document="Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği",
            document_identity="5411",
            chunk_reference="Madde 13 Fıkra 1",
            text_span=raw_text,
            metadata={"source_id": "bddk_official"},
        )

        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[sample_ev],
        )
        self.assertTrue(res.success)

        # Mock streamlit to inspect actual calls
        mock_st = MagicMock()
        mock_st.session_state = {"analysis_exec_result": res}
        mock_col = MagicMock()
        mock_col.button.return_value = False
        mock_st.columns.side_effect = lambda n, **kwargs: [mock_col for _ in range(n if isinstance(n, int) else len(n))]
        mock_st.button.return_value = False
        mock_st.text_input.return_value = ""
        mock_st.text_area.return_value = ""
        mock_st.selectbox.return_value = "Bekliyor"

        with patch("mufettis.ui.analysis_view.st", mock_st):
            # A & B: rendering succeeds without throwing an exception
            render_analysis_page(presenter=self.presenter)

            # C: the exact original text is passed to native st.code
            code_calls = [call.args for call in mock_st.code.call_args_list]
            self.assertTrue(len(code_calls) >= 1, "st.code must be called to render evidence text")

            # Find the call with sample_ev.text_span
            matching_code_call = [args for args in code_calls if args and args[0] == sample_ev.text_span]
            self.assertEqual(
                len(matching_code_call), 1,
                "Native st.code must receive the exact original evidence text without alteration"
            )

            # D: Old custom white-background HTML is no longer passed to st.markdown for evidence
            markdown_calls = [call.args[0] for call in mock_st.markdown.call_args_list if call.args]
            self.assertFalse(
                any("background-color:#f8f9fa" in m or "background-color: #f8f9fa" in m for m in markdown_calls),
                "Fragile custom white-background HTML must no longer be passed to st.markdown"
            )
            self.assertTrue(
                any('[data-testid="stCode"]' in m and "rgba(128, 128, 128, 0.08)" in m for m in markdown_calls),
                "Scoped contrast and theme protection CSS must be injected for stCode"
            )

            # E: All Turkish characters, line breaks, whitespace, and symbols are preserved identically
            passed_text = matching_code_call[0][0]
            self.assertEqual(passed_text, raw_text)
            self.assertIn("ç, ğ, ı, İ, ö, ş, ü, Ğ, Ş, Ç, Ö, Ü", passed_text)
            self.assertIn("<ücret/komisyon>", passed_text)
            self.assertIn("& 150 TL", passed_text)
            self.assertIn("\n", passed_text)
            self.assertIn("   Girintili satır", passed_text)

    # ── 26. admin._get_rag delegates to the canonical RAG factory ─────────────
    def test_26_admin_get_rag_returns_canonical_result(self):
        """admin._get_rag must return what the canonical ui.app._get_rag returns (never implicit None)."""
        from mufettis.ui import admin as admin_mod

        sentinel = object()
        with patch("mufettis.ui.app._get_rag", lambda **kwargs: sentinel):
            self.assertIs(admin_mod._get_rag(allow_download=False), sentinel)

    # ── 27. Progress callback reports real pipeline stages in order ───────────
    def test_27_progress_callback_reports_stages_in_order(self):
        from mufettis.analysis.analysis_presenter import PIPELINE_STAGES

        calls = []
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
            progress_callback=lambda idx, key, label: calls.append((idx, key, label)),
        )
        self.assertTrue(res.success)
        self.assertEqual([c[1] for c in calls], [key for key, _ in PIPELINE_STAGES])
        self.assertEqual([c[0] for c in calls], list(range(1, len(PIPELINE_STAGES) + 1)))
        self.assertEqual(set(res.stage_durations), {key for key, _ in PIPELINE_STAGES})

    def test_28_failing_progress_callback_does_not_break_analysis(self):
        def boom(*_args):
            raise RuntimeError("UI hatası")

        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
            progress_callback=boom,
        )
        self.assertTrue(res.success)
        self.assertIsNotNone(res.result)

    def test_29_progress_stops_at_failing_stage(self):
        """Progress must not claim stages that never ran."""
        failing_engine = MagicMock()
        failing_engine.retrieve_evidence.side_effect = RuntimeError("Chroma hatası")
        presenter = AnalysisPresenter(engine=failing_engine)

        keys = []
        res = presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            rag_client=MagicMock(),
            progress_callback=lambda idx, key, label: keys.append(key),
        )
        self.assertFalse(res.success)
        self.assertEqual(keys[-1], "evidence_retrieval")
        self.assertNotIn("analysis_engine", keys)

    # ── 30. Reviewer name and timestamp are preserved ─────────────────────────
    def test_30_reviewer_action_keeps_reviewer_name_and_timestamp(self):
        res = self.presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        self.assertIsNone(res.result.reviewer_name)
        self.assertIsNone(res.result.reviewed_at)

        updated = self.presenter.apply_reviewer_action(
            result=res.result, status="Kabul edildi", note="Uygun", reviewer="  Müfettiş Ayşe  ",
        )
        self.assertEqual(updated.reviewer_name, "Müfettiş Ayşe")
        self.assertTrue(updated.reviewed_at)
        self.assertEqual(updated.analysis_id, res.result.analysis_id)

        anonymous = self.presenter.apply_reviewer_action(result=res.result, status="İncelendi", reviewer="   ")
        self.assertIsNone(anonymous.reviewer_name)

    # ── 31. Review is persisted to audit sink with masking ────────────────────
    def test_31_record_review_writes_masked_audit_event(self):
        events = []
        presenter = AnalysisPresenter(audit_sink=lambda event, payload: events.append((event, payload)))
        res = presenter.execute_analysis(
            text=SAMPLE_COMPLAINT_TEXT,
            sample_evidence=[self.mock_fee_evidence],
        )
        updated = presenter.apply_reviewer_action(
            result=res.result,
            status="Düzeltilmeli",
            note="Müşteri IBAN TR120001000200030004000500 ve TCKN 12345678901 teyit edilmeli.",
            reviewer="Müfettiş Ayşe",
        )
        payload = presenter.record_review(updated)

        self.assertEqual(len(events), 1)
        event, logged = events[0]
        self.assertEqual(event, "document_analysis_review")
        self.assertIs(logged, payload)
        self.assertEqual(logged["analysis_id"], updated.analysis_id)
        self.assertEqual(logged["reviewer_status"], "Düzeltilmeli")
        self.assertEqual(logged["reviewer_name"], "Müfettiş Ayşe")
        self.assertEqual(logged["finding_count"], len(updated.findings))
        self.assertNotIn("TR120001000200030004000500", logged["reviewer_note"])
        self.assertNotIn("12345678901", logged["reviewer_note"])
        # Belge metninin tamamı denetim kaydına yazılmaz.
        self.assertNotIn(SAMPLE_COMPLAINT_TEXT, json.dumps(logged, ensure_ascii=False))

    # ── 32/33. "No finding" is never shown as a compliance confirmation ───────
    def _render_result_with_mock_st(self, exec_result):
        mock_st = MagicMock()
        mock_st.session_state = {"analysis_exec_result": exec_result}
        mock_col = MagicMock()
        mock_col.button.return_value = False  # şablon butonları tıklanmamış olmalı
        mock_st.columns.side_effect = lambda n, **kwargs: [mock_col for _ in range(n if isinstance(n, int) else len(n))]
        mock_st.button.return_value = False
        mock_st.text_input.return_value = ""
        mock_st.text_area.return_value = ""
        mock_st.selectbox.return_value = "Bekliyor"
        with patch("mufettis.ui.analysis_view.st", mock_st):
            render_analysis_page(presenter=self.presenter)
        self.assertIs(mock_st.session_state["analysis_exec_result"], exec_result)
        return mock_st

    @staticmethod
    def _call_texts(mock_fn):
        return [str(c.args[0]) for c in mock_fn.call_args_list if c.args]

    def test_32_insufficient_evidence_is_not_shown_as_compliance(self):
        res = self.presenter.execute_analysis(text=SAMPLE_COMPLAINT_TEXT, sample_evidence=[])
        self.assertEqual(res.result.status, "completed_insufficient_evidence")

        mock_st = self._render_result_with_mock_st(res)
        success_texts = self._call_texts(mock_st.success)
        self.assertFalse(any("İhlal" in t or "ihlal" in t for t in success_texts))
        self.assertTrue(any("uygun olduğu anlamına gelmez" in t for t in self._call_texts(mock_st.warning)))

    def test_33_no_matched_finding_is_not_shown_as_compliance(self):
        unrelated_ev = Evidence(
            source_document="Tekdüzen Hesap Planı",
            document_identity="thp",
            chunk_reference="Hesap 100",
            text_span="Kasa hesabı, işletmenin elindeki nakit mevcudunu izler.",
            metadata={},
        )
        res = self.presenter.execute_analysis(text=SAMPLE_UNSUPPORTED_TEXT, sample_evidence=[unrelated_ev])
        self.assertEqual(res.result.status, "completed")
        self.assertEqual(len(res.result.findings), 0)

        mock_st = self._render_result_with_mock_st(res)
        self.assertFalse(any("İhlal" in t or "ihlal" in t for t in self._call_texts(mock_st.success)))
        self.assertTrue(any("uygunluk teyidi değildir" in t for t in self._call_texts(mock_st.info)))

    # ── 35/36. Citation badge truthfulness and evidence list immutability ─────
    def test_35_citation_badge_reflects_engine_validation_only(self):
        from mufettis.ui.analysis_view import citation_badge

        def cit(meta):
            return Citation(source="s", document_identity="d", reference="r", text_span="t", metadata=meta)

        self.assertIn("doğrulandı", citation_badge(cit({"is_valid": True})))
        self.assertIn("doğrulanamadı", citation_badge(cit({"is_valid": False})))
        self.assertIn("bilgisi yok", citation_badge(cit({})))

    def test_36_render_does_not_mutate_stored_evidence_list(self):
        res = self.presenter.execute_analysis(text=SAMPLE_COMPLAINT_TEXT, sample_evidence=[self.mock_fee_evidence])
        self.assertTrue(res.result.findings)
        res.retrieved_evidence = []  # arayüz, bulgulardaki kanıtla yedek liste oluşturur
        self._render_result_with_mock_st(res)
        self._render_result_with_mock_st(res)
        self.assertEqual(res.retrieved_evidence, [])

    # ── 34. Save button records the review through the presenter ──────────────
    def test_34_save_review_button_records_audit_event(self):
        events = []
        presenter = AnalysisPresenter(audit_sink=lambda event, payload: events.append((event, payload)))
        res = presenter.execute_analysis(text=SAMPLE_COMPLAINT_TEXT, sample_evidence=[self.mock_fee_evidence])

        mock_st = MagicMock()
        session = {"analysis_exec_result": res}
        mock_st.session_state = session
        mock_col = MagicMock()
        mock_col.button.return_value = False
        mock_col.selectbox.return_value = "Kabul edildi"
        mock_col.text_input.return_value = "Müfettiş Ayşe"
        mock_col.text_area.return_value = "Bulgu yerinde."
        mock_st.columns.side_effect = lambda n, **kwargs: [mock_col for _ in range(n if isinstance(n, int) else len(n))]
        mock_st.button.side_effect = lambda *args, **kwargs: kwargs.get("key") == "save_analysis_review_btn"
        mock_st.text_area.return_value = SAMPLE_COMPLAINT_TEXT

        with patch("mufettis.ui.analysis_view.st", mock_st):
            render_analysis_page(presenter=presenter)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1]["reviewer_status"], "Kabul edildi")
        self.assertEqual(events[0][1]["reviewer_name"], "Müfettiş Ayşe")
        self.assertEqual(session["analysis_exec_result"].result.reviewer_name, "Müfettiş Ayşe")
        mock_st.error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
