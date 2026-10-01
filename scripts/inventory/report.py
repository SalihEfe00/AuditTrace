"""M6.15.4 sunum: birleşik envanter → CSV (depoya) + özet JSON (sayfa için).

Her aday: karar (al / alma / incele / mülga), kaynaklar, tür, başlık, resmî kod / adres, gerekçe, korpus durumu,
indirme durumu (veri kopyasının downloads.json'u), içerik eleği sonucu (screen.json).
Kullanım: MUFETTIS_DATA_DIR=<kopya> python report.py <csv çıktısı> <özet json>
"""
import collections
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent / "records"
inv = json.loads((HERE / "inventory.json").read_text(encoding="utf-8"))
items, unresolved = inv["items"], inv.get("unresolved_citations", [])
src = json.loads(Path(Path(__file__).resolve().parents[2] / "mufettis" / "corpus" / "legislation_sources.json")
                 .read_text(encoding="utf-8"))["entries"]
by_key = {(e.get("code") or e.get("url")): e for e in src}
dl = {}
if os.environ.get("MUFETTIS_DATA_DIR"):
    p = Path(os.environ["MUFETTIS_DATA_DIR"]) / "legislation_downloads" / "downloads.json"
    if p.exists():
        dl = json.loads(p.read_text(encoding="utf-8"))
screen = json.loads((HERE / "screen.json").read_text(encoding="utf-8")) if (HERE / "screen.json").exists() else {}

rows = []
for m in items:
    entry = None
    for k in [m.get("code")] + list(m.get("urls") or []):
        if k and k in by_key:
            entry = by_key[k]
            break
    key = (entry.get("code") or entry.get("url")) if entry else (m.get("code") or (m.get("urls") or [""])[0])
    rec = dl.get(key) or {}
    sc = screen.get(key) or {}
    rows.append({
        "karar": m["decision"], "gerekçe": m["reason"], "kaynaklar": ", ".join(m["sources"]),
        "tür": (entry or {}).get("instrument") or m.get("kind", ""), "başlık": m["title"],
        "kod": m.get("code", ""), "adres": (entry or {}).get("url") or (m.get("urls") or [""])[0],
        "kurum": (entry or {}).get("issuer", ""), "grup": (entry or {}).get("group", ""),
        "öncelik": (entry or {}).get("priority", ""), "korpusta": "evet" if m.get("in_corpus") else "",
        "indirme": rec.get("status", ""), "indirme_notu": rec.get("reason", ""),
        "banka_cümlesi": sc.get("bank", ""), "yükümlülük_cümlesi": sc.get("obligation", ""),
        "atıf": "; ".join(m.get("citations") or []),
    })
order = {"al": 0, "mülga": 1, "incele": 2, "alma": 3}
rows.sort(key=lambda r: (order.get(r["karar"], 9), r["kaynaklar"], r["başlık"]))
out_csv = Path(sys.argv[1])
with out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
    w.writeheader()
    w.writerows(rows)
summary = {
    "toplam": len(rows),
    "karar": collections.Counter(r["karar"] for r in rows),
    "kaynak_al": collections.Counter(r["kaynaklar"].split(", ")[0] for r in rows if r["karar"] == "al"),
    "tür_al": collections.Counter(r["tür"] for r in rows if r["karar"] == "al"),
    "gerekçe_alma": collections.Counter(r["gerekçe"][:70] for r in rows if r["karar"] == "alma").most_common(25),
    "indirme_al": collections.Counter(r["indirme"] or "-" for r in rows if r["karar"] == "al"),
    "çözülemeyen_atıf": len(unresolved),
}
Path(sys.argv[2]).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False)[:1500])
