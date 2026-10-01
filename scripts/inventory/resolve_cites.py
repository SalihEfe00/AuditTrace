"""M6.15.1 atıf çözümü: korpusun numarayla andığı kanun / KHK / CBK'yı mevzuat.gov.tr'de numarayla bul.

Yürürlükte bulunamayan numara "Mülga" sekmesinde aranır. Çıktı: cites_resolved.json.
"""
import ast
import json
import re
from pathlib import Path

import web

_src = Path(__file__).with_name("mevzuat_search.py").read_text(encoding="utf-8")
FORMS = next(ast.literal_eval(n.value) for n in ast.parse(_src).body
             if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "FORMS")
FORMS["mulga"] = {"MevzuatTur": "MulgaKanun", "YonetmelikMevzuatTur": "OsmanliKanunu"}
FORM_OF = {"kanun": "kanun", "khk": "khk", "cbk": "cbk"}
OUT = Path("cites_resolved.json")
done = {(r["kind"], r["number"]): r for r in json.loads(OUT.read_text(encoding="utf-8"))} if OUT.exists() else {}
home = web.get("https://www.mevzuat.gov.tr/", cache=False).decode("utf-8", "replace")
token = re.search(r'name="antiforgerytoken" type="hidden" value="([^"]+)"', home).group(1)


def by_number(form, no):
    p = dict(FORMS[form])
    p.update({"AranacakIfade": "", "AranacakYer": "2", "BaslangicTarihi": "", "BitisTarihi": "", "MevzuatNo": no,
              "antiforgerytoken": token})
    body = {"draw": 1, "columns": [], "order": [], "start": 0, "length": 20,
            "search": {"value": "", "regex": False}, "parameters": p}
    raw = web.get("https://www.mevzuat.gov.tr/Anasayfa/MevzuatDatatable", method="POST", data=json.dumps(body),
                  headers={"Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest"})
    return [it for it in (json.loads(raw).get("data") or []) if str(it.get("mevzuatNo")) == no]


cites = json.loads(Path("cites1.json").read_text(encoding="utf-8"))["numbered"]
for c in cites:
    form = FORM_OF.get(c["kind"])
    key = (c["kind"], c["number"])
    if not form or (key in done and done[key].get("code")):
        continue
    row = {"kind": c["kind"], "number": c["number"], "names": c["names"][:2], "docs": c["docs"], "count": c["count"]}
    try:
        hits = by_number(form, c["number"])
        mulga = False
        if not hits and form == "kanun":
            hits, mulga = by_number("mulga", c["number"]), True
        for other in ("khk", "cbk"):  # atıf çıkarıcı "90 sayılı ... Hakkında" KHK'yı kanun sanabilir
            if not hits and form == "kanun":
                hits, mulga = by_number(other, c["number"]), False
        if hits:
            it = hits[0]
            row.update(code=f"{it.get('mevzuatTur')}.{it.get('mevzuatTertip')}.{it.get('mevzuatNo')}",
                       title=re.sub("<[^>]+>", "", it.get("mevAdi") or ""), mulga=mulga)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    done[key] = row
    OUT.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    print(c["kind"], c["number"], row.get("code", "-"), row.get("title", "")[:60], "MÜLGA" if row.get("mulga") else "",
          flush=True)
print("bitti", len(done))
