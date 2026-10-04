"""M10.1: kredi dosyası modeli (sentetik belgeler)."""
import datetime as dt

from mufettis.rules.catalog import parse_rule
from mufettis.rules.credit_file import CreditFile, evaluate_file, file_event_date
from mufettis.rules.doctype import detect_collateral_type
from mufettis.rules.engine import MET, NOT_MET, UNKNOWN

ONAY = "KREDİ TEKLİF VE ONAY FORMU\nMüşteri: Örnek Ltd.\nOnaylanan Limit: 1.000.000 TL\nOnay Tarihi: 01.03.2024\n" \
       "Onay Mercii: Bölge Kredi Komitesi\n"
KULLANDIRIM = "KREDİ KULLANDIRIM TALİMATI\nMüşteri: Örnek Ltd.\nKullandırılan Tutar: 1.000.000 TL\n" \
              "Kullandırım Tarihi: 15.03.2024\n"
SIRKULER = "İMZA SİRKÜLERİ\nŞirket Unvanı: Örnek Ltd.\nYetkili: Ali Veli\nYetki Şekli: Münferit\n"
IPOTEK = "TAPU MÜDÜRLÜĞÜ\nRESMİ SENET (İPOTEK)\nYevmiye No: 12\nTescil Tarihi: {}\nİpotek Tutarı: 1.000.000 TL\n"


def _rule():
    return parse_rule({
        "id": "T-sira", "document_type": "kredi_dosyasi", "title": "teminat kullandırımdan önce",
        "check": {"type": "date_order", "before": "ipotek.tescil_tarihi", "after": "kullandirim.kullandirim_tarihi"},
        "basis": {"law": "test", "article": "1", "quote": "x"}, "result_class": "otomatik",
        "norm_type": "yükümlülük", "obligor": "banka", "absence_is_violation": False,
        "finding": "ipotek kullandırımdan sonra tescil edilmiş", "rationale": "r"})


def test_new_file_document_types_and_collateral_unchanged():
    assert detect_collateral_type(ONAY).document_type == "kredi_onay"
    assert detect_collateral_type(KULLANDIRIM).document_type == "kullandirim"
    assert detect_collateral_type(SIRKULER).document_type == "imza_sirkuleri"
    assert detect_collateral_type(IPOTEK.format("01.03.2024")).document_type == "ipotek"
    # kefalet metni kullandırımı anabilir; tür değişmez
    kef = "KEFALET SÖZLEŞMESİ\nKefil Adı Soyadı: Ali\nAzami Kefalet Tutarı: 1 TL\nKredi kullandırım tarihinden...\n"
    assert detect_collateral_type(kef).document_type == "kefalet_sozlesmesi"


def test_file_places_documents_and_reads_fields():
    f = CreditFile.from_texts([("onay.pdf", ONAY), ("kul.pdf", KULLANDIRIM), ("not.txt", "Merhaba")])
    kinds = f.by_kind()
    assert kinds["kredi_onay"][0].fields["onaylanan_tutar"].value == "1000000"
    assert kinds["kullandirim"][0].fields["kullandirim_tarihi"].value == dt.date(2024, 3, 15)
    assert [d.name for d in f.unrecognised()] == ["not.txt"]
    assert file_event_date(f) == (dt.date(2024, 3, 15), "kullandirim.kullandirim_tarihi")


def test_cross_document_rule_missing_document_and_multiple_instances():
    rule = _rule()
    ok = CreditFile.from_texts([("ip.pdf", IPOTEK.format("01.03.2024")), ("kul.pdf", KULLANDIRIM)])
    [r] = evaluate_file([rule], ok)
    assert r.result.status == MET and r.documents == ("ip.pdf", "kul.pdf")
    late = CreditFile.from_texts([("ip.pdf", IPOTEK.format("20.03.2024")), ("kul.pdf", KULLANDIRIM)])
    assert evaluate_file([rule], late)[0].result.status == NOT_MET
    # kanıtta belgenin adı
    assert any("ip.pdf" in e[3] for e in evaluate_file([rule], late)[0].result.evidence)
    # kullandırım belgesi yok: belirsiz ve hangi belgenin eksik olduğu yazılır
    [gone] = evaluate_file([rule], CreditFile.from_texts([("ip.pdf", IPOTEK.format("01.03.2024"))]))
    assert gone.result.status == UNKNOWN and "dosyada yok: kullandirim" in gone.result.finding
    # iki ipotek: her biri ayrı sonuç
    two = CreditFile.from_texts([("ip1.pdf", IPOTEK.format("01.03.2024")), ("ip2.pdf", IPOTEK.format("20.03.2024")),
                                 ("kul.pdf", KULLANDIRIM)])
    got = {r.documents[-1]: r.result.status for r in evaluate_file([rule], two)}
    assert got == {"ip1.pdf": MET, "ip2.pdf": NOT_MET}
