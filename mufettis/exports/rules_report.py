# -*- coding: utf-8 -*-
"""Teminat / kredi dosyası kontrolünün DOCX çalışma kağıdı (M11).

Yalnızca motorun ürettiği sonucu yazar: incelenen belgeler, kontrol listesi özeti, önem sırasıyla bulgular
(tespit, ölçüt + birebir alıntı, kanıt, sonuç sınıfı, öneri). Yeni yorum eklemez; serbest metin maskelenir.
"""
from __future__ import annotations

import datetime as dt
import io
from typing import Dict, Optional, Sequence, Tuple

from mufettis.common.utils import mask_sensitive, now_iso
from mufettis.config import APP_NAME, APP_VERSION
from mufettis.rules.display import plain_text, status_label
from mufettis.rules.findings import Finding

STATUS_ORDER = ("karşılanmadı", "manuel kontrol", "belirsiz", "karşılandı", "istisna", "uygulanmaz")
DISCLAIMER = (
    "Bu çalışma kağıdı karar destek amaçlıdır. Sonuçlar belgeden okunan alanlara ve kural katalogundaki mevzuat "
    "dayanaklarına göre belirlenimci olarak üretilmiştir; alıntılar korpustaki madde metniyle birebir doğrulanmıştır. "
    "\"Karar verilemedi\" sonucu ihlal değildir, belgenin bu kontrol için yetmediğini gösterir. \"Sizin cevabınız\" "
    "notu, belgeden okunamayan bilginin denetçi tarafından girildiğini gösterir. Nihai tespit yetkili denetçiye aittir."
)


def create_rules_docx(title: str, documents: Sequence[Tuple[str, str]], findings: Sequence[Finding],
                      counts: Dict[str, int], event_date: Optional[dt.date] = None, generated_at: str = "",
                      notes: Sequence[str] = ()) -> bytes:
    """`documents`: (belge adı, tür); `counts`: sonuç → kural sayısı (kontrol listesi özeti)."""
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except Exception as exc:
        raise RuntimeError("python-docx kurulu değil") from exc

    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(0.7)
    section.left_margin = section.right_margin = Inches(0.8)
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(10)

    heading = doc.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(f"{APP_NAME} – {title}")
    run.bold = True
    run.font.size = Pt(15)

    meta = doc.add_table(rows=0, cols=2)
    meta.style = "Table Grid"
    rows = [("Oluşturma", generated_at or now_iso()), ("Sürüm", APP_VERSION),
            ("Olay tarihi", event_date.strftime("%d.%m.%Y") if event_date else "-"),
            ("Kontrol özeti", ", ".join(f"{status_label(s)}: {counts[s]}" for s in STATUS_ORDER if counts.get(s))
             or "-")]
    for label, value in rows:
        cells = meta.add_row().cells
        cells[0].text, cells[1].text = label, mask_sensitive(str(value))

    doc.add_heading("İncelenen belgeler", level=2)
    for name, kind in documents:
        doc.add_paragraph(mask_sensitive(f"{name} — {kind}"), style="List Bullet")

    for note_text in notes:
        doc.add_paragraph(mask_sensitive(note_text))
    doc.add_heading(f"Bulgular ({len(findings)})", level=2)
    if not findings:
        doc.add_paragraph("Bulgu yok: bütün kontroller karşılandı ya da uygulanmadı.")
    for i, f in enumerate(findings, 1):
        doc.add_heading(f"{i}. [{status_label(f.status)}] {f.title}", level=3)
        table = doc.add_table(rows=0, cols=2)
        table.style = "Table Grid"
        lines = [("Tespit", plain_text(f.detection)), ("Ölçüt", f.criterion), ("Dayanak metni", f"“{f.quote}”"),
                 ("Kanıt", plain_text("\n".join(f.evidence)) or "-"),
                 ("Sonuç sınıfı", f"{status_label(f.status)}; {f.result_class}"),
                 ("Öneri", f.recommendation), ("Kural", f.rule_id)]
        if f.documents:
            lines.insert(4, ("Belgeler", ", ".join(f.documents)))
        for label, value in lines:
            cells = table.add_row().cells
            cells[0].text, cells[1].text = label, mask_sensitive(str(value))
            cells[0].paragraphs[0].runs[0].bold = True

    doc.add_paragraph()
    note = doc.add_paragraph(DISCLAIMER)
    note.runs[0].italic = True
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


__all__ = ["create_rules_docx", "DISCLAIMER"]
