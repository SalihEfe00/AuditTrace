"""K1.3: başlık aramasının eksik terimleri (şapkalı yazımlar, "ödünç") ve 1 000 sınırına takılan sorgular.

mevzuat.gov.tr araması harf duyarlı: "şikayet" "şikâyet"i bulmaz. Sonuçlar mevzuat_search.json'a form|terim
anahtarıyla eklenir (merge.py oradan okur). 1 000'i aşan sorgu yıl aralıklarına bölünür, parçalar birleştirilir.
"""
import json
import re
import sys
from pathlib import Path

import web

NEW_TERMS = [
    "ödünç", "şikâyet", "kâr pay", "kâr", "kâğıt", "usûl", "vekâlet", "vekâletname", "hâmil", "ilâm", "ilân",
    "hâkim", "tâbi", "nâma", "emânet", "kıymetli kâğıt", "mirasçılık", "veraset ilam",
    "ticari elektronik ileti", "ticari iletişim", "fatca", "vergi uyum", "idari yardım", "finansal hesap",
    "otomatik değişim", "işyeri açma", "yardım toplama", "çeyiz", "konut hesab", "değerleme",
]
RESPLIT = ["teblig|ithalat"]
YEARS = [("1920", "1979"), ("1980", "1999"), ("2000", "2009"), ("2010", "2015"), ("2016", "2020"), ("2021", "2023"),
         ("2024", "2026")]
_src = Path(__file__).with_name("mevzuat_search.py").read_text(encoding="utf-8")
import ast  # noqa: E402
FORMS = next(ast.literal_eval(n.value) for n in ast.parse(_src).body
             if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "FORMS")
OUT = Path("mevzuat_search.json")
results = json.loads(OUT.read_text(encoding="utf-8"))
home = web.get("https://www.mevzuat.gov.tr/", cache=False).decode("utf-8", "replace")
token = re.search(r'name="antiforgerytoken" type="hidden" value="([^"]+)"', home).group(1)


def query(form, term, start, y0="", y1=""):
    p = dict(FORMS[form])
    p.update({"AranacakIfade": term, "AranacakYer": "2", "BaslangicTarihi": y0, "BitisTarihi": y1, "MevzuatNo": "",
              "antiforgerytoken": token})
    body = {"draw": 1, "columns": [], "order": [], "start": start, "length": 100,
            "search": {"value": "", "regex": False}, "parameters": p}
    raw = web.get("https://www.mevzuat.gov.tr/Anasayfa/MevzuatDatatable", method="POST", data=json.dumps(body),
                  headers={"Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest"})
    return json.loads(raw)


def fetch(form, term, y0="", y1=""):
    rows, start = [], 0
    while True:
        d = query(form, term, start, y0, y1)
        total = int(d.get("recordsTotal") or d.get("recordsFiltered") or 0)
        if start == 0 and total > 1000:
            if (y0, y1) != ("", ""):
                raise RuntimeError(f"{form}|{term} {y0}-{y1}: {total} > 1000")
            out = []
            for a, b in YEARS:
                out += fetch(form, term, a, b)
            return out
        data = d.get("data") or []
        for it in data:
            rows.append({"code": f"{it.get('mevzuatTur')}.{it.get('mevzuatTertip')}.{it.get('mevzuatNo')}",
                         "title": re.sub("<[^>]+>", "", it.get("mevAdi") or ""),
                         "rg": it.get("resmiGazeteTarihi"), "no": it.get("mevzuatNo")})
        start += len(data)
        if not data or start >= total:
            return rows


jobs = [(f, t) for t in NEW_TERMS for f in FORMS if f"{f}|{t}" not in results] + \
       [tuple(k.split("|")) for k in RESPLIT]
for form, term in jobs:
    key = f"{form}|{term}"
    try:
        results[key] = fetch(form, term)
    except Exception as exc:
        results[key] = [{"error": f"{type(exc).__name__}: {exc}"}]
    OUT.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print(key, len(results[key]), flush=True)
print("bitti", file=sys.stderr)
