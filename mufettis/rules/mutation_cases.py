# -*- coding: utf-8 -*-
"""Kural ölçümü için mutasyon vakaları (M9.4, M9.0 eki): kusursuz bir kefalet sözleşmesinden her seferinde tek
öğe bozularak vaka üretilir; beklenen sonuç kanun maddesinden (cevap anahtarı sistem çıktısına bakılmadan) yazılır.

Bu vakalar yalnızca kuralların ve alan çıkarıcının birbirini doğru tamamladığını gösterir (sentetik, tek şablon);
gerçek ölçüm kullanıcının vakalarıyla (M9.4 kaynakları, holdout ayrımı) yapılır.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

BASE = """KEFALET SÖZLEŞMESİ
Alacaklı Banka: Örnek Bankası A.Ş.
Asıl Borçlu: Deniz Kaya
Kredi Türü: Ticari kredi
Kefil Adı Soyadı: Ali Veli
Kefilin Medeni Hali: Evli
Kefilin Mesleği: Ücretli çalışan
Azami Kefalet Tutarı: 250.000,00 TL
Kefalet Türü: Müteselsil kefalet
Sözleşme Tarihi: 12.03.2024
İmza Türü: Islak imza
EŞ RIZASI
Eşin rızası: Ayşe Veli (imzalı)
Rıza Tarihi: 12.03.2024
"""

# Kusursuz şablonda el yazısı soruları müfettişçe "var", ayrılık kararı / yasal ayrı yaşama sorusu "yok"
# cevaplanmış sayılır (TBK 584/1 istisnası belgeden okunmaz).
BASE_ANSWERS = {"el_yazisi_azami_tutar": "var", "el_yazisi_tarih": "var", "el_yazisi_kefalet_turu": "var",
                "ayrilik_durumu": "yok"}


@dataclass(frozen=True)
class Case:
    id: str
    text: str
    expected: Dict[str, str]                    # kural kimliği → beklenen sonuç (yalnızca değişmesi beklenenler)
    answers: Dict[str, str] = field(default_factory=dict)
    basis: str = ""                             # beklenen sonucun kanun dayanağı


def _swap(old: str, new: str) -> str:
    assert old in BASE, old
    return BASE.replace(old, new)


CASES: List[Case] = [
    Case("m00-kusursuz", BASE, {"TBK584-es-rizasi": "karşılandı", "TBK584-riza-zamani": "karşılandı",
                                "TBK583-azami-miktar-belirtilmis": "karşılandı", "TBK583-kefalet-tarihi": "karşılandı",
                                "5070-md5-e-imza-kefalet": "karşılandı", "TBK14-kefil-imzasi": "karşılandı",
                                "6502-4-adi-kefalet": "uygulanmaz"},
         basis="TBK 14/1, 583/1, 584/1, 5070 md. 5; ticari kredi → 6502 md. 4/6 uygulanmaz"),
    Case("m01-riza-bos", _swap("Eşin rızası: Ayşe Veli (imzalı)", "Eşin rızası: ……………"),
         {"TBK584-es-rizasi": "karşılanmadı"}, basis="TBK 584/1: evli kefil, rıza yok, istisna yok"),
    Case("m02-riza-bolumu-yok", _swap("EŞ RIZASI\nEşin rızası: Ayşe Veli (imzalı)\nRıza Tarihi: 12.03.2024\n", ""),
         {"TBK584-es-rizasi": "belirsiz"}, basis="alan bulunamadı: OCR / form farkı olabilir → belirsiz (M9.2)"),
    Case("m03-esnaf-istisna", _swap("Ücretli çalışan", "Esnaf (berber)").replace(
        "Eşin rızası: Ayşe Veli (imzalı)", "Eşin rızası: ……………"),
         {"TBK584-es-rizasi": "istisna"}, answers={"kefalet_isletmeyle_ilgili": "evet"},
         basis="TBK 584/3: esnaf ve sanatkârın mesleki faaliyetiyle ilgili kefaleti, ticari kredi"),
    Case("m03b-esnaf-ilgisiz", _swap("Ücretli çalışan", "Esnaf (berber)").replace(
        "Eşin rızası: Ayşe Veli (imzalı)", "Eşin rızası: ……………"),
         {"TBK584-es-rizasi": "karşılanmadı"}, answers={"kefalet_isletmeyle_ilgili": "hayir"},
         basis="TBK 584/3: sıfat yetmez, kefalet mesleki faaliyetle ilgili değil → eş rızası aranır"),
    Case("m03c-esnaf-ilgi-bilinmiyor", _swap("Ücretli çalışan", "Esnaf (berber)").replace(
        "Eşin rızası: Ayşe Veli (imzalı)", "Eşin rızası: ……………"),
         {"TBK584-es-rizasi": "belirsiz"}, basis="584/3 istisnasının işletmeyle ilgi koşulu bilinmiyor"),
    Case("m03d-ayrilik-karari", _swap("Eşin rızası: Ayşe Veli (imzalı)", "Eşin rızası: ……………"),
         {"TBK584-es-rizasi": "istisna"}, answers={"ayrilik_durumu": "mahkeme_ayrilik_karari"},
         basis="TBK 584/1: mahkemece verilmiş ayrılık kararı varsa eş rızası aranmaz"),
    Case("m04-bekar", _swap("Kefilin Medeni Hali: Evli", "Kefilin Medeni Hali: Bekar").replace(
        "Eşin rızası: Ayşe Veli (imzalı)", "Eşin rızası: ……………"),
         {"TBK584-es-rizasi": "uygulanmaz"}, basis="TBK 584/1 yalnızca eşler için"),
    Case("m05-medeni-hal-yok", _swap("Kefilin Medeni Hali: Evli\n", "").replace(
        "Eşin rızası: Ayşe Veli (imzalı)", "Eşin rızası: ……………"),
         {"TBK584-es-rizasi": "belirsiz"}, basis="kuralın uygulanıp uygulanmadığı bilinmiyor"),
    Case("m06-meslek-yok", _swap("Kefilin Mesleği: Ücretli çalışan\n", "").replace(
        "Eşin rızası: Ayşe Veli (imzalı)", "Eşin rızası: ……………"),
         {"TBK584-es-rizasi": "belirsiz"}, basis="584/3 istisnası bilinmiyor → karşılanmadı denmez"),
    Case("m07-riza-sonra", _swap("Rıza Tarihi: 12.03.2024", "Rıza Tarihi: 20.03.2024"),
         {"TBK584-riza-zamani": "karşılanmadı"}, basis="TBK 584/1: rıza en geç kurulma anında"),
    Case("m08-azami-bos", _swap("250.000,00 TL", "……………"),
         {"TBK583-azami-miktar-belirtilmis": "karşılanmadı"}, basis="TBK 583/1: azami miktar belirtilmedikçe geçersiz"),
    Case("m09-tarih-bos", _swap("Sözleşme Tarihi: 12.03.2024", "Sözleşme Tarihi: ..../..../......"),
         {"TBK583-kefalet-tarihi": "karşılanmadı"}, basis="TBK 583/1: kefalet tarihi belirtilmedikçe geçersiz"),
    Case("m10-muteselsil-matbu", BASE, {"TBK583-muteselsil-el-yazisi": "karşılanmadı"},
         answers={"el_yazisi_kefalet_turu": "yok"}, basis="TBK 583/1: müteselsil sıfat kefilin el yazısıyla"),
    Case("m11-e-imza", _swap("İmza Türü: Islak imza", "İmza Türü: Güvenli elektronik imza"),
         {"5070-md5-e-imza-kefalet": "karşılanmadı", "TBK14-kefil-imzasi": "uygulanmaz"},
         basis="5070 md. 5/2: teminat sözleşmesi güvenli e-imzayla kurulamaz; tek bulgu"),
    Case("m11b-e-imza-turu-yok", _swap("İmza Türü: Islak imza", "İmza Türü: Elektronik imza"),
         {"5070-md5-e-imza-kefalet": "belirsiz", "TBK14-kefil-imzasi": "belirsiz"},
         basis="elektronik imzanın güvenli olup olmadığı yazmıyor → müfettişe sorulur"),
    Case("m11c-basit-e-imza", _swap("İmza Türü: Islak imza", "İmza Türü: Basit elektronik imza"),
         {"5070-md5-e-imza-kefalet": "karşılandı", "TBK14-kefil-imzasi": "karşılanmadı"},
         basis="5070 yasağı güvenli e-imza için; basit e-imza yazılı şekli sağlamaz (TBK 14/1, 15/1)"),
    Case("m11d-imzasiz", _swap("İmza Türü: Islak imza", "İmza Türü: İmzasız"),
         {"TBK14-kefil-imzasi": "karşılanmadı"}, basis="TBK 14/1: yazılı şekil borç altına girenin imzasını ister"),
    Case("m12-tuketici-muteselsil", _swap("Kredi Türü: Ticari kredi", "Kredi Türü: Bireysel ihtiyaç kredisi"),
         {"6502-4-adi-kefalet": "karşılanmadı", "TBK583-muteselsil-el-yazisi": "istisna"},
         answers={"el_yazisi_kefalet_turu": "yok"},
         basis="6502 md. 4/6: tüketici işleminde şahsi teminat adi kefalet; müteselsil el yazısı bulgusu tekrarlanmaz"),
    Case("m13-tuketici-adi", _swap("Kredi Türü: Ticari kredi", "Kredi Türü: Bireysel ihtiyaç kredisi").replace(
        "Kefalet Türü: Müteselsil kefalet", "Kefalet Türü: Adi kefalet"),
         {"6502-4-adi-kefalet": "karşılandı", "TBK583-muteselsil-el-yazisi": "uygulanmaz"},
         basis="adi kefalet: 6502 md. 4/6 ile uyumlu; müteselsil el yazısı şartı yok"),
    Case("m14-kefalet-tarihi-ayri", BASE.replace("Sözleşme Tarihi: 12.03.2024\n",
                                                 "Sözleşme Tarihi: 01.03.2024\nKefalet Tarihi: 12.03.2024\n"),
         {"TBK584-riza-zamani": "karşılandı"},
         basis="TBK 584/1: rıza kefalet sözleşmesinin kurulmasına göre; ana sözleşme tarihi değil"),
]


def case_answers(case: Case) -> Dict[str, str]:
    return {**BASE_ANSWERS, **case.answers}


__all__ = ["Case", "CASES", "BASE", "case_answers"]
