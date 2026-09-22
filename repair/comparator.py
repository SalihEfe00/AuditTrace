# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 11A: Quality Gate Core / RAG-Independent Comparison
Comparator & Quality Gate Module.

Deterministic evaluation of CURRENT BASELINE vs EXTRACTION CANDIDATE.
Zero LLM usage; strictly rule-based, policy-driven quality gate.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from repair.models import (
    ComparisonResult,
    ComparisonStatus,
    ExtractionCandidate,
    MetricDirection,
    QualitySnapshot,
)


class QualityGatePolicy:
    """Merkezi deterministik kalite kapısı kuralları ve eşikleri."""

    SCORE_SEVERE_DROP_THRESHOLD: float = 0.05
    SCORE_MINOR_DROP_TOLERANCE: float = 0.02
    MIN_LENGTH_RETENTION_RATIO: float = 0.50
    STRUCTURE_CATASTROPHIC_LOSS_RATIO: float = 0.30
    CHUNK_EXPLOSION_RATIO: float = 3.0

    HIGHER_IS_BETTER = {
        "quality_score",
        "meaningful_chunk_count",
        "metadata_completeness",
    }

    LOWER_IS_BETTER = {
        "glued_token_count",
        "very_long_token_count",
        "mojibake_sequence_count",
        "replacement_char_count",
        "empty_chunk_count",
        "duplicate_chunk_count",
    }


def build_snapshot(source: Any) -> QualitySnapshot:
    """Farklı girdi türlerinden (QualitySnapshot, ExtractionCandidate, DocumentScanResult, dict) standart QualitySnapshot üretir."""
    if isinstance(source, QualitySnapshot):
        return source
    if isinstance(source, ExtractionCandidate):
        return QualitySnapshot.from_candidate(source)
    if hasattr(source, "current_metrics") and hasattr(source, "chroma_quality"):
        data = dict(source.current_metrics or {})
        cq = source.chroma_quality or {}
        if cq.get("glued_token_count", 0) > data.get("glued_token_count", 0):
            data["glued_token_count"] = cq["glued_token_count"]
        if cq.get("mojibake_sequence_count", 0) > data.get("mojibake_sequence_count", 0):
            data["mojibake_sequence_count"] = cq["mojibake_sequence_count"]
        if cq.get("replacement_char_count", 0) > data.get("replacement_char_count", 0):
            data["replacement_char_count"] = cq["replacement_char_count"]
        if cq.get("very_long_token_count", 0) > data.get("very_long_token_count", 0):
            data["very_long_token_count"] = cq["very_long_token_count"]
        if cq.get("avg_quality_score") is not None:
            cur_s = float(data.get("quality_score") or data.get("score") or 1.0)
            data["quality_score"] = min(cur_s, float(cq["avg_quality_score"]))
        elif cq.get("min_quality_score") is not None:
            cur_s = float(data.get("quality_score") or data.get("score") or 1.0)
            data["quality_score"] = min(cur_s, float(cq["min_quality_score"]))
        if getattr(source, "chunk_summary", None):
            data["chunk_count"] = source.chunk_summary.get("chunk_count", data.get("chunk_count", 0))
        data["document_id"] = getattr(source, "document_id", "")
        data["source_sha256"] = getattr(source, "source_hash", "")
        return QualitySnapshot.from_dict(data)
    if isinstance(source, dict):
        return QualitySnapshot.from_dict(source)
    return QualitySnapshot()


def calculate_metric_deltas(
    curr: QualitySnapshot,
    cand: QualitySnapshot,
) -> Dict[str, Dict[str, Any]]:
    """Tüm temel metrikler için eski, yeni, delta ve değişim yönünü hesaplar."""
    metrics_to_compare = [
        "quality_score",
        "glued_token_count",
        "very_long_token_count",
        "mojibake_sequence_count",
        "replacement_char_count",
        "chars",
        "article_count",
        "paragraph_count",
        "item_count",
        "subitem_count",
        "section_count",
        "accounting_code_count",
        "chunk_count",
        "meaningful_chunk_count",
        "empty_chunk_count",
        "duplicate_chunk_count",
        "metadata_completeness",
    ]

    deltas: Dict[str, Dict[str, Any]] = {}

    for name in metrics_to_compare:
        old_val = getattr(curr, name, 0)
        new_val = getattr(cand, name, 0)

        if isinstance(old_val, float) or isinstance(new_val, float):
            delta = round(float(new_val) - float(old_val), 4)
        else:
            delta = int(new_val) - int(old_val)

        if name in QualityGatePolicy.HIGHER_IS_BETTER:
            if delta > 0.0001:
                direction = MetricDirection.IMPROVED.value
            elif delta < -0.0001:
                direction = MetricDirection.REGRESSED.value
            else:
                direction = MetricDirection.NEUTRAL.value
        elif name in QualityGatePolicy.LOWER_IS_BETTER:
            if delta < -0.0001:
                direction = MetricDirection.IMPROVED.value
            elif delta > 0.0001:
                direction = MetricDirection.REGRESSED.value
            else:
                direction = MetricDirection.NEUTRAL.value
        else:
            # Structure & neutral metrics
            direction = MetricDirection.NEUTRAL.value

        deltas[name] = {
            "old": old_val,
            "new": new_val,
            "delta": delta,
            "direction": direction,
        }

    return deltas


def compare_quality(
    current: Any,
    candidate: Any,
    context: Optional[Dict[str, Any]] = None,
) -> ComparisonResult:
    """Mevcut baseline ile aday çıkarımı karşılaştırarak deterministik Quality Gate kararı üretir.

    Sonuç REJECT, READY_TO_COMMIT veya REVIEW'dir.
    Asla üretim ortamına doğrudan commit yapmaz.
    """
    context = dict(context or {})

    # Standart QualitySnapshot'lara dönüştür
    curr_snap = build_snapshot(current)
    cand_snap = build_snapshot(candidate)

    doc_id = (
        context.get("document_id")
        or cand_snap.document_id
        or curr_snap.document_id
        or ""
    )

    deltas = calculate_metric_deltas(curr_snap, cand_snap)
    delta_score = deltas["quality_score"]["delta"]

    hard_failures: List[str] = []
    warnings: List[str] = []
    quality_reasons: List[str] = []
    structure_reasons: List[str] = []
    retrieval_reasons: List[str] = []
    reasons: List[str] = []

    # Eski sözleşme (metrics_diff) geriye dönük uyumluluk için
    metrics_diff = {
        "delta_score": round(delta_score, 4),
        "delta_glued": deltas["glued_token_count"]["delta"],
        "delta_mojibake": deltas["mojibake_sequence_count"]["delta"],
        "delta_replacement_chars": deltas["replacement_char_count"]["delta"],
        "delta_chars": deltas["chars"]["delta"],
        "delta_long_tokens": deltas["very_long_token_count"]["delta"],
    }

    # -----------------------------------------------------------------
    # 1. SOURCE IDENTITY & VALIDATION CHECKS (Hard Safety)
    # -----------------------------------------------------------------
    source_identity_status = "VERIFIED"

    # Hash mismatch from context or candidate
    if context.get("hash_mismatch"):
        hard_failures.append("Kaynak hash uyumsuzluğu tespit edildi (context flag).")
        source_identity_status = "MISMATCH"

    if (
        curr_snap.source_sha256
        and cand_snap.source_sha256
        and curr_snap.source_sha256.lower() != cand_snap.source_sha256.lower()
    ):
        hard_failures.append(
            f"Kaynak SHA256 uyumsuzluğu: baseline={curr_snap.source_sha256}, aday={cand_snap.source_sha256}"
        )
        source_identity_status = "MISMATCH"

    if (
        curr_snap.document_id
        and cand_snap.document_id
        and curr_snap.document_id != cand_snap.document_id
    ):
        hard_failures.append(
            f"Belge kimlik uyumsuzluğu: baseline={curr_snap.document_id}, aday={cand_snap.document_id}"
        )
        source_identity_status = "DOCUMENT_MISMATCH"

    # Candidate internal status check
    candidate_status = getattr(candidate, "status", None)
    if candidate_status is None and isinstance(candidate, dict):
        candidate_status = candidate.get("status")

    if candidate_status in ("FAILED", "MISSING", "UNREADABLE", "HASH_MISMATCH", "EXTRACTION_FAILED", "INCOMPLETE"):
        hard_failures.append(f"Aday çıkarım başarısız veya eksik durumda: {candidate_status}")

    # -----------------------------------------------------------------
    # 2. HARD SAFETY CONSTRAINTS (Kritik Regresyonlar)
    # -----------------------------------------------------------------
    # Replacement characters
    cand_rep = cand_snap.replacement_char_count
    curr_rep = curr_snap.replacement_char_count
    if cand_rep > curr_rep or (curr_rep == 0 and cand_rep > 0):
        msg = f"Aday metinde yeni replacement character (\\ufffd) türedi: {curr_rep} -> {cand_rep}"
        hard_failures.append(msg)
        quality_reasons.append(msg)

    # Mojibake
    cand_mojibake = cand_snap.mojibake_sequence_count
    curr_mojibake = curr_snap.mojibake_sequence_count
    if cand_mojibake > curr_mojibake or (curr_mojibake == 0 and cand_mojibake > 0):
        msg = f"Aday metinde yeni mojibake dizisi tespit edildi: {curr_mojibake} -> {cand_mojibake}"
        hard_failures.append(msg)
        quality_reasons.append(msg)

    # Ciddi kalite puanı düşüşü
    if cand_snap.quality_score < (curr_snap.quality_score - QualityGatePolicy.SCORE_SEVERE_DROP_THRESHOLD):
        msg = f"Kalite puanında kabul edilemez düşüş: {curr_snap.quality_score:.3f} -> {cand_snap.quality_score:.3f} (delta: {delta_score:.3f})"
        hard_failures.append(msg)
        quality_reasons.append(msg)

    # Katastrofik metin boyutu kaybı
    if curr_snap.chars > 200 and cand_snap.chars < (curr_snap.chars * QualityGatePolicy.MIN_LENGTH_RETENTION_RATIO):
        msg = f"Metin boyutu kritik derecede çöktü: {curr_snap.chars} -> {cand_snap.chars} karakter"
        hard_failures.append(msg)
        structure_reasons.append(msg)

    # Katastrofik madde (article) kaybı
    if curr_snap.article_count > 5 and cand_snap.article_count < (curr_snap.article_count * QualityGatePolicy.STRUCTURE_CATASTROPHIC_LOSS_RATIO):
        msg = f"Hukuki madde yapısında katastrofik kayıp: {curr_snap.article_count} -> {cand_snap.article_count}"
        hard_failures.append(msg)
        structure_reasons.append(msg)

    # Boş chunk artışı
    if cand_snap.empty_chunk_count > curr_snap.empty_chunk_count and cand_snap.empty_chunk_count > 0:
        msg = f"Chroma aday parçalarında boş chunk tespit edildi: {curr_snap.empty_chunk_count} -> {cand_snap.empty_chunk_count}"
        hard_failures.append(msg)
        structure_reasons.append(msg)

    # Anlamlı chunk kaybı
    if curr_snap.meaningful_chunk_count > 0 and cand_snap.meaningful_chunk_count == 0:
        msg = "Aday çıkarımda anlamlı hukuki parça üretilemedi (0 chunk)."
        hard_failures.append(msg)
        structure_reasons.append(msg)

    # Hard safety failures result in strict REJECT
    if hard_failures:
        reasons.extend(hard_failures)
        return ComparisonResult(
            document_id=doc_id,
            status=ComparisonStatus.REJECT.value,
            current_score=curr_snap.quality_score,
            candidate_score=cand_snap.quality_score,
            delta_score=delta_score,
            quality_reasons=quality_reasons,
            structure_reasons=structure_reasons,
            retrieval_reasons=retrieval_reasons,
            reasons=reasons,
            metrics_diff=metrics_diff,
            hard_failures=hard_failures,
            warnings=warnings,
            metric_deltas=deltas,
            quality_before=curr_snap.to_dict(),
            quality_after=cand_snap.to_dict(),
            source_identity_status=source_identity_status,
            confidence_explanation="Hard safety gates failed: candidate rejected.",
        )

    # -----------------------------------------------------------------
    # 3. EXACT EQUALITY / NO MEASURABLE IMPROVEMENT
    # -----------------------------------------------------------------
    is_identical = (
        curr_snap.quality_score == cand_snap.quality_score
        and curr_snap.glued_token_count == cand_snap.glued_token_count
        and curr_snap.mojibake_sequence_count == cand_snap.mojibake_sequence_count
        and curr_snap.replacement_char_count == cand_snap.replacement_char_count
        and curr_snap.chars == cand_snap.chars
        and curr_snap.chunk_count == cand_snap.chunk_count
        and curr_snap.article_count == cand_snap.article_count
    )
    if is_identical:
        reasons.append("Aday metin mevcut baseline ile birebir aynı; ölçülebilir bir kalite farkı yok.")
        return ComparisonResult(
            document_id=doc_id,
            status=ComparisonStatus.REVIEW.value,
            current_score=curr_snap.quality_score,
            candidate_score=cand_snap.quality_score,
            delta_score=delta_score,
            quality_reasons=["Ölçülebilir iyileşme yok."],
            structure_reasons=[],
            retrieval_reasons=[],
            reasons=reasons,
            metrics_diff=metrics_diff,
            hard_failures=[],
            warnings=["No measurable improvement."],
            metric_deltas=deltas,
            quality_before=curr_snap.to_dict(),
            quality_after=cand_snap.to_dict(),
            source_identity_status=source_identity_status,
            confidence_explanation="Identical metrics: REVIEW required.",
        )

    # -----------------------------------------------------------------
    # 4. STRUCTURE & CHUNK DRIFT CHECKS (Warning / Review Triggers)
    # -----------------------------------------------------------------
    # Moderate structure drift
    if curr_snap.article_count > 5:
        art_diff_ratio = abs(cand_snap.article_count - curr_snap.article_count) / float(curr_snap.article_count)
        if art_diff_ratio > 0.30:
            warnings.append(
                f"Madde sayısında belirgin kayma: {curr_snap.article_count} -> {cand_snap.article_count} (%{art_diff_ratio*100:.1f})"
            )
            structure_reasons.append("Madde sayısı kabul toleransının ötesinde değişti.")

    # Chunk explosion
    if curr_snap.chunk_count > 0:
        chunk_ratio = cand_snap.chunk_count / float(curr_snap.chunk_count)
        chars_ratio = cand_snap.chars / float(max(curr_snap.chars, 1))
        if chunk_ratio >= QualityGatePolicy.CHUNK_EXPLOSION_RATIO and chars_ratio < 1.5:
            warnings.append(
                f"Parça sayısında anormal artış (chunk explosion): {curr_snap.chunk_count} -> {cand_snap.chunk_count}"
            )
            structure_reasons.append("Metin boyutu artmaksızın parça sayısı aşırı çoğaldı.")

    # Duplicate chunks increased
    if cand_snap.duplicate_chunk_count > curr_snap.duplicate_chunk_count:
        warnings.append(
            f"Yinelenen parça sayısı arttı: {curr_snap.duplicate_chunk_count} -> {cand_snap.duplicate_chunk_count}"
        )
        structure_reasons.append("Yinelenen parça sayısında artış var.")

    # Schema version
    if cand_snap.schema_version != "10.0.0":
        warnings.append(f"Aday şema versiyonu güncel değil: {cand_snap.schema_version}")

    # Metadata completeness
    if cand_snap.metadata_completeness < 0.8:
        warnings.append(f"Aday metadata eksiklik içeriyor (bütünlük: {cand_snap.metadata_completeness:.2f})")

    # -----------------------------------------------------------------
    # 5. DIRECTIONAL QUALITY & FINAL DECISION
    # -----------------------------------------------------------------
    is_improved = False

    # Glued tokens
    if curr_snap.glued_token_count > 0 and cand_snap.glued_token_count < curr_snap.glued_token_count:
        quality_reasons.append(
            f"Yapışık sözcük sayısı azaltıldı: {curr_snap.glued_token_count} -> {cand_snap.glued_token_count}"
        )
        is_improved = True

    # Mojibake
    if curr_snap.mojibake_sequence_count > 0 and cand_snap.mojibake_sequence_count < curr_snap.mojibake_sequence_count:
        quality_reasons.append(
            f"Mojibake sayısı azaltıldı: {curr_snap.mojibake_sequence_count} -> {cand_snap.mojibake_sequence_count}"
        )
        is_improved = True

    # Replacement chars
    if curr_snap.replacement_char_count > 0 and cand_snap.replacement_char_count < curr_snap.replacement_char_count:
        quality_reasons.append(
            f"Replacement karakter sayısı azaltıldı: {curr_snap.replacement_char_count} -> {cand_snap.replacement_char_count}"
        )
        is_improved = True

    # Quality score
    if delta_score > 0.01:
        quality_reasons.append(
            f"Kalite puanında artış: {curr_snap.quality_score:.3f} -> {cand_snap.quality_score:.3f}"
        )
        is_improved = True

    score_healthy = delta_score >= -QualityGatePolicy.SCORE_MINOR_DROP_TOLERANCE
    no_new_defects = (
        cand_snap.glued_token_count <= curr_snap.glued_token_count
        and cand_snap.mojibake_sequence_count <= curr_snap.mojibake_sequence_count
        and cand_snap.replacement_char_count <= curr_snap.replacement_char_count
    )

    # READY_TO_COMMIT: Belirgin iyileşme, kabul edilebilir puan, sıfır yeni kusur ve sıfır uyarı
    if is_improved and score_healthy and no_new_defects and not warnings:
        reasons.append("Aday metin mevcut duruma göre daha kaliteli ve güvenli iyileşme gösteriyor.")
        reasons.extend(quality_reasons)
        return ComparisonResult(
            document_id=doc_id,
            status=ComparisonStatus.READY_TO_COMMIT.value,
            current_score=curr_snap.quality_score,
            candidate_score=cand_snap.quality_score,
            delta_score=delta_score,
            quality_reasons=quality_reasons,
            structure_reasons=structure_reasons,
            retrieval_reasons=retrieval_reasons,
            reasons=reasons,
            metrics_diff=metrics_diff,
            hard_failures=[],
            warnings=warnings,
            metric_deltas=deltas,
            quality_before=curr_snap.to_dict(),
            quality_after=cand_snap.to_dict(),
            source_identity_status=source_identity_status,
            confidence_explanation="Candidate improved without regressions or warnings: READY_TO_COMMIT.",
        )

    # Glued token worse
    if cand_snap.glued_token_count > curr_snap.glued_token_count:
        warnings.append(
            f"Yapışık sözcük sayısında artış var: {curr_snap.glued_token_count} -> {cand_snap.glued_token_count}"
        )

    # REVIEW: Karışık sinyaller, yapı kaymaları veya inceleme gerektiren durumlar
    reasons.append(
        "Aday metin ve mevcut metin arasında belirsizlik, yapı kayması ya da insan onayı gerektiren durumlar var."
    )
    if warnings:
        reasons.extend(warnings)
    if quality_reasons:
        reasons.extend(quality_reasons)
    if structure_reasons:
        reasons.extend(structure_reasons)

    return ComparisonResult(
        document_id=doc_id,
        status=ComparisonStatus.REVIEW.value,
        current_score=curr_snap.quality_score,
        candidate_score=cand_snap.quality_score,
        delta_score=delta_score,
        quality_reasons=quality_reasons,
        structure_reasons=structure_reasons,
        retrieval_reasons=retrieval_reasons,
        reasons=reasons,
        metrics_diff=metrics_diff,
        hard_failures=[],
        warnings=warnings,
        metric_deltas=deltas,
        quality_before=curr_snap.to_dict(),
        quality_after=cand_snap.to_dict(),
        source_identity_status=source_identity_status,
        confidence_explanation="Mixed signals or warnings present: REVIEW required.",
    )
