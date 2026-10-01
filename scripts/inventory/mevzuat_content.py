"""mevzuat.gov.tr İÇERİK araması (AranacakYer=3): metninde banka hükmü geçen her yürürlükteki mevzuat.

Başlıkla bulunamayan (ör. bir vergi tebliğinin bankalara yükümlülük getiren maddesi) belgeleri yakalar.
1000 sonuç sınırını aşan sorgu yıl aralıklarına bölünür. Çıktı: mevzuat_content.json {form|terim|yıllar: [kayıt]}.
"""
import json
import re
import sys
from pathlib import Path

import web
import ast

_src = Path(__file__).with_name("mevzuat_search.py").read_text(encoding="utf-8")
FORMS = next(ast.literal_eval(n.value) for n in ast.parse(_src).body
             if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "FORMS")  # aynı tür tanımları

# Tek kelime kök / alt dizgi araması yapar: "banka" bankalar / bankaya / katılım bankasını da kapsar. Çok kelimeli
# ifade VEYA olarak aranır ("ödeme kuruluşu" = ödeme VEYA kuruluşu; kanunlarda 520 sonuç), tırnaklı ifade yalnızca
# tam biçimi bulur; bu yüzden yalnızca tek kelimeler. Kredi / ödeme / e-para kuruluşu metinleri "banka"yı da anar.
TERMS = ["banka", "mevduat"]
OUT = Path("mevzuat_content.json")
results = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
home = web.get("https://www.mevzuat.gov.tr/", cache=False).decode("utf-8", "replace")
token = re.search(r'name="antiforgerytoken" type="hidden" value="([^"]+)"', home).group(1)


def query(form, term, start, y0="", y1=""):
    p = dict(FORMS[form])
    p.update({"AranacakIfade": term, "AranacakYer": "3", "BaslangicTarihi": y0, "BitisTarihi": y1, "MevzuatNo": "",
              "antiforgerytoken": token})
    body = {"draw": 1, "columns": [], "order": [], "start": start, "length": 100,
            "search": {"value": "", "regex": False}, "parameters": p}
    raw = web.get("https://www.mevzuat.gov.tr/Anasayfa/MevzuatDatatable", method="POST", data=json.dumps(body),
                  headers={"Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest"})
    return json.loads(raw)


def fetch(form, term, y0="", y1=""):
    key = f"{form}|{term}|{y0}-{y1}"
    if key in results and not any("error" in r for r in results[key]):
        return
    rows, start = [], 0
    try:
        while True:
            d = query(form, term, start, y0, y1)
            total = int(d.get("recordsTotal") or d.get("recordsFiltered") or 0)
            if start == 0 and total > 1000 and (y0, y1) == ("", ""):
                for a, b in [("1920", "1979"), ("1980", "1999"), ("2000", "2009"), ("2010", "2015"), ("2016", "2020"),
                             ("2021", "2023"), ("2024", "2026")]:
                    fetch(form, term, a, b)
                return
            data = d.get("data") or []
            for it in data:
                rows.append({"code": f"{it.get('mevzuatTur')}.{it.get('mevzuatTertip')}.{it.get('mevzuatNo')}",
                             "title": re.sub("<[^>]+>", "", it.get("mevAdi") or ""), "rg": it.get("resmiGazeteTarihi")})
            start += len(data)
            if not data or start >= total or start >= 1000:
                if total > 1000:
                    rows.append({"truncated": total})
                break
    except Exception as exc:
        rows = [{"error": f"{type(exc).__name__}: {exc}"}]
    results[key] = rows
    OUT.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print(key, len(rows), flush=True)


for term in TERMS:
    for form in FORMS:
        fetch(form, term)
print("bitti", len(results), file=sys.stderr)
