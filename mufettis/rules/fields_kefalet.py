# -*- coding: utf-8 -*-
"""Kefalet sözleşmesi alanları (M9.3): metinden üç durumlu çıkarım.

Her alan bir ya da daha çok etiketle aranır ("Eş Rızası", "Eşin rızası" …). Etiket bulunursa ardındaki değer
okunur: boş değer kalıpları ("……", "....", "____", yalnızca "İmza:") `boş`, gerçek değer `dolu(değer, konum)`.
Etiket hiç yoksa `bulunamadı` (OCR kaçırmış olabilir; kural motoru "belirsiz" der).

El yazısı olup olmadığı metinden anlaşılmaz: `el_yazisi_*` alanları burada çıkarılmaz (bulunamadı kalır) ve
arayüz müfettişe sorar (müfettiş beyanı, M9.3 kullanıcı kararı). Belirlenimci; yalnızca düzenli ifadeler.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Dict, List, Optional, Tuple

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.rules.engine import EMPTY, FieldValue

# Boş değer: noktalı / alt çizgili / boşluklu doldurma yerleri ya da hiçbir şey.
_BLANK = re.compile(r"^[\s.…_\-–:/|]*$")
# Etiketten sonra iki nokta ya da doldurma yeri gelmeli; tek başına başlık satırı ("EŞ RIZASI") etiket değildir.
_LABEL_END = r"[ \t]*(?:\([^)\n]*\))?[ \t]*(?:[:：]|(?=[.…_]{3,}))"

# alan → etiket kalıpları (küçük harfe çevrilmiş metinde; Türkçe ekler tolere edilir)
LABELS: Dict[str, List[str]] = {
    "kefil": [r"kefil(?:in)?(?: adı soyadı| ad[ıi] ?soyad[ıi]| unvanı)?"],
    "borclu": [r"(?:asıl )?borçlu(?:nun)?(?: adı soyadı| unvanı)?", r"kredi kullanan"],
    "alacakli": [r"alacaklı(?: banka)?", r"banka unvanı"],
    "azami_tutar": [r"(?:kefilin sorumlu olacağı )?azam[iî] (?:kefalet )?(?:tutar[ıi]?|miktar[ıi]?)", r"kefalet (?:tutarı|limiti)"],
    "sozlesme_tarihi": [r"(?:sözleşme|kefalet) tarihi", r"düzenleme tarihi"],
    "kefalet_turu": [r"kefalet(?:in)? türü", r"kefalet şekli"],
    "es_rizasi": [r"eş(?:in)? rızası", r"eşin muvafakati", r"eş muvafakatnamesi"],
    "es_rizasi_tarihi": [r"(?:eş(?:in)? )?rıza tarihi", r"muvafakat tarihi"],
    "kefil_medeni_hal": [r"(?:kefilin )?medeni hal[iı]?"],
    "imza_turu": [r"imza türü"],
}

_DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_AMOUNT = re.compile(r"(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺|türk lirası)", re.I)


def _line_value(text: str, label: str) -> Optional[Tuple[str, str]]:
    """Etiketin ilk geçtiği satırdaki değer ve konum ('satır N: …'). Etiket yoksa None."""
    low = turkish_lower(text)
    m = re.search(r"(?m)^[ \t]*(?:[-•*\d.)]+\s*)?" + label + _LABEL_END + r"[ \t]*(.*)$", low)
    if not m:
        return None
    line_no = low.count("\n", 0, m.start()) + 1
    start = m.start(1)
    value = text[start:start + len(m.group(1))].strip()
    return value, f"satır {line_no}: {text[m.start():m.end()].strip()[:120]}"


def _value_for(field: str, raw: str):
    if field in ("sozlesme_tarihi", "es_rizasi_tarihi"):
        m = _DATE.search(raw)
        if not m:
            return None
        try:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    if field == "azami_tutar":
        m = _AMOUNT.search(raw)
        return m.group(1).replace(".", "").replace(",", ".") if m else None
    if field == "kefalet_turu":
        low = turkish_lower(raw)
        return "müteselsil" if "müteselsil" in low else ("adi" if re.search(r"\badi\b", low) else None)
    if field == "kefil_medeni_hal":
        low = turkish_lower(raw)
        for key in ("evli", "bekar", "bekâr", "boşanmış", "dul"):
            if key in low:
                return "bekar" if key == "bekâr" else key
        return None
    if field == "imza_turu":
        low = turkish_lower(raw)
        return "e-imza" if ("elektronik" in low or "e-imza" in low) else ("ıslak" if "ıslak" in low else None)
    return raw


def _is_blank(raw: str) -> bool:
    return bool(_BLANK.match(raw)) or bool(re.fullmatch(r"(?:ad[ıi] ?soyad[ıi]|imza|tarih)\s*[:：]?[\s.…_]*",
                                                         turkish_lower(raw)))


def extract_kefalet_fields(text: str) -> Dict[str, FieldValue]:
    """Alan adı → FieldValue. Etiketi bulunmayan alan sözlükte yer almaz (motor 'bulunamadı' sayar)."""
    out: Dict[str, FieldValue] = {}
    for field, labels in LABELS.items():
        for label in labels:
            hit = _line_value(text or "", label)
            if hit is None:
                continue
            raw, where = hit
            if _is_blank(raw):
                out[field] = FieldValue(EMPTY, None, where)
            else:
                value = _value_for(field, raw)
                out[field] = FieldValue.filled(value, where) if value is not None else FieldValue(EMPTY, None, where)
            break
    # Belgenin herhangi bir yerinde "müteselsil kefil" geçiyorsa tür etiketi olmasa da bellidir.
    if "kefalet_turu" not in out:
        m = re.search(r"müteselsil\s+kefil", turkish_lower(text or ""))
        if m:
            line = (text or "").count("\n", 0, m.start()) + 1
            out["kefalet_turu"] = FieldValue.filled("müteselsil", f"satır {line}: müteselsil kefil")
    return out


__all__ = ["extract_kefalet_fields", "LABELS"]
