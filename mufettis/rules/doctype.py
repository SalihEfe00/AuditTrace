# -*- coding: utf-8 -*-
"""Teminat belgesinin türü (M9.3 / M9.5 / M9.4c): başlık + belgeye özgü etiketlerden puanlama, belirlenimci.

İlk sürüm yalnızca birkaç sabit ifadeye bakıyordu; kullanıcı vakalarında "KEFİL SÖZLEŞMESİ", "MENKUL REHİN
MUKAVELESİ", başlıksız değerleme özeti ve taranmış (OCR bozuk) belgeler tanınmadı. Şimdi her tür için:

- başlık puanı: ilk satırlarda türün adı (3 puan; bono başlıktan ya da başlık yoksa ödeme vaadi + etiketlerden)
- gövde puanı: metinde türe özgü ifade (1 puan)
- etiket puanı: o türe özgü etiketli alanlardan kaçı belgede var (alan başına 1 puan)

En yüksek puanlı tür seçilir (en az 2 puan). Eşleştirme, taranmış metindeki tipik karışmalara (ş→s, ı→i, $→ş,
harf arasında 1→i) karşı Türkçe harfleri sadeleştirilmiş kopya üzerinde yapılır.

TBK 603: kefaletin şekline ve eşin rızasına ilişkin hükümler, gerçek kişilerin başka ad altında verdiği kişisel
güvence sözleşmelerine de uygulanır. Bu yüzden gerçek kişinin "garanti sözleşmesi", "taahhütname", "kişisel
güvence" belgesi `kefalet_sozlesmesi` kurallarına bağlanır (`via_603=True`). Tüzel kişinin (banka teminat mektubu,
şirket garantisi) belgesi bu eşlemeye girmez.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from mufettis.analysis.turkish_text import turkish_lower

_FOLD = str.maketrans("şığüöçâîû$", "siguocaius")


def _fold(text: str) -> str:
    """Küçük harf + Türkçe harfleri sadeleştir + harf arasındaki 1/0 karışmalarını düzelt (yalnızca tür tanıma için)."""
    low = turkish_lower(text).translate(_FOLD)
    low = re.sub(r"(?<=[a-z])1(?=[a-z])|(?<=[a-z])1\b", "i", low)
    return re.sub(r"(?<=[a-z])0(?=[a-z])", "o", low)


# tür → (başlık kalıbı, gövde kalıbı, türe özgü etiket kalıpları); hepsi sadeleştirilmiş metinde
_TYPES = {
    "dask": (r"deprem sigorta|\bdask\b", r"zorunlu deprem|\bdask\b",
             [r"police (?:no|numarasi|ref)", r"dain[- ]?i? ?murtehin", r"tapu islem tarihi", r"baslangic", r"bitis"]),
    "ekspertiz": (r"ekspertiz|degerleme rapor|gayrimenkul degerleme|degerleme ozet",
                  r"degerleme (?:raporu|uzmani|kurulusu)|ekspertiz",
                  [r"degerleme (?:kurulusu|sirketi)", r"(?:sorumlu )?degerleme uzmani|eksper", r"lisans (?:no|numarasi)",
                   r"rapor (?:tarihi|no)", r"(?:piyasa|nihai) deger"]),
    "ipotek": (r"ipotek", r"ipotek (?:akit tablosu|sozlesmesi|belgesi|resmi senedi)|resmi senet.{0,40}ipotek",
               [r"ipotek (?:tutari|bedeli|miktari)", r"yevmiye", r"derece", r"ada", r"parsel"]),
    "rehin": (r"rehin", r"(?:tasinir |menkul )?rehin (?:sozlesmesi|mukavelesi)|rehinli tasinir sicili",
              [r"rehin veren", r"rehin(?:in)? konusu", r"rehin (?:tutari|bedeli|miktari)", r"sicil(?:i)? (?:kayit )?no",
               r"imza onayi"]),
    "kefalet_sozlesmesi": (r"kefalet|kefil|kefaletname", r"kefalet sozlesmesi|muteselsil kefil|kefaletname",
                           [r"kefil(?:in)? ad", r"azami (?:kefalet|sorumluluk)|kefalet (?:tutari|limiti)",
                            r"kefalet(?:in)? (?:turu|sekli)", r"es(?:in)? (?:rizasi|muvafakat)", r"medeni (?:hal|durum)"]),
    "bono": (r"^(?:bono|emre (?:yazili|muharrer) senet)\b", None,
             [r"lehtar", r"duzenleyen", r"odeme yeri", r"duzen(?:le|len)me yeri"]),
    "senet_gks": (r"(?:genel )?kredi sozlesmesi", r"genel kredi sozlesmesi", []),
    # Kredi dosyasının teminat dışı belgeleri (M10.1): başlıkla ya da en az iki özgü etiketle
    "kredi_onay": (r"kredi (?:teklif|onay|tahsis)|teklif ve onay|komite karar|tahsis karar",
                   r"kredi komitesi|onaylanan (?:limit|tutar)|tahsis edilen limit",
                   [r"onay (?:tarihi|mercii)|onaylayan", r"onaylanan (?:limit|tutar|kredi)|tahsis edilen (?:limit|tutar)",
                    r"karar (?:tarihi|no)", r"teklif edilen (?:limit|tutar)"]),
    "kullandirim": (r"kullandirim", r"kullandirim (?:tarihi|talimati)|kullandirilan tutar",
                    [r"kullandirim tarihi", r"kullandirilan (?:tutar|kredi|limit)", r"kullandirim hesabi",
                     r"(?:valor|islem) tarihi"]),
    "imza_sirkuleri": (r"imza sirkuler|imza beyannamesi", r"imza sirkuleri|temsile yetkili",
                       [r"yetkili(?:ler|nin)?(?: adi soyadi)?", r"yetki (?:sekli|turu|siniri|suresi)",
                        r"(?:munferit|musterek) (?:imza|yetki)"]),
}
_BONO_PROMISE = r"emrine.{0,80}odeyecegi[mz]|odeyecegi[mz].{0,40}emrine|kayitsiz sartsiz.{0,60}odeyecegi[mz]"
_ORDER = list(_TYPES)  # eşitlikte önce gelen (daha özgül) seçilir
_PERSONAL_GUARANTEE = r"garanti sozlesmesi|taahhutname|kisisel guvence|sahsi teminat"
_LEGAL_ENTITY = r"(?:\ba\.s\.|\bltd\b\.?|limited sirketi|anonim sirketi).{0,80}(?:garantor|garanti eden|taahhut eden)|teminat mektubu"


@dataclass(frozen=True)
class DocType:
    document_type: Optional[str]
    via_603: bool = False
    reason: str = ""


def _label_hits(text: str, patterns) -> int:
    return sum(bool(re.search(r"(?m)^[ \t]*(?:[-•*\d.)]+\s*)?" + p + r"[^:\n]{0,40}[:：]", text)) for p in patterns)


def detect_collateral_type(text: str) -> DocType:
    head = _fold((text or "")[:4000])
    # Başlık: ilk satırlardan "etiket: değer" ya da madde olmayan kısa satırlar (gövde cümlesi başlık sayılmaz)
    lines = [ln.strip(" *=#-_|[]") for ln in head.splitlines()[:8]]
    title = "\n".join(ln for ln in lines if ln and ":" not in ln and len(ln) <= 100)
    scores = {}
    for kind, (title_pat, body_pat, labels) in _TYPES.items():
        score = 0
        if re.search(title_pat, title, re.M):
            score += 3
        if body_pat and re.search(body_pat, head):
            score += 1
        score += _label_hits(head, labels)
        scores[kind] = score
    # Kredi sözleşmesi metni teminat olarak bonoyu anabilir: başlık yoksa bono ancak düzenleyenin birinci ağızdan
    # ödeme vaadi ("... emrine ödeyeceğim") ve bono etiketleriyle tanınır (başlığı taranmamış / ayrı sayfada bono).
    if not re.search(_TYPES["bono"][0], title, re.M):
        promise = re.search(_BONO_PROMISE, head)
        scores["bono"] = scores["bono"] + 1 if promise and scores["bono"] >= 1 else 0
    best = max(_ORDER, key=lambda k: (scores[k], -_ORDER.index(k)))
    if scores[best] >= 2:
        return DocType(best, False, f"puan {scores[best]} (başlık / ifade / etiket)")
    m = re.search(_PERSONAL_GUARANTEE, head)
    if m and not re.search(_LEGAL_ENTITY, head):
        return DocType("kefalet_sozlesmesi", True,
                       f"ifade: {m.group(0)}; gerçek kişinin kişisel güvencesi (TBK 603: kefalet şekil ve eş rızası hükümleri uygulanır)")
    return DocType(None, False, "teminat belgesi ifadesi bulunamadı")


__all__ = ["DocType", "detect_collateral_type"]
