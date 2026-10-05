# -*- coding: utf-8 -*-
"""M15.7 sonuç ekranı: sade adlar yalnız gösterimde (motor değerleri aynı), tek cümlelik durum, kanıtın belgedeki
yeri ("belgede göster"), DOCX'te sade adlar."""
import io

from mufettis.rules.display import plain_text, status_label
from mufettis.rules.engine import INSPECTOR, MET, NOT_APPLICABLE, UNKNOWN
from mufettis.ui import rules_view


def test_display_names_do_not_touch_engine_values():
    assert status_label(UNKNOWN) == "karar verilemedi"
    assert status_label(NOT_APPLICABLE) == "bu dosyaya uygulanmaz"
    assert status_label(MET) == MET
    assert UNKNOWN == "belirsiz" and NOT_APPLICABLE == "uygulanmaz" and INSPECTOR == "müfettiş beyanı"
    assert plain_text("tutar: 5 (müfettiş beyanı)") == "tutar: 5 (sizin cevabınız)"


def test_status_sentence():
    groups = {"finding": [1, 2], "undecided": [1], "passed": [], "missing": [1, 2, 3]}
    assert rules_view._status_sentence(groups, 2, 1) == (
        "2 bulgu bulundu; 1 kontrolde karar verilemedi, 1 soru cevabınızı bekliyor, "
        "3 kontrol eksik belge yüzünden yapılamadı.")
    empty = {"finding": [], "undecided": [], "passed": [1], "missing": []}
    assert rules_view._status_sentence(empty, 0, 0) == "Belgeden açık bir aykırılık bulunmadı."


def test_show_in_document_highlights_the_evidence_line():
    text = "BAŞLIK\nMalik: A\nYevmiye No: 4521\nTescil Tarihi: 20.03.2024\nTutar: 1 TL\nSon\n"
    evidence = [("ipotek.tescil_tarihi", "dolu", None, "c_ipotek.docx, satır 4: Tescil Tarihi: 20.03.2024", "belge"),
                ("x", "dolu", None, "satır 9: yok", INSPECTOR)]  # müfettiş beyanının belgede yeri yok
    locations = rules_view._locations(evidence, {"c_ipotek.docx": text})
    assert [(doc, n) for doc, n, _ in locations] == [("c_ipotek.docx", 4)]
    shown = rules_view._show_in_document(locations)
    assert "<mark>Tescil Tarihi: 20.03.2024</mark>" in shown and "Yevmiye No: 4521" in shown
    # tek belgede konum belge adı taşımaz; satır numarası kaymışsa metinden bulunur
    single = rules_view._locations([("f", "dolu", None, "satır 2: Tescil Tarihi: 20.03.2024", "belge")],
                                   {"belge": text})
    assert [(doc, n) for doc, n, _ in single] == [("belge", 4)]


def test_docx_uses_plain_names():
    from docx import Document

    from mufettis.exports.rules_report import create_rules_docx
    from mufettis.rules.catalog import load_catalog
    from mufettis.rules.engine import evaluate
    from mufettis.rules.fields_teminat import extract_teminat_fields
    from mufettis.rules.findings import build_findings, summary
    text = "TAŞINIR REHİN SÖZLEŞMESİ\nRehin Tutarı: ……\n"
    rules = load_catalog()
    results = evaluate(rules, "rehin", extract_teminat_fields("rehin", text), text=text)
    data = create_rules_docx("Teminat Belgesi Kontrolü", [("r.pdf", "rehin")], build_findings(rules, results),
                             summary(results))
    doc = Document(io.BytesIO(data))
    all_text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(
        c.text for t in doc.tables for row in t.rows for c in row.cells)
    assert "karar verilemedi" in all_text
    assert "[belirsiz]" not in all_text and "belirsiz:" not in all_text
