# -*- coding: utf-8 -*-
"""
E9: Zayıf terimlerin korpustan belirlenmesi, yoksunluk eki, kural koymayan yapıların
(hesap planı) şikâyet kanıtından dışlanması ve kimlik sorgularının hızlı yolu.

Gerçek korpus ölçümünde görülen yanlış bulgular:
* "zararımın karşılanması" ~ hesap planındaki "beklenen zarar karşılığı" (otopark şikâyeti)
* "yetkisiz" (rızam olmadan) ~ "yetki"; "verilerimi" ~ "verilir"
* Hesap kodu sorgusu tüm koleksiyonu okuyup ~22 sn sürüyordu; yeniden sıralama da
  "934 bilgi sistemleri" sorgusunu belge 934 yerine hesap kodu 934 sanıyordu.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from mufettis.analysis import term_stats as TS
from mufettis.analysis.analysis_presenter import AnalysisPresenter
from mufettis.analysis.engine import AnalysisEngine
from mufettis.analysis.models import AnalysisType, Evidence
from mufettis.analysis.turkish_text import is_weak_term, terms_match
from mufettis.config import CHUNK_SCHEMA_VERSION


def _matches(meta: Dict[str, Any], where: Dict[str, Any]) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_matches(meta, w) for w in where["$and"])
    for key, cond in where.items():
        value = meta.get(key)
        if isinstance(cond, dict) and "$nin" in cond:
            # Gerçek Chroma (1.5) gibi: alanı olmayan kayıt `$nin` koşulunu geçer.
            if value is not None and value in cond["$nin"]:
                return False
        elif value != cond:
            return False
    return True


class FakeCollection:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows
        self.get_calls: List[Dict[str, Any]] = []

    def get(self, where=None, include=None, ids=None, limit=None, offset=0):
        self.get_calls.append(where or {})
        hit = [r for r in self.rows if _matches(r["metadata"], where or {})]
        hit = hit[offset:offset + limit] if limit is not None else hit
        return {"ids": [r["id"] for r in hit], "documents": [r["document"] for r in hit],
                "metadatas": [r["metadata"] for r in hit]}

    def query(self, query_embeddings=None, n_results=10, where=None):
        hit = [r for r in self.rows if _matches(r["metadata"], where or {})][:n_results]
        return {"documents": [[r["document"] for r in hit]], "metadatas": [[r["metadata"] for r in hit]],
                "distances": [[r.get("distance", 0.5) for r in hit]]}


def _row(i, doc, **meta):
    base = {"chunk_schema_version": CHUNK_SCHEMA_VERSION, "status": "active", "document_indexable": "True",
            "file_hash": meta.pop("file_hash", f"h{i}"), "structure_type": "article", "chunk_no": str(i)}
    return {"id": f"c{i}", "document": doc, "metadata": {**base, **meta}}


class TestPrivativeSuffix(unittest.TestCase):
    def test_privative_word_does_not_match_its_root(self):
        self.assertFalse(terms_match("yetkisiz", "yetki"))
        self.assertFalse(terms_match("ücret", "ücretsiz"))
        self.assertTrue(terms_match("yetki", "yetkisini"))
        self.assertTrue(terms_match("kart", "kartımdan"))

    def test_privative_word_has_its_own_frequency_key(self):
        self.assertEqual(TS.term_key("yetkisiz"), "yetkisiz")
        self.assertEqual(TS.term_key("yetkisizlik"), "yetkisiz")
        self.assertEqual(TS.term_key("yetki"), "yetki")
        self.assertEqual(TS.term_key("kart"), "kart*")  # 4 harfli kök: tüm çekimleriyle sayılır
        self.assertIn("kart*", TS.document_keys("Kartınızın aidatı"))


class TestCorpusWeakTerms(unittest.TestCase):
    def _stats(self, n_docs: int, common: str, rare: str) -> Dict[str, Any]:
        rows = [_row(i, f"Madde {i} {common} hükmü", file_hash=f"d{i}") for i in range(n_docs)]
        rows.append(_row(99, f"Madde 99 {rare} hükmü", file_hash="d0"))
        index = type("I", (), {"data": {"indexed_items": {f"d{i}": {"status": "active"} for i in range(n_docs)}}})()
        rag = type("R", (), {"collection": FakeCollection(rows)})()
        return TS.build_term_stats(rag, index)

    def test_common_root_becomes_weak_rare_root_stays_strong(self):
        stats = self._stats(TS.MIN_STATS_DOCS, "düzenleme", "aidat")
        weak = TS.weak_term_predicate(stats)
        self.assertTrue(weak("düzenlemesi"))
        self.assertFalse(weak("aidat"))

    def test_static_list_is_kept_union(self):
        weak = TS.weak_term_predicate(self._stats(TS.MIN_STATS_DOCS, "düzenleme", "aidat"))
        self.assertTrue(weak("ödeme"))  # sabit listede; korpus nadir görse bile zayıf kalır

    def test_small_corpus_falls_back_to_static_list(self):
        self.assertIs(TS.weak_term_predicate(self._stats(3, "düzenleme", "aidat")), is_weak_term)

    def test_superseded_documents_are_not_counted(self):
        index = type("I", (), {"data": {"indexed_items": {"a": {"status": "active"}, "b": {"status": "superseded"}}}})()
        rag = type("R", (), {"collection": FakeCollection([_row(1, "karar metni", file_hash="a"), _row(2, "karar metni", file_hash="b")])})()
        self.assertEqual(TS.build_term_stats(rag, index)["doc_count"], 1)

    def test_cache_is_reused_until_corpus_changes(self):
        tmp = Path(tempfile.mkdtemp(prefix="term_stats_"))
        try:
            path = tmp / "term_stats.json"
            items = {"a": {"status": "active"}}
            index = type("I", (), {"data": {"indexed_items": items}})()
            rag = type("R", (), {"collection": FakeCollection([_row(1, "karar metni", file_hash="a")])})()
            first = TS.ensure_term_stats(rag, index, path=path)
            calls = len(rag.collection.get_calls)
            self.assertEqual(TS.ensure_term_stats(rag, index, path=path)["built_at"], first["built_at"])
            self.assertEqual(len(rag.collection.get_calls), calls)
            items["b"] = {"status": "active"}
            TS.ensure_term_stats(rag, index, path=path)
            self.assertGreater(len(rag.collection.get_calls), calls)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestPresenterTermStats(unittest.TestCase):
    def test_sample_evidence_uses_static_list(self):
        p = AnalysisPresenter()
        p.engine.is_weak_term = lambda t: True
        p.execute_analysis(text="Bankanız hesabımdan onayım olmadan ücret kesmiştir. Şikayetçiyim.", sample_evidence=[])
        self.assertIs(p.engine.is_weak_term, is_weak_term)

    def test_stats_failure_is_reported_not_hidden(self):
        p = AnalysisPresenter()
        with patch("mufettis.analysis.term_stats.ensure_term_stats", side_effect=RuntimeError("kilitli")):
            warnings = p._use_corpus_term_stats(object())
        self.assertTrue(any("kilitli" in w for w in warnings))
        self.assertIs(p.engine.is_weak_term, is_weak_term)


class TestNonRuleStructuresExcluded(unittest.TestCase):
    def _plan(self, text):
        p = AnalysisPresenter()
        doc = p.doc_service.analyze(text)
        cl = p.classifier.classify(doc)
        ap = p.planner.plan(doc, cl)
        return ap, p.retrieval_planner.plan_retrieval(ap, doc)

    def test_complaint_and_unknown_exclude_accounting_plan(self):
        for text in ("Bankanız hesabımdan onayım olmadan ücret kesmiştir. Şikayetçiyim.",
                     "Kredi kartım için yıllık aidat ücreti tahsil edildi."):
            ap, rp = self._plan(text)
            self.assertNotEqual(ap.analysis_type, AnalysisType.COMPLIANCE.value)
            self.assertEqual(rp.filters.get("exclude_roles"), ["classification"])

    def test_internal_correspondence_keeps_accounting_plan(self):
        ap, rp = self._plan("Sayı: 2026/1\nKonu: Hesap Kaydı\nTeftiş Kurulu Başkanlığına\n"
                            "Şube tarafından kredi işlemlerinin yanlış hesaba kaydedildiği tespit edilmiştir.\n"
                            "Bilgilerinize arz ederim.")
        self.assertEqual(ap.analysis_type, AnalysisType.COMPLIANCE.value)
        self.assertNotIn("exclude_roles", rp.filters)

    def test_generic_plan_uses_document_sentences_and_readable_keywords(self):
        _, rp = self._plan("Kredi kartım için yıllık aidat ücreti tahsil edildi.")
        self.assertTrue(any("aidat" in q for q in rp.queries))
        self.assertFalse(any("_" in q for q in rp.queries))

    def test_retrieve_evidence_filters_and_passes_exclusion_only_to_capable_client(self):
        from mufettis.corpus import EligibilityPolicy

        _, rp = self._plan("Kredi kartım için yıllık aidat ücreti tahsil edildi.")
        results = [
            {"document": "39090 Kredi Kartı Ödemelerinden hesabı", "metadata": {"structure_type": "accounting_plan", "file_hash": "a"}},
            {"document": "Madde 10 – Kart hamiline aidat bildirilir.", "metadata": {"structure_type": "article", "file_hash": "k"}},
        ]
        seen_kwargs = []

        class Capable:
            supports_eligibility_policy = True

            def query(self, q, k=5, eligibility=None):
                seen_kwargs.append(eligibility)
                return results

        class Legacy:
            def query(self, q, k=5):
                return results

        for client in (Capable(), Legacy()):
            pool = AnalysisEngine().retrieve_evidence(rp, client)
            self.assertEqual({e.metadata.get("structure_type") for e in pool}, {"article"})
        self.assertTrue(seen_kwargs and all(isinstance(k, EligibilityPolicy) and k.exclude_roles == {"classification"}
                                            for k in seen_kwargs))


class TestStrongLexicalOverridesDistance(unittest.TestCase):
    def _evidence(self, text, distance):
        return Evidence(source_document="Aydınlatma Metni", document_identity="kvkk", chunk_reference="Bölüm 4",
                        text_span=text, metadata={"retrieval_distance": distance})

    def test_far_but_strong_sentence_is_accepted(self):
        text = "Bankam kişisel verilerimi açık rızam olmadan üçüncü kişilere aktarmıştır. Şikayetçiyim."
        ev = self._evidence("4) Kişisel Verilerin Üçüncü Kişilere Aktarılması ve aktarım rızası", 1.21)
        res = AnalysisPresenter().execute_analysis(text=text, sample_evidence=[ev])
        self.assertEqual(len(res.result.findings), 1)
        with patch("mufettis.analysis.engine.STRONG_LEXICAL_STEMS", 99):  # istisna kapalıyken mesafe eler
            res = AnalysisPresenter().execute_analysis(text=text, sample_evidence=[ev])
        self.assertEqual(res.result.findings, [])

    def test_far_and_weak_sentence_is_rejected(self):
        text = "Bankam kişisel verilerimi açık rızam olmadan üçüncü kişilere aktarmıştır. Şikayetçiyim."
        ev = self._evidence("Üçüncü kişiler tarafından yapılan başvurular değerlendirilir.", 1.21)
        res = AnalysisPresenter().execute_analysis(text=text, sample_evidence=[ev])
        self.assertEqual(res.result.findings, [])


class TestIdentityQueryFastPath(unittest.TestCase):
    def _rag(self, rows):
        from mufettis.rag.engine import LocalRAG

        rag = LocalRAG.__new__(LocalRAG)
        rag.collection = FakeCollection(rows)
        rag.embed = lambda texts, **kw: [[0.0, 0.0, 1.0] for _ in texts]
        return rag

    def test_account_query_does_not_scan_whole_collection(self):
        rows = [_row(i, f"{390 + i} HESAP ADI {i}", account_code=str(390 + i), structure_type="accounting_plan")
                for i in range(3)]
        rows += [_row(10 + i, "390 KREDİ KARTI ÖDEMELERİNDEN", account_code="390", structure_type="accounting_plan",
                      file_hash=f"p{i}") for i in range(5)]
        rag = self._rag(rows)
        out = rag.query("390 kredi kartı ödemeleri", k=5)
        self.assertEqual({r["metadata"]["account_code"] for r in out}, {"390"})
        self.assertNotIn({"chunk_schema_version": CHUNK_SCHEMA_VERSION}, rag.collection.get_calls)

    def test_resolved_document_ref_is_kept_in_rerank(self):
        # k'dan az belge parçası: hızlı yol yetmez, tam tarama + yeniden sıralama çalışır. Hesap planı
        # satırı metin başındaki "934" ile adaya girer; belge 934 önce gelmeli.
        rows = [_row(i, f"Bilgi sistemleri madde {i}", source_document_id="934", title="934", file_hash="doc934")
                for i in range(3)]
        rows += [_row(20, "934 MÜŞTERİ ADINA YAPILAN TÜREV İŞLEMLER", account_code="934",
                      structure_type="accounting_plan", source_document_id="1043", title="1043", source="BDDK")]
        out = self._rag(rows).query("934 bilgi sistemleri", k=5)
        self.assertEqual([r["metadata"]["source_document_id"] for r in out][:3], ["934"] * 3)


if __name__ == "__main__":
    unittest.main()
