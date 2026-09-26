# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Stage 4 Analysis Intelligence
Analysis Presenter / UI Adapter.

Orchestrates DocumentUnderstanding, DocumentClassifier, AnalysisPlanner,
RetrievalPlanner, and AnalysisEngine into a coherent, safe workflow for
Streamlit UI and other user-facing presentation layers.

Core principles:
- UI strictly does presentation, input gathering, and review recording.
- No legal reasoning, retrieval logic, or citation validation is done in the UI.
- All domain logic remains firmly encapsulated in mufettis.analysis components.
- Zero mutation of production databases from presentation adapters.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from mufettis.analysis.classifier import DocumentClassifierService
from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.cited_references import CatalogEntry, catalog_from_index, cited_references
from mufettis.analysis.engine import (
    AnalysisEngine,
    RetrievalUnavailableError,
    evidence_display_title,
    retrieval_error_warnings,
)
from mufettis.analysis.models import (
    AnalysisPlan,
    AnalysisResult,
    CitedRegulation,
    DocumentClassification,
    DocumentUnderstanding,
    Evidence,
    Finding,
    RetrievalPlan,
)
from mufettis.analysis.planner import AnalysisPlannerService
from mufettis.analysis.retrieval_planner import RetrievalPlannerService
from mufettis.audit.service import append_audit
from mufettis.common.utils import mask_sensitive, now_iso

logger = logging.getLogger(__name__)


# Pipeline aşamaları (anahtar, kullanıcıya gösterilen etiket); ilerleme bildirimi bu sırayla yapılır.
PIPELINE_STAGES: List[Tuple[str, str]] = [
    ("document_understanding", "Belge anlamsal varlıkları ve iddiaları çözümleniyor..."),
    ("classification", "Belge türü ve konusu sınıflandırılıyor..."),
    ("analysis_planning", "Mevzuat analiz planı formüle ediliyor..."),
    ("retrieval_planning", "Retrieval planı oluşturuluyor..."),
    ("evidence_retrieval", "Mevzuat kanıtları sorgulanıyor..."),
    ("analysis_engine", "Kanıtlar eşleştiriliyor, alıntılar doğrulanıyor ve bulgular üretiliyor..."),
]
_STAGE_LABELS = dict(PIPELINE_STAGES)

ProgressCallback = Callable[[int, str, str], None]


@dataclass
class AnalysisExecutionResult:
    """Encapsulates the complete outcome of an analysis run for UI presentation."""
    success: bool
    result: Optional[AnalysisResult] = None
    retrieved_evidence: List[Evidence] = field(default_factory=list)
    execution_time_sec: float = 0.0
    error_message: Optional[str] = None
    stage_durations: Dict[str, float] = field(default_factory=dict)


class AnalysisPresenter:
    """Presenter service bridging UI inputs to the Analysis Intelligence pipeline."""

    def __init__(
        self,
        doc_service: Optional[DocumentUnderstandingService] = None,
        classifier: Optional[DocumentClassifierService] = None,
        planner: Optional[AnalysisPlannerService] = None,
        retrieval_planner: Optional[RetrievalPlannerService] = None,
        engine: Optional[AnalysisEngine] = None,
        audit_sink: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        catalog_fn: Optional[Callable[[], List[CatalogEntry]]] = None,
    ):
        self.doc_service = doc_service or DocumentUnderstandingService()
        self.classifier = classifier or DocumentClassifierService()
        self.planner = planner or AnalysisPlannerService()
        self.retrieval_planner = retrieval_planner or RetrievalPlannerService()
        self.engine = engine or AnalysisEngine()
        self.audit_sink = audit_sink or append_audit
        # Belgenin atıf yaptığı mevzuatın veritabanında olup olmadığını söylemek için katalog.
        # Verilmezse gerçek veritabanıyla yapılan analizde ingest index'ten okunur.
        self.catalog_fn = catalog_fn

    @staticmethod
    def _notify(progress_callback: Optional[ProgressCallback], stage_key: str) -> None:
        """Reports the stage about to run; a failing callback never breaks the analysis."""
        if progress_callback is None:
            return
        index = next(i for i, (key, _) in enumerate(PIPELINE_STAGES, 1) if key == stage_key)
        try:
            progress_callback(index, stage_key, _STAGE_LABELS[stage_key])
        except Exception:
            pass

    def configure_llm_verification(
        self,
        enabled: bool,
        health_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        generate_fn: Optional[Callable[[str], str]] = None,
    ) -> Optional[str]:
        """İddia-kanıt eşleşmesinde yerel dil modeli doğrulamasını açar/kapatır.

        Model erişilemezse doğrulama kapalı kalır ve nedeni döner (analiz sözcüksel eşleştirmeyle
        sürer). health_fn/generate_fn verilmezse yerel Ollama kullanılır.
        """
        if not enabled:
            self.engine.claim_verifier = None
            return None
        from mufettis.analysis.claim_verifier import LLMClaimVerifier
        from mufettis.llm.client import ollama_generate, ollama_health

        health = (health_fn or ollama_health)()
        if not health.get("ok"):
            self.engine.claim_verifier = None
            return f"Yerel dil modeli erişilemiyor ({health.get('error') or health.get('status_code')}); sözcüksel eşleştirme kullanıldı."
        self.engine.claim_verifier = LLMClaimVerifier(generate_fn or ollama_generate)
        return None

    def validate_input(self, text: Optional[str]) -> Tuple[bool, Optional[str]]:
        """Validates that document input is non-empty and contains substantive content."""
        if text is None:
            return False, "Lütfen analiz edilecek belge metnini giriniz."
        cleaned = text.strip()
        if not cleaned:
            return False, "Belge metni boş veya yalnızca boşluklardan oluşamaz."
        if len(cleaned) < 15:
            return False, "Belge metni çok kısa. Lütfen en az 15 karakter içeren anlamlı bir metin giriniz."
        return True, None

    def _use_corpus_term_stats(self, rag_client: Any) -> List[str]:
        """Zayıf terimleri korpus belge frekansından belirler. Hesaplanamazsa analiz durmaz,
        sabit listeye dönülür ve bu durum uyarı olarak raporlanır."""
        from mufettis.analysis.term_stats import corpus_weak_keys, ensure_term_stats
        from mufettis.rag.index import IngestIndex

        try:
            stats = ensure_term_stats(rag_client, IngestIndex())
        except Exception as exc:
            logger.warning("Korpus terim istatistiği hesaplanamadı: %s", exc)
            return [f"Korpus terim istatistiği hesaplanamadı ({exc}); sabit zayıf terim listesi kullanıldı."]
        self.engine.use_term_stats(stats)
        if corpus_weak_keys(stats) is None:
            return ["Korpusta yeterli belge yok; sabit zayıf terim listesi kullanıldı."]
        return []

    def _catalog(self, rag_client: Any, sample_evidence: Optional[List[Evidence]]) -> Optional[List[CatalogEntry]]:
        """Atıf kontrolü kataloğu. Örnek kanıtla (test/gold set) veya veritabanı olmadan yapılan
        analizde katalog yoktur: atıflar "kontrol edilmedi" olarak gösterilir, "yok" denmez."""
        if self.catalog_fn is not None:
            return self.catalog_fn()
        if sample_evidence is not None or rag_client is None:
            return None
        from mufettis.rag.index import IngestIndex

        try:
            return catalog_from_index(IngestIndex().data.get("indexed_items", {}), evidence_display_title)
        except Exception as exc:
            logger.warning("Mevzuat kataloğu okunamadı: %s", exc)
            return None

    def execute_analysis(
        self,
        text: str,
        title: Optional[str] = None,
        source: Optional[str] = None,
        date: Optional[str] = None,
        rag_client: Optional[Any] = None,
        sample_evidence: Optional[List[Evidence]] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> AnalysisExecutionResult:
        """Executes the full Analysis Intelligence pipeline and returns an AnalysisExecutionResult."""
        start_time = time.perf_counter()
        stage_durations: Dict[str, float] = {}

        # 1. Input validation
        is_valid, err = self.validate_input(text)
        if not is_valid:
            return AnalysisExecutionResult(
                success=False,
                error_message=err,
                execution_time_sec=0.0,
            )

        try:
            # 2. Document Understanding
            self._notify(progress_callback, "document_understanding")
            t0 = time.perf_counter()
            meta = {}
            if title:
                meta["title"] = title
            if source:
                meta["source"] = source
            if date:
                meta["date"] = date
            doc = self.doc_service.analyze(
                text=text,
                metadata=meta,
            )
            stage_durations["document_understanding"] = round(time.perf_counter() - t0, 3)

            # 3. Document Classification
            self._notify(progress_callback, "classification")
            t0 = time.perf_counter()
            classification = self.classifier.classify(doc)
            stage_durations["classification"] = round(time.perf_counter() - t0, 3)

            # 4. Analysis Planning
            self._notify(progress_callback, "analysis_planning")
            t0 = time.perf_counter()
            analysis_plan = self.planner.plan(doc, classification)
            stage_durations["analysis_planning"] = round(time.perf_counter() - t0, 3)

            # 5. Retrieval Planning
            self._notify(progress_callback, "retrieval_planning")
            t0 = time.perf_counter()
            retrieval_plan = self.retrieval_planner.plan_retrieval(analysis_plan, doc)
            stage_durations["retrieval_planning"] = round(time.perf_counter() - t0, 3)

            # 6. Evidence Retrieval
            self._notify(progress_callback, "evidence_retrieval")
            t0 = time.perf_counter()
            retrieval_warnings: List[str] = []
            # Örnek kanıtla (test/gold set) sabit liste kullanılır; sonuçlar korpusa bağlı olmaz.
            self.engine.use_term_stats(None)
            if sample_evidence is not None:
                evidence_pool = list(sample_evidence)
            elif rag_client is not None:
                retrieval_warnings.extend(self._use_corpus_term_stats(rag_client))
                retrieval_errors: List[str] = []
                evidence_pool = self.engine.retrieve_evidence(retrieval_plan, rag_client, errors=retrieval_errors)
                # Tüm sorgular başarısızsa RetrievalUnavailableError: analiz "kanıt yok" yerine hata verir.
                retrieval_warnings = retrieval_error_warnings(retrieval_plan, retrieval_errors)
            else:
                evidence_pool = []
            stage_durations["evidence_retrieval"] = round(time.perf_counter() - t0, 3)

            # 7. Analysis Engine & Citation Validation
            self._notify(progress_callback, "analysis_engine")
            t0 = time.perf_counter()
            result = self.engine.analyze(
                doc=doc,
                classification=classification,
                analysis_plan=analysis_plan,
                retrieval_plan=retrieval_plan,
                retrieved_evidence=evidence_pool,
            )
            result.warnings.extend(retrieval_warnings)
            result.cited_regulations = [
                CitedRegulation(**vars(ref))
                for ref in cited_references(text, self._catalog(rag_client, sample_evidence))
            ]
            stage_durations["analysis_engine"] = round(time.perf_counter() - t0, 3)

            total_elapsed = round(time.perf_counter() - start_time, 3)

            return AnalysisExecutionResult(
                success=True,
                result=result,
                retrieved_evidence=evidence_pool,
                execution_time_sec=total_elapsed,
                stage_durations=stage_durations,
            )

        except RetrievalUnavailableError as exc:
            total_elapsed = round(time.perf_counter() - start_time, 3)
            return AnalysisExecutionResult(
                success=False,
                error_message=f"Analiz tamamlanamadı: {exc}",
                execution_time_sec=total_elapsed,
                stage_durations=stage_durations,
            )
        except Exception as exc:
            total_elapsed = round(time.perf_counter() - start_time, 3)
            return AnalysisExecutionResult(
                success=False,
                error_message=f"Analiz sırasında bir sorun oluştu: {exc}",
                execution_time_sec=total_elapsed,
                stage_durations=stage_durations,
            )

    @staticmethod
    def apply_reviewer_action(
        result: AnalysisResult,
        status: str,
        note: Optional[str] = None,
        reviewer: Optional[str] = None,
    ) -> AnalysisResult:
        """Applies human reviewer action to an AnalysisResult in a safe, traceable manner."""
        result_dict = result.model_dump()
        result_dict["reviewer_status"] = str(status or "Bekliyor").strip()
        result_dict["reviewer_note"] = str(note or "").strip() if note else None
        result_dict["reviewer_name"] = str(reviewer or "").strip() or None
        result_dict["reviewed_at"] = now_iso()

        updated = AnalysisResult.model_validate(result_dict)
        return updated

    def record_review(self, result: AnalysisResult) -> Dict[str, Any]:
        """Writes the reviewer decision of an AnalysisResult to the audit log.

        Free-text fields are masked before writing; only review metadata and
        summary counts are persisted, never the full document text.
        """
        payload = {
            "analysis_id": result.analysis_id,
            "document_title": mask_sensitive(result.document.title or ""),
            "document_type": result.classification.document_type,
            "analysis_status": result.status,
            "finding_count": len(result.findings),
            "unsupported_claim_count": len(result.unsupported_claims),
            "reviewer_status": result.reviewer_status,
            "reviewer_name": result.reviewer_name or "Belirtilmedi",
            "reviewer_note": mask_sensitive(result.reviewer_note or ""),
            "reviewed_at": result.reviewed_at,
        }
        self.audit_sink("document_analysis_review", payload)
        return payload
