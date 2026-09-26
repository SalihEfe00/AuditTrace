# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Analysis Intelligence Core
Analysis Planner Service.

Translates DocumentUnderstanding and DocumentClassification into a structured
AnalysisPlan. Determines analysis objectives, parameters, retrieval scope,
and required evidence types without fabricating findings or legal citations.
"""
from __future__ import annotations

from typing import List

from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.models import (
    AnalysisParameters,
    AnalysisPlan,
    AnalysisType,
    DocumentClassification,
    DocumentType,
    DocumentUnderstanding,
    RetrievalScope,
)
from mufettis.analysis.validator import assert_valid_analysis_plan


class AnalysisPlannerService:
    """Creates structured, validated AnalysisPlans tailored to document type and contents."""

    def plan(
        self,
        doc: DocumentUnderstanding,
        classification: DocumentClassification,
    ) -> AnalysisPlan:
        """Formulates an AnalysisPlan based on the understanding and classification of the document."""
        doc_type = classification.document_type

        if doc_type == DocumentType.COMPLAINT.value:
            plan = self._plan_complaint(doc, classification)
        elif doc_type == DocumentType.INTERNAL_CORRESPONDENCE.value:
            plan = self._plan_internal_correspondence(doc, classification)
        else:
            plan = self._plan_unknown_or_generic(doc, classification)

        assert_valid_analysis_plan(plan)
        return plan

    @staticmethod
    def _document_queries(doc: DocumentUnderstanding, limit: int = 2, max_chars: int = 200) -> List[str]:
        """Belgenin kendi içeriğinden sorgular: anlamlı başlık ve iddia/tespit cümleleri.

        Şablon sorgular konu kategorisine göre genel kalır; belgedeki olayın kendisiyle
        yapılan anlamsal arama çoğu zaman en isabetli kanıtı getirir.
        """
        candidates: List[str] = []
        if doc.title and doc.title != "Belge Metni":
            candidates.append(doc.title)
        candidates.extend(claim.statement for claim in doc.claims)
        if not doc.claims:
            observation = DocumentUnderstandingService.observation_statement(doc)
            if observation:
                candidates.append(observation)

        queries: List[str] = []
        for text in candidates:
            query = " ".join(text.split())[:max_chars].strip()
            if query and query not in queries:
                queries.append(query)
            if len(queries) >= limit:
                break
        return queries

    def _plan_complaint(
        self,
        doc: DocumentUnderstanding,
        classification: DocumentClassification,
    ) -> AnalysisPlan:
        target_entities = [e.text for e in doc.entities if e.entity_type == "institution"]
        if not target_entities:
            target_entities = ["Banka / Finans Kuruluşu"]

        # Scope and keywords from subjects
        scope: List[str] = ["finansal_tuketici_haklari", "mevzuat_uyumu"]
        keywords: List[str] = []
        queries: List[str] = []

        if "ücret_ve_komisyon" in doc.subjects:
            scope.append("ucret_ve_komisyon_mevzuati")
            keywords.extend(["ücret", "masraf", "komisyon", "bilgilendirme"])
            queries.append("Finansal tüketicilerden alınacak ücretler ve masraflar yönetmeliği")
            queries.append("Banka hesap işletim ücreti ve kesintiler yasal dayanak")

        if "yetkisiz_islem" in doc.subjects:
            scope.append("odeme_hizmetleri_guvenlik")
            keywords.extend(["yetkisiz işlem", "onaysız harcama", "güvenlik", "sorumluluk"])
            queries.append("Bankacılıkta yetkisiz ve hileli işlemlerde bankanın sorumluluğu")
            queries.append("Banka kartı ve kredi kartı şüpheli işlem itiraz prosedürü")

        if "kredi_ve_faiz" in doc.subjects:
            scope.append("kredi_ve_faiz_duzenlemeleri")
            keywords.extend(["kredi", "faiz", "cayma hakkı", "erken ödeme"])
            queries.append("Tüketici kredisi cayma hakkı ve erken ödeme tazminatı")

        if not queries:
            queries.append("Bankacılık Kanunu müşteri hakları ve bilgilendirme yükümlülüğü")
            keywords.extend(["tüketici hakları", "bankacılık", "hizmet kusuru"])

        retrieval_scope = RetrievalScope(
            target_domains=scope,
            explicit_laws=["5411", "6502", "5464"],
            keywords=list(set(keywords)),
            generated_queries=self._document_queries(doc) + queries,
            top_k_per_query=3,
        )

        parameters = AnalysisParameters(
            analysis_type=AnalysisType.REGULATORY_RELEVANCE.value,
            objective=(
                f"Müşteri şikâyetinde yer alan iddiaların ({', '.join(doc.subjects) if doc.subjects else 'genel şikayet'}) "
                f"mevzuat uyumu ve finansal tüketici hakları kapsamında incelenmesi."
            ),
            scope=scope,
            target_entities=target_entities,
            constraints={"min_confidence": classification.confidence, "dispute_type": "consumer_banking"},
            requested_outputs=["findings", "evidence_chains", "inspector_followup"],
        )

        return AnalysisPlan(
            analysis_type=AnalysisType.REGULATORY_RELEVANCE.value,
            objective=parameters.objective,
            parameters=parameters,
            retrieval_scope=retrieval_scope,
            required_evidence=[
                "prohibition_or_obligation_rule",
                "fee_legality_criteria",
                "bank_liability_standard",
            ],
            output_schema={
                "type": "inspector_finding_list",
                "item_fields": ["issue", "evidence", "regulation_reference", "inspector_action"],
            },
        )

    def _plan_internal_correspondence(
        self,
        doc: DocumentUnderstanding,
        classification: DocumentClassification,
    ) -> AnalysisPlan:
        target_entities = [e.text for e in doc.entities if e.entity_type in {"institution", "unit"}]
        if not target_entities:
            target_entities = ["Kurum İçi İlgili Birim"]

        scope = ["ic_sistemler_ve_denetim", "bankacilik_duzenleme"]
        keywords = ["yetki", "onay", "iç denetim", "prosedür", "mevzuat"]
        queries = [
            "Bankaların iç sistemleri ve iç kontrol süreçleri yönetmeliği",
            "Kredi tahsis yetkileri, limitler ve yönetim kurulu onay süreçleri",
        ]

        retrieval_scope = RetrievalScope(
            target_domains=scope,
            explicit_laws=["5411"],
            keywords=keywords,
            generated_queries=self._document_queries(doc) + queries,
            top_k_per_query=3,
        )

        parameters = AnalysisParameters(
            analysis_type=AnalysisType.COMPLIANCE.value,
            objective=(
                "Kurum içi yazışmanın yönetsel yetki, onay sınırları ve bankacılık iç kontrol "
                "mevzuatına uygunluğunun doğrulanması."
            ),
            scope=scope,
            target_entities=target_entities,
            constraints={"governance_level": "internal_procedure"},
            requested_outputs=["findings", "compliance_gaps", "authority_check"],
        )

        return AnalysisPlan(
            analysis_type=AnalysisType.COMPLIANCE.value,
            objective=parameters.objective,
            parameters=parameters,
            retrieval_scope=retrieval_scope,
            required_evidence=[
                "statutory_authority_limit",
                "reporting_obligation_deadline",
                "internal_control_requirement",
            ],
            output_schema={
                "type": "compliance_audit_matrix",
                "item_fields": ["issue", "evidence", "regulation_reference", "inspector_action"],
            },
        )

    def _plan_unknown_or_generic(
        self,
        doc: DocumentUnderstanding,
        classification: DocumentClassification,
    ) -> AnalysisPlan:
        scope = ["genel_bankacilik_mevzuati"]
        keywords = doc.subjects or ["bankacılık", "mevzuat", "denetim"]
        queries = ["5411 sayılı Bankacılık Kanunu temel yükümlülükler ve denetim"]

        retrieval_scope = RetrievalScope(
            target_domains=scope,
            explicit_laws=["5411"],
            keywords=keywords,
            generated_queries=queries,
            top_k_per_query=2,
        )

        parameters = AnalysisParameters(
            analysis_type=AnalysisType.REGULATORY_RELEVANCE.value,
            objective="Belirlenemeyen doküman türü için genel mevzuat ilişkilendirme ve ön tarama planı.",
            scope=scope,
            target_entities=["Belirtilmemiş Taraf"],
            constraints={"low_confidence_fallback": True},
            requested_outputs=["preliminary_matches"],
        )

        return AnalysisPlan(
            analysis_type=AnalysisType.REGULATORY_RELEVANCE.value,
            objective=parameters.objective,
            parameters=parameters,
            retrieval_scope=retrieval_scope,
            required_evidence=["general_statutory_rule"],
            output_schema={"type": "preliminary_review_list"},
        )
