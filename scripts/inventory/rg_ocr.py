"""K1.4 aşama 2: rg_pdf/*.pdf → rg_docs/<ad>.txt (Tesseract tur, sayfa sayfa; kaldığı yerden sürer, küçükten büyüğe)."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mufettis.extraction import ocr  # noqa: E402

pages = json.loads(Path("rg_pdf_pages.json").read_text(encoding="utf-8"))
done_pages, t0 = 0, time.time()
total = sum(pages.values())
for name, n in sorted(pages.items(), key=lambda x: x[1]):
    out = Path("rg_docs") / (name[:-4] + ".txt")
    if out.exists():
        done_pages += n
        continue
    try:
        texts = ocr.ocr_pdf_pages((Path("rg_pdf") / name).read_bytes())
    except Exception as exc:
        print(name, "HATA", type(exc).__name__, str(exc)[:100], flush=True)
        continue
    out.write_text(f"https://www.resmigazete.gov.tr/eskiler/{name[:4]}/{name[4:6]}/{name}\n" + "\n\f\n".join(texts),
                   encoding="utf-8")
    done_pages += n
    el = time.time() - t0
    print(f"{done_pages}/{total} sayfa, {name} ({n}), {el/60:.0f} dk", flush=True)
print("bitti")
