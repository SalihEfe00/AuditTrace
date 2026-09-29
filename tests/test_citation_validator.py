# -*- coding: utf-8 -*-
"""
Tests for CitationValidator & Unsupported Claim Guard (FAZ 19.9 Stage 3).
"""

from mufettis.analysis.citation_validator import CitationValidator
from mufettis.analysis.models import Citation, Evidence, Finding


class TestCitationValidator:
    """Verifies evidence grounding, citation text span checks, and cross-document mismatch guards."""

    def setup_method(self):
        self.validator = CitationValidator()

        self.ev_valid = Evidence(
            source_document="5411 Sayılı Bankacılık Kanunu",
            document_identity="5411",
            chunk_reference="Madde 76 Fıkra 1",
            text_span=(
                "Bankalar, müşterilerinin hak ve menfaatlerini korumak, sözleşme öncesinde "
                "gerekli bilgilendirmeyi yapmakla yükümlüdür."
            ),
            metadata={"source": "BDDK", "file_hash": "hash_5411_76"},
        )

    def test_a_valid_evidence_and_citation_accepted(self):
        cit = Citation(
            source="BDDK",
            document_identity="5411",
            reference="5411 sayılı Bankacılık Kanunu Madde 76 Fıkra 1",
            text_span="sözleşme öncesinde gerekli bilgilendirmeyi yapmakla yükümlüdür.",
            metadata={"file_hash": "hash_5411_76"},
        )
        finding = Finding(
            issue="Müşteri bilgilendirme yükümlülüğü incelemesi",
            evidence=[self.ev_valid],
            citations=[cit],
            regulation_reference="5411 sayılı Bankacılık Kanunu Madde 76",
            explanation="Banka tarafından işlem öncesi bilgilendirme yapılmadığı iddia edilmektedir.",
            inspector_action="Şube bilgilendirme formunu inceleyiniz.",
        )

        report = self.validator.validate_finding(finding)
        assert report.is_valid is True
        assert len(report.errors) == 0
        assert report.verified_citations == 1

    def test_b_missing_evidence_rejected_by_unsupported_claim_guard(self):
        finding = Finding(
            issue="Kanıtsız iddia edilen mevzuata aykırılık",
            evidence=[],  # Zero evidence
            citations=[],
            regulation_reference="5411 sayılı Kanun Madde 76",
            explanation="Banka kesinlikle suç işlemiştir.",
            inspector_action="Ceza kesilmelidir.",
        )
        report = self.validator.validate_finding(finding)
        assert report.is_valid is False
        assert any("no supporting evidence" in e for e in report.errors)
        assert len(report.unsupported_reasons) > 0

    def test_c_missing_document_identity_rejected(self):
        ev_bad_id = Evidence(
            source_document="Bilinmeyen Düzenleme",
            document_identity="   ",  # Empty id
            chunk_reference="Madde 1",
            text_span="Geçerli metin.",
        )
        finding = Finding(
            issue="Geçersiz kimlik testi",
            evidence=[ev_bad_id],
            citations=[],
            regulation_reference="Bilinmeyen Düzenleme Madde 1",
            explanation="Açıklama",
            inspector_action="İnceleme",
        )
        report = self.validator.validate_finding(finding)
        assert report.is_valid is False
        assert any("document_identity" in e for e in report.errors)

    def test_d_missing_chunk_reference_rejected(self):
        ev_bad_ref = Evidence(
            source_document="Bankacılık Kanunu",
            document_identity="5411",
            chunk_reference="   ",  # Empty reference
            text_span="Geçerli metin.",
        )
        finding = Finding(
            issue="Geçersiz referans testi",
            evidence=[ev_bad_ref],
            citations=[],
            regulation_reference="5411 sayılı Kanun",
            explanation="Açıklama",
            inspector_action="İnceleme",
        )
        report = self.validator.validate_finding(finding)
        assert report.is_valid is False
        assert any("chunk_reference" in e for e in report.errors)

    def test_e_citation_text_span_not_in_evidence_rejected(self):
        # Citation quotes words that do NOT exist in the attached evidence text
        cit_hallucinated = Citation(
            source="BDDK",
            document_identity="5411",
            reference="Madde 76",
            text_span="Bankalar müşterilerine bedava tatil kuponu dağıtmak zorundadır.",
        )
        finding = Finding(
            issue="Uydurma kanıt testi",
            evidence=[self.ev_valid],
            citations=[cit_hallucinated],
            regulation_reference="5411 sayılı Bankacılık Kanunu Madde 76",
            explanation="Açıklama",
            inspector_action="İnceleme",
        )
        report = self.validator.validate_finding(finding)
        assert report.is_valid is False
        assert any("not grounded in the attached evidence text" in e for e in report.errors)

    def test_f_cross_document_identity_mismatch_rejected(self):
        # Evidence is from 5411, but Citation claims to be from 6502
        cit_mismatch = Citation(
            source="Resmi Gazete",
            document_identity="6502",  # Mismatch with 5411
            reference="6502 Sayılı Kanun Madde 28",
            text_span="sözleşme öncesinde gerekli bilgilendirmeyi yapmakla yükümlüdür.",
        )
        finding = Finding(
            issue="Çapraz belge uyuşmazlığı",
            evidence=[self.ev_valid],  # Has document_identity="5411"
            citations=[cit_mismatch],
            regulation_reference="6502 sayılı Kanun",
            explanation="Açıklama",
            inspector_action="İnceleme",
        )
        report = self.validator.validate_finding(finding)
        assert report.is_valid is False
        assert any("Cross-document mismatch" in e for e in report.errors)

    def test_g_multiple_evidence_items_validated(self):
        ev2 = Evidence(
            source_document="5464 Sayılı Kart Kanunu",
            document_identity="5464",
            chunk_reference="Madde 12",
            text_span="Kart çıkaran kuruluş şüpheli işlemi askıya alır.",
        )
        cit1 = Citation(
            source="BDDK",
            document_identity="5411",
            reference="Madde 76",
            text_span="sözleşme öncesinde gerekli bilgilendirmeyi yapmakla yükümlüdür.",
        )
        cit2 = Citation(
            source="Resmi Gazete",
            document_identity="5464",
            reference="Madde 12",
            text_span="şüpheli işlemi askıya alır",
        )
        finding = Finding(
            issue="Çoklu kanıtlı bulgu",
            evidence=[self.ev_valid, ev2],
            citations=[cit1, cit2],
            regulation_reference="5411 ve 5464 sayılı mevzuat",
            explanation="Açıklama",
            inspector_action="İnceleme",
        )
        report = self.validator.validate_finding(finding)
        assert report.is_valid is True
        assert report.verified_citations == 2
