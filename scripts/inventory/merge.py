"""M6.15.4 birleşik envanter: kataloglar + mevzuat.gov.tr (başlık / içerik) + atıflar + şube haritası → inventory.json.

Tekilleştirme resmî koda (mevzuat.gov.tr Tür.Tertip.No), yoksa normalize başlığa göre. Kaynaklardan biri "al" diyorsa
kayıt alınır; gerekçeler birleşir. Korpusta olanlar in_corpus ile işaretlenir.
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mufettis.corpus.citations import turkish_lower  # noqa: E402

import catalog_rules  # noqa: E402
import search_rules  # noqa: E402

HERE = Path(__file__).parent / "records"
RANK = {"al": 3, "incele": 2, "mülga": 1, "alma": 0}


def norm(t):
    t = turkish_lower(re.sub(r"\s+", " ", t or ""))
    t = re.sub(r"[“”\"'’`´.,;:()\[\]\-–/]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def code_from_url(u):
    u = u or ""
    m = re.search(r"MevzuatKod=(\d+\.\d+\.\d+)", u)
    if m:
        return m.group(1)
    q = dict(re.findall(r"(MevzuatNo|MevzuatTur|MevzuatTertip)=(\d+)", u, re.I))
    q = {k.lower(): v for k, v in q.items()}
    if {"mevzuatno", "mevzuattur", "mevzuattertip"} <= set(q):
        return f"{q['mevzuattur']}.{q['mevzuattertip']}.{q['mevzuatno']}"
    return ""


def load(name):
    p = HERE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


cands = []


def add(source, title, url="", code="", kind="", decision="incele", reason="", **extra):
    cands.append({"source": source, "title": re.sub(r"\s+", " ", title).strip(), "url": url, "code": code or code_from_url(url),
                  "kind": kind, "decision": decision, "reason": reason, **extra})


# 1) BDDK
from urllib.parse import urljoin  # noqa: E402

for r in load("bddk_classified.json") or []:
    add("bddk", r["title"], urljoin("https://www.bddk.org.tr/", r["url"]), kind=r["list_name"], decision=r["decision"],
        reason=r["reason"])
# 2) MASAK
for r in load("masak_candidates.json") or []:
    add("masak", r["title"], r["url"], r.get("code", ""), r["kind"], r["decision"], r["reason"])
# 3) Diğer kurum katalogları
for src in ("tcmb", "tbb", "tmsf", "kvkk", "seddk"):
    for r in load(f"catalog_{src}.json") or []:
        d, why = catalog_rules.classify(src, r["title"], r.get("page", ""))
        add(src, r["title"], r["url"], kind=r.get("page", "").rstrip("/").split("/")[-1], decision=d, reason=why)
# 3b) TKBB (kararı katalogda), KVKK Kurul kararları, elle eklenen kaynak sınıfları
for r in load("catalog_tkbb.json") or []:
    add("tkbb", r["title"], r["url"], kind=r["page"], decision=r["decision"], reason=r["reason"])
for r in load("catalog_kvkk_kararlar.json") or []:
    if r["financial"]:
        add("kvkk", r["title"], r["url"], kind="kurul kararı", decision="al",
            reason="KVKK Kurulunun banka / finans kuruluşu hakkındaki kararı; şubede kişisel veri işlemeye emsal")
    else:
        add("kvkk", r["title"], r["url"], kind="kurul kararı", decision="alma",
            reason="banka / finans dışı veri sorumlusu hakkında karar")
for r in load("manual_candidates.json") or []:
    add(r["source"], r["title"], r.get("url", ""), r.get("code", ""), r.get("kind", ""), r["decision"], r["reason"])
# 4) mevzuat.gov.tr başlık araması
hits = collections.defaultdict(set)
for key, rows in (load("mevzuat_search.json") or {}).items():
    for r in rows:
        if "code" in r:
            hits[r["code"]].add(key)
search_rows = {}
for key, rows in (load("mevzuat_search.json") or {}).items():
    for r in rows:
        if "code" in r and r["code"] not in search_rows:
            search_rows[r["code"]] = r
# 5) içerik araması: metninde banka / mevduat hükmü geçenler
content = collections.defaultdict(set)
for key, rows in (load("mevzuat_content.json") or {}).items():
    for r in rows:
        if "code" in r:
            content[r["code"]].add(key.split("|")[1])
            search_rows.setdefault(r["code"], r)
FORM_OF_TUR = {"1": "kanun", "4": "khk", "19": "cbk", "20": "cbkarar", "3": "yonetmelik", "7": "yonetmelik",
               "8": "yonetmelik", "10": "yonetmelik", "21": "yonetmelik", "9": "teblig", "2": "tuzuk", "22": "genelge"}
CONTENT_TERMS = ("banka", "mevduat")
content_raw = load("mevzuat_content.json") or {}


def _bucket_complete(form, term, year):
    """(tür, terim, yıl) için içerik araması eksiksiz mi: hata yok, 1000 sınırında kesilmemiş."""
    whole = content_raw.get(f"{form}|{term}|-")
    if whole is not None:
        return not any("error" in r or "truncated" in r for r in whole)
    for key, rows in content_raw.items():
        f, t, span = key.split("|")
        if f == form and t == term and span != "-":
            y0, y1 = span.split("-")
            if y0 and int(y0) <= year <= int(y1):
                return not any("error" in r or "truncated" in r for r in rows)
    return False


def content_covered(code, rg):
    form = FORM_OF_TUR.get(code.split(".")[0])
    m = re.search(r"(\d{4})", str(rg or ""))
    if not form or not m:
        return False
    return all(_bucket_complete(form, t, int(m.group(1))) for t in CONTENT_TERMS)


for code, r in search_rows.items():
    d, why = search_rules.decide(code, r["title"])
    if code in content and d in ("incele", "alma") and code.split(".")[0] != "8":
        d, why = "al", f"metninde '{', '.join(sorted(content[code]))}' hükmü geçiyor (mevzuat.gov.tr içerik araması)"
    elif d == "incele" and code not in content and content_covered(code, r.get("rg")):
        d, why = "alma", ("metninde banka / mevduat geçmiyor (mevzuat.gov.tr içerik araması, eksiksiz sorgu)")
    kind = search_rules.TUR.get(code.split(".")[0], code.split(".")[0])
    add("mevzuat.gov.tr", r["title"], f"https://www.mevzuat.gov.tr/mevzuat?MevzuatNo={code.split('.')[2]}"
        f"&MevzuatTur={code.split('.')[0]}&MevzuatTertip={code.split('.')[1]}", code, kind, d, why, rg=r.get("rg"),
        search_hits=sorted(hits.get(code, [])))

# 5b) Numarayla çözülen atıflar (resolve_cites.py)
for r in load("cites_resolved.json") or []:
    # Katalogdan çözülen BDDK / KVKK kurul kararları (kodsuz) katalog satırlarında kendi gerekçeleriyle zaten var;
    # atıf almaları tek kuruluşa özgü izin kararlarını "al"a çevirmez.
    if r.get("code"):
        d, why = search_rules.decide(r["code"], r["title"])
        if r.get("mulga"):
            d, why = "mülga", "yürürlükten kalkmış; korpusta atıf var, olay tarihi eski belgeler için"
        add("atıf", r["title"], "", r["code"], search_rules.TUR.get(r["code"].split(".")[0], ""), d, why)
# 5c) Korpustaki belgeler (listede kendi satırları olsun)
for r in load("corpus_now.json") or []:
    if r["status"] in ("active", "indexed", "repaired_and_indexed") and r.get("title") and not r["title"].isdigit():
        key = str(r.get("listed_document_key") or "").replace("mevzuat:", "")
        add("korpus", r["title"], r.get("source_url") or "", key if re.fullmatch(r"\d+\.\d+\.\d+", key) else "",
            "", "al", "zaten korpusta")

# Değiştirici metinler ayrı belge olarak alınmaz (kaynağı ne olursa olsun)
for c in cands:
    if c["decision"] in ("al", "incele") and c["source"] != "korpus" and search_rules.amending(c["title"]):
        c["decision"], c["reason"] = "alma", ("değiştirici metin; değişiklikler değiştirdiği metnin güncel birleşik "
                                              "hâlinde ve önceki metinlerinde (M7.3) yer alıyor")

# Başlıktan koda eşleme (kodsuz katalog kayıtlarına resmî kod bağla)
by_title = {}
for c in cands:
    if c["code"]:
        by_title.setdefault(norm(c["title"]), c["code"])
for c in cands:
    if not c["code"]:
        c["code"] = by_title.get(norm(c["title"]), "")

# 6) Atıflar (M6.15.1): numaralı kanun / KHK / CBK → kod; adlı → başlık
# K1.2: iki atıf turunun birleşimi (resolve_cites2.py üretir; ilk turun tür adları normalleştirilmiş)
cites = load("cites_all.json") or load("cites1.json") or {"numbered": [], "named": []}
TUR_OF = {"kanun": "1", "khk": "4", "cbk": "19", "bkk": "20", "cb_karari": "20", "karar": "20"}
nums = collections.defaultdict(list)
resolved_codes = [r["code"] for r in (load("cites_resolved.json") or []) if r.get("code")]
for code in list(search_rows) + resolved_codes:
    t, _, no = code.split(".")
    if code not in nums[(t, no)]:
        nums[(t, no)].append(code)


def cite_no(kind, number):
    """Atıftaki sayı → mevzuat.gov.tr MevzuatNo ("2009/15481" → 200915481, "88/12944" → 198812944)."""
    if TUR_OF.get(kind) == "20" and "/" in number:
        y, n = number.split("/", 1)
        if len(y) == 2:
            y = ("19" if int(y) > 30 else "20") + y
        return y + n
    return number
cite_notes = collections.defaultdict(list)
unresolved = []
for c in cites["numbered"]:
    tur = TUR_OF.get(c["kind"])
    if not tur:
        continue
    codes = nums.get((tur, cite_no(c["kind"], c["number"])))
    note = f"korpusta {c['docs']} belgede {c['count']} atıf"
    if codes:
        for code in codes:
            cite_notes[code].append(note)
    else:
        unresolved.append({"kind": c["kind"], "number": c["number"], "names": c["names"][:2], "note": note})
titles = {norm(c["title"]): c for c in cands}
STOP = {"ve", "ile", "ilişkin", "hakkında", "dair", "bu", "bir", "için", "olan", "sayılı", "tarihli"}


ROOTS = {"yönetmeliği": "yönetmelik", "yönetmeliğin": "yönetmelik", "yönetmeliğinin": "yönetmelik",
         "tebliği": "tebliğ", "tebliğin": "tebliğ", "tebliğinin": "tebliğ", "kanunu": "kanun", "kanununun": "kanun",
         "kararı": "karar", "kararının": "karar", "genelgesi": "genelge", "rehberi": "rehber", "esasları": "esaslar"}


def words(t):
    return {ROOTS.get(w, w) for w in norm(t).split() if len(w) > 2 and w not in STOP}


title_words = [(words(c["title"]), c) for c in cands if c["decision"] != "alma" or c["source"] == "mevzuat.gov.tr"]


def fuzzy(name):
    """Adlı atıf ↔ aday başlığı: kelime kümesi benzerliği (Jaccard) ≥ 0,8 olan en iyi aday."""
    w = words(name)
    if len(w) < 3:
        return None
    best, score = None, 0.0
    for tw, cand in title_words:
        if tw:
            s = len(w & tw) / len(w | tw)
            if s > score:
                best, score = cand, s
    return best if score >= 0.8 else None


named_resolved = load("named_resolved.json") or {}
for c in cites["named"]:
    hit = titles.get(norm(c["name"])) or fuzzy(c["name"])
    if not hit and (named_resolved.get(c["name"]) or {}).get("code"):
        r = named_resolved[c["name"]]
        d, why = search_rules.decide(r["code"], r["title"])
        add("atıf", r["title"], "", r["code"], search_rules.TUR.get(r["code"].split(".")[0], ""), d, why)
        hit = cands[-1]
    if hit:
        cite_notes[hit["code"] or norm(hit["title"])].append(f"korpusta {c['docs']} belgede {c['count']} atıf")
    elif c["docs"] >= 1:
        unresolved.append({"kind": "adlı", "name": c["name"], "note": f"korpusta {c['docs']} belgede {c['count']} atıf"})

# 7) Tekilleştir
merged = {}
for c in cands:
    k = c["code"] or "t:" + norm(c["title"])
    m = merged.get(k)
    if not m:
        merged[k] = {**c, "sources": [c["source"]], "reasons": [f"{c['source']}: {c['reason']}"], "urls": [c["url"]]}
        continue
    if c["source"] not in m["sources"]:
        m["sources"].append(c["source"])
    m["reasons"].append(f"{c['source']}: {c['reason']}")
    if c["url"] and c["url"] not in m["urls"]:
        m["urls"].append(c["url"])
    if RANK[c["decision"]] > RANK[m["decision"]]:
        m["decision"], m["reason"] = c["decision"], c["reason"]
for k, m in merged.items():
    notes = cite_notes.get(m["code"] or "") or cite_notes.get(norm(m["title"])) or []
    if notes:
        m["citations"] = notes
        if m["decision"] == "incele":
            m["decision"], m["reason"] = "al", "korpustaki mevzuat bu belgeye atıf yapıyor (" + notes[0] + ")"

# 7b) Şube faaliyet haritası güvenlik ağı: bütün işverenlere / herkese uygulanan genel metinler (İSG, İş Kanunu)
# metinlerinde "banka" geçmediği için içerik ölçütüyle elenebilir; haritadaki bir gereksinimle eşleşen ve yalnızca
# içerik / belirsizlik nedeniyle elenen asıl metin alınır.
SOFT = ("metninde banka / mevduat geçmiyor", "başlık kapsamı belirsiz")
# Asıl metin (kanun, yönetmelik, tüzük); tebliğ serileri ve tek seferlik kararlar güvenlik ağına girmez.
BASE_ACT = re.compile(r"(KANUNU?|YÖNETMELİ(K|Ğİ)|TÜZÜ(K|ĞÜ)|Kanunu?|Yönetmeli(k|ği)|Tüzü(k|ğü))$")
for req in load("branch_map.json") or []:
    rx = re.compile(req["pattern"], re.I)
    for m in merged.values():
        if m["decision"] in ("alma", "incele") and m["reason"].startswith(SOFT) and rx.search(m["title"])                 and BASE_ACT.search(m["title"].strip()) and not search_rules.amending(m["title"]):
            m["decision"], m["reason"] = "al", f"şube faaliyet haritası: {req['activity']} ({req['label']})"
            m["reasons"].append("harita: " + m["reason"])

# 8) Korpus eşleşmesi
corpus = load("corpus_now.json") or []
ckeys, ctitles = set(), set()
for r in corpus:
    if r["status"] in ("superseded", "quarantined", "excluded"):
        continue
    for v in (r.get("listed_document_key"), r.get("declared_work_id"), r.get("source_url")):
        if v:
            ckeys.add(str(v).replace("mevzuat:", ""))
    ctitles.add(norm(r.get("title")))
for m in merged.values():
    m["in_corpus"] = bool((m["code"] and m["code"] in ckeys) or any(u in ckeys for u in m["urls"] if u)
                          or norm(m["title"]) in ctitles
                          or any((re.search(r"DokumanGetir/(\d+)", u or "") or [None, None])[1] in ctitles
                                 for u in m["urls"]))

inv = sorted(merged.values(), key=lambda m: (-RANK[m["decision"]], m["sources"][0], m["title"]))
# 7c) K1.7: değiştirici metnin temel metni envanterde (resmî kodla / adresle) var mı? Yoksa "değiştirici → alma"
# kuralının varsayımı (birleşik güncel metin resmî kaynakta) tutmuyor; temel metin + değişiklikler birlikte gerekir.
LOC = [("kararnamesinde", "kararnamesi"), ("kararında", "kararı"), ("kararda", "karar"), ("yönetmeliğinde", "yönetmeliği"),
       ("yönetmelikte", "yönetmelik"), ("tebliğinde", "tebliği"), ("tebliğde", "tebliğ"), ("esaslarında", "esasları"),
       ("esaslarda", "esaslar"), ("genelgesinde", "genelgesi"), ("genelgede", "genelge"), ("kanununda", "kanunu"),
       ("kanunda", "kanun"), ("talimatında", "talimatı"), ("talimatta", "talimat"), ("tüzükte", "tüzük")]
have = {norm(m["title"]) for m in merged.values() if (m.get("code") or m.get("url"))}
base_missing = collections.defaultdict(list)
for m in merged.values():
    t = norm(m["title"])
    mm = re.search(r"^(.*?)\b(\w+) (?:bazı maddelerinde |eki? |ekinde |eklerinde )?değişiklik yapılmasına", t)
    if not mm or not search_rules.amending(m["title"]):
        continue
    word = mm.group(2)
    base_word = next((b for a, b in LOC if word == a), None)
    if not base_word:
        continue
    base = (mm.group(1) + base_word).strip()
    base = re.sub(r"^\d+ sayılı ", "", base)
    # Tam eşleşme her uzunlukta ("çek kanunu"); sonek eşleşmesi yalnızca uzun başlıklarda (kısa genel adlar yanıltır)
    if base not in have and not any(h.endswith(base) or base.endswith(h) for h in have if len(h) > 20):
        base_missing[base].append(m.get("code") or m.get("url") or m["title"][:60])
(HERE / "amend_base_missing.json").write_text(json.dumps(
    sorted(({"base": b, "amendments": a} for b, a in base_missing.items()), key=lambda x: -len(x["amendments"])),
    ensure_ascii=False, indent=1), encoding="utf-8")
print("temel metni envanterde olmayan değiştirici metin grubu", len(base_missing))

(HERE / "inventory.json").write_text(json.dumps({"items": inv, "unresolved_citations": unresolved}, ensure_ascii=False,
                                                indent=1), encoding="utf-8")

# 9) Şube haritası karşılığı
bm = load("branch_map.json") or []
missing = []
for req in bm:
    rx = re.compile(req["pattern"], re.I)
    if not any(rx.search(m["title"]) for m in inv if m["decision"] in ("al", "incele", "mülga")):
        missing.append(req)
(HERE / "branch_missing.json").write_text(json.dumps(missing, ensure_ascii=False, indent=1), encoding="utf-8")

c = collections.Counter(m["decision"] for m in inv)
print("aday", len(cands), "tekil", len(inv), dict(c))
print("korpusta", sum(m["in_corpus"] for m in inv), "| al ve korpusta yok",
      sum(m["decision"] == "al" and not m["in_corpus"] for m in inv))
print("kaynağa göre al:", collections.Counter(m["sources"][0] for m in inv if m["decision"] == "al"))
print("çözülemeyen atıf", len(unresolved), "| haritada karşılıksız gereksinim", len(missing))
