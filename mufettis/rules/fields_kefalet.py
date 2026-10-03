# -*- coding: utf-8 -*-
"""Kefalet sözleşmesi alanları (M9.3): ortak etiket çıkarıcıyla (mufettis.rules.fields) üç durumlu çıkarım.

El yazısı olup olmadığı metinden anlaşılmaz: `el_yazisi_*` alanları burada çıkarılmaz (bulunamadı kalır) ve
arayüz müfettişe sorar (müfettiş beyanı, M9.3 kullanıcı kararı). Belirlenimci; yalnızca düzenli ifadeler.
"""
from __future__ import annotations

import re
from typing import Dict, List

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.rules.engine import FieldValue
from mufettis.rules.fields import UNDECIDED, as_amount, as_date, extract_labeled_fields, one_of

# alan → etiket kalıpları (küçük harfe çevrilmiş metinde; Türkçe ekler tolere edilir)
LABELS: Dict[str, List[str]] = {
    "kefil": [r"kefil(?:in)?(?: adı soyadı| ad[ıi] ?soyad[ıi]| unvanı)?"],
    "borclu": [r"(?:asıl )?borçlu(?:nun)?(?: adı soyadı| unvanı)?", r"kredi kullanan"],
    "alacakli": [r"alacaklı(?: banka)?", r"banka unvanı"],
    "azami_tutar": [r"(?:kefilin sorumlu olacağı )?azam[iî] (?:kefalet )?(?:tutar[ıi]?|miktar[ıi]?)", r"kefalet (?:tutarı|limiti)"],
    # TBK 583 "kefalet tarihi" ile ana sözleşmenin tarihi ayrı alanlar (M9.7b); kefalet sözleşmesinde kefalet tarihi
    # etiketi yoksa sözleşme tarihi kefalet tarihidir (aşağıda).
    "kefalet_tarihi": [r"kefalet tarihi"],
    "sozlesme_tarihi": [r"sözleşme tarihi", r"düzenleme tarihi"],
    "kefalet_turu": [r"kefalet(?:in)? türü", r"kefalet şekli"],
    "es_rizasi": [r"eş(?:in)? rızası", r"eşin muvafakati", r"eş muvafakatnamesi"],
    "es_rizasi_tarihi": [r"(?:eş(?:in)? )?rıza tarihi", r"muvafakat tarihi"],
    "kefil_medeni_hal": [r"(?:kefilin )?medeni hal[iı]?"],
    "imza_turu": [r"imza türü", r"kefil(?:in)? imzası"],
    "ayrilik_durumu": [r"ayrılık (?:kararı|durumu)", r"ayrı yaşama"],
    "kefil_ticari_sifat": [r"kefil(?:in)? (?:ticari )?sıfatı", r"kefil(?:in)? mesleği"],
    "kredi_turu": [r"kredi(?:nin)? türü", r"kredi cinsi"],
}

CONVERT = {
    "sozlesme_tarihi": as_date,
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
                         (r"elektronik|e-imza", UNDECIDED), (r"ıslak", "ıslak"), (r"imzasız|imza yok", "imzasiz")]),
    "ayrilik_durumu": one_of([(r"mahkeme|ayrılık kararı", "mahkeme_ayrilik_karari"),
                              (r"ayrı yaşama hakkı", "yasal_ayri_yasama"), (r"\byok\b", "yok")]),
    # TBK 584/3 istisnasının koşul değerleri (katalogdaki kefil_ticari_sifat değerleri)
    "kefil_ticari_sifat": one_of([
        (r"esnaf|sanatkâr|sanatkar", "esnaf_sanatkar"),
        (r"ortak|yönetici|müdür|yönetim kurulu", "sirket_ortagi_yoneticisi"),
        (r"tacir|işletme sahibi|ticari işletme", "tacir_isletme_sahibi"),
        (r"kooperatif", "kooperatif_kredisi"),
        (r"\byok\b|bireysel|ücretli|çalışan|memur|emekli|ev hanımı|öğrenci", "yok"),
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
    return out


__all__ = ["extract_kefalet_fields", "LABELS"]
