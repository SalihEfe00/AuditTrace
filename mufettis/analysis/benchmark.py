# -*- coding: utf-8 -*-
"""
Gerçek korpus doğruluk ölçümü (benchmark).

gold_set.py sentetik kanıtla motorun karar kurallarını sınar; burada ise tüm hat (belge anlama,
planlama, arama, eşleştirme) gerçek korpus üzerinde çalışır. Her değişiklik bu ölçümle kabul
edilir veya reddedilir.

Tasarım:
- Beklenen dayanak görünen etiketle değil meta veriyle tanımlanır (kanun no, madde, önek,
  bölüm, başlık parçası): etiket biçimi değişince ölçüm bozulmaz.
- Beklenen dayanak korpusta yoksa vaka negatif sayılır: o hüküm olmadan üretilen her bulgu
  yanlış dayanaktır. Böylece korpus değişse de vakalar anlamlı kalır.
- Aşamalar ayrı ölçülür: doğru hüküm kanıt havuzuna geldi mi (arama) ve havuzdan doğru hüküm
  seçildi mi (eşleştirme). Hata hangi aşamadaysa düzeltme orada yapılır.
- Vakalar "dev" (geliştirmede bakılan) ve "holdout" (yalnızca doğrulamada bakılan) olarak
  ayrılır; holdout sonucu kurala uydurmanın (overfit) göstergesidir.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from mufettis.analysis.turkish_text import turkish_lower

CASES_FILE = Path(__file__).resolve().parents[2] / "benchmarks" / "real_corpus_cases.json"

# Vaka sonuçları
CORRECT = "correct"            # bulgu var, en az biri beklenen hükme dayanıyor
WRONG_BASIS = "wrong_basis"    # bulgu var, hiçbiri beklenen hükme dayanmıyor
MISSED = "missed"              # beklenen hüküm korpusta var ama bulgu yok
FALSE_POSITIVE = "false_positive"  # bulgu beklenmiyordu (veya dayanağı korpusta yok) ama üretildi
TRUE_NEGATIVE = "true_negative"


def _norm(value: Any) -> str:
    return turkish_lower(str(value or "")).strip()


@dataclass(frozen=True)
class EvidenceSpec:
    """Kabul edilen dayanak. Verilen alanların hepsi eşleşmeli."""
    law_number: str = ""
    article: str = ""
    article_prefix: str = ""   # "" = asıl madde; "geçici" / "ek"
    section: str = ""
    title_contains: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceSpec":
        return cls(**{k: str(v) for k, v in data.items()})

    def matches(self, meta: Dict[str, Any]) -> bool:
        meta = meta or {}
        if self.law_number and str(meta.get("law_number") or "").strip() != self.law_number:
            return False
        if self.article:
            if str(meta.get("article_no") or "").strip() != self.article:
                return False
            # Önek karşılaştırması: "GEÇİCİ"/"GEÇICI"/"geçici" aynı; boş = asıl madde.
            if _norm(meta.get("article_prefix"))[:3] != _norm(self.article_prefix)[:3]:
                return False
        if self.section and str(meta.get("section_no") or "").strip() != self.section:
            return False
        if self.title_contains:
            needle = _norm(self.title_contains)
            from mufettis.analysis.engine import evidence_display_title

            # Görünen ad da bakılır: bazı kayıtlarda başlık alanı yalnızca bir numaradır ("1135").
            titles = (meta.get("declared_title"), meta.get("title"), meta.get("title_normalized"),
                      evidence_display_title(meta))
            if not any(needle in _norm(t) for t in titles):
                return False
        return True


@dataclass
class BenchmarkCase:
    case_id: str
    split: str
    text: str
    expect: List[EvidenceSpec] = field(default_factory=list)
    note: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkCase":
        return cls(
            case_id=data["id"], split=data.get("split", "dev"), text=data["text"],
            expect=[EvidenceSpec.from_dict(e) for e in data.get("expect", [])], note=data.get("note", ""),
        )


@dataclass
class CaseResult:
    case_id: str
    split: str
    outcome: str
    expected_available: bool
    retrieval_hit: Optional[bool]          # yalnızca beklenen hüküm korpustaysa
    first_hit_rank: Optional[int]
    findings: List[str]
    pool_size: int
    seconds: float


def load_cases(path: Path = CASES_FILE) -> List[BenchmarkCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [BenchmarkCase.from_dict(c) for c in data["cases"]]


def _label(meta: Dict[str, Any], fallback: str) -> str:
    parts = [str(meta.get("law_number") or "") or str(meta.get("declared_title") or meta.get("title") or "")[:40]]
    return " ".join(p for p in parts + [fallback] if p)


def evaluate_case(
    case: BenchmarkCase,
    findings_meta: Sequence[Tuple[Dict[str, Any], str]],
    pool_meta: Sequence[Dict[str, Any]],
    expected_available: bool,
    seconds: float = 0.0,
) -> CaseResult:
    """findings_meta: (bulgunun kanıt meta verisi, görünen referans); pool_meta: havuz sırasıyla."""
    matches = lambda meta: any(spec.matches(meta) for spec in case.expect)
    positive = bool(case.expect) and expected_available
    labels = [_label(meta, ref) for meta, ref in findings_meta]
    first_hit = next((i for i, meta in enumerate(pool_meta) if matches(meta)), None) if positive else None
    if positive:
        if not findings_meta:
            outcome = MISSED
        elif any(matches(meta) for meta, _ in findings_meta):
            outcome = CORRECT
        else:
            outcome = WRONG_BASIS
    else:
        outcome = FALSE_POSITIVE if findings_meta else TRUE_NEGATIVE
    return CaseResult(
        case_id=case.case_id, split=case.split, outcome=outcome, expected_available=expected_available,
        retrieval_hit=(first_hit is not None) if positive else None, first_hit_rank=first_hit,
        findings=labels, pool_size=len(pool_meta), seconds=round(seconds, 2),
    )


def available_specs(cases: Iterable[BenchmarkCase], metadatas: Iterable[Dict[str, Any]]) -> Set[EvidenceSpec]:
    """Korpusta en az bir parçası bulunan beklenen dayanaklar (tek geçişte)."""
    pending = {spec for case in cases for spec in case.expect}
    found: Set[EvidenceSpec] = set()
    for meta in metadatas:
        if not pending:
            break
        hit = {spec for spec in pending if spec.matches(meta)}
        found |= hit
        pending -= hit
    return found


def summarize(results: Sequence[CaseResult]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for split in sorted({r.split for r in results}) + ["all"]:
        rs = [r for r in results if split in ("all", r.split)]
        positives = [r for r in rs if r.retrieval_hit is not None]
        negatives = [r for r in rs if r.retrieval_hit is None]
        hits = [r for r in positives if r.retrieval_hit]
        count = lambda items, outcome: sum(1 for r in items if r.outcome == outcome)
        out[split] = {
            "positives": len(positives),
            "correct": count(positives, CORRECT),
            "wrong_basis": count(positives, WRONG_BASIS),
            "missed": count(positives, MISSED),
            "retrieval_recall": round(len(hits) / len(positives), 3) if positives else None,
            "selection_accuracy_on_hits": round(count(hits, CORRECT) / len(hits), 3) if hits else None,
            "negatives": len(negatives),
            "false_positives": count(negatives, FALSE_POSITIVE),
        }
    return out


def run_benchmark(
    cases: Sequence[BenchmarkCase],
    analyze: Callable[[str], Any],
    corpus_metadatas: Iterable[Dict[str, Any]],
    progress: Optional[Callable[[CaseResult], None]] = None,
) -> List[CaseResult]:
    """analyze(text) -> AnalysisExecutionResult (AnalysisPresenter.execute_analysis ile)."""
    import time

    available = available_specs(cases, corpus_metadatas)
    results = []
    for case in cases:
        start = time.perf_counter()
        execution = analyze(case.text)
        if not execution.success or execution.result is None:
            raise RuntimeError(f"{case.case_id}: analiz başarısız: {execution.error_message}")
        findings = [(f.evidence[0].metadata, f.evidence[0].chunk_reference) for f in execution.result.findings if f.evidence]
        pool = [ev.metadata for ev in execution.retrieved_evidence or []]
        result = evaluate_case(
            case, findings, pool, expected_available=any(spec in available for spec in case.expect),
            seconds=time.perf_counter() - start,
        )
        results.append(result)
        if progress:
            progress(result)
    return results


def compare(previous: Sequence[Dict[str, Any]], current: Sequence[CaseResult]) -> List[str]:
    """Önceki koşuya göre sonucu değişen vakalar."""
    before = {r["case_id"]: r for r in previous}
    lines = []
    for r in current:
        old = before.get(r.case_id)
        if old and (old["outcome"] != r.outcome or old.get("retrieval_hit") != r.retrieval_hit):
            lines.append(f"{r.case_id}: {old['outcome']} -> {r.outcome} (havuz: {old.get('retrieval_hit')} -> {r.retrieval_hit})")
    return lines


def results_to_json(results: Sequence[CaseResult]) -> List[Dict[str, Any]]:
    return [asdict(r) for r in results]


__all__ = [
    "CASES_FILE", "CORRECT", "WRONG_BASIS", "MISSED", "FALSE_POSITIVE", "TRUE_NEGATIVE",
    "EvidenceSpec", "BenchmarkCase", "CaseResult", "load_cases", "evaluate_case",
    "available_specs", "summarize", "run_benchmark", "compare", "results_to_json",
]
