"""K1.2: iki atıf turunun numaralı atıflarının tamamını resmî koda çöz.

- İlk tur (cites1.json) eski çıkarıcıyla üretildi; tür adları normalleştirilir ("kararı" → karar, "bakanlar" → bkk,
  "cumhurbaşkanı" → cb_karari). İki tur (kind, number) anahtarıyla birleştirilir (sayılar toplanır) →
  ../cites_all.json (merge.py bunu okur).
- kanun → Kanun, bulunamazsa Mülga, KHK, CBK; khk → KHK; cbk → CBK.
- bkk / cb_karari / karar → Cumhurbaşkanı Kararları türü (mevzuat.gov.tr Bakanlar Kurulu kararlarını da burada
  tutar; "2009/15481" → MevzuatNo 200915481). karar türündeki sayı biçimi uymuyorsa (ör. KVKK "2019/10") çevrimdışı
  kurum katalogları (BDDK, KVKK) denenir.
- kurul_karari → BDDK / KVKK kataloglarında sayı eşleşmesi (çevrimdışı).
Çözülemeyen her atıf gerekçesiyle "çözülemedi" kalır. Çıktı: cites_resolved.json (önceki sonuçlar korunur).
"""
import ast
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import web

_src = Path(__file__).with_name("mevzuat_search.py").read_text(encoding="utf-8")
FORMS = next(ast.literal_eval(n.value) for n in ast.parse(_src).body
             if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "FORMS")
FORMS["mulga"] = {"MevzuatTur": "MulgaKanun", "YonetmelikMevzuatTur": "OsmanliKanunu"}
KIND_NORM = {"kararı": "karar", "kararının": "karar", "bakanlar": "bkk", "cumhurbaşkanı": "cb_karari",
             "tebliği": "tebliğ", "genelgesi": "genelge"}
OUT = Path("cites_resolved.json")
done = {(r["kind"], r["number"]): r for r in json.loads(OUT.read_text(encoding="utf-8"))} if OUT.exists() else {}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def combined():
    rows = {}
    for path in ("cites1.json", "cites2.json"):
        for c in load(path)["numbered"]:
            kind = KIND_NORM.get(c["kind"], c["kind"])
            key = (kind, c["number"])
            r = rows.setdefault(key, {"kind": kind, "number": c["number"], "count": 0, "docs": 0, "names": []})
            r["count"] += c["count"]
            r["docs"] += c["docs"]
            r["names"] = r["names"] or c.get("names", [])
    named = {}
    for path in ("cites1.json", "cites2.json"):
        for c in load(path)["named"]:
            k = c["name"].lower()
            r = named.setdefault(k, {"name": c["name"], "count": 0, "docs": 0})
            r["count"] += c["count"]
            r["docs"] += c["docs"]
    Path("cites_all.json").write_text(json.dumps({"numbered": list(rows.values()), "named": list(named.values())},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
    return list(rows.values())


def catalog_numbers():
    """BDDK ve KVKK kataloglarında karar sayısı → (başlık, adres)."""
    # Yalnızca kurumların kendi karar sayısı biçimleri: BDDK "(04.04.2019 - 8320) …", KVKK "… Tarihli ve 2018/63
    # Sayılı Kararı". RG tarihi / sayısı karar sayısı değildir.
    patterns = (re.compile(r"\(\d{2}\.\d{2}\.\d{4}\s*-\s*(\d{2,5})\)"),
                re.compile(r"\b((?:19|20)\d{2}/\d{1,4})\s+Sayılı\s+Karar", re.I))
    out = {}

    def walk(node, base):
        if isinstance(node, dict):
            if isinstance(node.get("title"), str):
                url = urljoin(base, node.get("url") or "")
                for pat in patterns:
                    for m in pat.finditer(node["title"]):
                        out.setdefault(m.group(1), (node["title"], url))
            for v in node.values():
                walk(v, base)
        elif isinstance(node, list):
            for v in node:
                walk(v, base)

    for path, base in (("bddk_catalog.json", "https://www.bddk.org.tr/"),
                       ("bddk_classified.json", "https://www.bddk.org.tr/"),
                       ("catalog_kvkk_kararlar.json", "https://www.kvkk.gov.tr/")):
        if Path(path).exists():
            walk(load(path), base)
    return out


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


def hit_row(it, mulga=False):
    return {"code": f"{it.get('mevzuatTur')}.{it.get('mevzuatTertip')}.{it.get('mevzuatNo')}",
            "title": re.sub("<[^>]+>", "", it.get("mevAdi") or ""), "mulga": mulga}


def resolve(kind, number, catalog):
    if kind == "kanun":
        for form in ("kanun", "mulga", "khk", "cbk"):
            hits = by_number(form, number)
            if hits:
                return hit_row(hits[0], mulga=form == "mulga")
    elif kind in ("khk", "cbk"):
        hits = by_number(kind, number)
        if hits:
            return hit_row(hits[0])
    elif kind in ("bkk", "cb_karari", "karar"):
        if re.fullmatch(r"\d{2,5}|(?:19|20)\d{2}/\d{1,6}|\d{2}/\d{1,6}", number):
            no = number
            if re.fullmatch(r"\d{2}/\d+", number):  # "88/12944" → 1988
                y, n = number.split("/")
                no = ("19" if int(y) > 30 else "20") + y + n
            no = no.replace("/", "")
            hits = by_number("cbkarar", no)
            if hits:
                return hit_row(hits[0])
    if kind in ("kurul_karari", "karar") and number in catalog:
        title, url = catalog[number]
        return {"code": "", "url": url, "title": title, "mulga": False, "catalog": True}
    return None


if __name__ == "__main__":
    catalog = catalog_numbers()
    todo = [c for c in combined() if c["kind"] in ("kanun", "khk", "cbk", "bkk", "cb_karari", "karar", "kurul_karari")]
    print(len(todo), "numaralı atıf;", sum(1 for c in todo if (c["kind"], c["number"]) in done), "önceden çözülmüş",
          flush=True)
    for c in todo:
        key = (c["kind"], c["number"])
        if key in done and (done[key].get("code") or done[key].get("url") or done[key].get("unresolved")):
            continue
        row = {"kind": c["kind"], "number": c["number"], "names": c["names"][:2], "docs": c["docs"],
               "count": c["count"]}
        try:
            hit = resolve(c["kind"], c["number"], catalog)
            row.update(hit) if hit else row.update(unresolved=(
                "resmî kaynakta numarayla bulunamadı (mevzuat.gov.tr salt değişiklik kanunlarını ayrı tutmaz; "
                "değişiklik kanunuysa metni değiştirdiği kanuna işlenmiştir; değilse K1.7 Resmî Gazete arşivi)"))
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        done[key] = row
        OUT.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1), encoding="utf-8")
        print(c["kind"], c["number"], row.get("code") or row.get("url", "-")[:50], row.get("title", "")[:60],
              "MÜLGA" if row.get("mulga") else "", flush=True)
    print("bitti", len(done))
