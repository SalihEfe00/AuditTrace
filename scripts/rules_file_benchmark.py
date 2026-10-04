# -*- coding: utf-8 -*-
"""Kredi dosyası ölçümü (M10): yapay zekalara yazdırılan dosya vakaları (split_cases.py, bölüm P10) cevap anahtarına
karşı.

Her vaka "--- BELGE n ---" ayraçlı belgelerden oluşan bir kredi dosyasıdır. Arayüzle aynı akış: belgeler türüne göre
tanınır (ayraçta tür yazmaz), alanlar çıkarılır, dosya kuralları (mufettis.rules.credit_file) değerlendirilir; müfettiş
hiçbir soruyu cevaplamaz. İki ölçüm: dosyadaki belge türleri (anahtardaki "kinds") ve kural sonuçları ("expected").

Anahtar: benchmarks/rules/file_case_key.json (vakalar görülmeden prompt türlerinden yazıldı). Holdout yalnızca
--holdout ile ölçülür; geliştirme sırasında çalıştırılmaz.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mufettis.rules.catalog import load_catalog  # noqa: E402
from mufettis.rules.credit_file import CreditFile, evaluate_file  # noqa: E402

KEY = ROOT / "benchmarks" / "rules" / "file_case_key.json"
DOCUMENT_SPLIT = re.compile(r"(?im)^[ \t]*\**[ \t]*-{2,}[ \t]*BELGE[ \t]*\d*[ \t]*-{2,}\**[ \t]*$")


def split_documents(text: str) -> list:
    """Vaka metni → [(ad, belge metni)]; ayraçtan önceki kısa metin (yapay zekanın ara satırı) belge sayılmaz."""
    parts = DOCUMENT_SPLIT.split(text)
    docs = [p.strip("\n") for p in parts[1:] if p.strip()]
    if not docs and text.strip():  # ayraç yazılmamış: tek belge
        docs = [text.strip("\n")]
    return [(f"belge{i}", d + "\n") for i, d in enumerate(docs, 1)]


def run_case(text: str, rules) -> dict:
    credit_file = CreditFile.from_texts(split_documents(text))
    statuses = collections.defaultdict(set)
    for fr in evaluate_file(rules, credit_file):
        statuses[fr.result.rule_id].add(fr.result.status)
    return {"kinds": sorted(d.kind or "(tanınmadı)" for d in credit_file.documents),
            "results": {rid: "/".join(sorted(s)) for rid, s in statuses.items()}}


def score(manifest, cases_dir: Path, key: dict, holdout: bool) -> dict:
    rules = [r for r in load_catalog() if r.document_type == "kredi_dosyasi"]
    tally, by_type, by_ai, by_rule = (collections.Counter(), collections.defaultdict(collections.Counter),
                                      collections.defaultdict(collections.Counter),
                                      collections.defaultdict(collections.Counter))
    failures = []
    for case in manifest:
        if case.get("excluded") or case.get("document_type") != "kredi_dosyasi" or bool(case["holdout"]) != holdout:
            continue
        entry = key["types"].get(case["type"])
        if entry is None:
            tally["anahtarsız"] += 1
            continue
        override = key.get("case_overrides", {}).get(case["id"], {})
        expected = {**entry["expected"], **override.get("expected", {})}
        kinds = override.get("kinds", entry["kinds"])
        text = (cases_dir / ("holdout" if case["holdout"] else "gelistirme") / f"{case['id']}.txt").read_text(
            encoding="utf-8")
        got = run_case(text, rules)
        checks = [("belge türleri", ", ".join(kinds), ", ".join(got["kinds"]))]
        checks += [(rid, want, got["results"].get(rid, "(yok)")) for rid, want in expected.items()]
        for what, want, have in checks:
            verdict = "doğru" if want == have else "yanlış"
            tally[verdict] += 1
            by_type[case["type"]][verdict] += 1
            by_ai[case["ai"]][verdict] += 1
            by_rule[what][verdict] += 1
            if verdict == "yanlış":
                failures.append({"case": case["id"], "type": case["type"], "check": what, "expected": want,
                                 "got": have})
    return {"total": dict(tally), "by_type": {t: dict(c) for t, c in sorted(by_type.items())},
            "by_ai": {a: dict(c) for a, c in sorted(by_ai.items())},
            "by_rule": {r: dict(c) for r, c in sorted(by_rule.items())}, "failures": failures}


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
    t = report["total"]
    print(f"[dosya] kontrol {sum(t.values())}: doğru {t.get('doğru', 0)}, yanlış {t.get('yanlış', 0)}"
          + (f", anahtarsız vaka {t['anahtarsız']}" if t.get("anahtarsız") else ""))
    print("   yapay zekaya göre: " + "; ".join(f"{ai} {c.get('doğru', 0)}/{sum(c.values())}"
                                               for ai, c in report["by_ai"].items()))
    for rule, c in report["by_rule"].items():
        if c.get("yanlış"):
            print(f"   {rule}: {c.get('doğru', 0)}/{sum(c.values())}")
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
