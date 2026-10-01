import html
import json
import re

import web

LISTS = {49: "Kanunlar", 50: "Bankacılık Kanununa İlişkin Düzenlemeler",
         51: "Banka Kartları ve Kredi Kartları Kanununa İlişkin Düzenlemeler",
         52: "6361 Sayılı Kanuna İlişkin Düzenlemeler", 54: "Kuruma İlişkin Düzenlemeler",
         55: "RG'de Yayımlanan Kurul Kararları", 56: "RG'de Yayımlanmayan Kurul Kararları",
         58: "Düzenleme Taslakları", 63: "Mülga Düzenlemeler"}


def text(s):
    return html.unescape(re.sub(r"<[^>]+>|\s+", " ", s)).strip()


out = []
for n, name in LISTS.items():
    h = web.get(f"https://www.bddk.org.tr/Mevzuat/Liste/{n}").decode("utf-8", "replace")
    seen = {}
    for u, a in re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', h, re.S):
        t = text(a)
        if not t or len(t) < 6:
            continue
        if "DokumanGetir" in u or "mevzuat.gov.tr" in u or u.lower().endswith(".pdf"):
            seen.setdefault(u, t)
    # Bölüm başlıkları (liste içi gruplar)
    groups = [text(x) for x in re.findall(r'<h[3-5][^>]*>(.*?)</h[3-5]>', h, re.S)]
    for u, t in seen.items():
        out.append({"list": n, "list_name": name, "url": u, "title": t})
    print(n, name, len(seen), "grup:", groups[:12])
json.dump(out, open("bddk_catalog.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("toplam", len(out))
