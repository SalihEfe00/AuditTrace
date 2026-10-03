"""Teminat uygunluk kontrolü bölümü (mufettis/ui/rules_view.py) tarayıcısız: Streamlit AppTest ile çizilir,
soru cevaplanır, sonuç değişimi denetlenir. Veri dizinine dokunmaz (katalog paket içinde)."""
import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

REHIN = ("TAŞINIR REHİN SÖZLEŞMESİ\nRehin Veren: Kaya Tekstil Ltd. Şti.\nRehin Konusu: Dokuma makinesi seri no 778\n"
         "Rehin Tutarı: 800.000,00 TL\nSicil No: RTS-2024-118\n")


def _app(text):
    def script(text):
        import datetime as dt

        from mufettis.ui.rules_view import render_collateral_check
        render_collateral_check(text, event_date=dt.date(2024, 3, 12))

    return AppTest.from_function(script, args=(text,), default_timeout=60)


def _markdown(at):
    return "\n".join(m.value for m in at.markdown)


def test_pledge_question_answer_changes_result():
    at = _app(REHIN).run()
    assert not at.exception
    assert any("taşınır rehni" in c.value for c in at.caption)
    assert "❔ **belirsiz** — `6750-4-imza-onayi`" in _markdown(at)
    radio = next(r for r in at.radio if "imzalar nasıl onaylanmış" in r.label)
    radio.set_value("noter onayı").run()
    assert not at.exception
    assert "✅ **karşılandı** — `6750-4-imza-onayi`" in _markdown(at)
    # Karara bağlanan kuralın sorusu kalır; müfettiş cevabını değiştirebilir
    radio = next(r for r in at.radio if "imzalar nasıl onaylanmış" in r.label)
    radio.set_value("onaysız").run()
    assert "❌ **karşılanmadı** — `6750-4-imza-onayi`" in _markdown(at)


def test_typed_amount_triggers_second_report_check():
    text = ("GAYRİMENKUL DEĞERLEME RAPORU\nRapor Tarihi: 10.01.2024\nDeğerleme Uzmanı: Ece Tan\nLisans No: 400123\n")
    at = _app(text).run()
    radio = next(r for r in at.radio if "nihai değer" in r.label)
    radio.set_value("yazacağım").run()
    at.text_input[0].input("12.500.000").run()
    assert not at.exception
    assert "📝 **manuel kontrol** — `BDDK-DHY20-iki-rapor`" in _markdown(at)


def test_kefalet_handwriting_answer_and_answers_bound_to_document():
    from mufettis.rules.mutation_cases import BASE
    at = _app(BASE).run()
    assert "❔ **belirsiz** — `TBK583-muteselsil-el-yazisi`" in _markdown(at)
    radio = next(r for r in at.radio if "'Müteselsil kefil' ibaresi" in r.label)
    radio.set_value("hayır").run()
    assert "❌ **karşılanmadı** — `TBK583-muteselsil-el-yazisi`" in _markdown(at)
    # Başka belge: önceki belgenin beyanı taşınmaz
    at2 = _app(BASE.replace("Ali Veli", "Can Demir")).run()
    assert "❔ **belirsiz** — `TBK583-muteselsil-el-yazisi`" in _markdown(at2)


def test_personal_guarantee_under_another_name_follows_kefalet_rules():
    at = _app("TAAHHÜTNAME\nBen Ali Veli, Deniz Kaya'nın kredi borcunu ödemeyi taahhüt ederim.\n"
              "Azami Tutar: ……………").run()
    assert not at.exception
    assert any("TBK 603" in i.value for i in at.info)
    assert "`TBK583-azami-miktar-belirtilmis`" in _markdown(at)


def test_unrecognised_document_is_not_checked():
    at = _app("Müşteri şikâyeti: kartımdan ücret kesildi.").run()
    assert not at.exception and not at.radio
    assert any("tanınmadı" in c.value for c in at.caption)
