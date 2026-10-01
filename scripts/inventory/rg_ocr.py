"""K1.4 aşama 2: rg_pdf/*.pdf → rg_docs/<ad>.txt (Tesseract tur, sayfa sayfa; kaldığı yerden sürer, küçükten büyüğe).

Kipler (K1.4a, Seçenek A daraltması):
  (kipsiz)     tüm PDF'ler tam OCR
  --ibk        yalnızca içtihadı birleştirme kararlarının PDF'leri, tam
  --head N     tam OCR'ı olmayan her PDF'in ilk N sayfası → rg_heads/<ad>.txt
  --selected   ilk sayfalarında kredi / teminat terimi geçen PDF'ler (rg_heads/), tam
MUFETTIS_OCR_REST=0.5: her PDF'ten sonra OCR süresinin yarısı kadar dinlenir (gece fan / ısı için).
"""
import json, os, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mufettis.corpus.citations import turkish_lower  # noqa: E402
from mufettis.extraction import ocr  # noqa: E402
from rg_screen import CORE  # noqa: E402

CREDIT_TERMS = ("kefalet", "ipotek", "kredi", "banka", "senet", "çek")
CREDIT = re.compile("|".join(CORE[k] for k in CREDIT_TERMS))
IBK_TITLE = re.compile(r"birle[şs]tirme", re.I)


def pdf_name(url):
    return url.rsplit("/", 1)[-1]


def ibk_names():
    rows = json.loads(Path("rg_judiciary.json").read_text(encoding="utf-8"))
    return {pdf_name(r["url"]) for r in rows if IBK_TITLE.search(r["text"]) and r["url"].lower().endswith(".pdf")}


def selected_names():
    return {f.stem + ".pdf" for f in Path("rg_heads").glob("*.txt")
            if CREDIT.search(turkish_lower(f.read_text(encoding="utf-8")))}


def main(argv):
    pages = json.loads(Path("rg_pdf_pages.json").read_text(encoding="utf-8"))
    head = int(argv[argv.index("--head") + 1]) if "--head" in argv else None
    if "--ibk" in argv or "--selected" in argv:
        keep = ibk_names() if "--ibk" in argv else selected_names()
        pages = {n: p for n, p in pages.items() if n in keep}
    out_dir = Path("rg_heads" if head else "rg_docs")
    out_dir.mkdir(exist_ok=True)
    total = sum(min(n, head) if head else n for n in pages.values())
    done_pages, t0 = 0, time.time()
    for name, n in sorted(pages.items(), key=lambda x: x[1]):
        n = min(n, head) if head else n
        out = out_dir / (name[:-4] + ".txt")
        if out.exists() or (head and (Path("rg_docs") / out.name).exists()):
            done_pages += n
            continue
        started = time.time()
        try:
            texts = ocr.ocr_pdf_pages((Path("rg_pdf") / name).read_bytes(), max_pages=head)
        except Exception as exc:
            print(name, "HATA", type(exc).__name__, str(exc)[:100], flush=True)
            continue
        if not any(texts):  # OCR boş döndü (Tesseract yok, bozuk PDF): bitti sayılmaz
            print(name, "HATA boş OCR", flush=True)
            continue
        out.write_text(f"https://www.resmigazete.gov.tr/eskiler/{name[:4]}/{name[4:6]}/{name}\n" + "\n\f\n".join(texts),
                       encoding="utf-8")
        done_pages += n
        el = time.time() - t0
        print(f"{done_pages}/{total} sayfa, {name} ({n}), {el/60:.0f} dk", flush=True)
        time.sleep((time.time() - started) * float(os.environ.get("MUFETTIS_OCR_REST", "0")))
    print("bitti", len(pages), "pdf")


if __name__ == "__main__":
    main(sys.argv[1:])
