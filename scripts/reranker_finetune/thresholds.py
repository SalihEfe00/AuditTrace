# -*- coding: utf-8 -*-
"""Yeni bir sıralayıcı modeli için eşik seçimi (yalnızca dev) ve eşiklerin sonradan benzetimi.

Girdi: eşiksiz bir ölçüm sonucu —
    MUFETTIS_RERANKER_DIR=<model> python scripts/eval_accuracy.py --split all --min-score none --out r.json
Seçim kuralı: dev'de (doğru − yanlış dayanak − yanlış pozitif) en büyük; eşitlikte doğru sayısı büyük olan.
Eşik, dev'deki bir bulgu puanının hemen altına konur. Holdout ve mühürlü kümeler seçimde kullanılmaz.

Kullanım:
    python scripts/reranker_finetune/thresholds.py r.json                # dev'den eşik öner, tüm bölümlerde göster
    python scripts/reranker_finetune/thresholds.py r.json -4.48 -3.1     # verilen eşikleri benzet
"""
from __future__ import annotations

import json
import sys
from collections import Counter


def simulate(rows, threshold: float) -> Counter:
    """Puanı eşiğin altında kalan bulgu düşer: doğru / yanlış dayanak kaçana, yanlış pozitif doğru negatife döner."""
    out = Counter()
    for r in rows:
        kept = any(s is not None and s >= threshold for s in r["scores"])
        outcome = r["outcome"]
        if not kept:
            outcome = {"correct": "missed", "wrong_basis": "missed", "false_positive": "true_negative"}.get(outcome, outcome)
        out[outcome] += 1
    return out


def objective(c: Counter):
    return c["correct"] - c["wrong_basis"] - c["false_positive"], c["correct"]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    rows = json.load(open(sys.argv[1], encoding="utf-8"))
    splits = sorted({r["split"] for r in rows})
    if len(sys.argv) > 2:
        thresholds = [float(t) for t in sys.argv[2:]]
    else:
        dev = [r for r in rows if r["split"] == "dev"]
        scores = sorted({s for r in dev for s in r["scores"] if s is not None})
        if not scores:
            print("dev'de puanlı bulgu yok")
            return 1
        best = max(scores, key=lambda t: objective(simulate(dev, t)))
        thresholds = [round(best - 0.005, 2)]
        print(f"dev'den seçilen eşik: {thresholds[0]}")
    for t in thresholds:
        parts = []
        for split in splits:
            c = simulate([r for r in rows if r["split"] == split], t)
            parts.append(f"{split}: doğru {c['correct']} yanlış dayanak {c['wrong_basis']} kaçan {c['missed']} "
                         f"YP {c['false_positive']}")
        print(f"eşik {t:6.2f} | " + " | ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
