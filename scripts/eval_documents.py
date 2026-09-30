# -*- coding: utf-8 -*-
"""Belge türü benchmark'ı (M4.5; mufettis/analysis/document_benchmark.py).

İki seviye:
- understanding: belge türü + alan çıkarımı; korpus ve model gerektirmez, saniyeler sürer.
- decision: tam hat (arama, eşleştirme); uygunluk kararı da ölçülür. Veri dizini MUFETTIS_DATA_DIR ile
  seçilir ve bir kopya olmalıdır (BM25 indeksi ve terim istatistiği o dizine yazılabilir); analiz denetim
  kaydı tutulmaz.

Kullanım:
    python scripts/eval_documents.py                                  # understanding, tüm bölümler
    python scripts/eval_documents.py --level decision --split dev --out run.json
    python scripts/eval_documents.py --compare onceki.json
    python scripts/eval_documents.py --check-baseline                 # tabandan kötüleşme varsa çıkış kodu 1
    python scripts/eval_documents.py --write-baseline "not"           # tabanı bilinçli olarak güncelle
    python scripts/eval_documents.py --sealed                          # yalnızca mühürlü bölüm, yalnızca özet
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OMP_NUM_THREADS", "2")

from mufettis.analysis.document_benchmark import (  # noqa: E402
    CASES_DIR, DOCUMENT_BASELINE_FILE, FIELD_KINDS, baseline_level_entry, case_set_fingerprint,
    check_document_baseline, compare_document_results, document_results_to_json, evaluate_document_case,
    load_case_dir, predict_decision, predict_understanding, summarize_documents,
)


def print_summary(summary) -> None:
    for split, m in summary.items():
        fields = ", ".join(f"{k} {m['fields'][k]['tp']}/{m['fields'][k]['tp'] + m['fields'][k]['fn']}"
                           f" (+{m['fields'][k]['fp']} fazla)" for k in FIELD_KINDS)
        line = (f"[{split}] {m['cases']} vaka, {m['documents']} belge | tür {m['type_correct']}/{m['documents']} "
                f"| alan (bulunan/beklenen): {fields}")
        if m["decided"]:
            line += (f" | karar {m['decision_correct']}/{m['decided']}: kaçan kusur {m['missed_defects']}, "
                     f"yanlış alarm {m['false_alarms']}")
        print(line)
    confusion = summary.get("all", {}).get("confusion", {})
    wrong = {e: {p: n for p, n in row.items() if p != e} for e, row in confusion.items()}
    wrong = {e: row for e, row in wrong.items() if row}
    if wrong:
        print("[tür karışıklığı] " + "; ".join(f"{e} -> {row}" for e, row in sorted(wrong.items())))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--level", choices=("understanding", "decision"), default="understanding")
    parser.add_argument("--cases-dir", type=Path, default=CASES_DIR)
    parser.add_argument("--split", default="all", help="dev | holdout | all")
    parser.add_argument("--only", default="", help="vaka kimliği parçası")
    parser.add_argument("--sealed", action="store_true", help="yalnızca mühürlü bölüm (son doğrulama); vaka ayrıntısı basılmaz")
    parser.add_argument("--out", type=Path, help="vaka sonuçlarının yazılacağı JSON")
    parser.add_argument("--compare", type=Path, help="karşılaştırılacak önceki sonuç JSON'u")
    parser.add_argument("--check-baseline", action="store_true", help=f"sonucu {DOCUMENT_BASELINE_FILE.name} ile karşılaştır")
    parser.add_argument("--write-baseline", metavar="NOT", help="sonucu bu seviyenin yeni tabanı olarak yaz")
    args = parser.parse_args()
    if args.sealed and (args.write_baseline or args.check_baseline or args.out or args.compare or args.only):
        parser.error("--sealed yalnızca özet basar; taban, çıktı, karşılaştırma ve --only ile kullanılmaz")
    if args.write_baseline and (args.split != "all" or args.only):
        parser.error("--write-baseline yalnızca --split all ile ve --only olmadan kullanılır")

    cases = load_case_dir(args.cases_dir, include_sealed=args.sealed)
    if args.sealed:
        cases = [c for c in cases if c.split == "sealed"]
    else:
        cases = [c for c in cases if args.split in ("all", c.split) and args.only in c.case_id]
    if not cases:
        print(f"Vaka yok: {args.cases_dir}")
        return 1

    reranker_name = ""
    if args.level == "decision":
        from mufettis.analysis.analysis_presenter import AnalysisPresenter
        from mufettis.rag.engine import LocalRAG

        rag = LocalRAG()
        presenter = AnalysisPresenter(audit_sink=lambda *a, **k: None)
        reranker = presenter.reranker_fn()
        reranker_name = getattr(reranker, "name", "") or "yok"

        def predict(case):
            return predict_decision(case, presenter.execute_analysis(text=case.text, rag_client=rag))
    else:
        predict = predict_understanding

    results = []
    for case in cases:
        start = time.perf_counter()
        result = evaluate_document_case(case, predict(case), seconds=time.perf_counter() - start)
        results.append(result)
        if not args.sealed:
            ok_type = result.expected_types == result.predicted_types
            missing = sum(len(v) for v in result.missing_fields.values())
            extra = sum(len(v) for v in result.extra_fields.values())
            decision = f" karar={result.decision}" if result.decision else ""
            print(f"{'OK ' if ok_type else 'XX '}{case.case_id:32s} tür={','.join(result.predicted_types):28s} "
                  f"alan eksik {missing} fazla {extra}{decision} {result.seconds:.1f}s", flush=True)
    print()
    summary = summarize_documents(results)
    print_summary(summary)

    status = 0
    fingerprint = case_set_fingerprint(cases)
    if args.check_baseline:
        baseline = json.loads(DOCUMENT_BASELINE_FILE.read_text(encoding="utf-8"))
        entry = (baseline.get("levels") or {}).get(args.level)
        if entry is None:
            print(f"\nTabanda '{args.level}' seviyesi yok.")
            return 1
        if args.level == "decision" and entry.get("reranker") != reranker_name:
            print(f"\nUYARI: taban '{entry.get('reranker')}' yeniden sıralayıcısıyla ölçüldü, bu koşu '{reranker_name}'.")
        problems = check_document_baseline(summary, entry, fingerprint)
        print("\nTabandan kötüleşme:" if problems else "\nTaban korunuyor.")
        for line in problems:
            print("  " + line)
        status = 1 if problems else 0
    if args.write_baseline:
        baseline = (json.loads(DOCUMENT_BASELINE_FILE.read_text(encoding="utf-8"))
                    if DOCUMENT_BASELINE_FILE.exists() else {"levels": {}})
        baseline.setdefault("levels", {})[args.level] = baseline_level_entry(
            summary, args.level, fingerprint, args.write_baseline, reranker_name)
        DOCUMENT_BASELINE_FILE.write_text(json.dumps(baseline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\nTaban yazıldı ({args.level}): {DOCUMENT_BASELINE_FILE}")
    if args.compare:
        changes = compare_document_results(json.loads(args.compare.read_text(encoding="utf-8")), results)
        print("\nDeğişen vakalar:" if changes else "\nDeğişen vaka yok.")
        for line in changes:
            print("  " + line)
    if args.out:
        args.out.write_text(json.dumps(document_results_to_json(results), ensure_ascii=False, indent=1), encoding="utf-8")
    return status


if __name__ == "__main__":
    sys.exit(main())
