"""K1.4 aşama 1: rg_judiciary.json kayıtlarını resmî RG'den indir (kaldığı yerden sürer, tek akış, web.py nazik istemci).

- Eski HTML günler (2000–~2009): kararlar gün sayfasının içinde. Günün yargı kayıtlarının en küçük çapasından
  (name=N) sayfa sonuna / sonraki "İLAN BÖLÜMÜ"ne kadar metin → rg_docs/<YYYYMMDD>.txt (gün başına bir dosya).
- PDF'ler (~2009 sonrası): taranmış görüntü, metin katmanı yok → rg_pdf/<ad>.pdf olarak saklanır, sayfa sayısı
  rg_pdf_pages.json'a yazılır; OCR ayrı aşama (yük / süre tahmini kullanıcıya sunulur).
"""
import collections
import html
import json
import re
from pathlib import Path

import pypdfium2 as pdfium

import web

rows = json.loads(Path("rg_judiciary.json").read_text(encoding="utf-8"))
DOCS, PDFS = Path("rg_docs"), Path("rg_pdf")
DOCS.mkdir(exist_ok=True)
PDFS.mkdir(exist_ok=True)
PAGES = Path("rg_pdf_pages.json")
pages = json.loads(PAGES.read_text(encoding="utf-8")) if PAGES.exists() else {}

by_page = collections.defaultdict(list)
for r in rows:
    page, _, anchor = r["url"].partition("#")
    by_page[page].append(int(anchor) if anchor.isdigit() else None)

for n, (page, anchors) in enumerate(sorted(by_page.items(), reverse=True)):
    name = page.rsplit("/", 1)[1]
    try:
        if name.lower().endswith(".pdf"):
            f = PDFS / name
            if not f.exists():
                f.write_bytes(web.get(page))
            if name not in pages:
                doc = pdfium.PdfDocument(str(f))
                pages[name] = len(doc)
                doc.close()
                PAGES.write_text(json.dumps(pages, indent=0), encoding="utf-8")
        else:
            f = DOCS / (name.rsplit(".", 1)[0] + ".txt")
            if f.exists():
                continue
            raw = web.get(page).decode("windows-1254", "replace")
            marks = {int(m.group(1)): m.start() for m in re.finditer(r'(?i)<a\s+name="?(\d+)', raw)}
            known = [marks[a] for a in anchors if a in marks]
            start = min(known) if known else 0
            ilan = raw.find("LAN BÖLÜMÜ", start + 1)
            part = raw[start:ilan if ilan > 0 else None]
            # Word'den dışa aktarılmış sayfalar: stil, XML ve yorum blokları metin değil
            part = re.sub(r"(?is)<(style|script|xml)\b.*?</\1>|<!--.*?-->", " ", part)
            text = re.sub(r"[ \t\xa0]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", part)))
            f.write_text(f"{page}\n{text}", encoding="utf-8")
    except Exception as exc:
        print(n, name, "HATA", type(exc).__name__, str(exc)[:100], flush=True)
        continue
    if n % 25 == 0:
        print(n, "/", len(by_page), name, flush=True)
print("bitti", len(by_page), "sayfa;", sum(1 for _ in DOCS.glob("*.txt")), "html;", len(pages), "pdf;",
      sum(pages.values()), "pdf sayfası")
