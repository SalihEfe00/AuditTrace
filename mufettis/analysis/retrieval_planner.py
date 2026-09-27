# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Analysis Intelligence Core
Retrieval Planner Service.

Translates an AnalysisPlan into an actionable RetrievalPlan for the RAG engine.
Focuses strictly on WHAT to search (queries, domains, filters, evidence criteria)
without altering the RAG retrieval or reranking algorithms.
"""
from __future__ import annotations

from typing import List, Optional

from mufettis.analysis.models import (
    AnalysisPlan,
    AnalysisType,
    DocumentUnderstanding,
    RetrievalPlan,
)
from mufettis.analysis.validator import assert_valid_retrieval_plan
from mufettis.corpus import NormativeRole, PublisherKind

# Kural koymayan parçalar (hesap planı satırları, kod listeleri): yalnızca uyum (iç yazışma)
# analizinde kanıt olabilir. Rol, kanonik corpus modelinden gelir (mufettis.corpus).
NON_RULE_ROLES = (NormativeRole.CLASSIFICATION,)
# İkincil yayıncıların kopyaları tek başına hukuki dayanak sayılmaz. Kaynak kimliği listesi
# yerine yayıncı niteliğine bakılır: yeni bir kaynak eklendiğinde planlayıcı değişmez.
EXCLUDED_PUBLISHER_KINDS = (PublisherKind.SECONDARY,)
DOCUMENT_QUERY_DEPTH_FACTOR = 2
MAX_EVIDENCE_POOL = 20


class RetrievalPlannerService:
    """Creates actionable, prioritized RetrievalPlans from an AnalysisPlan."""

    def plan_retrieval(
        self,
        analysis_plan: AnalysisPlan,
        doc: Optional[DocumentUnderstanding] = None,
    ) -> RetrievalPlan:
        """Converts an AnalysisPlan into a validated RetrievalPlan."""
        scope = analysis_plan.retrieval_scope

        queries: List[str] = []

        # 1. Base generated queries
        for q in scope.generated_queries:
            clean_q = q.strip()
            if clean_q and clean_q not in queries:
                queries.append(clean_q)

        # 2. Targeted law queries if explicit laws exist
        for law in scope.explicit_laws:
            for kw in scope.keywords[:2]:
                targeted = f"{law} sayılı Kanun {kw}".strip()
                if targeted not in queries:
                    queries.append(targeted)

        # 3. Fallback if somehow queries list is empty
        if not queries:
            if scope.keywords:
                queries.append(" ".join(scope.keywords[:4]))
            else:
                queries.append(analysis_plan.objective)

        # Filters
        filters = {"exclude_publisher_kinds": list(EXCLUDED_PUBLISHER_KINDS)}
        if len(scope.explicit_laws) == 1:
            filters["law_number"] = str(scope.explicit_laws[0])
        if analysis_plan.analysis_type != AnalysisType.COMPLIANCE.value:
            # Hesap planı satırları yükümlülük değil muhasebe kaydı tanımıdır; şikâyete dayanak
            # olamaz. Ayrıca ~17 bin parçayla havuzu doldurup asıl kanunları dışarıda bırakıyordu
            # ("zararımın karşılanması" ~ "beklenen zarar karşılığı" gibi yanlış eşleşmeler).
            filters["exclude_roles"] = list(NON_RULE_ROLES)

        # Belgenin kendi cümleleriyle yapılan sorgular olayı taşır; şablon sorgular konu kategorisine
        # göre geneldir. Belge sorgularının havuza katkısı iki kat derindir (ilgili hüküm bu
        # sorgularda da çoğu zaman ilk 3'ün hemen altında kalıyor), şablonlarınki top_k ile sınırlı.
        document_queries = set(scope.generated_queries[:scope.document_query_count])
        depths = {
            q: scope.top_k_per_query * (DOCUMENT_QUERY_DEPTH_FACTOR if q in document_queries else 1)
            for q in queries
        }
        max_items = max(3, min(MAX_EVIDENCE_POOL, sum(depths.values())))

        retrieval_plan = RetrievalPlan(
            queries=queries,
            filters=filters,
            # Kaynak kapsamı yalnızca kullanıcı açıkça daralttığında doldurulur; varsayılan
            # uygunluk kanonik modele göre filters üzerinden belirlenir.
            source_scope=[],
            evidence_requirements=analysis_plan.required_evidence or ["statutory_rule"],
            max_evidence_items=max_items,
            query_depths=depths,
            citation_requirements=[
                "source",
                "document_identity",
                "article_reference",
                "verbatim_text_span",
            ],
        )

        assert_valid_retrieval_plan(retrieval_plan)
        return retrieval_plan
