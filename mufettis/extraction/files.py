# -*- coding: utf-8 -*-
"""Yüklenen dosyadan metin çıkarma (PDF, DOCX, DOC, HTML, TXT).

Mevzuat içe aktarma (rag.maintenance.ingest_local_regulation_file) ve analiz ekranındaki belge
yükleme aynı çıkarma hattını kullanır: PDF'ler için pypdfium2 hızlı geçiş + kalite kapısı + koşullu
yedek motorlar/OCR (harvesting.resmi_gazete.ResmiGazeteScraper._extract_pdf_text).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# Arayüzün kabul ettiği türler. Listede olmayan uzantılar düz metin olarak çözülür.
SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".doc", ".txt", ".html", ".htm")


@dataclass
class ExtractedFile:
    text: str
    extension: str
    pages: int = 0
    ocr_used: bool = False
    diagnostic: Dict[str, Any] = field(default_factory=dict)


class _NoIndex:
    """PDF çıkarımı dizin kullanmaz; tarayıcı yapıcısının üretim dizinini açmaması için."""
    data: Dict[str, Any] = {"indexed_items": {}}


_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_-]+)""", re.I)


def decode_html(raw: bytes) -> str:
    """Sayfanın bildirdiği kodlamayla (eski RG sayfaları: Windows-1254, K5.n); bildirim yoksa ya da tanınmıyorsa UTF-8."""
    m = _CHARSET_RE.search(raw[:4096])
    charset = m.group(1).decode("ascii").lower() if m else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def extract_file_text(name: str, data: bytes, index: Optional[Any] = None) -> ExtractedFile:
    extension = Path(str(name or "")).suffix.lower()
    if extension == ".pdf":
        from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper

        scraper = ResmiGazeteScraper(index=index if index is not None else _NoIndex(), local_only=True)
        text, pages, ocr_used = scraper._extract_pdf_text(data)
        return ExtractedFile(text=text, extension=extension, pages=pages, ocr_used=ocr_used,
                             diagnostic=dict(scraper._last_pdf_diagnostic))
    if extension == ".docx":
        try:
            from docx import Document
        except Exception as exc:
            raise RuntimeError("DOCX desteği için python-docx kurulu değil") from exc
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return ExtractedFile(text="\n".join(parts).strip(), extension=extension)
    if extension == ".doc":
        from mufettis.extraction.word97 import word97_text

        return ExtractedFile(text=word97_text(data), extension=extension)
    if extension in {".html", ".htm"}:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(decode_html(data), "html.parser")
        for el in soup(["script", "style", "header", "footer", "nav"]):
            el.extract()
        return ExtractedFile(text=re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip(), extension=extension)
    return ExtractedFile(text=data.decode("utf-8-sig", errors="replace").strip(), extension=extension)


__all__ = ["ExtractedFile", "SUPPORTED_EXTENSIONS", "extract_file_text"]
