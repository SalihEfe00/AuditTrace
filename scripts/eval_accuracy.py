# -*- coding: utf-8 -*-
"""Gerçek korpus doğruluk ölçümü (mufettis/analysis/benchmark.py).

Korpusa yazmaz; analiz denetim kaydı (audit) tutulmaz. Veri dizini MUFETTIS_DATA_DIR ile seçilir.

Kullanım:
    python scripts/eval_accuracy.py                          # dev vakaları
    python scripts/eval_accuracy.py --split all --out run.json
    python scripts/eval_accuracy.py --compare onceki.json    # sonucu değişen vakaları listeler
    python scripts/eval_accuracy.py --only dev-card          # kimliği bu metni içeren vakalar
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OMP_NUM_THREADS", "2")

from mufettis.analysis.benchmark import (  # noqa: E402
    CASES_FILE, compare, load_cases, results_to_json, run_benchmark, summarize,
)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=CASES_FILE)
    parser.add_argument("--split", default="dev", help="dev | holdout | all")
    parser.add_argument("--only", default="", help="vaka kimliği parçası")
    parser.add_argument("--out", type=Path, help="sonuçların yazılacağı JSON")
    parser.add_argument("--compare", type=Path, help="karşılaştırılacak önceki sonuç JSON'u")
    args = parser.parse_args()

    from mufettis.analysis.analysis_presenter import AnalysisPresenter
    from mufettis.rag.engine import LocalRAG
    from mufettis.rag.paging import iter_pages

    cases = [c for c in load_cases(args.cases) if args.split in ("all", c.split) and args.only in c.case_id]
    rag = LocalRAG()
    presenter = AnalysisPresenter(audit_sink=lambda *a, **k: None)

    def corpus_metadatas():
        for _ids, _docs, metas in iter_pages(rag.collection, include=("metadatas",)):
            yield from metas

    def show(r):
        mark = {"correct": "OK ", "true_negative": "OK "}.get(r.outcome, "XX ")
        pool = "" if r.retrieval_hit is None else f"havuz={'+' if r.retrieval_hit else '-'}{'' if r.first_hit_rank is None else r.first_hit_rank}/{r.pool_size}"
        print(f"{mark}{r.outcome:14s} {r.case_id:34s} {pool:12s} {r.seconds:4.1f}s {r.findings[:3]}", flush=True)

    results = run_benchmark(cases, lambda text: presenter.execute_analysis(text=text, rag_client=rag),
                            corpus_metadatas(), progress=show)
    print()
    for split, m in summarize(results).items():
        print(f"[{split}] pozitif {m['positives']}: doğru {m['correct']}, yanlış dayanak {m['wrong_basis']}, "
              f"kaçan {m['missed']} | havuz isabeti {m['retrieval_recall']}, isabette seçim {m['selection_accuracy_on_hits']} "
              f"| negatif {m['negatives']}: yanlış pozitif {m['false_positives']}")
    if args.compare:
        changes = compare(json.loads(args.compare.read_text(encoding="utf-8")), results)
        print("\nDeğişen vakalar:" if changes else "\nDeğişen vaka yok.")
        for line in changes:
            print("  " + line)
    if args.out:
        args.out.write_text(json.dumps(results_to_json(results), ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
