# -*- coding: utf-8 -*-
"""Tarama ölçümü (M9.4a d): kullanıcının doldurup taradığı şablonlar OCR'dan geçer, sentetik vakalarla aynı ölçüme girer.

Girdi klasöründe vaka numarasıyla adlandırılmış taramalar (01.pdf, 02.jpg ...). OCR metni çıktı klasörüne yazılır
(gelistirme/<no>.txt + manifest.json; elle bakılabilsin diye), ardından scripts/rules_case_benchmark.py'nin ölçümü
benchmarks/rules/scan_key.json anahtarıyla çalışır: belge / müfettiş kipleri, alan ve motor katmanları.

    python scripts/rules_scan_benchmark.py <taramalar> <çıktı> [--out rapor.json]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mufettis.extraction import ocr  # noqa: E402
from rules_case_benchmark import score  # noqa: E402

KEY = ROOT / "benchmarks" / "rules" / "scan_key.json"
IMAGES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def scan_text(path: Path) -> str:
    """Taramanın OCR metni; sayfalar alt alta (PDF ya da görüntü)."""
    if path.suffix.lower() == ".pdf":
        return "\n\n".join(ocr.ocr_pdf_pages(path.read_bytes()))
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = ocr.tesseract_cmd()
    with Image.open(io.BytesIO(path.read_bytes())) as image:
        return pytesseract.image_to_string(image, lang=ocr.OCR_LANG).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scans", help="taramaların klasörü (01.pdf, 02.jpg ...)")
    ap.add_argument("work", help="OCR metinlerinin yazılacağı klasör")
    ap.add_argument("--out", help="ayrıntılı rapor (JSON)")
    a = ap.parse_args(argv)
    if not ocr.available():
        print("OCR kullanılamıyor (tesseract / pytesseract / pypdfium2 eksik)")
        return 1
    key = json.loads(KEY.read_text(encoding="utf-8"))
    work = Path(a.work)
    (work / "gelistirme").mkdir(parents=True, exist_ok=True)
    manifest = []
    for path in sorted(Path(a.scans).iterdir()):
        if path.suffix.lower() not in IMAGES | {".pdf"}:
            continue
        case = path.stem.split("_")[0].zfill(2)  # tür = vaka numarası
        if case not in key["types"]:
            print(f"anahtarda yok, atlandı: {path.name}")
            continue
        text = scan_text(path)
        (work / "gelistirme" / f"{case}.txt").write_text(text, encoding="utf-8")
        manifest.append({"id": case, "type": case, "ai": "tarama", "holdout": False})
        print(f"{path.name}: {len(text)} karakter")
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    report = score(manifest, work, key, holdout=False)
    for mode, m in report["modes"].items():
        t = m["total"]
        print(f"[{mode}] kontrol {t.get('doğru', 0) + t.get('yanlış', 0)}: doğru {t.get('doğru', 0)}, "
              f"yanlış {t.get('yanlış', 0)}")
    al, mo = report["layers"]["alan"], report["layers"]["motor"]
    print(f"[katman: alan] doğru {al.get('doğru', 0)}, güvenli {al.get('güvenli', 0)}, yanlış {al.get('yanlış', 0)}")
    print(f"[katman: motor] doğru {mo.get('doğru', 0)}, alan kaynaklı {mo.get('alan kaynaklı', 0)}, "
          f"yanlış {mo.get('yanlış', 0)}")
    for f in report["failures"] + report["field_failures"]:
        print("  -", json.dumps(f, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
