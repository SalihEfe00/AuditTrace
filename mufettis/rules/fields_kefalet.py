# -*- coding: utf-8 -*-
"""Kefalet sözleşmesi alanları (M9.3): ortak etiket çıkarıcıyla (mufettis.rules.fields) üç durumlu çıkarım.

El yazısı olup olmadığı metinden anlaşılmaz: `el_yazisi_*` alanları burada çıkarılmaz (bulunamadı kalır) ve
arayüz müfettişe sorar (müfettiş beyanı, M9.3 kullanıcı kararı). Belirlenimci; yalnızca düzenli ifadeler.
"""
from __future__ import annotations

import re
from typing import Dict, List

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.rules.engine import EMPTY, FILLED, NOT_FOUND, FieldValue
from mufettis.rules.fields import UNDECIDED, as_amount, as_date, extract_labeled_fields, one_of

# alan → etiket kalıpları (küçük harfe çevrilmiş metinde; Türkçe ekler tolere edilir)
LABELS: Dict[str, List[str]] = {
    "kefil": [r"kefil(?:in)?(?: adı soyadı| ad[ıi]? ?soyad[ıi]?| unvanı)?"],
    "borclu": [r"(?:asıl )?borçlu(?:nun)?(?: adı soyadı| unvanı)?", r"kredi kullanan"],
    "alacakli": [r"alacaklı(?: banka)?", r"banka unvanı"],
    "azami_tutar": [r"(?:kefilin sorumlu olacağı )?azam[iî] (?:kefalet |sorumluluk )?(?:tutar[ıi]?|miktar[ıi]?)",
                    r"azam[iî] kefalet (?:sorumluluğu|limiti)", r"kefalet (?:tutarı|limiti|miktarı)",
                    r"sorumluluk (?:limiti|tutarı|miktarı)"],
    # TBK 583 "kefalet tarihi" ile ana sözleşmenin tarihi ayrı alanlar (M9.7b); kefalet sözleşmesinde kefalet tarihi
    # etiketi yoksa sözleşme tarihi kefalet tarihidir (aşağıda).
    "kefalet_tarihi": [r"kefalet (?:veriliş |düzenleme |tesis(?:i)? )?tarihi"],
    # Ana kredi sözleşmesinin tarihi ayrı alan: kefalet tarihinin yerine geçmez (M9.4g)
    "sozlesme_tarihi": [r"sözleşme(?:si|nin)? tarihi", r"düzenleme tarihi", r"tanzim tarihi"],
    "kredi_sozlesme_tarihi": [r"(?:genel |ana )?kredi sözleşme(?:si|sinin)? tarihi"],
    "kefalet_turu": [r"kefalet(?:in)? türü", r"kefalet şekli"],
    "es_rizasi": [r"eş(?:in)? rızası", r"eş(?:in)? (?:muvafakati|onayı|izni)", r"eş muvafakatnamesi"],
    "es_rizasi_tarihi": [r"(?:eş(?:in)? )?rıza tarihi", r"eş(?:in)? rızası(?:nın)? tarihi", r"muvafakat tarihi"],
    "kefil_medeni_hal": [r"(?:kefil(?:in)? )?medeni (?:hal[iı]?|durum(?:u)?)"],
    "imza_turu": [r"imza türü", r"imza şekli"],
    "ayrilik_durumu": [r"ayrılık (?:kararı|durumu)", r"ayrı yaşama"],
    "kefil_ticari_sifat": [r"(?:kefil(?:in)? )?(?:ticari )?sıfat(?:ı)?", r"(?:kefil(?:in)? )?mesle(?:k|ki|ği)"],
    "kredi_turu": [r"kredi(?:nin)? (?:türü|cinsi|tipi|ürünü)", r"kullandırılan kredi"],
}

CONVERT = {
    "sozlesme_tarihi": as_date,
    "kredi_sozlesme_tarihi": as_date,
    "kefalet_tarihi": as_date,
    "es_rizasi_tarihi": as_date,
    "azami_tutar": as_amount,
    "kefalet_turu": one_of([(r"müteselsil", "müteselsil"), (r"\badi\b", "adi")]),
    "kefil_medeni_hal": one_of([(r"evli", "evli"), (r"bek[aâ]r", "bekar"), (r"boşanmış", "boşanmış"), (r"\bdul\b", "dul")]),
    "kredi_turu": one_of([(r"tüketici|bireysel|ihtiyaç|konut|taşıt", "tüketici"),
                          (r"ticari|kurumsal|işletme|kobi", "ticari")]),
    # 5070 md. 5/2 yalnızca güvenli elektronik imzayı yasaklar; türü yazmayan "elektronik imza" karar için yetersiz
    "imza_turu": one_of([(r"güvenli elektronik|nitelikli elektronik", "guvenli_e_imza"),
                         (r"basit elektronik|güvenli olmayan|ekrana|tablet", "diger_elektronik"),
                         # türü yazmayan e-imza: güvenliyse 5070 yasağı, değilse yazılı şekil eksik (M9.4e)
                         (r"elektronik|e-imza", "e_imza"), (r"ıslak", "ıslak"), (r"imzasız|imza yok", "imzasiz")]),
    "ayrilik_durumu": one_of([(r"mahkeme|ayrılık kararı", "mahkeme_ayrilik_karari"),
                              (r"ayrı yaşama hakkı", "yasal_ayri_yasama"), (r"\byok\b", "yok")]),
    # TBK 584/3 istisnasının koşul değerleri (katalogdaki kefil_ticari_sifat değerleri)
    # Tanınmayan meslek tahmin edilmez: müfettişe sorulur (UNDECIDED, M9.4c)
    "kefil_ticari_sifat": one_of([
        # "Servis müdürü" gibi çalışan unvanı şirket yöneticiliği değildir: yalnızca ortaklık / şirket yöneticiliği
        (r"ortağı|ortak|şirket (?:müdürü|yöneticisi)|yönetim kurulu|limited.{0,20}müdür", "sirket_ortagi_yoneticisi"),
        (r"esnaf|sanatkâr|sanatkar|berber|terzi|bakkal|kuaför|kasap|fırın|manav|tamirci|marangoz|nalbur|"
         r"dükkan|dükkân|zanaat", "esnaf_sanatkar"),
        (r"tacir|işletme sahibi|ticari işletme|şahıs şirketi", "tacir_isletme_sahibi"),
        (r"kooperatif", "kooperatif_kredisi"),
        (r"\byok\b|bireysel|ücretli|çalışan|memur|emekli|ev hanımı|öğrenci|işçi|personel|uzman|mühendis|"
         r"öğretmen|eleman|doktor|hekim|hemşire|teknisyen|şoför|sekreter|muhasebe|avukat|akademisyen", "yok"),
        (r".", UNDECIDED),
    ]),
}


def extract_kefalet_fields(text: str) -> Dict[str, FieldValue]:
    """Alan adı → FieldValue. Etiketi bulunmayan alan sözlükte yer almaz (motor 'bulunamadı' sayar)."""
    out = extract_labeled_fields(text, LABELS, CONVERT)
    if "kefalet_tarihi" not in out and "sozlesme_tarihi" in out:
        fv = out["sozlesme_tarihi"]  # belge kefalet sözleşmesi: sözleşmenin tarihi kefalet tarihidir
        out["kefalet_tarihi"] = FieldValue(fv.state, fv.value, f"{fv.location} (sözleşme tarihi)", fv.source)
    # Belgenin herhangi bir yerinde "müteselsil kefil" geçiyorsa tür etiketi olmasa da bellidir.
    if "kefalet_turu" not in out:
        m = re.search(r"müteselsil\s+kefil", turkish_lower(text or ""))
        if m:
            line = (text or "").count("\n", 0, m.start()) + 1
            out["kefalet_turu"] = FieldValue.filled("müteselsil", f"satır {line}: müteselsil kefil")
    _consent(text, out)
    _guarantor_signature(text, out)
    return out


# Eş rızası bölümünün parçaları (M9.4c): bölümde eşin adı matbu basılı olabilir; rıza, beyan / imza / tarihle verilir.
_CONSENT_PARTS = {
    "ad": [r"(?:muvafakat eden |rıza veren )?eş(?:in)? ad[ıi](?: ?soyad[ıi])?", r"eş"],
    "imza": [r"eş(?:in)? imza(?:sı)?", r"muvafakat eden(?:in)? imzası"],
    "beyan": [r"eş(?:in)? (?:rıza |muvafakat )?beyanı"],
}


_SECTION_HEAD = r"eş(?:in)? (?:rıza|muvaf+akat|onay)|muvaf+akatname"
_SECTION_PARTS = {
    "imza": [r"(?:eş(?:in)? )?imza(?:sı)?"],
    "ad": [r"(?:[^\W\d_]+ ){0,3}ad[ıi](?: ?soyad[ıi])?"],
    "tarih": [r"(?:eş(?:in)? |rıza |imza )*tarih(?:i)?"],
}


def _consent_section(text: str) -> str:
    """Eş rızası başlığından sonraki satırlar; yeni bölüm başlığında (büyük harfli satır) ya da kefil / banka
    satırında biter (onların imza / tarih satırları eşinkiyle karışmasın)."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if re.search(_SECTION_HEAD, turkish_lower(line)):
            block = []
            for nxt in lines[i + 1:i + 13]:
                bare = nxt.strip()
                low = turkish_lower(bare)
                heading = bare and bare.upper() == bare and re.search(r"[A-ZÇĞİÖŞÜ]{3}", bare) and (
                    ":" not in bare or bare.endswith(":"))
                if heading or re.match(r"(?:müteselsil |adi )?kefil(?!in eş)|banka|yetkili|alacaklı", low):
                    break
                block.append(nxt)
            return "\n" * (i + 1) + "\n".join(block)  # satır numaraları belgedekiyle aynı kalsın
    return ""


def _consent(text: str, out: Dict[str, FieldValue]) -> None:
    """Rıza yalnızca matbu metinle ya da adla verilmez (formda eşin adı ve "muvafakat ediyorum" beyanı basılı olabilir):
    eşin imzası doluysa ya da adı ile rıza tarihi birlikte doluysa var; imza yeri boşsa ya da bölümdeki ad / tarih /
    imza öğelerinin hepsi boşsa boş; yalnızca ad varsa karar verilmez (müfettişe sorulur)."""
    direct, printed = out.get("es_rizasi"), None
    if direct is not None and direct.state == FILLED:
        if not re.search(r"muvafakat ediyorum|rıza gösteriyorum|onay veriyorum|razıyım|kabul ediyorum",
                         turkish_lower(str(direct.value))):
            return
        printed, direct = out.pop("es_rizasi"), None  # matbu rıza cümlesi: imza / ad / tarihe bakılır
    parts = extract_labeled_fields(text, _CONSENT_PARTS)
    # Bölümün kendi genel etiketleri ("EŞ MUVAFAKATNAMESİ:" başlığı altında "İmza:", "Tarih:", "Kefilin Eşinin Adı
    # Soyadı:"); özel etiket bulunamayan öğe için kullanılır (M9.4h).
    section = extract_labeled_fields(_consent_section(text), _SECTION_PARTS)
    for key in ("imza", "ad"):
        if key not in parts and key in section:
            parts[key] = section[key]
    date = out.get("es_rizasi_tarihi") or section.get("tarih")
    sign, name = parts.get("imza"), parts.get("ad")
    if direct is not None and direct.state == EMPTY and any(fv.state == FILLED for fv in section.values()):
        direct = None  # boş değerli etiket bölüm başlığıymış: altındaki öğelere bakılır

    def filled(fv):
        return fv is not None and fv.state == FILLED

    core = [fv for fv in (sign, name, date, direct) if fv is not None]
    if filled(sign) or (filled(name) and filled(date)):
        src = sign if filled(sign) else name
        out["es_rizasi"] = FieldValue.filled(src.value, src.location if src is sign else f"{name.location}; {date.location}")
    elif sign is not None and sign.state == EMPTY and filled(date):
        # Kefil imzasındaki gibi: taranmış metin imzayı göstermez; rıza tarihi elle doldurulmuş, imza yeri boş →
        # müfettiş bakar. Eşin adı matbu basılı olabildiğinden yalnızca ad yetmez.
        out["es_rizasi"] = FieldValue(NOT_FOUND, None, f"{sign.location} (eşin imza yeri boş görünüyor; rıza tarihi dolu)")
    elif (sign is not None and sign.state == EMPTY) or (core and all(fv.state == EMPTY for fv in core)):
        out["es_rizasi"] = FieldValue(EMPTY, None, "; ".join(fv.location for fv in core if fv.state == EMPTY)[:200])
    elif filled(name) and direct is None:
        out["es_rizasi"] = FieldValue(NOT_FOUND, None, f"{name.location} (yalnızca eşin adı; rıza imzası görünmüyor)")
    elif printed is not None:
        out["es_rizasi"] = FieldValue(NOT_FOUND, None, f"{printed.location} (matbu rıza cümlesi; imza / tarih görünmüyor)")


def _guarantor_signature(text: str, out: Dict[str, FieldValue]) -> None:
    """Kefilin imza yeri boş görünüyorsa ıslak imza satırına güvenilmez: taranmış metin imzayı göstermez, karar
    müfettişin (TBK 14 kuralı belirsiz kalır ve sorulur). Elektronik imzada imza yerinin boş olması olağandır."""
    sign = extract_labeled_fields(text, {"s": [r"(?:müteselsil |müşterek |adi )?kefil(?:in)? imza(?:sı)?"]}).get("s")
    current = out.get("imza_turu")
    electronic = current is not None and current.value in ("guvenli_e_imza", "diger_elektronik", "e_imza")
    if sign is not None and sign.state == EMPTY and not electronic:
        out["imza_turu"] = FieldValue(NOT_FOUND, None, f"{sign.location} (kefil imza yeri boş görünüyor)")


__all__ = ["extract_kefalet_fields", "LABELS"]
