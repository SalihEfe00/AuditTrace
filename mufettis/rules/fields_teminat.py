# -*- coding: utf-8 -*-
"""Kefalet dışı teminat belgelerinin alanları (M9.3, blok 2): ipotek, taşınır rehni, ekspertiz raporu, DASK
poliçesi, senet / genel kredi sözleşmesi. Ortak üç durumlu etiket çıkarıcıyla (mufettis.rules.fields).
"""
from __future__ import annotations

import re
from typing import Dict, List

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.rules.engine import FILLED, FieldValue
from mufettis.rules.fields import UNDECIDED, as_amount, as_date, as_money, extract_labeled_fields, one_of

DOCUMENT_LABELS: Dict[str, Dict[str, List[str]]] = {
    "ipotek": {
        "malik": [r"(?:taşınmaz )?malik(?:i)?", r"ipotek veren"],
        "tasinmaz": [r"taşınmaz(?:ın)?(?: bilgileri| niteliği)?", r"ada ?/ ?parsel", r"ada parsel"],
        "ipotek_tutari": [r"(?:tescil edilen |tesis olunan )?ipotek (?:tutarı|bedeli|miktarı)"],
        "derece": [r"(?:ipotek )?derece(?:si)?(?: ?/ ?sıra)?"],
        "tescil_tarihi": [r"(?:tescil|yevmiye) tarihi"],
        "yevmiye_no": [r"yevmiye (?:no|numarası)"],
        "alacakli": [r"alacaklı(?: banka)?", r"ipotek alacaklısı"],
    },
    "rehin": {
        "rehin_veren": [r"rehin veren"],
        "rehin_konusu": [r"rehin(?:in)? konusu(?: taşınır| cinsi| mal(?:ın)? cinsi)?", r"rehinli (?:taşınır|varlık)"],
        "rehin_tutari": [r"rehin (?:tutarı|bedeli|miktarı)", r"azam[iî] (?:rehin |teminat |güvence )?(?:tutar[ıi]?|miktar[ıi]?|limiti?)"],
        "sicil_tescil_tarihi": [r"(?:rts |sicil )?tescil tarihi", r"rehinli taşınır sicili kayıt tarihi",
                                r"(?:rehinli taşınır )?sicil(?:i|ine) tescil tarihi"],
        "sicil_no": [r"(?:rts |rehinli taşınır )?sicil(?:i)? (?:kayıt )?(?:no|numarası)", r"kayıt (?:no|numarası)"],
        "imza_onayi": [r"imza onayı", r"onay şekli"],
        "sozlesme_tarihi": [r"(?:rehin )?sözleşme(?:nin)? tarihi", r"düzenleme tarihi", r"tarih(?:i)?"],
    },
    "ekspertiz": {
        # Raporun tarihi (md. 14/3-a-1) ile inceleme / değerleme tarihi ayrı: biri öbürünün yerine geçmez (M9.4h)
        "rapor_tarihi": [r"(?:değerleme |ekspertiz )?rapor(?:u|unun)? tarihi", r"(?:rapor )?(?:tanzim|düzenleme|düzenlenme) tarihi"],
        "inceleme_tarihi": [r"(?:ekspertiz|değerleme|inceleme|keşif) tarihi"],
        # Md. 14 nihai değer ister: "Nihai Değer" etiketi varsa o okunur, yoksa piyasa / pazar değeri
        "deger": [r"(?:takdir edilen )?nihai değer(?:i)?", r"(?:piyasa|pazar|tespit edilen) değer(?:i)?",
                  r"değer(?:i)?", r"değerleme sonucu"],
        "degerleme_kurulusu": [r"değerleme (?:kuruluşu|şirketi)", r"ekspertiz (?:firması|şirketi)"],
        "eksper": [r"(?:sorumlu )?(?:değerleme uzmanı|eksper)"],
        "lisans_no": [r"(?:spk |eksper |uzman |değerleme uzmanı )?lisans (?:sicil )?(?:no|numarası|bilgisi)"],
    },
    "dask": {
        "police_no": [r"poliçe (?:no|numarası|ref\.?)"],
        "baslangic_tarihi": [r"(?:poliçe |sigorta )?başla(?:ngıç|ngıcı|ma)(?: tarihi)?", r"yürürlük (?:tarihi|başla(?:ngıcı|ngıç)(?: tarihi)?)"],
        "bitis_tarihi": [r"(?:poliçe |sigorta )?bitiş(?:i)?(?: tarihi)?", r"vade (?:sonu|bitiş)(?: tarihi)?",
                        r"yürürlük (?:bitiş|sonu)(?:i)?(?: tarihi)?"],
        "sigortali": [r"sigortalı(?: adı soyadı)?"],
        "dain_murtehin": [r"da[iı]n[- ]?i? ?mürtehin", r"rehin alacaklısı"],
        "islem_tarihi": [r"tapu (?:işlem|ipotek tescil|tescil) (?:tarihi|günü)"],
    },
    "senet_gks": {
        "borclu": [r"borçlu(?: adı soyadı| unvanı)?", r"kredi kullanan"],
        "tutar": [r"(?:kredi |senet )?(?:tutarı|bedeli|limiti)"],
        "duzenleme_tarihi": [r"(?:düzenleme|sözleşme|tanzim) tarihi", r"tarih"],
        "vade_tarihi": [r"vade(?: tarihi)?"],
        "imza": [r"(?:borçlu )?imza(?:sı)?"],
    },
    "bono": {
        "duzenleyen": [r"düzenleyen(?: adı soyadı| unvanı)?", r"borçlu(?: adı soyadı)?"],
        "lehtar": [r"lehtar", r"alacaklı"],
        "tutar": [r"(?:senet |bono )?(?:tutar|bedel)(?:ı|i)?(?: \(rakamla\))?", r"meblağ"],
        "vade_tarihi": [r"vade(?: tarihi)?", r"ödeme günü"],
        "odeme_yeri": [r"ödeme yeri"],
        "duzenleme_tarihi": [r"(?:düzenleme|düzenlenme|tanzim) tarihi"],
        "duzenleme_yeri": [r"(?:düzenleme|düzenlenme|tanzim) yeri"],
        "imza": [r"(?:düzenleyen(?:in)? )?imza(?:sı)?"],
    },
    # Kredi dosyasının teminat dışı belgeleri (M10.1); belgeler arası kurallar mufettis/rules/credit_file.py
    "kredi_onay": {
        "borclu": [r"(?:müşteri|borçlu|kredi talep eden)(?: adı soyadı| unvanı)?", r"firma(?: unvanı)?"],
        "onaylanan_tutar": [r"onaylanan (?:kredi |finansman )?(?:limit|tutar|kredi)\w*", r"onay tutarı",
                            r"tahsis (?:edilen|olunan) (?:kredi |finansman )?(?:limit|tutar)\w*",
                            r"(?:kredi )?limit(?:i)?"],
        "onay_tarihi": [r"(?:onay|karar|tahsis|komite) tarihi"],
        "onay_mercii": [r"onay (?:mercii|makamı|yetkisi)", r"onaylayan(?: (?:merci|komite|yetkili))?",
                        r"karar (?:mercii|organı)"],
        # Onaydaki teminat zamanlaması şartı (M10.2 iç kontrol): kural yalnızca bu şart okunursa uygulanır
        "teminat_sarti": [r"teminat (?:şartı|koşulu|şartları|koşulları|hükmü|kriteri)", r"kullandırım (?:şartı|koşulu|şartları|koşulları)",
                          r"kullandırım öncesi (?:şartlar|koşullar)"],
    },
    "kullandirim": {
        "borclu": [r"(?:müşteri|borçlu|kredi kullanan)(?: adı soyadı| unvanı)?"],
        "kullandirilan_tutar": [r"kullandırılan (?:kredi |finansman )?(?:tutar|kredi|limit)\w*", r"kullandırım tutarı"],
        "kullandirim_tarihi": [r"kullandırım tarihi", r"(?:valör|işlem) tarihi"],
        # Müşteri adına imzalayan (M10.3: imza sirküleri yetkilisi mi); bankanın onaylayan personelinden ayrı
        # Birden çok satır olabilir ("Müşteri İmzası 1: A", "Müşteri İmzası 2: B"): hepsi toplanır (_COLLECT)
        "musteri_imzalayan": [
            r"(?:müşteri|firma|şirket|borçlu)(?: şirket| firma)? (?:adına|tarafından) (?:birlikte )?imza(?:layan(?:lar)?|lar)?",
            r"(?:müşteri|firma|şirket|borçlu)(?: şirket)? (?:yetkilisi|yetkilileri|temsilcisi|temsilcileri)(?: adı soyadı)?",
            r"talimat(?:ı)? veren(?: adı soyadı)?", r"müşteri imza(?:sı)?(?: \d+)? adı soyadı",
            r"müşteri imza(?:sı)? \d+"],
        "imzalayan": [r"(?:onaylayan|imzalayan|banka yetkilisi)(?: personel| adı soyadı)?"],
    },
    "imza_sirkuleri": {
        "unvan": [r"(?:şirket|firma)?(?: unvanı)", r"ticaret unvanı"],
        # "Yetkili 1: A" / "Yetkili 2: B" ya da "Müdürler: A, B": hepsi toplanır (_COLLECT)
        "yetkili": [r"yetkili(?:ler|nin)?(?: \d+)?(?: adı soyadı)?", r"(?:temsile|imza) yetkili(?:leri|ler|si)?(?: \d+)?",
                    r"(?:yetkili |şirket |yönetim kurulu )?temsilci(?:ler|leri)?(?: \d+)?",
                    r"(?:şirket )?müdür(?:ler|leri)?(?: kurulu)?(?: \d+)?",
                    r"(?:idare meclisi|yönetim kurulu) üyeleri"],
        "yetki_sekli": [r"yetki (?:şekli|türü|durumu|kapsamı)", r"temsil (?:şekli|tarzı|yetkisi|ve ilzam şekli)",
                        r"imza (?:şekli|yetkisi)"],
        "yetki_suresi": [r"yetki (?:süresi(?: sonu)?|bitiş tarihi|sona erme tarihi|son geçerlilik tarihi)",
                         r"geçerlilik (?:süresi|tarihi)"],
        "duzenleme_tarihi": [r"(?:düzenleme|onay|tasdik) tarihi"],
    },
}

# Genel kredi sözleşmesini müşteri adına imzalayan (M10.3): kullandırım talimatıyla aynı etiketler
DOCUMENT_LABELS["senet_gks"]["musteri_imzalayan"] = DOCUMENT_LABELS["kullandirim"]["musteri_imzalayan"]

# Kişi listesi alanları: bütün etiketli satırlar birleşir (M10.5a)
_COLLECT = frozenset({"yetkili", "musteri_imzalayan"})

_DATES = {"sozlesme_tarihi", "tescil_tarihi", "sicil_tescil_tarihi", "rapor_tarihi", "inceleme_tarihi", "baslangic_tarihi", "bitis_tarihi",
          "duzenleme_tarihi", "vade_tarihi", "islem_tarihi", "onay_tarihi", "kullandirim_tarihi"}
_AMOUNTS = {"ipotek_tutari", "rehin_tutari", "deger", "tutar"}
_CONVERT = {f: as_date for f in _DATES}
_CONVERT.update({f: as_amount for f in _AMOUNTS})
# Kredi dosyası tutarları döviz de olabilir: para birimiyle (M10.3)
_CONVERT.update({f: as_money for f in ("onaylanan_tutar", "kullandirilan_tutar")})
_CONVERT["imza_onayi"] = one_of([(r"noter", "noter"), (r"sicil(?:i)? yetkilisi", "sicil_yetkilisi"),
                                  (r"güvenli elektronik imza", "guvenli_e_imza"),
                                  (r"elektronik imza|e-imza", UNDECIDED)])  # güvenli mi belli değil
# "Önce" ile "aynı gün / eş zamanlı" aynı kurala bağlanır: kural aynı günü karşılandı sayar, sonraki tescili bulgu
# (M10.5a; önceden eş zamanlı "sonra" sayılıyordu ve geç tescil bulgusuz kalıyordu)
_CONVERT["teminat_sarti"] = one_of([
    # "kredi serbest bırakılmadan / verilmeden / açılışından önce": kredinin kullanıma açılmasından önce
    # "En geç aynı gün" ayrı değer (M9.7c): "önce" şartında aynı gün tescil belirsizdir (saat yok), bu şartta karşılar
    (r"aynı gün|eş zamanlı|kullandırım\w*\s+(?:ile\s+)?birlikte", "en_gec_kullandirim_gunu"),
    (r"(?:kullandır\w*|kredi\w*)(?:\s+\w+){0,3}?\s+(?:öncesi\w*|önce|evvel)|tesis edil\w*\s+sonra\s+kullandır|"
     r"(?:alınmadan|tesis edilmeden)\s+kullandır", "kullandirim_oncesi"),
    (r"kullandır\w*\s+sonra", "kullandirim_sonrasi"),
    (r".", UNDECIDED),  # şart başka bir şey söylüyor: tahmin edilmez, müfettişe sorulur
])
# İmza yetkisinin şekli (M10.3): müşterek ise en az iki yetkilinin imzası gerekir
_SOLE, _JOINT = r"(?:münferi[td]|tek başına|tek imza|ayrı ayrı)", r"(?:müşterek|birlikte|çift imza)"
_CONVERT["yetki_sekli"] = one_of([  # ikisi birden ("münferiden veya müştereken", kişiye göre değişen) → müfettişe sorulur
    (_SOLE + r".*" + _JOINT + "|" + _JOINT + r".*" + _SOLE, UNDECIDED),
    (_JOINT + r"|iki(?:si)? (?:yetkili|imza)", "müşterek"),
    (_SOLE, "münferit"),
])


def _authority_end(raw: str) -> object:
    """İmza yetkisinin bitişi (M10.3): bitiş tarihi, "süresiz" ya da UNDECIDED. "01.01.2024'ten itibaren 3 yıl" gibi
    başlangıç + süre yazımında tarih bitiş sanılmaz (hesap yapılmaz, müfettişe sorulur)."""
    low = turkish_lower(raw)
    if re.search(r"süresiz|sınırsız|azl|aksi (?:karar|bildirim)", low):
        return "süresiz"
    date = as_date(raw)
    if date and (re.search(r"kadar|bitiş|sona er", low) or not re.search(r"\b(?:yıl|ay|gün)\b|itibaren|başla", low)):
        return date
    return UNDECIDED


_CONVERT["yetki_suresi"] = _authority_end
_CONVERT["derece"] = one_of([(r"\b1\b|birinci", "1"), (r"\b2\b|ikinci", "2"), (r"\b3\b|üçüncü", "3")])


def extract_teminat_fields(document_type: str, text: str) -> Dict[str, FieldValue]:
    """Belge türünün alanları; bilinmeyen tür → ValueError."""
    labels = DOCUMENT_LABELS.get(document_type)
    if labels is None:
        raise ValueError(f"bilinmeyen teminat belgesi türü: {document_type}")
    # Md. 14/3: raporu hazırlayan her uzmanın lisans numarası; "Lisans No" satırlarından biri boşsa alan boştur
    out = extract_labeled_fields(text, labels, {k: v for k, v in _CONVERT.items() if k in labels},
                                 every=frozenset({"lisans_no"}), collect=_COLLECT)
    if document_type == "imza_sirkuleri" and getattr(out.get("yetki_suresi"), "state", None) != FILLED:
        # "Yetki Bitiş Tarihi: Belirtilmemiş" ama başka satırda "Yetki süresi: süresiz / azledilinceye kadar" (M10.5b)
        low = turkish_lower(text or "")
        m = re.search(r"(?m)^.*(?:yetki süresi\W+süresiz|azledilinceye kadar|süresiz olarak).*$", low)
        if m:
            line = low.count("\n", 0, m.start()) + 1
            out["yetki_suresi"] = FieldValue.filled("süresiz", f"satır {line}: {text[m.start():m.end()].strip()[:120]}")
    return out


__all__ = ["extract_teminat_fields", "DOCUMENT_LABELS"]
