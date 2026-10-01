"""KVKK Kurul karar özetleri (tüm sayfalar) → catalog_kvkk_kararlar.json; finans sektörü olanlar işaretli."""
import html, json, re
import web
rows, seen, last = [], set(), None
for p in range(1, 80):
    h = web.get(f"https://www.kvkk.gov.tr/Icerik/5406/kurul-karar-ozetleri?page={p}").decode("utf-8", "replace")
    items = []
    for m in re.finditer(r'href="(?:https?://www\.kvkk\.gov\.tr)?(/Icerik/(\d+)/(\d{4}-\d+))"', h):
        if m.group(2) in seen:
            continue
        seen.add(m.group(2))
        before = h[max(0, m.start() - 1500):m.start()]
        t = re.findall(r">([^<>]{40,})<", before)
        title = html.unescape(t[-1]).strip() if t else m.group(3)
        items.append({"source": "kvkk", "page": "kurul-karar-ozetleri", "title": re.sub(r"\s+", " ", title),
                      "url": "https://www.kvkk.gov.tr" + m.group(1), "decision_no": m.group(3)})
    if not items:
        break
    rows += items
fin = re.compile(r"bank|finans|kredi|katılım|ödeme|sigorta|kart|varlık yönetim", re.I)
for r in rows:
    r["financial"] = bool(fin.search(r["title"]))
json.dump(rows, open("catalog_kvkk_kararlar.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(rows), "karar,", sum(r["financial"] for r in rows), "finans")
for r in rows[:3]: print(r["title"][:150])
