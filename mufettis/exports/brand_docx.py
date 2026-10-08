# -*- coding: utf-8 -*-
"""M16.4: Word çalışma kâğıtlarında AuditTrace kimliği — içerik değişmez, yalnız görünüm.

Kapak satırı: amblem + "Audit" (mürekkep) "Trace" (mürekkep, italik) + kâğıdın adı, altında ince çizgi.
Başlıklar Georgia (her Windows'ta var; arayüzdeki Libre Baskerville Word'de olmayabilir), mürekkep rengi. İki sütunlu
bilgi tablolarında etiket sütunu kâğıt rengi zeminli. Alt bilgi: ad, sürüm, "karar destek çıktısı".
"""
from __future__ import annotations

from mufettis.config import APP_NAME, APP_VERSION
from mufettis.brand import BRAND_DIR, PRIMARY, INK, INK_MUTED, LINE, PAPER_2

HEADING_FONT = "Georgia"


def _rgb(hex_color: str):
    from docx.shared import RGBColor

    return RGBColor.from_string(hex_color.lstrip("#"))


def style_document(doc) -> None:
    """Başlık biçemleri: Georgia, mürekkep rengi."""
    from docx.oxml.ns import qn

    for name in ("Heading 1", "Heading 2", "Heading 3"):
        try:
            font = doc.styles[name].font
        except KeyError:
            continue
        font.name = HEADING_FONT
        font.color.rgb = _rgb(INK)
        # Word teması (asciiTheme) font.name'i ezer; tema bağı kaldırılır.
        fonts = doc.styles[name].element.rPr.rFonts
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            fonts.attrib.pop(qn(attr), None)
        fonts.set(qn("w:cs"), HEADING_FONT)


def title_block(doc, title: str) -> None:
    """Kapak satırı: amblem + AuditTrace + kâğıdın adı; altında ince çizgi."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    head = doc.add_paragraph()
    icon = BRAND_DIR / "icon_256.png"
    if icon.is_file():
        head.add_run().add_picture(str(icon), width=Inches(0.42))
        head.add_run("  ")
    for text, color, italic in (("Audit", INK, False), ("Trace", PRIMARY, True)):
        run = head.add_run(text)
        run.font.name, run.font.size, run.bold, run.italic = HEADING_FONT, Pt(20), not italic, italic
        run.font.color.rgb = _rgb(color)

    sub = doc.add_paragraph()
    run = sub.add_run(title)
    run.font.name, run.font.size = HEADING_FONT, Pt(13)
    run.font.color.rgb = _rgb(INK_MUTED)
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("w:val", "single"), ("w:sz", "6"), ("w:space", "6"), ("w:color", LINE.lstrip("#"))):
        bottom.set(qn(key), value)
    border.append(bottom)
    sub._p.get_or_add_pPr().append(border)


def finish(doc) -> None:
    """Kaydetmeden önce: iki sütunlu tablolarda etiket sütunu gölgesi; alt bilgi boşsa ad ve sürüm."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    width = doc.sections[0].page_width - doc.sections[0].left_margin - doc.sections[0].right_margin
    for table in doc.tables:
        if len(table.columns) != 2:
            continue
        table.autofit = False
        for row in table.rows:
            row.cells[0].width, row.cells[1].width = Inches(1.5), width - Inches(1.5)
            props = row.cells[0]._tc.get_or_add_tcPr()
            shade = OxmlElement("w:shd")
            shade.set(qn("w:val"), "clear")
            shade.set(qn("w:color"), "auto")
            shade.set(qn("w:fill"), PAPER_2.lstrip("#"))
            props.append(shade)
    footer = doc.sections[0].footer.paragraphs[0]
    if not footer.text.strip():
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run(f"{APP_NAME} {APP_VERSION} · karar destek çıktısı; nihai değerlendirme müfettişe aittir")
        run.font.size = Pt(8)
        run.font.color.rgb = _rgb(INK_MUTED)
