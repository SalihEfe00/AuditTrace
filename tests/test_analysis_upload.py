# -*- coding: utf-8 -*-
"""Analiz ekranında belge yükleme: PDF/DOCX/TXT'den metin ve başlık."""
from __future__ import annotations

import io
import unittest

from mufettis.ui.analysis_view import load_uploaded_document

SENTENCE = "Bankaniz kredi kartim icin yillik aidat ucreti tahsil etmistir."


def _minimal_pdf(text: str) -> bytes:
    """Tek sayfalı, Helvetica ile yazılmış ASCII metin içeren geçerli PDF."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


class TestLoadUploadedDocument(unittest.TestCase):
    def test_txt(self):
        doc = load_uploaded_document("Şikayet Dilekçesi.txt", "Bankanız kartımdan ücret kesmiştir.".encode("utf-8"))
        self.assertEqual(doc.title, "Şikayet Dilekçesi")
        self.assertIn("ücret kesmiştir", doc.text)

    def test_docx_paragraphs_and_tables(self):
        from docx import Document

        document = Document()
        document.add_paragraph("Bankanız hesabımdan onayım olmadan ücret kesmiştir.")
        table = document.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "Tutar"
        table.rows[0].cells[1].text = "150 TL"
        buffer = io.BytesIO()
        document.save(buffer)
        doc = load_uploaded_document("itiraz.docx", buffer.getvalue())
        self.assertIn("onayım olmadan", doc.text)
        self.assertIn("Tutar | 150 TL", doc.text)

    def test_pdf_goes_through_the_extraction_pipeline(self):
        doc = load_uploaded_document("sikayet.pdf", _minimal_pdf(SENTENCE))
        self.assertIn("aidat", doc.text)
        self.assertIn("1 sayfa", doc.info)
        self.assertEqual(doc.title, "sikayet")

    def test_empty_document_is_reported(self):
        with self.assertRaises(ValueError):
            load_uploaded_document("bos.txt", b"   ")


if __name__ == "__main__":
    unittest.main()
