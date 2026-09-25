# -*- coding: utf-8 -*-
"""
Tests for DocumentUnderstandingService (FAZ 19.9 Stage 2).
"""
import pytest

from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.models import DocumentUnderstanding


class TestDocumentUnderstanding:
    """Verifies deterministic parsing and entity extraction for operational documents."""

    def setup_method(self):
        self.service = DocumentUnderstandingService()

    def test_a_extract_entities_and_claims_from_complaint(self):
        text = (
            "Ziraat Bankası Kadıköy Şubesi nezdindeki TR12 0001 0002 0003 0004 0005 06 "
            "numaralı hesabımdan 14.02.2026 tarihinde onayım olmadan 2.500 TL "
            "hesap işletim ücreti kesildi. Yapılan haksız kesintinin derhal hesabıma iade edilmesini "
            "talep ediyorum ve mağdur edildim."
        )
        doc = self.service.analyze(text, metadata={"source_file": "sikayet_1.txt"})

        assert isinstance(doc, DocumentUnderstanding)
        assert doc.source == "sikayet_1.txt"
        assert doc.date == "14.02.2026"

        # Entities
        entity_types = {e.entity_type: e.text for e in doc.entities}
        assert "institution" in entity_types
        assert "amount" in entity_types
        assert "iban" in entity_types
        assert any(e.normalized_value == "Ziraat Bankası" for e in doc.entities)

        # Subjects
        assert "ücret_ve_komisyon" in doc.subjects

        # Claims
        assert len(doc.claims) >= 1
        claim_statements = " ".join(c.statement for c in doc.claims)
        assert "kesildi" in claim_statements or "talep ediyorum" in claim_statements

        # Events
        assert len(doc.events) >= 1
        assert doc.events[0].date == "14.02.2026"

    def test_b_extract_from_internal_correspondence(self):
        text = (
            "T.C. ZİRAAT BANKASI A.Ş.\n"
            "Teftiş Kurulu Başkanlığına\n"
            "Sayı: 2026/894\n"
            "Konu: Kredi Tahsis Yetki Aşımı İncelemesi\n"
            "Tarih: 10/01/2026\n\n"
            "Kadıköy Şubesi tarafından 15.000.000 TL tutarındaki ticari kredinin "
            "yönetim kurulu onay limiti aşılarak kullandırıldığı tespit edilmiştir.\n"
            "Gereğini bilgilerinize arz ederim."
        )
        doc = self.service.analyze(text)

        assert doc.title.startswith("T.C.") or "Kredi Tahsis" in doc.title
        assert doc.date == "10/01/2026" or "10/01/2026" in str(doc.date)

        # Entities check
        units = [e.text for e in doc.entities if e.entity_type == "unit"]
        assert any("Teftiş Kurulu" in u or "Kadıköy Şubesi" in u for u in units)

        amounts = [e.text for e in doc.entities if e.entity_type == "amount"]
        assert any("15.000.000 TL" in a for a in amounts)

    def test_c_empty_or_whitespace_input_rejected(self):
        with pytest.raises(ValueError):
            self.service.analyze("")

        with pytest.raises(ValueError):
            self.service.analyze("   \n\t  ")

    def test_d_deterministic_behavior(self):
        text = "VakıfBank şubesinden 15.03.2026 tarihinde 1.000 TL haksız masraf kesildi."
        run1 = self.service.analyze(text)
        run2 = self.service.analyze(text)

        assert run1.model_dump() == run2.model_dump()
