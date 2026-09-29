# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 11B: Retrieval Quality Gate / Mini-RAG Validation.
Deterministic evaluation of Candidate RAG retrieval quality against Current Baseline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from repair.models import (
    ComparisonResult,
    ComparisonStatus,
    FinalGateDecision,
    RetrievalBenchmarkResult,
    RetrievalComparisonResult,
)


@dataclass
class RetrievalGatePolicy:
    """Configurable deterministic policy thresholds for retrieval gate.

    A benchmark that CURRENT finds and CANDIDATE misses (hit -> miss) is always a hard
    failure; there is no hit-rate tolerance.
    """
    max_rank_drop_tolerance: int = 2
    min_improvements_for_ready: int = 1
    allow_embedding_model_mismatch: bool = False


def evaluate_single_benchmark(
    bm_id: str,
    query: str,
    q_type: str,
    current_data: Dict[str, Any],
    candidate_data: Dict[str, Any],
    policy: RetrievalGatePolicy,
) -> Tuple[RetrievalBenchmarkResult, List[str], List[str]]:
    """Evaluates a single benchmark query between CURRENT baseline and CANDIDATE results.

    Returns (RetrievalBenchmarkResult, hard_failures, warnings).
    """
    hard_failures: List[str] = []
    warnings: List[str] = []
    reasons: List[str] = []

    c_hit = bool(current_data.get("hit", len(current_data.get("results", [])) > 0))
    cand_hit = bool(candidate_data.get("hit", len(candidate_data.get("results", [])) > 0))

    c_rank = current_data.get("rank")
    if c_rank is None and current_data.get("results"):
        c_rank = 1
    cand_rank = candidate_data.get("rank")
    if cand_rank is None and candidate_data.get("results"):
        cand_rank = 1

    c_doc_match = bool(current_data.get("document_match", True if c_hit else False))
    cand_doc_match = bool(candidate_data.get("document_match", True if cand_hit else False))

    c_acc_match = bool(current_data.get("account_match", True if q_type == "accounting_lookup" and c_hit else False))
    cand_acc_match = bool(candidate_data.get("account_match", True if q_type == "accounting_lookup" and cand_hit else False))

    c_ref_match = bool(current_data.get("reference_match", True if c_hit else False))
    cand_ref_match = bool(candidate_data.get("reference_match", True if cand_hit else False))

    c_meta_match = bool(current_data.get("metadata_match", True))
    cand_meta_match = bool(candidate_data.get("metadata_match", True))

    c_retrievable = bool(current_data.get("retrievable", True))
    cand_retrievable = bool(candidate_data.get("retrievable", True))

    # 1. HARD REGRESSIONS
    # Loss of hit
    if c_hit and not cand_hit:
        msg = f"[{bm_id}] CURRENT başarılı erişim sağlarken CANDIDATE sorguyu bulamadı (hit -> miss)."
        hard_failures.append(msg)
        reasons.append(msg)

    # Loss of expected document
    if c_doc_match and not cand_doc_match:
        msg = f"[{bm_id}] Beklenen doküman eşleşmesi CANDIDATE sonuçlarında kayboldu."
        hard_failures.append(msg)
        reasons.append(msg)

    # Loss of expected account code
    if q_type == "accounting_lookup" and c_acc_match and not cand_acc_match:
        msg = f"[{bm_id}] Beklenen muhasebe hesap kodu CANDIDATE sonuçlarında kayboldu."
        hard_failures.append(msg)
        reasons.append(msg)

    # Loss of expected legal reference
    if q_type == "exact_law" and c_ref_match and not cand_ref_match:
        msg = f"[{bm_id}] Beklenen yasal dayanak/atıf CANDIDATE sonuçlarında kayboldu."
        hard_failures.append(msg)
        reasons.append(msg)

    # Loss of retrievability
    if not cand_retrievable:
        msg = f"[{bm_id}] CANDIDATE sonuçları retrievability filtresinden geçemeyen chunk içeriyor."
        hard_failures.append(msg)
        reasons.append(msg)

    # 2. METADATA & RANK REGRESSIONS / WARNINGS
    if c_meta_match and not cand_meta_match:
        msg = f"[{bm_id}] CANDIDATE metadata filtrelemesinde uyumsuzluk tespit edildi."
        warnings.append(msg)
        reasons.append(msg)

    # Rank comparison (lower rank number is better: rank 1 is best)
    rank_improved = False
    rank_regressed = False
    if c_rank is not None and cand_rank is not None:
        if cand_rank < c_rank:
            rank_improved = True
            reasons.append(f"[{bm_id}] Rank iyileşti: {c_rank} -> {cand_rank}")
        elif cand_rank > c_rank:
            rank_diff = cand_rank - c_rank
            if rank_diff > policy.max_rank_drop_tolerance:
                msg = f"[{bm_id}] Rank belirgin geriledi: {c_rank} -> {cand_rank} (fark: {rank_diff})"
                warnings.append(msg)
                reasons.append(msg)
                rank_regressed = True
            else:
                reasons.append(f"[{bm_id}] Rank tolerans dahilinde geriledi: {c_rank} -> {cand_rank}")

    # Determine status
    if hard_failures:
        b_status = "FAILED"
    elif rank_regressed or (c_meta_match and not cand_meta_match):
        b_status = "REGRESSED"
    elif (not c_hit and cand_hit) or rank_improved or (not c_doc_match and cand_doc_match) or (not c_acc_match and cand_acc_match):
        b_status = "IMPROVED"
    else:
        b_status = "NEUTRAL"

    res = RetrievalBenchmarkResult(
        benchmark_id=bm_id,
        query=query,
        query_type=q_type,
        current_hit=c_hit,
        candidate_hit=cand_hit,
        current_rank=c_rank,
        candidate_rank=cand_rank,
        current_document_match=c_doc_match,
        candidate_document_match=cand_doc_match,
        current_account_match=c_acc_match,
        candidate_account_match=cand_acc_match,
        current_reference_match=c_ref_match,
        candidate_reference_match=cand_ref_match,
        current_metadata_match=c_meta_match,
        candidate_metadata_match=cand_meta_match,
        current_retrievable=c_retrievable,
        candidate_retrievable=cand_retrievable,
        status=b_status,
        reasons=reasons,
    )
    return res, hard_failures, warnings


def compare_retrieval(
    current_baseline: Union[Dict[str, Any], Path, str],
    candidate_benchmarks: Union[Dict[str, Any], List[Dict[str, Any]], Path, str],
    policy: Optional[RetrievalGatePolicy] = None,
    candidate_config: Optional[Dict[str, Any]] = None,
) -> RetrievalComparisonResult:
    """Executes FAZ 11B Retrieval Quality Gate comparing CURRENT baseline vs CANDIDATE.

    Determines REJECT, REVIEW, or READY_TO_COMMIT deterministically.
    """
    if policy is None:
        policy = RetrievalGatePolicy()

    # Load baseline
    if isinstance(current_baseline, (str, Path)):
        p = Path(current_baseline)
        if not p.exists():
            return RetrievalComparisonResult(
                status=ComparisonStatus.REJECT.value,
                hard_failures=[f"Baseline dosyası bulunamadı: {p}"],
                reasons=[f"Baseline dosyası bulunamadı: {p}"],
                confidence_explanation="Baseline verisi mevcut olmadığı için karşılaştırma reddedildi.",
            )
        base_data = json.loads(p.read_text(encoding="utf-8"))
    else:
        base_data = dict(current_baseline)

    # Load candidate benchmarks
    if isinstance(candidate_benchmarks, (str, Path)):
        p = Path(candidate_benchmarks)
        if not p.exists():
            return RetrievalComparisonResult(
                status=ComparisonStatus.REJECT.value,
                hard_failures=[f"Candidate sonuç dosyası bulunamadı: {p}"],
                reasons=[f"Candidate sonuç dosyası bulunamadı: {p}"],
                confidence_explanation="Aday sonuç verisi mevcut olmadığı için karşılaştırma reddedildi.",
            )
        cand_input = json.loads(p.read_text(encoding="utf-8"))
    else:
        cand_input = candidate_benchmarks

    if isinstance(cand_input, dict):
        cand_bms = cand_input.get("benchmarks", [])
        cand_cfg = candidate_config or cand_input.get("config") or {}
        cand_model = cand_input.get("model_name")
    else:
        cand_bms = cand_input
        cand_cfg = candidate_config or {}
        cand_model = cand_cfg.get("model_name")

    # Embedding config verification
    base_model = base_data.get("model_name")
    if base_model and cand_model and base_model != cand_model:
        if not policy.allow_embedding_model_mismatch:
            return RetrievalComparisonResult(
                status=ComparisonStatus.REJECT.value,
                hard_failures=[
                    f"Embedding model uyumsuzluğu: baseline='{base_model}', candidate='{cand_model}'"
                ],
                reasons=["Embedding modeli baseline ile eşleşmiyor."],
                confidence_explanation="Farklı embedding modelleri arasında deterministik karşılaştırma yapılamaz.",
            )

    cand_map: Dict[str, Dict[str, Any]] = {}
    for item in cand_bms:
        bid = item.get("id") or item.get("benchmark_id")
        if bid:
            cand_map[bid] = item

    all_hard_failures: List[str] = []
    all_warnings: List[str] = []
    all_reasons: List[str] = []
    benchmark_results: List[RetrievalBenchmarkResult] = []

    improved_count = 0
    regressed_count = 0
    neutral_count = 0
    failed_count = 0

    base_bms = base_data.get("benchmarks", [])
    if not base_bms:
        return RetrievalComparisonResult(
            status=ComparisonStatus.REVIEW.value,
            warnings=["Baseline içinde benchmark sorgusu bulunamadı."],
            reasons=["Boş baseline."],
            confidence_explanation="Değerlendirilecek benchmark bulunamadı.",
        )

    for b in base_bms:
        bid = b.get("id") or b.get("benchmark_id")
        q = b.get("query", "")
        q_type = b.get("type", "semantic")

        cand_data = cand_map.get(bid, {})
        bm_res, h_fails, warns = evaluate_single_benchmark(
            bm_id=bid,
            query=q,
            q_type=q_type,
            current_data=b,
            candidate_data=cand_data,
            policy=policy,
        )
        benchmark_results.append(bm_res)
        all_hard_failures.extend(h_fails)
        all_warnings.extend(warns)
        all_reasons.extend(bm_res.reasons)

        if bm_res.status == "IMPROVED":
            improved_count += 1
        elif bm_res.status == "REGRESSED":
            regressed_count += 1
        elif bm_res.status == "FAILED":
            failed_count += 1
        else:
            neutral_count += 1

    current_hits = sum(1 for b in benchmark_results if b.current_hit)
    candidate_hits = sum(1 for b in benchmark_results if b.candidate_hit)
    total_bms = len(benchmark_results)

    current_summary = {
        "total_benchmarks": total_bms,
        "hits": current_hits,
        "hit_rate": round(current_hits / total_bms, 4) if total_bms else 0.0,
    }
    candidate_summary = {
        "total_benchmarks": total_bms,
        "hits": candidate_hits,
        "hit_rate": round(candidate_hits / total_bms, 4) if total_bms else 0.0,
        "improved_count": improved_count,
        "regressed_count": regressed_count,
        "failed_count": failed_count,
        "neutral_count": neutral_count,
    }
    aggregate_delta = {
        "hit_delta": candidate_hits - current_hits,
        "improved_benchmarks": improved_count,
        "regressed_benchmarks": regressed_count,
        "failed_benchmarks": failed_count,
        "neutral_benchmarks": neutral_count,
    }

    # Deterministic Decision Logic
    if all_hard_failures or failed_count > 0:
        status = ComparisonStatus.REJECT.value
        explanation = (
            f"REJECT: {len(all_hard_failures)} kritik retrieval gerilemesi tespit edildi. "
            f"Kayıp sorgular veya erişilemeyen chunk'lar nedeniyle aday reddedildi."
        )
    elif regressed_count > 0 and improved_count > 0:
        status = ComparisonStatus.REVIEW.value
        explanation = (
            f"REVIEW: Karışık sinyaller (Mixed signals) tespit edildi: "
            f"{improved_count} benchmark iyileşirken {regressed_count} benchmark geriledi."
        )
    elif regressed_count > 0:
        status = ComparisonStatus.REJECT.value if regressed_count >= 2 else ComparisonStatus.REVIEW.value
        explanation = f"{status}: {regressed_count} retrieval benchmark'ında gerileme tespit edildi."
    elif improved_count >= policy.min_improvements_for_ready and regressed_count == 0 and not all_warnings:
        status = ComparisonStatus.READY_TO_COMMIT.value
        explanation = (
            f"READY_TO_COMMIT: Retrieval kalite kapısı başarıyla geçildi. "
            f"{improved_count} benchmarkta ölçülebilir iyileşme sağlandı, 0 gerileme, 0 hard failure."
        )
    else:
        # All identical / unchanged
        status = ComparisonStatus.REVIEW.value
        explanation = (
            f"REVIEW: Ölçülebilir retrieval iyileşmesi saptanmadı (tüm {neutral_count} benchmark eşit veya belirsiz)."
        )

    return RetrievalComparisonResult(
        status=status,
        benchmark_results=benchmark_results,
        current_summary=current_summary,
        candidate_summary=candidate_summary,
        aggregate_delta=aggregate_delta,
        hard_failures=all_hard_failures,
        warnings=all_warnings,
        reasons=all_reasons,
        confidence_explanation=explanation,
    )


def evaluate_final_gate(
    faz11a_result: ComparisonResult,
    faz11b_result: RetrievalComparisonResult,
) -> FinalGateDecision:
    """Combines FAZ 11A (Text/Structure) and FAZ 11B (Retrieval) gate results.

    Truth table:
    - 11A REJECT or 11B REJECT -> REJECT
    - 11A REVIEW or 11B REVIEW -> REVIEW
    - 11A READY_TO_COMMIT and 11B READY_TO_COMMIT -> READY_TO_COMMIT
    """
    s_a = getattr(faz11a_result, "status", ComparisonStatus.REVIEW.value)
    s_b = getattr(faz11b_result, "status", ComparisonStatus.REVIEW.value)

    reasons: List[str] = []

    if s_a == ComparisonStatus.REJECT.value or s_b == ComparisonStatus.REJECT.value:
        final_status = ComparisonStatus.REJECT.value
        if s_a == ComparisonStatus.REJECT.value:
            reasons.append("FAZ 11A Text/Structure kapısı REJECT kararı verdi.")
            reasons.extend(getattr(faz11a_result, "hard_failures", []))
        if s_b == ComparisonStatus.REJECT.value:
            reasons.append("FAZ 11B Retrieval kapısı REJECT kararı verdi.")
            reasons.extend(getattr(faz11b_result, "hard_failures", []))
        explanation = "En az bir kalite kapısında kritik güvenlik veya regresyon hatası nedeniyle aday REDDEDİLDİ."

    elif s_a == ComparisonStatus.REVIEW.value or s_b == ComparisonStatus.REVIEW.value:
        final_status = ComparisonStatus.REVIEW.value
        if s_a == ComparisonStatus.REVIEW.value:
            reasons.append("FAZ 11A Text/Structure kapısı REVIEW incelemesi gerektiriyor.")
            reasons.extend(getattr(faz11a_result, "warnings", []))
        if s_b == ComparisonStatus.REVIEW.value:
            reasons.append("FAZ 11B Retrieval kapısı REVIEW incelemesi gerektiriyor.")
            reasons.extend(getattr(faz11b_result, "warnings", []))
        explanation = "Kalite kapılarında belirsizlik, karışık sinyal veya nötr durum nedeniyle insan İNCELEMESİ (REVIEW) önerildi."

    elif s_a == ComparisonStatus.READY_TO_COMMIT.value and s_b == ComparisonStatus.READY_TO_COMMIT.value:
        final_status = ComparisonStatus.READY_TO_COMMIT.value
        explanation = (
            "Tüm kalite kapıları (Text/Structure + Retrieval) başarıyla geçildi. "
            "Aday belge güvenli commit hazırlığına (READY_TO_COMMIT) uygundur (Henüz commit yapılmamıştır)."
        )
        reasons.append("FAZ 11A: Metin, biçim ve madde yapısı doğrulandı.")
        reasons.append("FAZ 11B: Belge, hesap ve anlamsal retrieval doğruluğu korundu ve iyileştirildi.")

    else:
        final_status = ComparisonStatus.REVIEW.value
        explanation = "Tanımlanmayan durum kombinasyonu nedeniyle güvenli REVIEW kararı atandı."

    return FinalGateDecision(
        faz11a_status=s_a,
        faz11b_status=s_b,
        final_status=final_status,
        reasons=reasons,
        confidence_explanation=explanation,
        faz11a_result=faz11a_result,
        faz11b_result=faz11b_result,
    )
