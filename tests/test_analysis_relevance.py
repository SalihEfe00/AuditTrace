# -*- coding: utf-8 -*-
"""
Aşama C: Analiz doğruluğu — iddia/kanıt alakası, tekilleştirme, kaynak kapsamı,
alıntı seçimi ve doğrulama işareti.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mufettis.analysis.analysis_presenter import AnalysisPresenter
from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.engine import MAX_RELEVANT_DISTANCE, AnalysisEngine
from mufettis.analysis.models import DocumentClaim, Evidence, RetrievalPlan
from mufettis.analysis.planner import AnalysisPlannerService
from mufettis.analysis.classifier import DocumentClassifierService


FEE_EVIDENCE = Evidence(
    source_document="Finansal Tüketicilerden Alınacak Ücretlere İlişkin Yönetmelik",
    document_identity="29138",
    chunk_reference="Madde 13 Fıkra 1",
    text_span=(
        "Kuruluşlar ücret tarifelerini ilan eder. Bankalar tarafından finansal tüketicilere sunulan "
        "vadesiz mevduat hesaplarından hesap işletim ücreti veya benzeri adlar altında herhangi bir ücret alınamaz."
    ),
    metadata={"file_hash": "fee"},
)

# Bankacılık dili taşıyan ama şikâyet konularıyla ilgisiz parçalar.
DISTRACTORS = [
    Evidence(
        source_document="Tekdüzen Hesap Planı", document_identity="thp", chunk_reference="Hesap 100",
        text_span="Kasa hesabı, bankanın elindeki Türk parası nakit mevcudunu izler. Hesap işleyişi şube bazında tutulur.",
        metadata={},
    ),
    Evidence(
        source_document="Bağımsız Denetim Yönetmeliği", document_identity="bdy", chunk_reference="Madde 9",
        text_span="Bağımsız denetim kuruluşu, müşteri bankanın finansal tablolarını denetim standartlarına göre inceler ve rapor düzenler.",
        metadata={},
    ),
]

FEE_COMPLAINT = (
    "Ziraat Bankası Kadıköy Şubesi hesabımdan 15.02.2026 tarihinde onayım olmadan 450 TL "
    "hesap işletim ücreti kesilmiştir. Kesilen tutarın derhal iadesini talep ediyorum."
)


def _analyze(text, evidence):
    doc = DocumentUnderstandingService().analyze(text)
    classification = DocumentClassifierService().classify(doc)
    return AnalysisPresenter().execute_analysis(text=text, sample_evidence=evidence).result, doc, classification


class TestClaimEvidenceRelevance(unittest.TestCase):
    def test_relevant_evidence_produces_finding(self):
        result, _, _ = _analyze(FEE_COMPLAINT, [FEE_EVIDENCE])
        self.assertGreaterEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].evidence[0].document_identity, "29138")

    def test_distractors_alone_produce_no_finding(self):
        """Tek bir genel kelime ('hesap', 'banka') ortaklığı bulgu üretmemeli."""
        result, _, _ = _analyze(FEE_COMPLAINT, DISTRACTORS)
        self.assertEqual(result.findings, [])
        self.assertGreaterEqual(len(result.unsupported_claims), 1)

    def test_relevant_evidence_wins_over_distractors(self):
        result, _, _ = _analyze(FEE_COMPLAINT, DISTRACTORS + [FEE_EVIDENCE])
        self.assertTrue(result.findings)
        self.assertTrue(all(f.evidence[0].document_identity == "29138" for f in result.findings))

    def test_institution_name_is_not_a_relevance_term(self):
        """'Finansbank' ile 'finansal' önek eşleşmesi alaka sayılmamalı."""
        text = (
            "QNB Finansbank vadesiz mevduat hesabıma herhangi bir mahkeme veya icra kararı olmadan "
            "haksız şekilde bloke konulmuştur. Şikayetçiyim."
        )
        doc = DocumentUnderstandingService().analyze(text)
        statement_terms, context_terms = AnalysisEngine._claim_terms(doc.claims[0], doc)
        self.assertNotIn("finansbank", statement_terms | context_terms)

    def test_title_only_match_is_not_enough(self):
        engine = AnalysisEngine()
        claim = DocumentClaim(claim_id="c1", statement="Teminat ekspertiz kontrolünde eksiklik tespit edildi", subject="genel_uyum")
        title_only = Evidence(
            source_document="İç Kontrol ve Ekspertiz Yönetmeliği", document_identity="x", chunk_reference="Madde 1",
            text_span="Bu yönetmeliğin amacı usul ve esasları belirlemektir.", metadata={},
        )
        self.assertIsNone(engine._find_best_evidence_for_claim(claim, [], [title_only]))

    def test_far_retrieval_distance_is_rejected(self):
        engine = AnalysisEngine()
        claim = DocumentClaim(claim_id="c1", statement="hesap işletim ücreti kesilmiştir", subject="ücret_ve_komisyon")
        near = FEE_EVIDENCE.model_copy(update={"metadata": {"retrieval_distance": 0.5}})
        far = FEE_EVIDENCE.model_copy(update={"metadata": {"retrieval_distance": MAX_RELEVANT_DISTANCE + 0.1}})
        self.assertIsNone(engine._find_best_evidence_for_claim(claim, [], [far]))
        self.assertIs(engine._find_best_evidence_for_claim(claim, [], [far, near]), near)

    def test_internal_note_without_claims_uses_observation_sentences(self):
        text = (
            "Sayı: 2026/MAS-12\nKonu: Şüpheli İşlem Bildirim Süresi İhlali\nUyum Birimi Müdürlüğüne\n"
            "Şube tarafından tespit edilen 500.000 TL tutarındaki şüpheli nakit transferinin "
            "yasal süre olan 10 iş günü aşılarak bildirildiği belirlenmiştir.\nBilgilerinize arz ederim."
        )
        doc = DocumentUnderstandingService().analyze(text)
        self.assertEqual(doc.claims, [])
        statement = DocumentUnderstandingService.observation_statement(doc)
        self.assertIn("aşılarak bildirildiği", statement)


class TestCitationSelectionAndMarking(unittest.TestCase):
    def test_citation_is_the_relevant_sentence(self):
        result, _, _ = _analyze(FEE_COMPLAINT, [FEE_EVIDENCE])
        span = result.findings[0].citations[0].text_span
        self.assertIn("hesap işletim ücreti", span)
        self.assertNotIn("tarifelerini", span)

    def test_validated_citation_is_marked(self):
        result, _, _ = _analyze(FEE_COMPLAINT, [FEE_EVIDENCE])
        for finding in result.findings:
            for citation in finding.citations:
                self.assertIs(citation.metadata.get("is_valid"), True)


class TestRetrieveEvidence(unittest.TestCase):
    @staticmethod
    def _plan(**overrides):
        base = dict(
            queries=["q1"], filters={}, source_scope=[], evidence_requirements=["statutory_rule"],
            max_evidence_items=10, citation_requirements=["source"],
        )
        base.update(overrides)
        return RetrievalPlan(**base)

    def test_missing_metadata_does_not_collapse_distinct_chunks(self):
        rag = MagicMock()
        rag.query.return_value = [
            {"document": "Birinci hüküm metni.", "metadata": {}},
            {"document": "İkinci farklı hüküm metni.", "metadata": {}},
            {"document": "Birinci hüküm metni.", "metadata": {}},
        ]
        evidence = AnalysisEngine().retrieve_evidence(self._plan(), rag)
        self.assertEqual([e.text_span for e in evidence], ["Birinci hüküm metni.", "İkinci farklı hüküm metni."])

    def test_identity_fallback_differs_per_document(self):
        a = AnalysisEngine.chunk_to_evidence({"document": "x", "metadata": {"title": "A Yönetmeliği"}})
        b = AnalysisEngine.chunk_to_evidence({"document": "y", "metadata": {"title": "B Tebliği"}})
        self.assertNotEqual(a.document_identity, b.document_identity)
        self.assertNotEqual(a.document_identity, "mevzuat_kaydi")

    def test_distance_is_preserved_without_mutating_rag_metadata(self):
        meta = {"file_hash": "h"}
        ev = AnalysisEngine.chunk_to_evidence({"document": "x", "metadata": meta, "distance": 0.42})
        self.assertEqual(ev.metadata["retrieval_distance"], 0.42)
        self.assertNotIn("retrieval_distance", meta)

    def test_source_scope_filters_out_of_scope_sources(self):
        rag = MagicMock()
        rag.query.return_value = [
            {"document": "resmî", "metadata": {"source_id": "bddk_official", "file_hash": "a"}},
            {"document": "ikincil", "metadata": {"source_id": "mevzuat_org", "file_hash": "b"}},
            {"document": "yerel", "metadata": {"source_id": "local_file", "file_hash": "c"}},
            {"document": "bilinmeyen", "metadata": {"file_hash": "d"}},
        ]
        plan = self._plan(source_scope=["bddk_official", "resmi_gazete", "local_file"])
        texts = [e.text_span for e in AnalysisEngine().retrieve_evidence(plan, rag)]
        self.assertEqual(texts, ["resmî", "yerel", "bilinmeyen"])

    def test_alias_source_ids_are_canonicalized(self):
        rag = MagicMock()
        rag.query.return_value = [{"document": "eski ad", "metadata": {"source_id": "bdkk_official", "file_hash": "a"}}]
        plan = self._plan(source_scope=["bddk_official"])
        self.assertEqual(len(AnalysisEngine().retrieve_evidence(plan, rag)), 1)

    def test_law_filter_moves_matching_law_first(self):
        rag = MagicMock()
        rag.query.return_value = [
            {"document": "başka kanun", "metadata": {"law_number": "6502", "file_hash": "a"}},
            {"document": "hedef kanun", "metadata": {"law_number": "5411", "file_hash": "b"}},
        ]
        plan = self._plan(filters={"law_number": "5411"})
        texts = [e.text_span for e in AnalysisEngine().retrieve_evidence(plan, rag)]
        self.assertEqual(texts, ["hedef kanun", "başka kanun"])


class TestUnderstandingAndPlanning(unittest.TestCase):
    def test_subjects_tolerate_turkish_suffixes_and_uppercase(self):
        ds = DocumentUnderstandingService()
        self.assertIn("kredi_ve_faiz", ds.analyze("Konut kredisi borcumu erken kapattım, haksız kesinti yapıldı.").subjects)
        self.assertIn("yetkisiz_islem", ds.analyze("KONU: ŞÜPHELİ İŞLEM tespit edilmiştir.").subjects)

    def test_claim_subject_prefers_more_specific_phrase(self):
        doc = DocumentUnderstandingService().analyze(
            "Garanti BBVA kredi kartımdan bilgim dışında 3.500 TL nakit avans çekilmiş olup işlem iptal edilmemiştir. Mağdur edildim."
        )
        self.assertEqual(doc.claims[0].subject, "yetkisiz_islem")

    def test_complaint_key_sentences_become_claims(self):
        doc = DocumentUnderstandingService().analyze(
            "Hesabımdan 150 TL 'Hesap İşletim Ücreti' kesintisi yapılmıştır. "
            "Bu kesinti hukuka aykırıdır. Banka konut kredisi için erken ödeme cezası tahsil etti."
        )
        statements = " ".join(c.statement for c in doc.claims)
        self.assertIn("kesintisi yapılmıştır", statements)
        self.assertIn("hukuka aykırıdır", statements)
        self.assertIn("tahsil etti", statements)

    def test_plan_queries_start_with_document_content(self):
        ds = DocumentUnderstandingService()
        doc = ds.analyze(FEE_COMPLAINT, metadata={"title": "Hesap işletim ücreti itirazı"})
        plan = AnalysisPlannerService().plan(doc, DocumentClassifierService().classify(doc))
        queries = plan.retrieval_scope.generated_queries
        self.assertEqual(queries[0], "Hesap işletim ücreti itirazı")
        self.assertIn("hesap işletim ücreti kesilmiştir", queries[1])


class TestRetrievalErrorsAreNotHidden(unittest.TestCase):
    """RAG erişim hatası denetçiye "kanıt bulunamadı" olarak gösterilmemeli."""

    def test_all_queries_failing_is_an_error_not_insufficient_evidence(self):
        rag = MagicMock()
        rag.query.side_effect = RuntimeError("chroma kilitli")
        res = AnalysisPresenter().execute_analysis(text=FEE_COMPLAINT, rag_client=rag)
        self.assertFalse(res.success)
        self.assertIsNone(res.result)
        self.assertIn("erişilemedi", res.error_message)
        self.assertIn("chroma kilitli", res.error_message)

    def test_partial_query_failure_is_reported_as_warning(self):
        calls = []

        def query(q, k=5):
            calls.append(q)
            if len(calls) == 1:
                raise RuntimeError("geçici hata")
            return [{"document": FEE_EVIDENCE.text_span, "metadata": {"file_hash": "fee", "title": FEE_EVIDENCE.source_document}}]

        rag = MagicMock()
        rag.query.side_effect = query
        res = AnalysisPresenter().execute_analysis(text=FEE_COMPLAINT, rag_client=rag)
        self.assertTrue(res.success)
        self.assertTrue(any("hata verdi" in w and "geçici hata" in w for w in res.result.warnings))

    def test_engine_analyze_with_unreachable_rag_raises(self):
        from mufettis.analysis.engine import RetrievalUnavailableError

        doc = DocumentUnderstandingService().analyze(FEE_COMPLAINT)
        classification = DocumentClassifierService().classify(doc)
        plan = AnalysisPlannerService().plan(doc, classification)
        from mufettis.analysis.retrieval_planner import RetrievalPlannerService

        retrieval_plan = RetrievalPlannerService().plan_retrieval(plan, doc)
        rag = MagicMock()
        rag.query.side_effect = RuntimeError("bağlantı yok")
        with self.assertRaises(RetrievalUnavailableError):
            AnalysisEngine().analyze(doc, classification, plan, retrieval_plan, rag_client=rag)


class TestOfflineModelLoading(unittest.TestCase):
    def test_model_load_is_local_only_without_download_permission(self):
        from mufettis.rag import engine as rag_engine

        for allow_download, expected_local_only in ((False, True), (True, False)):
            with tempfile.TemporaryDirectory() as tmp, \
                    patch.object(rag_engine, "SentenceTransformer") as st_cls, \
                    patch.object(rag_engine, "chromadb"):
                rag = rag_engine.LocalRAG(db_path=Path(tmp), allow_download=allow_download)
                rag._load_model()
                self.assertEqual(st_cls.call_args.kwargs.get("local_files_only"), expected_local_only)


if __name__ == "__main__":
    unittest.main()
