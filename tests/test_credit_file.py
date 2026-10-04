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
    assert kinds["kredi_onay"][0].fields["onaylanan_tutar"].value == "1000000 TRY"
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


def test_disbursed_amount_does_not_exceed_approval_in_same_currency():
    """M10.3: kullandırılan ≤ onaylanan (kısmi kullandırım bulgu değil); para birimi farklıysa belirsiz."""
    rules = [r for r in load_catalog() if r.id == "ONAY-kullandirim-tutari"]

    def run(onay_amount, used_amount):
        f = CreditFile.from_texts([("o", ONAY.replace("1.000.000 TL", onay_amount)),
                                   ("k", KULLANDIRIM.replace("1.000.000 TL", used_amount))])
        return evaluate_file(rules, f)[0].result.status
    assert run("1.000.000 TL", "1.000.000 TL") == MET
    assert run("1.000.000 TL", "400.000 TL") == MET
    assert run("1.000.000 TL", "1.000.000,01 TL") == NOT_MET
    assert run("250.000 USD", "1.000.000 TL") == UNKNOWN
    # müfettiş birimsiz yazarsa TL sayılır
    from mufettis.rules.review import apply_answers
    assert apply_answers({}, {"kredi_onay.onaylanan_tutar": "1.000.000"})["kredi_onay.onaylanan_tutar"].value == \
        "1000000 TRY"


def test_internal_control_rule_must_be_conditioned_on_the_approval():
    import pytest

    from mufettis.rules.catalog import CatalogError
    raw = dict(_rule().raw, basis={"law": "kredi_onayi", "article": "teminat şartı", "quote": "x"},
               norm_type="iç_kontrol")
    with pytest.raises(CatalogError, match="applies_if kredi_onay"):
        parse_rule(raw)
    with pytest.raises(CatalogError, match="iç_kontrol"):
        parse_rule(dict(raw, norm_type="yükümlülük"))


def test_signer_is_authorized_in_signature_circular():
    """M10.3: müşteri adına imzalayan imza sirkülerinde yetkili mi (Tedbirler Yön. md. 7/3); müşterek yetkide iki imza."""
    rules = [r for r in load_catalog() if r.id in ("MASAK-7-imzalayan-yetkili", "MASAK-7-musterek-imza")]

    def run(signer, authorized, how=None):
        sirk = f"İMZA SİRKÜLERİ\nŞirket Unvanı: Örnek Ltd.\nYetkili: {authorized}\n" + (f"Yetki Şekli: {how}\n" if how else "")
        kul = KULLANDIRIM + f"Müşteri Adına İmzalayan: {signer}\n"
        f = CreditFile.from_texts([("k", kul), ("s", sirk)])
        assert f.by_kind()["kullandirim"][0].fields["musteri_imzalayan"].value == signer
        return {r.result.rule_id: r.result.status for r in evaluate_file(rules, f)}
    # OCR noktaları düşürse, ikinci ad yazılmasa da aynı kişi
    assert run("ALI VELI", "Ali Rıza Veli, Ayşe Kaya", "Münferit") == {
        "MASAK-7-imzalayan-yetkili": MET, "MASAK-7-musterek-imza": MET}
    assert run("Can Er", "Ali Veli, Ayşe Kaya", "Münferit")["MASAK-7-imzalayan-yetkili"] == NOT_MET
    # müşterek: tek imza bulgu, iki yetkili imza karşılar; şekil okunamazsa tek imza belirsiz
    assert run("Ali Veli", "Ali Veli, Ayşe Kaya", "Müşterek")["MASAK-7-musterek-imza"] == NOT_MET
    assert run("Ali Veli ve Ayşe Kaya", "Ali Veli, Ayşe Kaya", "Müşterek")["MASAK-7-musterek-imza"] == MET
    assert run("Ali Veli", "Ali Veli, Ayşe Kaya")["MASAK-7-musterek-imza"] == UNKNOWN
    # "münferiden veya müştereken": tahmin edilmez
    assert run("Ali Veli", "Ali Veli, Ayşe Kaya", "Münferiden veya müştereken")["MASAK-7-musterek-imza"] == UNKNOWN
    # bankanın onaylayan personeli müşteri imzası sayılmaz
    f = CreditFile.from_texts([("k", KULLANDIRIM + "Onaylayan Personel: Zeki Banka\n")])
    assert "musteri_imzalayan" not in f.by_kind()["kullandirim"][0].fields


def test_signature_authority_on_loan_agreement_and_authority_term():
    """M10.3 kalan: GKS imzalayanı da yetkili olmalı; yetki süresi kullandırım / GKS tarihinde bitmemiş olmalı."""
    rules = [r for r in load_catalog() if r.id.startswith("MASAK-7-")]
    gks = "GENEL KREDİ SÖZLEŞMESİ\nBorçlu Unvanı: Örnek Ltd.\nSözleşme Tarihi: 10.03.2024\nŞirket Yetkilisi: {}\n"

    def run(signer, term):
        sirk = f"İMZA SİRKÜLERİ\nŞirket Unvanı: Örnek Ltd.\nYetkili: Ali Veli\nYetki Şekli: Münferit\nYetki Süresi: {term}\n"
        f = CreditFile.from_texts([("g", gks.format(signer)), ("k", KULLANDIRIM), ("s", sirk)])
        return {r.result.rule_id: r.result.status for r in evaluate_file(rules, f)}
    got = run("Ali Veli", "31.12.2024 tarihine kadar")
    assert got["MASAK-7-gks-imzalayan-yetkili"] == MET and got["MASAK-7-gks-musterek-imza"] == MET
    assert got["MASAK-7-yetki-suresi-kullandirim"] == MET and got["MASAK-7-yetki-suresi-gks"] == MET
    assert run("Can Er", "Süresiz")["MASAK-7-gks-imzalayan-yetkili"] == NOT_MET
    # yetki 12.03.2024'te bitmiş: GKS (10.03) içinde, kullandırım (15.03) dışında
    late = run("Ali Veli", "12.03.2024")
    assert late["MASAK-7-yetki-suresi-gks"] == MET and late["MASAK-7-yetki-suresi-kullandirim"] == NOT_MET
    assert run("Ali Veli", "Süresiz (azledilinceye kadar)")["MASAK-7-yetki-suresi-kullandirim"] == "uygulanmaz"
    # başlangıç + süre: başlangıç tarihi bitiş sanılmaz
    assert run("Ali Veli", "01.01.2021 tarihinden itibaren 3 yıl")["MASAK-7-yetki-suresi-kullandirim"] == UNKNOWN


def test_file_case_splitter_reads_document_separators():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "rules_file_benchmark.py"
    spec = importlib.util.spec_from_file_location("rules_file_benchmark", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = "ara satır\n--- BELGE 1 ---\n" + ONAY + "**--- BELGE 2 ---**\n" + KULLANDIRIM
    docs = mod.split_documents(text)
    assert [n for n, _ in docs] == ["belge1", "belge2"] and docs[1][1].startswith("KREDİ KULLANDIRIM")
    got = mod.run_case(text, [r for r in load_catalog() if r.id == "ONAY-kullandirim-tutari"])
    assert got["kinds"] == ["kredi_onay", "kullandirim"] and got["results"]["ONAY-kullandirim-tutari"] == MET


def test_file_field_variants_from_measurement():
    """M10.5a (dosya vakaları dev ölçümü): numaralı / liste halinde yetkililer TAMAMI okunur (yalnız ilk satır öbür
    yetkiliyi yetkisiz gösterirdi), birden çok imzalayan satırı birleşir, "(İmza)" ad sayılmaz, "aynı gün" şartı
    kurala bağlanır."""
    rules = [r for r in load_catalog() if r.id in ("MASAK-7-gks-imzalayan-yetkili", "MASAK-7-gks-musterek-imza")]
    gks = ("GENEL KREDİ SÖZLEŞMESİ\nKredi Borçlusu: Örnek Ltd.\nTarih: 06.04.2024\n"
           "Borçlu Şirket Adına İmzalayan: Selim Tan (İmza)\nBorçlu Şirket Adına İmzalayan: Engin Boz (İmza)\n"
           "Banka Yetkilisi: Hasan Güler (İmza)\n")
    for listing in ("Temsilciler:\n1. Selim Tan (Müdür)\n2. Engin Boz (Müdür)\n",
                    "Yetkili 1: Selim Tan\nYetkili 2: Engin Boz\n",
                    "İmza Yetkilileri:\n- Selim Tan (Genel Müdür)\n- Engin Boz (Müdür)\n"):
        sirk = "İMZA SİRKÜLERİ\nŞirket Unvanı: Örnek Ltd.\n" + listing + "Temsil ve İlzam Şekli: MÜŞTEREK\n"
        f = CreditFile.from_texts([("g", gks), ("s", sirk)])
        assert f.by_kind()["senet_gks"][0].fields["duzenleme_tarihi"].value == dt.date(2024, 4, 6)
        assert {r.result.rule_id: r.result.status for r in evaluate_file(rules, f)} == {
            "MASAK-7-gks-imzalayan-yetkili": MET, "MASAK-7-gks-musterek-imza": MET}, listing
    from mufettis.rules.fields_teminat import extract_teminat_fields
    for cond, want in (("Teminatın kredi kullandırımı ile aynı gün tesis edilmesi şarttır.", "kullandirim_oncesi"),
                       ("Kredi kullandırılmadan evvel ipotek tesis olunacaktır.", "kullandirim_oncesi"),
                       ("Kredi çekilmeden önce ipotek tesis edilecektir.", "kullandirim_oncesi"),
                       ("Teminatların kredi kullandırıldıktan sonra 30 gün içinde tesis edilmesi mümkündür.",
                        "kullandirim_sonrasi")):
        got = extract_teminat_fields("kredi_onay", ONAY + f"Teminat Koşulu: {cond}\n")["teminat_sarti"].value
        assert got == want, cond


def test_file_field_variants_from_spent_holdout():
    """M10.5b: "Borçlu Tarafından İmzalayan", "Şirket Müdürleri:" listesi, "Yetki Süresi Sonu", "kredi serbest
    bırakılmadan önce"; bitiş tarihi "Belirtilmemiş" iken başka satırdaki "süresiz" okunur."""
    from mufettis.rules.fields_teminat import extract_teminat_fields
    kul = extract_teminat_fields("kullandirim", KULLANDIRIM + "Borçlu Tarafından İmzalayan: Hakan Var\n")
    assert kul["musteri_imzalayan"].value == "Hakan Var"
    sirk = extract_teminat_fields("imza_sirkuleri", "İMZA SİRKÜLERİ\nŞirket Müdürleri:\n1. Ali Veli (Müdür)\n"
                                  "2. Can Er (Müdür)\nYetki Süresi Sonu: 20.08.2026\n")
    assert sirk["yetkili"].value == "Ali Veli (Müdür), Can Er (Müdür)" and sirk["yetki_suresi"].value == dt.date(2026, 8, 20)
    open_ended = extract_teminat_fields("imza_sirkuleri", "İMZA SİRKÜLERİ\nYetkili 1: Ali Veli\n"
                                        "Yetki Şekli: MÜNFERİT; Yetki süresi: süresiz / azledilinceye kadar\n"
                                        "Yetki Bitiş Tarihi: Belirtilmemiş\n")
    assert open_ended["yetki_suresi"].value == "süresiz"
    onay = extract_teminat_fields("kredi_onay", ONAY + "Teminat Kriteri: Kredi serbest bırakılmadan önce ipotek tesis edilmelidir.\n")
    assert onay["teminat_sarti"].value == "kullandirim_oncesi"
