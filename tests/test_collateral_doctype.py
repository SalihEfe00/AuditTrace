import pytest

from mufettis.rules.doctype import detect_collateral_type


@pytest.mark.parametrize("text, expected, via_603", [
    ("KEFALET SÖZLEŞMESİ\nKefil Adı Soyadı: Ali Veli", "kefalet_sozlesmesi", False),
    ("Müteselsil kefil sıfatıyla imza eden", "kefalet_sozlesmesi", False),
    ("TAAHHÜTNAME\nBen Ali Veli, Deniz Kaya'nın kredi borcunu ödemeyi taahhüt ederim.", "kefalet_sozlesmesi", True),
    ("GARANTİ SÖZLEŞMESİ\nÖrnek Yapı A.Ş. garantör sıfatıyla", None, False),
    ("İPOTEK AKİT TABLOSU\nMalik: Deniz Kaya", "ipotek", False),
    ("GAYRİMENKUL DEĞERLEME RAPORU\nRapor Tarihi: 10.01.2024", "ekspertiz", False),
    ("ZORUNLU DEPREM SİGORTASI POLİÇESİ", "dask", False),
    ("GENEL KREDİ SÖZLEŞMESİ", "senet_gks", False),
    ("GENEL KREDİ SÖZLEŞMESİ\nMadde 9: Borçlu teminat olarak bono verecektir.", "senet_gks", False),
    ("BONO\nVade: 12.09.2024\nDüzenleyen: Deniz Kaya", "bono", False),
    ("Emre Yazılı Senet\nTutarı: 50.000 TL", "bono", False),
    # Başlıksız bono: düzenleyenin ödeme vaadi + bono etiketleri
    ("Düzenleyen (Borçlu): Deniz Kaya\nLehtar (Alacaklı): Örnek Bankası A.Ş.\nVade Tarihi: 15.12.2026\n"
     "Ödeme Vaadi: İşbu bonoda belirtilen bedeli vade tarihinde lehtarın emrine ödeyeceğim.", "bono", False),
    # Vaat yoksa bono etiketleri tek başına yetmez; kredi sözleşmesindeki bono maddesi vaat değildir
    ("Düzenleyen: Deniz Kaya\nLehtar: Örnek Bankası A.Ş.", None, False),
    ("Madde 9: Borçlu, bankanın emrine ödenmek üzere bono verecektir.\nDüzenleyen: Deniz Kaya", None, False),
    ("Müşteri şikâyeti: kartımdan ücret kesildi.", None, False),
])
def test_detect_collateral_type(text, expected, via_603):
    got = detect_collateral_type(text)
    assert got.document_type == expected and got.via_603 == via_603
