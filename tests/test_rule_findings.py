"""M11: kural sonucundan bulgu (tespit, ölçüt + alıntı, kanıt, sonuç sınıfı, öneri) ve DOCX çalışma kağıdı."""
import io

from mufettis.rules.catalog import load_catalog
from mufettis.rules.credit_file import CreditFile, evaluate_file
from mufettis.rules.engine import MANUAL, MET, NOT_MET, UNKNOWN, evaluate
from mufettis.rules.fields_teminat import extract_teminat_fields
from mufettis.rules.findings import build_findings, summary

IPOTEK = ("TAPU MÜDÜRLÜĞÜ\nRESMİ SENET (İPOTEK)\nMalik: Ali Veli (TC 12345678901)\nYevmiye No: ……\n"
          "Tescil Tarihi: 10.03.2024\nİpotek Tutarı: ……\n")


def test_findings_are_ordered_and_carry_criterion_quote_evidence_and_recommendation():
    rules = load_catalog()
    fields = extract_teminat_fields("ipotek", IPOTEK)
    results = evaluate(rules, "ipotek", fields, text=IPOTEK)
    findings = build_findings(rules, results)
    assert findings and all(f.status in (NOT_MET, MANUAL, UNKNOWN) for f in findings)
    assert [f.status for f in findings] == sorted((f.status for f in findings),
                                                   key=[NOT_MET, MANUAL, UNKNOWN].index)
    amount = next(f for f in findings if f.rule_id == "TMK851-ipotek-tutari")
    assert amount.status == NOT_MET
    assert amount.criterion.startswith("Türk Medeni Kanunu (4721 md. 851")
    assert amount.quote and amount.recommendation.startswith("Eksiklik giderilmeli")
    assert any("ipotek_tutari: boş" in e for e in amount.evidence)
    # belirsiz sonuç ihlal gibi yazılmaz
    registry = next(f for f in findings if f.rule_id == "TMK856-tapu-tescili")
    assert registry.status == UNKNOWN and registry.detection.startswith("Belgeden karar verilemedi")
    assert sum(summary(results).values()) == len(results)


def test_internal_control_finding_names_its_source_and_file_documents():
    onay = ("KREDİ TEKLİF VE ONAY FORMU\nMüşteri: Örnek Ltd.\nOnaylanan Limit: 1.000.000 TL\n"
            "Onay Tarihi: 01.03.2024\n")
    kul = "KREDİ KULLANDIRIM TALİMATI\nKullandırılan Tutar: 1.200.000 TL\nKullandırım Tarihi: 15.03.2024\n"
    rules = [r for r in load_catalog() if r.id == "ONAY-kullandirim-tutari"]
    results = evaluate_file(rules, CreditFile.from_texts([("onay.pdf", onay), ("kul.pdf", kul)]))
    [f] = build_findings(rules, results)
    assert f.status == NOT_MET and f.documents == ("kul.pdf", "onay.pdf")
    # iç kontrolün "alıntısı" onay belgesindeki satırın kendisi
    assert f.criterion.startswith("kredi onayındaki şart (iç kontrol)") and f.quote == "Onaylanan Limit: 1.000.000 TL"
    assert f.recommendation.startswith("Banka içi şartın")
    # karşılanan sonuç bulgu değil
    ok = evaluate_file(rules, CreditFile.from_texts([("onay.pdf", onay), ("kul.pdf", kul.replace("1.200", "1.000"))]))
    assert ok[0].result.status == MET and build_findings(rules, ok) == []


def test_docx_worksheet_lists_findings_and_masks_personal_data():
    from docx import Document

    from mufettis.exports.rules_report import create_rules_docx
    rules = load_catalog()
    fields = extract_teminat_fields("ipotek", IPOTEK)
    results = evaluate(rules, "ipotek", fields, text=IPOTEK)
    findings = build_findings(rules, results)
    data = create_rules_docx("Teminat Belgesi Kontrolü", [("ip.pdf", "ipotek")], findings, summary(results))
    doc = Document(io.BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(
        c.text for t in doc.tables for row in t.rows for c in row.cells)
    assert f"Bulgular ({len(findings)})" in text and "Tespit" in text and "Öneri" in text
    assert "TMK851-ipotek-tutari" in text and "Türk Medeni Kanunu" in text
    assert "12345678901" not in text


def test_rules_about_collateral_missing_from_the_file_are_not_findings():
    """Dosyada rehin yoksa rehin kuralı bulgu değil (teminat verilmemiş); DASK gibi beklenen belgenin yokluğu bulgu."""
    onay = ("KREDİ TEKLİF VE ONAY FORMU\nOnaylanan Limit: 1.000.000 TL\nOnay Tarihi: 01.03.2024\n"
            "Teminat Şartı: Teminatlar kullandırımdan önce tesis edilecektir.\n")
    kul = "KREDİ KULLANDIRIM TALİMATI\nKullandırılan Tutar: 1.000.000 TL\nKullandırım Tarihi: 15.03.2024\n"
    ip = "TAPU MÜDÜRLÜĞÜ\nRESMİ SENET (İPOTEK)\nYevmiye No: 12\nTescil Tarihi: 10.03.2024\n"
    rules = [r for r in load_catalog() if r.document_type == "kredi_dosyasi"]
    results = evaluate_file(rules, CreditFile.from_texts([("o", onay), ("k", kul), ("i", ip)]))
    ids = {f.rule_id for f in build_findings(rules, results)}
    assert "ONAY-teminat-once-rehin" not in ids and "ONAY-teminat-once-kefalet" not in ids
    assert "6305-11-dask-ipotek-baslangic" in ids  # ipotek var, DASK poliçesi yok: belirsiz bulgu
    assert "ONAY-teminat-once-ipotek" not in ids  # ipotek kullandırımdan önce: karşılandı, bulgu yok
