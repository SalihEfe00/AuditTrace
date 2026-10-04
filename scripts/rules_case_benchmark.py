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
from mufettis.rules.engine import EMPTY, FILLED, NOT_FOUND, NOT_MET, FieldValue, evaluate  # noqa: E402
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
    out.update(results={r.rule_id: r.status for r in results}, asked=sorted(answers), event_date=str(day),
               fields=fields)
    return out


# --- Katmanlı ölçüm (M9.3 / M9.0 eki a): hata alan okumasından mı, kural motorundan mı? -------------------------
_STATES = (FILLED, EMPTY, NOT_FOUND)
UNKNOWN_FIELD = "bilinmiyor"  # "formda yazmıyor": satır hiç yok ya da boş; ikisi de bilinmeyen sayılır
PLACEHOLDER = "(dolu)"


def field_layer(fields: dict, truth: dict) -> list:
    """(alan, beklenen, okunan, sonuç) — sonuç: doğru / güvenli (bulunamadı: müfettişe sorulur) / yanlış."""
    rows = []
    for name, want in truth.items():
        fv = fields.get(name) or FieldValue(NOT_FOUND)
        # durum beklenen alanda durum, değer beklenen (kategori) alanda dolu ise değer karşılaştırılır
        if want == UNKNOWN_FIELD:
            got = fv.state if fv.state != FILLED else str(fv.value)
            verdict = "doğru" if fv.state in (EMPTY, NOT_FOUND) else "yanlış"
            rows.append((name, want, got, verdict))
            continue
        got = fv.state if want in _STATES or fv.state != FILLED else str(fv.value)
        verdict = "doğru" if got == want else ("güvenli" if fv.state == NOT_FOUND else "yanlış")
        rows.append((name, want, got, verdict))
    return rows


def engine_layer(text: str, kind: str, fields: dict, truth: dict, facts: dict, rules) -> dict:
    """Doğru alanlar verildiğinde motorun kararı: okunan alanların üstüne gerçek alan durumları ve bütün müfettiş
    bilgileri yazılır. Dolu ama değeri bilinmeyen alanda okunan değer kalır (okunamadıysa yer tutucu)."""
    fixed = dict(fields)
    for name, want in truth.items():
        if want in (NOT_FOUND, UNKNOWN_FIELD):
            fixed.pop(name, None)
        elif want == EMPTY:
            fixed[name] = FieldValue(EMPTY, None, "anahtar")
        elif want == FILLED:
            if name not in fixed or fixed[name].state != FILLED:
                fixed[name] = FieldValue.filled(PLACEHOLDER, "anahtar")
        else:
            fixed[name] = FieldValue.filled(want, "anahtar")
    # Gerçek alan değeri önce gelir; gerçekte boş alanda müfettişin "yok" beyanı da işlenir (sicil / tapu gibi
    # belgedeki boşluğun tek başına ihlal sayılmadığı kurallar beyanla karar verir).
    fixed = apply_answers(fixed, {k: v for k, v in facts.items() if k not in truth or truth[k] == EMPTY})
    day, _src = document_event_date(kind, fixed)
    # (sonuç, yer tutucu kullanıldı mı): okunamayan değerin yerine konan yer tutucuyla verilemeyen karar alan kaynaklıdır
    return {r.rule_id: (r.status, any(e[2] == PLACEHOLDER for e in r.evidence))
            for r in evaluate(rules, kind, fixed, text=text, event_date=day)}


def score(manifest, cases_dir: Path, key: dict, holdout: bool) -> dict:
    rules = load_catalog()
    report = {"modes": {}, "failures": [], "field_failures": [], "engine_failures": []}
    layers = {"alan": collections.Counter(), "motor": collections.Counter(),
              "alan_by_field": collections.defaultdict(collections.Counter)}
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
                         "no_violation": override.get("no_violation", entry.get("no_violation")),
                         "facts": {**entry.get("facts", {}), **override.get("facts", {})}}
            text = (cases_dir / ("holdout" if case["holdout"] else "gelistirme") / f"{case['id']}.txt").read_text(
                encoding="utf-8")
            got = run_case(text, entry, key.get("defaults", {}), rules, answer=(mode == "müfettiş"))
            if mode == "müfettiş" and got["doc_type"] == entry["doc_type"]:  # katmanlar bir kez, türü doğru vakada
                truth = {**entry.get("fields", {}), **(override or {}).get("fields", {})}
                for name, want, have, verdict in field_layer(got["fields"], truth):
                    layers["alan"][verdict] += 1
                    layers["alan_by_field"][name][verdict] += 1
                    if verdict != "doğru":
                        report["field_failures"].append({"case": case["id"], "type": case["type"], "field": name,
                                                         "expected": want, "got": have, "verdict": verdict})
                facts = {**key.get("defaults", {}).get(got["doc_type"], {}), **entry.get("facts", {})}
                fixed = engine_layer(text, got["doc_type"], got["fields"], truth, facts, rules)
                for rid, want in entry["expected"].items():
                    have, placeholder = fixed.get(rid, ("(yok)", False))
                    verdict = "doğru" if have == want else ("alan kaynaklı" if placeholder else "yanlış")
                    layers["motor"][verdict] += 1
                    if verdict != "doğru":
                        report["engine_failures"].append({"case": case["id"], "type": case["type"], "check": rid,
                                                          "expected": want, "got": have, "verdict": verdict})
            expected = entry["expected"]
            if mode == "belge":  # müfettiş bilgisi gerektiren karar belgeden verilemez: anahtar ayrıca yazar
                expected = {**expected, **entry.get("expected_document", {})}
            checks = [("belge türü", entry["doc_type"], got["doc_type"])]
            checks += [(rid, want, got["results"].get(rid, "(yok)")) for rid, want in expected.items()]
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
    report["layers"] = {"alan": dict(layers["alan"]), "motor": dict(layers["motor"]),
                        "alan_by_field": {f: dict(c) for f, c in sorted(layers["alan_by_field"].items())}}
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
    lay = report["layers"]
    al, mo = lay["alan"], lay["motor"]
    print(f"[katman: alan] {sum(al.values())} alan: doğru {al.get('doğru', 0)}, güvenli (bulunamadı, müfettişe sorulur) "
          f"{al.get('güvenli', 0)}, yanlış {al.get('yanlış', 0)}")
    print(f"[katman: motor] doğru alanlarla {sum(mo.values())} karar: doğru {mo.get('doğru', 0)}, "
          f"alan kaynaklı (değer okunamadı) {mo.get('alan kaynaklı', 0)}, yanlış {mo.get('yanlış', 0)}")
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
