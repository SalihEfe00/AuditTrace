# -*- coding: utf-8 -*-
"""Kural kataloğunu hukuki gözden geçirme için okunur bir HTML tabloya döker (M9.7).

Her satır: kural, belge türü, dayanak (madde / fıkra) ve birebir alıntı, kontrol, sonuç sınıfı, yokluk ihlal mi,
istisnalar, geçerlilik, bulgu metni, gerekçe; gözden geçirenin işaretleyeceği "onay / itiraz / not" sütunu boş.

Kullanım:
    python scripts/rules_review_table.py <çıktı.html>
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.rules.catalog import load_catalog  # noqa: E402

CHECK_TEXT = {
    "field_present": "alan dolu mu: {field}",
    "field_value": "{field} ∈ {expected}",
    "date_order": "{before} ≤ {after}",
    "threshold": "{field} eşik",
    "text_pattern": "metinde kalıp: {pattern}",
    "human": "müfettiş: {instruction}",
}


def describe_check(check: dict) -> str:
    text = CHECK_TEXT[check["type"]].format(**{k: check.get(k, "") for k in
                                               ("field", "expected", "before", "after", "pattern", "instruction")})
    cond = check.get("applies_if")
    if not cond:
        return text
    if "above" in cond:
        return text + f"; yalnızca {cond['field']} > {cond['above']:,}".replace(",", ".")
    if cond.get("present"):
        return text + f"; yalnızca {cond['field']} doluysa"
    return text + f"; yalnızca {cond['field']} ∈ {cond['values']}"


def main(out: str) -> int:
    rules = load_catalog()
    rows = []
    for r in rules:
        ref = f"{r.basis.law} md. {r.basis.article}" + (f"/{r.basis.paragraph}" if r.basis.paragraph else "")
        exc = "<br>".join(f"<b>{html.escape(e.id)}</b> ({e.basis.law} md. {e.basis.article}/{e.basis.paragraph}): "
                          f"{' ve '.join(f'{html.escape(n)} ∈ {html.escape(str(list(v)))}' for n, v in e.conditions)}<br>"
                          f"<i>“{html.escape(e.basis.quote)}”</i>" for e in r.exceptions) or "—"
        valid = f"{r.valid_from or '…'} → {r.valid_to or 'bugün'}"
        rows.append(
            f"<tr><td><b>{html.escape(r.id)}</b><br>{html.escape(r.title)}</td><td>{html.escape(r.document_type)}</td>"
            f"<td>{html.escape(ref)}<br><i>“{html.escape(r.basis.quote)}”</i></td>"
            f"<td>{html.escape(describe_check(r.check))}</td><td>{html.escape(r.result_class)} / {html.escape(r.norm_type)}"
            f" / {html.escape(r.obligor)}<br>boş = {'ihlal' if r.absence_is_violation else 'belirsiz'}</td>"
            f"<td>{exc}</td><td>{valid}</td><td>{html.escape(r.finding)}</td><td>{html.escape(r.rationale)}</td>"
            f"<td class=rev></td></tr>")
    page = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kural Kataloğu Gözden Geçirme</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--line:#ddd;--mut:#666}}@media (prefers-color-scheme:dark){{:root{{--bg:#161618;--fg:#eee;--line:#333;--mut:#999}}}}
body{{background:var(--bg);color:var(--fg);font:13px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:19px;margin:0 0 4px}}p{{color:var(--mut);margin:0 0 12px}}.w{{overflow-x:auto}}
table{{border-collapse:collapse;min-width:1200px}}th,td{{border:1px solid var(--line);padding:6px;vertical-align:top;text-align:left}}
th{{position:sticky;top:0;background:var(--bg)}}td.rev{{min-width:140px}}i{{color:var(--mut)}}
</style></head><body><h1>Kural kataloğu: hukuki gözden geçirme (M9.7)</h1>
<p>{len(rules)} kural. Alıntılar korpustaki madde metninden birebir. "Onay / itiraz" sütununa yazıp geri ver; itirazlar kurala işlenir.</p>
<div class=w><table><tr><th>Kural</th><th>Belge</th><th>Dayanak ve alıntı</th><th>Kontrol</th><th>Sınıf / norm / yükümlü</th>
<th>İstisnalar</th><th>Geçerlilik</th><th>Bulgu metni</th><th>Gerekçe</th><th>Onay / itiraz / not</th></tr>
{''.join(rows)}</table></div></body></html>"""
    Path(out).write_text(page, encoding="utf-8")
    print(out, len(rules), "kural")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "rules_review.html"))
