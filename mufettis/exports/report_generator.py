# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt
except Exception:
    Document = None

from mufettis.config import EXPORT_DIR, APP_VERSION
from mufettis.common.utils import now_iso, mask_sensitive
from mufettis.audit.service import load_reviews
from mufettis.extraction.text_repair import clean_evidence_text
from mufettis.analysis.risk_engine import format_turkish_number

def validate_export_bytes(data: bytes, kind: str) -> None:
    if not data:
        raise ValueError("Boş çıktı")
    if kind in {"docx", "xlsx"} and not data.startswith(b"PK"):
        raise ValueError(f"{kind} ZIP/OOXML imzası geçersiz")
def create_docx_report(
    summary: Dict[str, Any],
    findings: List[Dict[str, Any]],
    auditor: str = "",
) -> bytes:
    """Temiz, deterministik, OOXML içinde teknik çöp bırakmayan DOCX."""
    if Document is None:
        raise RuntimeError("python-docx kurulu değil")

    def report_text(value: Any, limit: int = 1800) -> str:
        s=clean_evidence_text(value, max_chars=limit)
        return mask_sensitive(s)

    doc=Document()
    sec=doc.sections[0]
    sec.top_margin=Inches(0.65)
    sec.bottom_margin=Inches(0.65)
    sec.left_margin=Inches(0.7)
    sec.right_margin=Inches(0.7)

    try:
        doc.styles["Normal"].font.name="Arial"
        doc.styles["Normal"].font.size=Pt(10)
    except Exception:
        pass

    title=doc.add_paragraph()
    title.alignment=WD_ALIGN_PARAGRAPH.CENTER
    rr=title.add_run("Müfettiş AI – Denetim Çalışma Kağıdı")
    rr.bold=True
    rr.font.size=Pt(16)

    p=doc.add_paragraph()
    p.add_run("Sürüm: ").bold=True
    p.add_run(APP_VERSION)
    p.add_run(" | Tarih: ").bold=True
    p.add_run(now_iso())
    if auditor:
        p.add_run(" | Denetçi: ").bold=True
        p.add_run(report_text(auditor,300))

    p=doc.add_paragraph()
    p.add_run("Üretim türü: ").bold=True
    p.add_run(
        "Deterministik analiz raporu. Yerel LLM taslağı bu DOCX'e dahil değildir."
    )

    warning=doc.add_paragraph()
    warning.add_run("Karar destek sınırı: ").bold=True
    warning.add_run(
        "Bu belge analitik risk sinyallerini ve aday mevzuat eşleşmelerini "
        "gösterir; kesin hukuki ihlal, suç, şüpheli işlem veya denetim görüşü değildir."
    )

    doc.add_heading("1. Analiz Özeti",level=1)
    summary_fields=[
        ("Satır sayısı",summary.get("row_count","")),
        ("Toplam bulgu",summary.get("finding_count","")),
        ("Etkilenen satır",summary.get("affected_rows","")),
        ("Yüksek risk",summary.get("high_count","")),
        ("Orta risk",summary.get("medium_count","")),
        ("Düşük risk",summary.get("low_count","")),
        ("Z-score eşiği",summary.get("z_threshold","")),
        ("Medyan / MAD eşiği",summary.get("mad_threshold","")),
        ("IQR katsayısı",summary.get("iqr_multiplier","")),
    ]
    table=doc.add_table(rows=1,cols=2)
    table.style="Table Grid"
    table.rows[0].cells[0].text="Metrik"
    table.rows[0].cells[1].text="Değer"
    for key,value in summary_fields:
        cells=table.add_row().cells
        cells[0].text=report_text(key,200)
        cells[1].text=report_text(value,500)

    doc.add_heading("2. Bulgular",level=1)
    if not findings:
        doc.add_paragraph("Analiz sonucunda etkin bulgu üretilmedi.")
    else:
        reviews=load_reviews().get("items",{})
        ordered=sorted(
            findings,
            key=lambda x: float(x.get("risk_skoru",0) or 0),
            reverse=True,
        )

        for idx,finding in enumerate(ordered,1):
            ftype=report_text(finding.get("bulgu_tipi",""),300)
            level=report_text(finding.get("risk_seviyesi",""),100)
            doc.add_heading(f"{idx}. {ftype} — {level}",level=2)

            detail=doc.add_table(rows=0,cols=2)
            detail.style="Table Grid"
            review=reviews.get(finding.get("bulgu_id"),{})

            raw_value=finding.get("deger","")
            try:
                display_value=format_turkish_number(raw_value,2)
            except Exception:
                display_value=report_text(raw_value,500)

            fields=[
                ("Bulgu ID",finding.get("bulgu_id","")),
                ("Kaynak dosya",finding.get("source_file","")),
                ("Satır",finding.get("satir","")),
                ("Risk skoru",finding.get("risk_skoru","")),
                ("Risk seviyesi",finding.get("risk_seviyesi","")),
                ("Değer",display_value),
                (
                    "Kolonlar",
                    ", ".join(map(str,finding.get("kolonlar",[]) or [])),
                ),
                ("Analitik gerekçe",finding.get("gerekce","")),
                ("İnceleme durumu",review.get("status","Bekliyor")),
                ("İnceleme notu",review.get("note","")),
            ]

            for key,value in fields:
                cells=detail.add_row().cells
                cells[0].text=report_text(key,200)
                cells[1].text=report_text(value,1800)

            candidates=finding.get("mevzuat_eslesmeleri",[]) or []
            if candidates:
                doc.add_paragraph().add_run(
                    "3. Aday Mevzuat Evidence Chain"
                ).bold=True

                for candidate in candidates[:3]:
                    title_text=report_text(
                        candidate.get("title","Belirtilmedi"),1000
                    )
                    if not title_text:
                        title_text="Belirtilmedi"

                    cp=doc.add_paragraph()
                    cp.add_run(title_text).bold=True

                    hierarchy=[]
                    if candidate.get("article_no"):
                        hierarchy.append(f"Madde {candidate.get('article_no')}")
                    if candidate.get("paragraph_no"):
                        hierarchy.append(f"Fıkra {candidate.get('paragraph_no')}")
                    if candidate.get("item_no"):
                        hierarchy.append(f"Bent {candidate.get('item_no')}")
                    if candidate.get("subitem_no"):
                        hierarchy.append(f"Alt bent {candidate.get('subitem_no')}")
                    if candidate.get("section_no"):
                        hierarchy.append(f"Bölüm {candidate.get('section_no')}")

                    citation_parts = []
                    if candidate.get("title"):
                        citation_parts.append(
                            report_text(candidate.get("title"), 1000)
                        )
                    if hierarchy:
                        citation_parts.append(" / ".join(hierarchy))
                    if candidate.get("law_number"):
                        citation_parts.append(
                            f"Kanun No: {report_text(candidate.get('law_number'),100)}"
                        )
                    elif candidate.get("document_number"):
                        citation_parts.append(
                            f"Belge No: {report_text(candidate.get('document_number'),100)}"
                        )

                    citation_p = doc.add_paragraph()
                    citation_p.add_run("Mevzuat atfı: ").bold = True
                    citation_p.add_run(
                        " | ".join(citation_parts)
                        if citation_parts
                        else "Aday kaynak bulundu; kimlik bilgisi eksik."
                    )

                    mp=doc.add_paragraph()
                    mp.add_run(
                        f"Kaynak: {report_text(candidate.get('source',''),500)}"
                    )
                    if hierarchy:
                        mp.add_run(" | " + " / ".join(hierarchy))
                    mp.add_run(
                        f" | Sayfa: {report_text(candidate.get('page',''),50)}"
                        f" | Yürürlük: {report_text(candidate.get('effective_date',''),100)}"
                        f" | Durum: {report_text(candidate.get('status',''),100)}"
                    )

                    snippet=report_text(
                        candidate.get("snippet",""),
                        1600,
                    )
                    if snippet:
                        doc.add_paragraph(snippet)

                    url=str(candidate.get("url","") or "").strip()
                    if url.startswith(("https://www.bddk.gov.tr/", "https://www.resmigazete.gov.tr/", "https://www.mevzuat.gov.tr/")):
                        doc.add_paragraph(url)

            else:
                no_ref = doc.add_paragraph()
                no_ref.add_run("Mevzuat atfı: ").bold = True
                no_ref.add_run(
                    "Bu bulgu için doğrulanabilir güncel aday mevzuat eşleşmesi üretilemedi. "
                    "Hukuki dayanak uydurulmamıştır; uzman doğrulaması gereklidir."
                )

            note=doc.add_paragraph()
            note.add_run(
                "Not: Aday mevzuat eşleşmeleri hukuki uygulanabilirlik kanıtı değildir; "
                    "kaynak, yürürlük ve madde/fıkra kapsamı uzman tarafından doğrulanmalıdır."
                ).italic=True

    footer=doc.sections[0].footer.paragraphs[0]
    footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run(
        f"Müfettiş AI {APP_VERSION} | Deterministik karar destek çıktısı"
    ).font.size=Pt(8)

    output=io.BytesIO()
    doc.save(output)
    data=output.getvalue()
    validate_export_bytes(data,"docx")

    # Gerçek OOXML içeriğini tekrar açarak raporun serileştiğini doğrula.
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml=zf.read("word/document.xml").decode("utf-8",errors="strict")
        if "<Kalem" in xml or "<Islem" in xml or "TÃ¼rkiye" in xml or "TemettÃ¼" in xml:
            raise ValueError("DOCX sanitization başarısız: teknik/encoding çöpü bulundu.")
    return data
def create_xlsx_bytes(working: pd.DataFrame, findings_df: pd.DataFrame, summary: Dict[str, Any]) -> bytes:
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        working.to_excel(writer, index=False, sheet_name="Analiz")
        findings_df.to_excel(writer, index=False, sheet_name="Bulgular")
        pd.DataFrame([summary]).to_excel(writer, index=False, sheet_name="Ozet")
    data = out.getvalue()
    validate_export_bytes(data, "xlsx")
    # Reopen smoke test
    out.seek(0)
    pd.ExcelFile(out)
    return data
def _save_export(data: bytes, name: str) -> Path:
    path = EXPORT_DIR / name
    path.write_bytes(data)
    return path


__all__ = [k for k in dict(globals()).keys() if not k.startswith('__')]
