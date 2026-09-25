# -*- coding: utf-8 -*-
"""
Tests for DocumentClassifierService (FAZ 19.9 Stage 2).
"""
import pytest

from mufettis.analysis.classifier import DocumentClassifierService
from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.models import DocumentClassification, DocumentType


class TestDocumentClassifier:
    """Verifies deterministic classification of operational document types."""

    def setup_method(self):
        self.doc_service = DocumentUnderstandingService()
        self.classifier = DocumentClassifierService()

    def test_a_classify_complaint(self):
        text = (
            "Garanti BBVA şubesinden aldığım krediye ilişkin hesabımdan bilgim ve onayım olmadan "
            "750 TL sigorta ücreti kesilmiştir. Yapılan bu haksız kesinti nedeniyle mağdur edildim. "
            "Tutarın tarafıma iadesini talep ediyorum ve şikayetçiyim."
        )
        doc = self.doc_service.analyze(text)
        result = self.classifier.classify(doc)

        assert isinstance(result, DocumentClassification)
        assert result.document_type == DocumentType.COMPLAINT.value
        assert result.confidence >= 0.70
        assert len(result.signals) > 0
        assert any("complaint" in s for s in result.signals)
        assert len(result.reasons) > 0

    def test_b_classify_internal_correspondence(self):
        text = (
            "Sayı: 2026/04-122\n"
            "Konu: İç Kontrol Sistemi Denetim Raporu\n"
            "İlgi: 12.01.2026 tarihli yönetim kurulu kararı\n"
            "Kredi Operasyonları Müdürlüğüne\n\n"
            "Müdürlüğünüzce yürütülen kredi kullandırım süreçlerinde yetki sınırlarının aşıldığı "
            "tespit edilmiş olup ekteki denetim bulgularının ivedilikle incelenmesi rica olunur.\n\n"
            "Bilgilerinize arz ederim."
        )
        doc = self.doc_service.analyze(text)
        result = self.classifier.classify(doc)

        assert isinstance(result, DocumentClassification)
        assert result.document_type == DocumentType.INTERNAL_CORRESPONDENCE.value
        assert result.confidence >= 0.70
        assert any("header:" in s for s in result.signals)
        assert any("closing:" in s for s in result.signals)

    def test_c_classify_unknown(self):
        text = (
            "Hava sıcaklığı mevsim normallerinin üzerinde seyrediyor. Parktaki ağaçlar "
            "çiçek açmaya başladı ve bahar geldi."
        )
        doc = self.doc_service.analyze(text)
        result = self.classifier.classify(doc)

        assert isinstance(result, DocumentClassification)
        assert result.document_type == DocumentType.UNKNOWN.value
        assert result.confidence <= 0.40

    def test_d_confidence_bounds_enforced(self):
        # Confidence must be between 0.0 and 1.0
        doc = self.doc_service.analyze("Kısa şikayet metni şikayetçiyim mağdurum.")
        result = self.classifier.classify(doc)
        assert 0.0 <= result.confidence <= 1.0
