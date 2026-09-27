# -*- coding: utf-8 -*-
"""
Kanıt havuzu ve kanıt seçimi: sorgu harmanlama, belge sorgusu derinliği, geçici madde
etiketi ve önceliği, eşdizim eşleşmesi, başlık satırı ve ünlü uyumu.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from mufettis.analysis.document_understanding import DocumentUnderstandingService, states_a_fact
from mufettis.analysis.engine import AnalysisEngine
from mufettis.analysis.models import DocumentClaim, Evidence, RetrievalPlan
from mufettis.analysis.planner import AnalysisPlannerService
from mufettis.analysis.classifier import DocumentClassifierService
from mufettis.analysis.retrieval_planner import DOCUMENT_QUERY_DEPTH_FACTOR, RetrievalPlannerService
from mufettis.analysis.turkish_text import matched_bigrams, statement_bigrams
from mufettis.common.utils import article_label


def _chunk(text, **meta):
    return {"document": text, "metadata": {"file_hash": text[:12], **meta}}


def _plan(queries, limit, depths=None):
    return RetrievalPlan(
        queries=queries, filters={}, source_scope=[], evidence_requirements=["statutory_rule"],
        max_evidence_items=limit, query_depths=depths or {}, citation_requirements=["source"],
    )


class TestPoolInterleaving(unittest.TestCase):
    def _rag(self, per_query):
        rag = MagicMock()
        rag.query.side_effect = lambda q, k, **kw: per_query[q][:k]
        return rag

    def test_first_query_cannot_fill_the_pool(self):
        rag = self._rag({
            "başlık": [_chunk(f"başlık sonucu {i}") for i in range(10)],
            "iddia": [_chunk("iddia sonucu 0"), _chunk("iddia sonucu 1")],
        })
        texts = [e.text_span for e in AnalysisEngine().retrieve_evidence(_plan(["başlık", "iddia"], 4), rag)]
        self.assertEqual(texts, ["başlık sonucu 0", "iddia sonucu 0", "başlık sonucu 1", "iddia sonucu 1"])

    def test_query_depth_caps_contribution(self):
        rag = self._rag({
            "iddia": [_chunk(f"iddia sonucu {i}") for i in range(10)],
            "şablon": [_chunk(f"şablon sonucu {i}") for i in range(10)],
        })
        plan = _plan(["iddia", "şablon"], 6, {"iddia": 4, "şablon": 2})
        texts = [e.text_span for e in AnalysisEngine().retrieve_evidence(plan, rag)]
        self.assertEqual(sum(t.startswith("iddia") for t in texts), 4)
        self.assertEqual(sum(t.startswith("şablon") for t in texts), 2)

    def test_duplicate_does_not_consume_depth(self):
        shared = _chunk("ortak sonuç")
        rag = self._rag({"a": [shared, _chunk("a sonucu")], "b": [shared, _chunk("b sonucu")]})
        plan = _plan(["a", "b"], 4, {"a": 1, "b": 1})
        texts = [e.text_span for e in AnalysisEngine().retrieve_evidence(plan, rag)]
        self.assertEqual(texts, ["ortak sonuç", "b sonucu"])

    def test_document_queries_are_deeper_than_templates(self):
        doc = DocumentUnderstandingService().analyze(
            "Bankam kişisel verilerimi açık rızam olmadan üçüncü kişilere aktarmıştır."
        )
        classification = DocumentClassifierService().classify(doc)
        analysis_plan = AnalysisPlannerService().plan(doc, classification)
        plan = RetrievalPlannerService().plan_retrieval(analysis_plan, doc)
        top_k = analysis_plan.retrieval_scope.top_k_per_query
        claim_query = doc.claims[0].statement
        self.assertEqual(plan.query_depths[claim_query], top_k * DOCUMENT_QUERY_DEPTH_FACTOR)
        for query in plan.queries[analysis_plan.retrieval_scope.document_query_count:]:
            self.assertEqual(plan.query_depths[query], top_k)


class TestTransitionalArticles(unittest.TestCase):
    def test_article_label_keeps_prefix(self):
        self.assertEqual(article_label("4", "GEÇİCİ"), "Geçici Madde 4")
        self.assertEqual(article_label("4", "GEÇICI"), "Geçici Madde 4")  # eski kayıtlar (upper())
        self.assertEqual(article_label("3", "EK"), "Ek Madde 3")
        self.assertEqual(article_label("4", ""), "Madde 4")
        self.assertEqual(article_label("", "EK"), "")

    def test_evidence_reference_names_transitional_article(self):
        ev = AnalysisEngine.chunk_to_evidence({"document": "x", "metadata": {"article_no": "4", "article_prefix": "GEÇİCİ"}})
        self.assertEqual(ev.chunk_reference, "Geçici Madde 4")

    def test_regular_article_wins_equal_match(self):
        claim = DocumentClaim(claim_id="c1", statement="Kredi kartı borcuma itiraz ettim.", subject="genel")
        common = "Kart hamili kredi kartı borcuna itiraz edebilir."
        transitional = Evidence(source_document="Kart Kanunu", document_identity="k", chunk_reference="Geçici Madde 4",
                                text_span=common, metadata={"article_prefix": "GEÇİCİ", "article_no": "4"})
        regular = Evidence(source_document="Kart Kanunu", document_identity="k", chunk_reference="Madde 11",
                           text_span=common, metadata={"article_no": "11"})
        ranked = AnalysisEngine()._rank_evidence_for_claim(claim, [transitional, regular])
        self.assertIs(ranked[0], regular)


class TestCollocations(unittest.TestCase):
    def test_bigrams_skip_weak_only_pairs(self):
        bigrams = statement_bigrams("Kredi kartı için iç denetim sistemi kurulmamıştır.")
        self.assertIn(("iç", "denetim"), bigrams)
        self.assertIn(("denetim", "sistemi"), bigrams)
        self.assertNotIn(("kredi", "kartı"), bigrams)

    def test_bigram_matches_inflected_adjacent_words(self):
        bigrams = [("hesap", "kesim"), ("iç", "denetim")]
        self.assertEqual(matched_bigrams(bigrams, "hesap kesimi tarihinden önce"), [("hesap", "kesim")])
        self.assertEqual(matched_bigrams(bigrams, "denetim ve iç kontrol"), [])

    def test_collocation_accepts_evidence_of_common_words(self):
        # "denetim", "sistem", "kapsam" korpusta her yerde geçer (zayıf); bitişik "iç denetim
        # sistemi" ise hükmü adlandırır.
        engine = AnalysisEngine()
        engine.is_weak_term = lambda t: t[:5] in {"denet", "siste", "kapsa", "tespi", "alınm"}
        claim = DocumentClaim(
            claim_id="c1", subject="genel",
            statement="Yurt dışı şubelerin iç denetim sistemi kapsamına alınmadığı tespit edilmiştir.",
        )
        evidence = Evidence(
            source_document="Bankacılık Kanunu", document_identity="5411", chunk_reference="Madde 32",
            text_span="Bankalar bütün birim ve şubelerini kapsayan bir iç denetim sistemi kurmak zorundadır.",
            metadata={},
        )
        self.assertEqual(engine._rank_evidence_for_claim(claim, [evidence]), [evidence])


class TestClaimDetection(unittest.TestCase):
    def test_necessity_and_recommendation_are_not_facts(self):
        for sentence in (
            "İlgili yetki aşımının risk ve uyum açısından incelenmesi gerekmektedir.",
            "Müşteri dekontu doğrulandı, banka iade yapmalıdır.",
            "Şube personelinin kredi süreçleri konusunda eğitilmesi önerilmektedir.",
            "Kredi dosyalarının genel müdürlükçe yeniden incelenmeli.",
        ):
            self.assertFalse(states_a_fact(sentence), sentence)
        self.assertTrue(states_a_fact("Şube onay limitini aşarak kredi kullandırılmıştır."))

    def test_front_vowel_plural_predicates_state_facts(self):
        self.assertTrue(states_a_fact("Soruma cevap verecek bir birim bulunmadığını söylediler."))
        self.assertTrue(states_a_fact("Bankanın çalışanları bana eksik bilgi vermişlerdir."))

    def test_heading_line_is_not_a_claim(self):
        self.assertFalse(states_a_fact("Teftiş Kurulu Başkanlığı - İç Denetim Notu"))
        self.assertTrue(states_a_fact("Şubede kredi dosyalarının denetim notu eksik tutuldu"))


if __name__ == "__main__":
    unittest.main()
