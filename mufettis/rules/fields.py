# -*- coding: utf-8 -*-
"""Etiketli belge alanlarının üç durumlu çıkarımı (M9.3), bütün teminat belgeleri için ortak.

Alan bir ya da daha çok etiketle aranır. Etiketten sonra iki nokta ya da doldurma yeri gelmelidir (tek başına
başlık satırı etiket değildir). Değer boşsa ("……", "____", yalnızca "İmza:") `boş`, doluysa `dolu(değer, konum)`.
Dolu ama biçimi tanınmayan değer (ör. "12 Mart 2024" gibi yazılmış tarih) ham metniyle `dolu` kalır: motor
biçim isteyen kontrolde "belirsiz" der, asla yanlışlıkla "boş" saymaz. Etiket yoksa alan sözlükte yer almaz
(`bulunamadı`).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Callable, Dict, List, Optional, Tuple

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.rules.engine import EMPTY, NOT_FOUND, FieldValue

_BLANK = re.compile(r"^[\s.…_\-–:/|]*$")
_PLACEHOLDER = re.compile(r"(?:ad[ıi] ?soyad[ıi]|imza|tarih|kaşe)\s*[:：]?[\s.…_/]*")
# Etiketten sonra iki nokta ya da doldurma yeri gelmeli; tek başına başlık satırı ("EŞ RIZASI") etiket değildir.
_LABEL_END = r"[ \t]*(?:\([^)\n]*\))?[ \t]*(?:[:：]|(?=[.…_]{3,}))"

_DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_AMOUNT = re.compile(r"(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|₺|türk lirası)", re.I)


def as_date(raw: str) -> Optional[dt.date]:
    m = _DATE.search(raw)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def as_amount(raw: str) -> Optional[str]:
    m = _AMOUNT.search(raw)
    return m.group(1).replace(".", "").replace(",", ".") if m else None


def one_of(choices: List[Tuple[str, str]]) -> Callable[[str], Optional[str]]:
    """Değeri (kalıp, normal değer) listesinden ilk eşleşene çevirir."""
    def convert(raw: str) -> Optional[str]:
        low = turkish_lower(raw)
        return next((value for pattern, value in choices if re.search(pattern, low)), None)
    return convert


def _line_value(text: str, low: str, label: str) -> Optional[Tuple[str, str]]:
    m = re.search(r"(?m)^[ \t]*(?:[-•*\d.)]+\s*)?" + label + _LABEL_END + r"[ \t]*(.*)$", low)
    if not m:
        return None
    line_no = low.count("\n", 0, m.start()) + 1
    value = text[m.start(1):m.start(1) + len(m.group(1))].strip()
    return value, f"satır {line_no}: {text[m.start():m.end()].strip()[:120]}"


def is_blank(raw: str) -> bool:
    return bool(_BLANK.match(raw)) or bool(_PLACEHOLDER.fullmatch(turkish_lower(raw)))


# Dönüştürücü değeri tanıdı ama karar için yetersiz bulduysa (ör. "elektronik imza", güvenli mi belli değil) bunu
# döndürür: alan "bulunamadı" kalır, konumu not edilir ve kural belirsiz der (tanınmayan ham değer gibi
# "karşılanmadı"ya gitmez).
UNDECIDED = object()


def extract_labeled_fields(text: str, labels: Dict[str, List[str]],
                           convert: Optional[Dict[str, Callable[[str], object]]] = None) -> Dict[str, FieldValue]:
    """Alan adı → FieldValue (etiketi bulunmayan alan yok). `convert` alanın değerini normalleştirir; tanınmayan
    değer ham metniyle dolu kalır."""
    text = text or ""
    low = turkish_lower(text)
    assert len(low) == len(text)  # konumlar küçük harfli metinden okunur
    convert = convert or {}
    out: Dict[str, FieldValue] = {}
    for field, patterns in labels.items():
        for label in patterns:
            hit = _line_value(text, low, label)
            if hit is None:
                continue
            raw, where = hit
            if is_blank(raw):
                out[field] = FieldValue(EMPTY, None, where)
            else:
                fn = convert.get(field)
                value = fn(raw) if fn else raw
                if value is UNDECIDED:
                    out[field] = FieldValue(NOT_FOUND, None, f"{where} (değer karar için yetersiz)")
                else:
                    out[field] = FieldValue.filled(value if value is not None else raw, where)
            break
    return out


__all__ = ["UNDECIDED", "extract_labeled_fields", "as_date", "as_amount", "one_of", "is_blank"]
