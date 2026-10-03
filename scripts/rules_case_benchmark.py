# -*- coding: utf-8 -*-
"""Kural ölçümü (M9.4): kullanıcının vakaları (scripts/split_cases.py çıktısı) cevap anahtarına karşı.

Her vaka için arayüzle aynı akış: belge türü tanınır → alanlar çıkarılır → olay tarihi belgeden seçilir → kurallar
değerlendirilir. İki ölçüm:

- belge: yalnızca belge metni (müfettiş hiçbir soruyu cevaplamadı)
- müfettiş: sistemin SORDUĞU sorular anahtardaki "facts" ile cevaplanır (sorulmayan alana cevap verilmez); cevaplar
  yeni soru doğurabildiği için en çok üç tur

Anahtar: benchmarks/rules/case_key.json (tür → beklenen kural sonuçları). Holdout yalnızca --holdout ile ölçülür;
geliştirme sırasında çalıştırılmaz.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mufettis.rules.catalog import load_catalog  # noqa: E402
from mufettis.rules.doctype import detect_collateral_type  # noqa: E402
from mufettis.rules.engine import NOT_MET, evaluate  # noqa: E402
from mufettis.rules.event_date import document_event_date  # noqa: E402
from mufettis.rules.fields_kefalet import extract_kefalet_fields  # noqa: E402
from mufettis.rules.fields_teminat import DOCUMENT_LABELS, extract_teminat_fields  # noqa: E402
from mufettis.rules.review import apply_answers, pending_questions  # noqa: E402

KEY = ROOT / "benchmarks" / "rules" / "case_key.json"


def run_case(text: str, key_entry: dict, defaults: dict, rules, answer: bool) -> dict:
    kind = detect_collateral_type(text).document_type
    out = {"doc_type": kind, "results": {}, "asked": [], "event_date": None}
    if kind == "kefalet_sozlesmesi":
        fields = extract_kefalet_fields(text)
    elif kind in DOCUMENT_LABELS:
        fields = extract_teminat_fields(kind, text)
    else:
        return out
    facts = {**defaults.get(kind, {}), **key_entry.get("facts", {})}
    answers = {}
    for _ in range(3 if answer else 1):
        current = apply_answers(fields, answers)
        day, _src = document_event_date(kind, current)
        results = evaluate(rules, kind, current, text=text, event_date=day)
        if not answer:
            break
        new = {q.field: facts[q.field] for q in pending_questions(rules, results, current)
               if q.field in facts and q.field not in answers}
        if not new:
            break
        answers.update(new)
    out.update(results={r.rule_id: r.status for r in results}, asked=sorted(answers), event_date=str(day))
    return out


def score(manifest, cases_dir: Path, key: dict, holdout: bool) -> dict:
    rules = load_catalog()
    report = {"modes": {}, "failures": []}
    for mode in ("belge", "müfettiş"):
        tally = collections.Counter()
        by_type = collections.defaultdict(collections.Counter)
        by_ai = collections.defaultdict(collections.Counter)
        for case in manifest:
            if case.get("excluded") or bool(case["holdout"]) != holdout:
                continue
            entry = key["types"].get(case["type"])
            if entry is None:
                tally["anahtarsız"] += 1
                continue
            override = key.get("case_overrides", {}).get(case["id"])
            if override:  # türüne uymayan vaka: beklenen sonuç vakanın gerçek içeriğinden
                entry = {**entry, "expected": {**entry["expected"], **override.get("expected", {})},
                         "no_violation": override.get("no_violation", entry.get("no_violation"))}
            text = (cases_dir / ("holdout" if case["holdout"] else "gelistirme") / f"{case['id']}.txt").read_text(
                encoding="utf-8")
            got = run_case(text, entry, key.get("defaults", {}), rules, answer=(mode == "müfettiş"))
            checks = [("belge türü", entry["doc_type"], got["doc_type"])]
            checks += [(rid, want, got["results"].get(rid, "(yok)")) for rid, want in entry["expected"].items()]
            if entry.get("no_violation"):
                bad = sorted(r for r, s in got["results"].items() if s == NOT_MET)
                checks.append(("ihlal yok", "-", ", ".join(bad) or "-"))
            for what, want, have in checks:
                ok = want == have
                tally["doğru" if ok else "yanlış"] += 1
                by_type[case["type"]]["doğru" if ok else "yanlış"] += 1
                by_ai[case["ai"]]["doğru" if ok else "yanlış"] += 1
                if not ok:
                    report["failures"].append({"mode": mode, "case": case["id"], "type": case["type"], "check": what,
                                               "expected": want, "got": have, "asked": got["asked"],
                                               "event_date": got["event_date"]})
        report["modes"][mode] = {"total": dict(tally), "by_type": {t: dict(c) for t, c in sorted(by_type.items())},
                                 "by_ai": {a: dict(c) for a, c in sorted(by_ai.items())}}
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cases_dir", help="split_cases.py çıktı klasörü (manifest.json burada)")
    ap.add_argument("--holdout", action="store_true", help="gizli sınav setini ölç (yalnızca son ölçümde)")
    ap.add_argument("--out", help="ayrıntılı rapor (JSON)")
    a = ap.parse_args(argv)
    cases_dir = Path(a.cases_dir)
    manifest = json.loads((cases_dir / "manifest.json").read_text(encoding="utf-8"))
    key = json.loads(KEY.read_text(encoding="utf-8"))
    report = score(manifest, cases_dir, key, a.holdout)
    for mode, m in report["modes"].items():
        t = m["total"]
        n = t.get("doğru", 0) + t.get("yanlış", 0)
        print(f"[{mode}] kontrol {n}: doğru {t.get('doğru', 0)}, yanlış {t.get('yanlış', 0)}"
              + (f", anahtarsız vaka {t['anahtarsız']}" if t.get("anahtarsız") else ""))
        print("   yapay zekaya göre: " + "; ".join(f"{ai} {c.get('doğru', 0)}/{c.get('doğru', 0) + c.get('yanlış', 0)}"
                                                for ai, c in m["by_ai"].items()))
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
