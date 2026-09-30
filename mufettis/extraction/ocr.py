# -*- coding: utf-8 -*-
"""Taranmış (yalnızca görüntü) PDF'ler için OCR: pypdfium2 ile sayfa görüntüsü → Tesseract (Türkçe).

Eski yol (pdf2image + PATH'teki tesseract) bu makinede hiç çalışmıyordu: pdf2image kurulu değil (poppler da yok),
Tesseract ise PATH dışında kurulu. Sayfalar artık PDF metnini zaten çıkaran pypdfium2 ile çizilir; Tesseract
`MUFETTIS_TESSERACT_CMD`, PATH ya da bilinen kurulum yerlerinden bulunur. Bulunamazsa OCR sessizce boş döner ve
çağıran taraf belgeyi "metin çıkarılamadı" diye raporlar (M6.15: taranmış SEDDK / TCMB genelgeleri).
"""
from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from mufettis.config import PDFIUM_LOCK

OCR_LANG = "tur"
OCR_DPI = 200
# Tesseract'ın kendi iş parçacığı sayısı; donanım yükü düşük kalsın (OMP_NUM_THREADS=2 ile aynı ilke).
os.environ.setdefault("OMP_THREAD_LIMIT", "2")

_KNOWN_PATHS = (
    r"C:\mufettis_ai\tesseract.exe",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


@lru_cache(maxsize=1)
def tesseract_cmd() -> Optional[str]:
    """Kullanılabilir tesseract yürütülebilir dosyası; yoksa None."""
    for raw in (os.environ.get("MUFETTIS_TESSERACT_CMD", "").strip(), shutil.which("tesseract") or "", *_KNOWN_PATHS):
        if raw and Path(raw).is_file():
            return str(Path(raw))
    return None


def available() -> bool:
    if not tesseract_cmd():
        return False
    try:
        import pytesseract  # noqa: F401
        import pypdfium2  # noqa: F401
    except ImportError:
        return False
    return True


def render_pages(pdf_bytes: bytes, dpi: int = OCR_DPI, max_pages: Optional[int] = None) -> list:
    """PDF sayfalarının PIL görüntüleri (pypdfium2; poppler gerekmez)."""
    import pypdfium2 as pdfium

    images = []
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            count = len(pdf) if max_pages is None else min(len(pdf), max_pages)
            for index in range(count):
                page = pdf.get_page(index)
                try:
                    images.append(page.render(scale=dpi / 72).to_pil())
                finally:
                    page.close()
        finally:
            pdf.close()
    return images


def ocr_pdf_pages(pdf_bytes: bytes, max_pages: Optional[int] = None, lang: str = OCR_LANG) -> List[str]:
    """Sayfa sayfa OCR metni; OCR kullanılamıyorsa ya da hata olursa boş liste."""
    if not available():
        return []
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd()
        return [pytesseract.image_to_string(image, lang=lang).strip()
                for image in render_pages(pdf_bytes, max_pages=max_pages)]
    except Exception as exc:  # bozuk PDF, eksik dil dosyası: OCR yok sayılır
        print(f"[OCR] Çalıştırılamadı: {exc}")
        return []


__all__ = ["tesseract_cmd", "available", "render_pages", "ocr_pdf_pages", "OCR_LANG"]
