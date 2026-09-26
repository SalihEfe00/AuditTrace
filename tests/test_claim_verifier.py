# -*- coding: utf-8 -*-
"""İsteğe bağlı dil modeli doğrulayıcısı: güvenceler ve motor entegrasyonu (sahte model ile)."""
from __future__ import annotations

import json
import unittest
from typing import List

from mufettis.analysis.analysis_presenter import AnalysisPresenter
from mufettis.analysis.claim_verifier import LLMClaimVerifier
from mufettis.analysis.models import Evidence

CLAIM_TEXT = ("Bankanız kredi kartım için bana önceden bildirmeden yıllık aidat ücreti tahsil etmiştir. "
              "Şikayetçiyim.")

# Sözcüksel eşleştiricinin seçeceği tesadüfi hüküm: kredi kartı, bildirilen, tahsil, yıllık, ücret.
COINCIDENTAL = Evidence(
    source_document="Örnek Kart Kanunu", document_identity="99001", chunk_reference="Geçici Madde 4",
    text_span=("Kredi kartı borçluları kendisine bildirilen son dönem borcu tamamen tahsil edilinceye kadar "
               "yıllık yüzde on sekiz faiz ile vekâlet ücreti dahil taksitle öder."),
    metadata={"retrieval_distance": 0.80},
)
# Doğru dayanak: iddiayla neredeyse hiç ortak terimi yok.
CORRECT = Evidence(
    source_document="Örnek Kart Kanunu", document_identity="99001", chunk_reference="Madde 24",
    text_span=("Sözleşmede yer almayan faiz, komisyon veya masraf gibi adlar altında hiçbir şekil ve surette "
               "ödeme talep edilemez ve hesaptan kesinti yapılamaz."),
    metadata={"retrieval_distance": 1.10},
)
POOL = [COINCIDENTAL, CORRECT]


def _model(response: dict, prompts: List[str]):
    def generate(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(response, ensure_ascii=False)
    return generate


def _analyze(verifier=None):
    presenter = AnalysisPresenter()
    presenter.engine.claim_verifier = verifier
    return presenter.execute_analysis(text=CLAIM_TEXT, sample_evidence=list(POOL)).result


class TestVerifierGuards(unittest.TestCase):
    def test_choice_must_quote_the_chosen_provision_verbatim(self):
        prompts: List[str] = []
        good = LLMClaimVerifier(_model({"secim": 2, "alinti": "hiçbir şekil ve surette ödeme talep edilemez",
                                        "gerekce": "Sözleşmede olmayan ücret yasak."}, prompts))
        verdict = good.choose("aidat", POOL)
        self.assertEqual(verdict.evidence_index, 1)
        self.assertIn("[2] Örnek Kart Kanunu — Madde 24", prompts[0])

        invented = LLMClaimVerifier(_model({"secim": 2, "alinti": "Banka aidat alamaz ve iade eder.",
                                            "gerekce": "x"}, []))
        self.assertIsNone(invented.choose("aidat", POOL))
        out_of_range = LLMClaimVerifier(_model({"secim": 7, "alinti": "", "gerekce": ""}, []))
        self.assertIsNone(out_of_range.choose("aidat", POOL))

    def test_unreachable_or_unparseable_model_returns_none(self):
        self.assertIsNone(LLMClaimVerifier(lambda p: "[LLM HATASI] bağlantı yok").choose("aidat", POOL))
        self.assertIsNone(LLMClaimVerifier(lambda p: "bilmiyorum").choose("aidat", POOL))

    def test_personal_data_is_masked_before_it_reaches_the_model(self):
        prompts: List[str] = []
        LLMClaimVerifier(_model({"secim": 0}, prompts)).choose("TR12 0001 0002 0003 0004 0005 06 hesabım", POOL)
        self.assertNotIn("0003 0004", prompts[0])


class TestEngineIntegration(unittest.TestCase):
    def test_without_verifier_the_lexical_match_is_used(self):
        result = _analyze()
        self.assertEqual(result.findings[0].evidence[0].chunk_reference, "Geçici Madde 4")

    def test_model_choice_replaces_a_coincidental_lexical_match(self):
        verifier = LLMClaimVerifier(_model({"secim": 2, "alinti": "hiçbir şekil ve surette ödeme talep edilemez",
                                            "gerekce": "Sözleşmede yer almayan ücret talep edilemez."}, []))
        finding = _analyze(verifier).findings[0]
        self.assertEqual(finding.evidence[0].chunk_reference, "Madde 24")
        self.assertIn("ödeme talep edilemez", finding.citations[0].text_span)
        self.assertIs(finding.citations[0].metadata.get("is_valid"), True)
        self.assertIn("dil modeli", finding.model_interpretation)

    def test_model_can_reject_all_candidates(self):
        verifier = LLMClaimVerifier(_model({"secim": 0, "gerekce": "Hiçbiri aidatı düzenlemiyor."}, []))
        result = _analyze(verifier)
        self.assertEqual(result.findings, [])
        self.assertTrue(result.unsupported_claims)

    def test_invalid_model_answer_falls_back_to_lexical(self):
        verifier = LLMClaimVerifier(_model({"secim": 2, "alinti": "uydurma cümle burada yer alıyor", "gerekce": ""}, []))
        self.assertEqual(_analyze(verifier).findings[0].evidence[0].chunk_reference, "Geçici Madde 4")



class TestPresenterConfiguration(unittest.TestCase):
    def test_unreachable_model_keeps_lexical_matching_and_says_why(self):
        presenter = AnalysisPresenter()
        note = presenter.configure_llm_verification(True, health_fn=lambda: {"ok": False, "error": "bağlantı reddedildi"})
        self.assertIsNone(presenter.engine.claim_verifier)
        self.assertIn("bağlantı reddedildi", note)

    def test_reachable_model_enables_and_disable_turns_off(self):
        presenter = AnalysisPresenter()
        self.assertIsNone(presenter.configure_llm_verification(True, health_fn=lambda: {"ok": True},
                                                               generate_fn=lambda p: "{}"))
        self.assertIsInstance(presenter.engine.claim_verifier, LLMClaimVerifier)
        presenter.configure_llm_verification(False)
        self.assertIsNone(presenter.engine.claim_verifier)


if __name__ == "__main__":
    unittest.main()
