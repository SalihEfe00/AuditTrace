# -*- coding: utf-8 -*-
"""
İddia başına aday arama ve yeniden sıralayıcıyla kanıt seçimi: RRF birleştirmesi, iddia adaylarının
havuza katılması, sözcüksel kapının yeniden sıralayıcıdan önce gelmesi, puan eşiği ve yeniden
sıralayıcının yalnızca gerçek korpusla kullanılması.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from mufettis.analysis.analysis_presenter import AnalysisPresenter
from mufettis.analysis.engine import AnalysisEngine
from mufettis.analysis.models import DocumentClaim, DocumentUnderstanding, Evidence, RetrievalPlan
from mufettis.rag.fusion import reciprocal_rank_fusion
from mufettis.rag.reranker import load_reranker


def _ev(text, **meta):
    return Evidence(source_document="Kanun", document_identity=meta.get("law_number", "1"),
                    chunk_reference="Madde 1", text_span=text, metadata={"file_hash": text[:12], **meta})


def _plan(filters=None, scope=()):
    return RetrievalPlan(
        queries=["q"], filters=filters or {}, source_scope=list(scope), evidence_requirements=["statutory_rule"],
        max_evidence_items=5, query_depths={}, citation_requirements=["source"],
    )


def _doc(statement):
    return DocumentUnderstanding(
        title="Belge Metni", text=statement, normalized_text=statement, claims=[DocumentClaim(claim_id="c1", statement=statement, subject="genel")],
    )


class FakeReranker:
    """Metinde geçen anahtar kelimeye göre sabit puan verir."""

    def __init__(self, scores, min_score=None):
        self.scores = scores
        self.min_score = min_score
        self.calls = []

    def score(self, query, passages):
        self.calls.append(list(passages))
        return [next((v for k, v in self.scores.items() if k in p), 0.0) for p in passages]


class TestFusion(unittest.TestCase):
    def test_item_in_both_lists_beats_single_list_leaders(self):
        scores = reciprocal_rank_fusion([(["a", "b", "c"], 1.0), (["c", "d"], 0.5)])
        self.assertEqual(max(scores, key=scores.get), "c")

    def test_repeated_key_counts_once(self):
        self.assertEqual(reciprocal_rank_fusion([(["a", "a"], 1.0)], k=0), {"a": 1.0})


class TestClaimCandidates(unittest.TestCase):
    def test_client_without_capability_gives_no_candidates(self):
        self.assertEqual(AnalysisEngine().retrieve_claim_candidates(_doc("x"), _plan(), MagicMock()), {})

    def test_candidates_are_searched_with_claim_statement_and_filtered(self):
        rag = MagicMock()
        rag.supports_claim_candidates = True
        rag.claim_candidates.return_value = [
            {"document": "kabul edilen hüküm", "metadata": {"file_hash": "a"}},
            {"document": "ikincil kopya", "metadata": {"file_hash": "b", "source_id": "baska_kaynak"}},
            {"document": "kabul edilen hüküm", "metadata": {"file_hash": "a"}},
        ]
        plan = _plan(scope=["bddk_official"])
        out = AnalysisEngine().retrieve_claim_candidates(_doc("kredi kartı aidatı iade edilmedi"), plan, rag)
        self.assertEqual(rag.claim_candidates.call_args.args[0], "kredi kartı aidatı iade edilmedi")
        self.assertEqual([e.text_span for e in out["c1"]], ["kabul edilen hüküm"])

    def test_candidate_search_error_is_reported(self):
        rag = MagicMock()
        rag.supports_claim_candidates = True
        rag.claim_candidates.side_effect = RuntimeError("disk")
        errors = []
        self.assertEqual(AnalysisEngine().retrieve_claim_candidates(_doc("x y z"), _plan(), rag, errors=errors), {})
        self.assertEqual(len(errors), 1)


class TestRerankedSelection(unittest.TestCase):
    CLAIM = DocumentClaim(claim_id="c1", statement="Kredi kartı yıllık aidat ücreti iade edilmedi.", subject="genel")

    def _pool(self):
        return [
            _ev("Kredi kartı yıllık aidat ücreti tahsil edilir ve sözleşmede gösterilir.", law_number="A"),
            _ev("Yıllık aidat ücreti iade edilir; kredi kartı sözleşmesi feshedilirse.", law_number="B"),
            _ev("Mevduat faizleri üç ayda bir hesaplanır.", law_number="C"),
        ]

    def test_reranker_picks_among_lexically_accepted(self):
        engine = AnalysisEngine()
        engine.reranker = FakeReranker({"iade edilir": 5.0, "tahsil": 1.0, "Mevduat": 9.0})
        chosen, score = engine._select_evidence(self.CLAIM, self._pool())
        self.assertEqual(chosen.metadata["law_number"], "B")
        self.assertEqual(score, 5.0)
        # Sözcüksel kapıyı geçemeyen parça (mevduat) yeniden sıralayıcıya hiç gönderilmez.
        self.assertFalse(any("Mevduat" in p for p in engine.reranker.calls[0]))

    def test_below_threshold_gives_no_finding(self):
        engine = AnalysisEngine()
        engine.reranker = FakeReranker({"iade edilir": -3.0, "tahsil": -4.0}, min_score=-2.0)
        self.assertEqual(engine._select_evidence(self.CLAIM, self._pool()), (None, -3.0))

    def test_without_reranker_lexical_order_decides(self):
        chosen, score = AnalysisEngine()._select_evidence(self.CLAIM, self._pool())
        self.assertIsNotNone(chosen)
        self.assertIsNone(score)

    def test_claim_candidates_join_pool_and_score_is_cited(self):
        engine = AnalysisEngine()
        engine.reranker = FakeReranker({"iade edilir": 5.0, "tahsil": 1.0})
        pool = self._pool()
        doc = _doc(self.CLAIM.statement)
        findings, unsupported = engine._match_claims_to_evidence(doc, MagicMock(), [pool[0]], {"c1": [pool[1]]})
        self.assertEqual(findings[0].evidence[0].metadata["law_number"], "B")
        self.assertEqual(findings[0].citations[0].metadata["rerank_score"], 5.0)
        self.assertEqual(unsupported, [])


class TestVerifierUsesSameRanking(unittest.TestCase):
    def test_verifier_candidates_follow_reranker_order_and_scores_once(self):
        engine = AnalysisEngine()
        engine.reranker = FakeReranker({"iade edilir": 5.0, "tahsil": 1.0})
        verifier = MagicMock(max_candidates=2)
        verifier.choose.return_value = None
        engine.claim_verifier = verifier
        pool = TestRerankedSelection._pool(None)
        engine._match_claims_to_evidence(_doc(TestRerankedSelection.CLAIM.statement), MagicMock(), pool)
        shown = verifier.choose.call_args.args[1]
        self.assertEqual([e.metadata["law_number"] for e in shown], ["B", "A"])
        self.assertEqual(len(engine.reranker.calls), 1)


class TestPresenterRerankerScope(unittest.TestCase):
    def test_sample_evidence_never_uses_reranker(self):
        presenter = AnalysisPresenter(audit_sink=lambda *a, **k: None)
        presenter.reranker_fn = MagicMock(return_value=FakeReranker({}))
        presenter.execute_analysis(
            text="Kredi kartı yıllık aidat ücreti iade edilmedi, banka şikâyetime cevap vermedi.",
            sample_evidence=self._pool(),
        )
        presenter.reranker_fn.assert_not_called()
        self.assertIsNone(presenter.engine.reranker)

    def _pool(self):
        return TestRerankedSelection._pool(None)


class TestPresenterCandidateScope(unittest.TestCase):
    TEXT = "Kredi kartı yıllık aidat ücreti iade edilmedi, banka şikâyetime cevap vermedi."

    def _run(self, reranker):
        rag = MagicMock()
        rag.supports_claim_candidates = True
        rag.query.return_value = []
        rag.claim_candidates.return_value = []
        presenter = AnalysisPresenter(audit_sink=lambda *a, **k: None, catalog_fn=lambda: [])
        presenter._use_corpus_term_stats = lambda _rag: []
        presenter.reranker_fn = lambda: reranker
        presenter.execute_analysis(text=self.TEXT, rag_client=rag)
        return rag

    def test_claim_candidates_only_with_reranker(self):
        self.assertFalse(self._run(None).claim_candidates.called)
        self.assertTrue(self._run(FakeReranker({})).claim_candidates.called)


class TestLoadReranker(unittest.TestCase):
    def test_missing_model_dir_gives_none(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_reranker(Path(tmp) / "yok"))

    def test_threshold_travels_with_model(self):
        import tempfile
        from pathlib import Path

        from mufettis.rag.reranker import MIN_SCORES

        name = next(iter(MIN_SCORES))
        with tempfile.TemporaryDirectory() as tmp:
            for model_name, expected in ((name, MIN_SCORES[name]), ("ayarsiz-model", None)):
                model_dir = Path(tmp) / model_name
                model_dir.mkdir()
                (model_dir / "config.json").write_text("{}", encoding="utf-8")
                self.assertEqual(load_reranker(model_dir).min_score, expected)


if __name__ == "__main__":
    unittest.main()
