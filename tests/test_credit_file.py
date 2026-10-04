"""M10.1: kredi dosyası modeli (sentetik belgeler)."""
import datetime as dt

from mufettis.rules.catalog import load_catalog, parse_rule
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


DASK = "ZORUNLU DEPREM SİGORTASI POLİÇESİ\nPoliçe No: 1\nBaşlangıç Tarihi: {}\nBitiş Tarihi: {}\n"


def test_dask_policy_valid_on_mortgage_registration_day():
    """6305 md. 11/2: ipotek tescil günü geçerli DASK; poliçeye yazılan işlem tarihi değil, ipoteğin kendi tarihi."""
    rules = [r for r in load_catalog() if r.document_type == "kredi_dosyasi"]

    def run(*docs):
        return {r.result.rule_id: r.result.status for r in evaluate_file(rules, CreditFile.from_texts(docs))
                if r.result.rule_id.startswith("6305-11-dask-ipotek")}
    ip = ("ip.pdf", IPOTEK.format("10.03.2024"))
    assert run(ip, ("d.pdf", DASK.format("01.03.2024", "01.03.2025"))) == {
        "6305-11-dask-ipotek-baslangic": MET, "6305-11-dask-ipotek-bitis": MET}
    assert run(ip, ("d.pdf", DASK.format("11.03.2024", "11.03.2025")))["6305-11-dask-ipotek-baslangic"] == NOT_MET
    assert run(ip, ("d.pdf", DASK.format("01.03.2023", "01.03.2024")))["6305-11-dask-ipotek-bitis"] == NOT_MET
    # iki ipotek, tek poliçe: hangi taşınmazın poliçesi olduğu belgeden bilinmez
    two = run(ip, ("ip2.pdf", IPOTEK.format("20.03.2024")), ("d.pdf", DASK.format("01.03.2024", "01.03.2025")))
    assert set(two.values()) == {UNKNOWN}


def test_collateral_before_disbursement_is_an_internal_control_from_the_approval():
    """M10.2 (kullanıcı kararı): kanuni dayanak yok; şart kredi onayından okunursa iç kontrol bulgusu."""
    rules = [r for r in load_catalog() if r.id.startswith("ONAY-teminat-once")]
    cond = ONAY + "Teminat Şartı: Teminatlar kullandırımdan önce tesis edilecektir.\n"

    def run(*docs):
        return {r.documents: (r.result.status, r.result.basis) for r in evaluate_file(rules, CreditFile.from_texts(docs))
                if r.result.rule_id == "ONAY-teminat-once-ipotek"}
    kul = ("kul.pdf", KULLANDIRIM)  # kullandırım 15.03.2024
    [(status, basis)] = run(("onay.pdf", cond), ("ip.pdf", IPOTEK.format("15.03.2024")), kul).values()
    assert status == MET and "iç kontrol" in basis
    assert list(run(("onay.pdf", cond), ("ip.pdf", IPOTEK.format("20.03.2024")), kul).values())[0][0] == NOT_MET
    # onayda şart yok: kural uygulanmaz; onay dosyada yok: belirsiz (müfettişe sorulur)
    later = ONAY + "Teminat Şartı: Teminatlar kullandırımdan sonra 30 gün içinde tesis edilecektir.\n"
    assert list(run(("onay.pdf", later), ("ip.pdf", IPOTEK.format("20.03.2024")), kul).values())[0][0] == "uygulanmaz"
    assert list(run(("ip.pdf", IPOTEK.format("20.03.2024")), kul).values())[0][0] == UNKNOWN


def test_internal_control_rule_must_be_conditioned_on_the_approval():
    import pytest

    from mufettis.rules.catalog import CatalogError
    raw = dict(_rule().raw, basis={"law": "kredi_onayi", "article": "teminat şartı", "quote": "x"},
               norm_type="iç_kontrol")
    with pytest.raises(CatalogError, match="applies_if kredi_onay"):
        parse_rule(raw)
    with pytest.raises(CatalogError, match="iç_kontrol"):
        parse_rule(dict(raw, norm_type="yükümlülük"))
