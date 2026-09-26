# -*- coding: utf-8 -*-
"""Belge analizi sonucunun DOCX çalışma kağıdı olarak dışa aktarımı.

Rapor yalnızca motorun ürettiği yapılandırılmış sonucu yazar (bulgu, doğrulanmış alıntı, dayanaksız
iddia, uyarı, denetçi kararı); yeni yorum eklemez. Tüm serbest metinler maskelenir.
"""
from __future__ import annotations

import io
from typing import Any

from mufettis.analysis.cited_references import describe as describe_cited, missing_note
from mufettis.analysis.models import AnalysisResult
from mufettis.common.utils import mask_sensitive, now_iso
from mufettis.config import APP_VERSION

DOCUMENT_TYPE_LABELS = {
    "complaint": "Tüketici şikâyeti",
    "consumer_complaint": "Tüketici şikâyeti",
    "internal_correspondence": "İç denetim / yazışma",
    "unknown": "Belirlenemeyen belge türü",
}
DISCLAIMER = (
    "Bu çalışma kağıdı karar destek amaçlıdır. Bulgular yerel mevzuat veritabanından getirilen hükümlerle "
    "iddialar arasındaki olası ilişkiyi gösterir; alıntı doğrulaması yalnızca alıntının kaynak metinde "
    "geçtiğini teyit eder. Hükmün somut olaya uygulanabilirliği ve nihai tespit yetkili denetçiye aittir."
)


def _text(value: Any) -> str:
    return mask_sensitive(" ".join(str(value or "").split()))


def create_analysis_docx(result: AnalysisResult, generated_at: str = "") -> bytes:
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
    run = heading.add_run("Müfettiş AI – Belge Analizi Çalışma Kağıdı")
    run.bold = True
    run.font.size = Pt(15)

    document = result.document
    classification = result.classification
    meta = doc.add_table(rows=0, cols=2)
    meta.style = "Table Grid"
    for label, value in [
        ("Analiz kimliği", result.analysis_id),
        ("Oluşturulma", generated_at or now_iso()),
        ("Sürüm", APP_VERSION),
        ("Belge başlığı", document.title or "-"),
        ("Belge kaynağı", document.source or "-"),
        ("Belge türü", f"{DOCUMENT_TYPE_LABELS.get(classification.document_type, classification.document_type)} "
                       f"(güven %{classification.confidence * 100:.0f})"),
        ("Konular", ", ".join(s.replace("_", " ") for s in document.subjects) or "-"),
        ("Analiz durumu", result.status),
    ]:
        cells = meta.add_row().cells
        cells[0].text = label
        cells[1].text = _text(value)

    doc.add_heading("1. Bulgular", level=2)
    if not result.findings:
        doc.add_paragraph(
            "Doğrulanmış bulgu üretilmedi. Bu sonuç belgenin mevzuata uygun olduğu anlamına gelmez; "
            "iddialar denetçi tarafından ayrıca değerlendirilmelidir."
        )
    for index, finding in enumerate(result.findings, 1):
        doc.add_heading(f"Bulgu {index} — Risk: {finding.risk_level or 'Orta'}", level=3)
        doc.add_paragraph(_text(finding.issue))
        doc.add_paragraph(f"Mevzuat atfı: {_text(finding.regulation_reference)}")
        for citation in finding.citations:
            status = citation.metadata.get("is_valid")
            badge = "doğrulandı" if status is True else ("doğrulanamadı" if status is False else "bilgi yok")
            quote = doc.add_paragraph()
            quote.add_run(f"Alıntı ({_text(citation.reference)}, alıntı kontrolü: {badge}): ").bold = True
            quote.add_run(f"“{_text(citation.text_span)}”").italic = True
        doc.add_paragraph(f"Gözlem: {_text(finding.explanation)}")
        doc.add_paragraph(f"Model yorumu: {_text(finding.model_interpretation)}")
        doc.add_paragraph(f"Takip aksiyonu: {_text(finding.inspector_action)}")

    doc.add_heading("2. Belgenin atıf yaptığı mevzuat", level=2)
    if result.cited_regulations:
        for ref in result.cited_regulations:
            doc.add_paragraph(_text(describe_cited(ref)), style="List Bullet")
        note = missing_note(result.cited_regulations, has_findings=bool(result.findings))
        if note:
            doc.add_paragraph(_text(note))
    else:
        doc.add_paragraph("Belgede mevzuat atfı saptanmadı.")

    doc.add_heading("3. Mevzuat dayanağı bulunamayan iddialar", level=2)
    if result.unsupported_claims:
        for claim in result.unsupported_claims:
            doc.add_paragraph(_text(claim.statement), style="List Bullet")
    else:
        doc.add_paragraph("Yok.")

    if result.warnings:
        doc.add_heading("4. Uyarılar ve veri sınırlamaları", level=2)
        for warning in result.warnings:
            doc.add_paragraph(_text(warning), style="List Bullet")

    doc.add_heading(f"{5 if result.warnings else 4}. Denetçi değerlendirmesi", level=2)
    review = doc.add_table(rows=0, cols=2)
    review.style = "Table Grid"
    for label, value in [
        ("Durum", result.reviewer_status or "Bekliyor"),
        ("Denetçi", result.reviewer_name or "-"),
        ("Tarih", result.reviewed_at or "-"),
        ("Not", result.reviewer_note or "-"),
    ]:
        cells = review.add_row().cells
        cells[0].text = label
        cells[1].text = _text(value)

    note = doc.add_paragraph()
    note_run = note.add_run(DISCLAIMER)
    note_run.italic = True
    note_run.font.size = Pt(8)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


__all__ = ["create_analysis_docx"]
