"""Kurum sayfalarındaki içerik bağlantılarını catalog_<kurum>.json'a ekle (argv: kurum url...). Menü (çok sayfada ortak) atılır."""
import collections, html, json, re, sys
from pathlib import Path
from urllib.parse import urljoin
import web
inst, urls = sys.argv[1], sys.argv[2:]
out = Path(f"catalog_{inst}.json")
rows = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
have = {(r["page"], r["url"]) for r in rows}
pages = {}
for url in urls:
    try:
        h = web.get(url).decode("utf-8", "replace")
    except Exception as exc:
        print("HATA", url, type(exc).__name__, str(exc)[:100]); continue
    ls = []
    for u, a in re.findall(r'href="([^"#]+)"[^>]*>(.*?)</a>', h, re.S):
        t = html.unescape(re.sub(r"<[^>]+>|\s+", " ", a)).strip()
        if len(t) > 8 and not u.startswith(("javascript", "mailto", "tel", "call")):
            ls.append((t, urljoin(url, html.unescape(u))))
    pages[url] = ls
cnt = collections.Counter(l for ls in pages.values() for l in set(ls))
n = 0
for url, ls in pages.items():
    for t, u in dict.fromkeys(ls):
        if (cnt[(t, u)] == 1 or len(pages) == 1) and (url, u) not in have:
            rows.append({"source": inst, "page": url, "title": t, "url": u}); have.add((url, u)); n += 1
out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(inst, "+", n, "toplam", len(rows))
