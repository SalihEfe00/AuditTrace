"""K1.7: 'al' kararlı temel metinlerin RG tarih/sayısını değiştirici metinlerin içinden çıkar."""
import json, re, html
from pathlib import Path
import web

dec = json.loads(Path("amend_base_decisions.json").read_text(encoding="utf-8"))["al"]
groups = json.loads(Path("amend_base_missing.json").read_text(encoding="utf-8"))
URL = "https://www.mevzuat.gov.tr/anasayfa/MevzuatFihristDetayIframe?MevzuatTur={tur}&MevzuatNo={no}&MevzuatTertip={tertip}"
PAT = re.compile(r"(\d{1,2}[./]\d{1,2}[./]\d{4})\s*tarihli\s*ve\s*([\d/]+)\s*sayılı\s*([^.;]{0,160})", re.I)
out = {}
for g in groups:
    key = next((k for k in dec if g["base"].startswith(k[:60])), None)
    if not key:
        continue
    found = {}
    for code in sorted([c for c in g["amendments"] if re.fullmatch(r"d+.d+.d+", c)], key=lambda c: int(c.split(".")[2]))[:3]:  # en eski 3 değişiklik
        tur, tertip, no = code.split(".")
        try:
            raw = web.get(URL.format(tur=tur, no=no, tertip=tertip)).decode("utf-8", "replace")
        except Exception as e:
            print("HATA", code, e); continue
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
        text = re.sub(r"\s+", " ", text)
        for m in PAT.finditer(text):
            found.setdefault(f"{m.group(1)} / {m.group(2)}", m.group(3)[:120])
    out[g["base"]] = {"decision": key, "n_amend": len(g["amendments"]), "refs": found}
    print("\n==", g["base"][:100], len(g["amendments"]))
    for k, v in list(found.items())[:6]:
        print("   ", k, "|", v)
Path("k17_basedates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
