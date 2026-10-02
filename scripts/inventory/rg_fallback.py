"""K1.4: gün sayfasında karar metni olmayan (<1000 kr) eski HTML günleri için tam gazete PDF'i (taranmış) → OCR.

Aşama "indir": rg_full/<YYYYMMDD>.pdf (tek akış). Kullanım: python rg_fallback.py indir|ocr [gün ...]. Aşama "ocr": OCR → rg_docs/<gün>.txt yerine yazılır (eski metin
.htmltxt olarak saklanır). Gazetenin tamamı okunur; yargı bölümünü eleme metnin kendisinden yapılır.
"""
import json, os, re, sys, time
from pathlib import Path
import web

miss = json.loads(Path("rg_htm_missing.json").read_text())
days = [d for d in miss if len(Path(f"rg_docs/{d}.txt").read_text(encoding="utf-8")) < 1000]
if sys.argv[2:]:  # yalnızca verilen günler (K1.4a: İBK içeren günler)
    days = [d for d in days if d in sys.argv[2:]]
FULL = Path("rg_full"); FULL.mkdir(exist_ok=True)
if sys.argv[1] == "indir":
    for d in days:
        day = re.match(r"\d{8}", d).group(0)
        f = FULL / f"{d}.pdf"
        if f.exists():
            continue
        url = f"https://www.resmigazete.gov.tr/eskiler/{day[:4]}/{day[4:6]}/{d.replace('M', 'm') if 'M' in d else day}.pdf"
        try:
            f.write_bytes(web.get(url))
            print(d, "indi", url, flush=True)
        except Exception as exc:
            print(d, "HATA", url, str(exc)[:80], flush=True)
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from mufettis.extraction import ocr
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = ocr.tesseract_cmd()
    for d in days:
        f = FULL / f"{d}.pdf"
        out = Path(f"rg_docs/{d}.txt")
        if not f.exists() or out.with_suffix(".htmltxt").exists():
            continue
        texts = []
        for image in ocr.iter_page_images(f.read_bytes()):  # sayfa sayfa; her sayfadan sonra dinlenir (ısı)
            started = time.time()
            texts.append(pytesseract.image_to_string(image, lang=ocr.OCR_LANG).strip())
            time.sleep((time.time() - started) * float(os.environ.get("MUFETTIS_OCR_REST", "0")))
        if not texts:
            print(d, "OCR boş", flush=True); continue
        out.rename(out.with_suffix(".htmltxt"))
        out.write_text("tam-gazete:" + f.name + "\n" + "\n\f\n".join(texts), encoding="utf-8")
        print(d, len(texts), "sayfa", flush=True)
    print("bitti")
print(len(days), "gün")
