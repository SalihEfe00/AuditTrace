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
    DocumentUnderstanding,
    RetrievalPlan,
)
from mufettis.analysis.validator import assert_valid_retrieval_plan


class RetrievalPlannerService:
    """Creates actionable, prioritized RetrievalPlans from an AnalysisPlan."""

    # Resmî kaynaklar + kullanıcının yerel olarak yüklediği mevzuat. İkincil kaynaklar
    # (ör. mevzuat_org) tek başına hukuki dayanak sayılmadığı için kapsam dışıdır.
    DEFAULT_SOURCES = ["bddk_official", "resmi_gazete", "local_file"]

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
        filters = {}
        if len(scope.explicit_laws) == 1:
            filters["law_number"] = str(scope.explicit_laws[0])

        max_items = max(3, min(20, scope.top_k_per_query * len(queries[:4])))

        retrieval_plan = RetrievalPlan(
            queries=queries,
            filters=filters,
            source_scope=self.DEFAULT_SOURCES,
            evidence_requirements=analysis_plan.required_evidence or ["statutory_rule"],
            max_evidence_items=max_items,
            citation_requirements=[
                "source",
                "document_identity",
                "article_reference",
                "verbatim_text_span",
            ],
        )

        assert_valid_retrieval_plan(retrieval_plan)
        return retrieval_plan
