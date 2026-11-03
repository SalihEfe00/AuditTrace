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

# Parantez de doldurma yerinin parçası olabilir: "____ TL (__________)" (yazıyla tutar yeri, M9.4i)
_BLANK = re.compile(r"^[\s.…_\-–:/|()\[\]]*$")
_PLACEHOLDER = re.compile(r"(?:ad[ıi] ?soyad[ıi]|imza|tarih|kaşe)\s*[:：]?[\s.…_/]*")
# Doldurma yerinin yanındaki birim boş değeri doldurmaz: "…………… TL", "____ ₺", "........ (TL)" (M9.4c)
_UNIT = re.compile(r"\(?\s*(?:tl|₺|türk lirası|try)\s*\)?", re.I)
# Etiketten sonra iki nokta ya da doldurma yeri gelmeli; tek başına başlık satırı ("EŞ RIZASI") etiket değildir.
# Etiketten sonra açıklama ("Bedel (Tutar):", "Rehin Tutarı / Azami Miktar (TL):") gelebilir (M9.4c): parantez ya da
# eğik çizgiyle eklenen kısa nitelemeler etiketin parçası sayılır.
_LABEL_END = r"[ \t]*(?:(?:\([^)\n]*\)|/[^:：\n]{1,40})[ \t]*)*(?:[:：]|(?=[.…_]{3,}))"

# Form kalıbına elle yazılan tarih "12 / 03 / 2024" (M9.4a): yalnızca eğik çizginin çevresinde boşluk
_SEP = r"(?:[./-]|[ \t]+/[ \t]*|/[ \t]+)"
_DATE = re.compile(r"\b(\d{1,2})" + _SEP + r"(\d{1,2})" + _SEP + r"(\d{4})\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık"]
_WORD_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b")
# Tutar yalnızca Türkçe biçimde okunur (1.000.000,00 ya da ayraçsız 1000000). Sayının ortasından başlayan ya da
# ortasında biten eşleşme yasak: "1000000.50 TL" içinden "50", "1,000,000.00" içinden "1,00" okunmasın; tanınmayan
# biçim None olur ve kural belirsiz der (sessizce yanlış tutar yerine).
_NUM = r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)(?!\d|[.,]\d)"
_AMOUNT = re.compile(_NUM + r"\s*(?:TL|₺|türk lirası)", re.I)


def as_date(raw: str) -> Optional[dt.date]:
    """GG.AA.YYYY (/ ya da -), YYYY-AA-GG ya da "12 Mart 2024". Geçersiz bir tarih (OCR: "31.02.2024") aramayı
    bitirmez; sıradaki eşleşmeye bakılır."""
    for pattern, text, parts in (
            (_DATE, raw, lambda m: (int(m.group(3)), int(m.group(2)), int(m.group(1)))),
            (_ISO_DATE, raw, lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
            (_WORD_DATE, turkish_lower(raw),
             lambda m: (int(m.group(3)), _MONTHS.index(m.group(2)) + 1, int(m.group(1))))):
        for m in pattern.finditer(text):
            try:
                return dt.date(*parts(m))
            except ValueError:
                continue
    return None


def as_amount(raw: str) -> Optional[str]:
    m = _AMOUNT.search(raw)
    return m.group(1).replace(".", "").replace(",", ".") if m else None


# Para birimiyle tutar (M10.3: onaylanan ↔ kullandırılan tutar karşılaştırması); değer "1000000 TRY" biçiminde
_CURRENCIES = (("TRY", r"tl|₺|türk lirası|try"), ("USD", r"usd|\$|abd doları|amerikan doları"),
               ("EUR", r"eur|€|avro|euro"), ("GBP", r"gbp|£|sterlin"), ("CHF", r"chf|isviçre frangı"))
_MONEY = re.compile(r"(?:(?P<pre>" + "|".join(p for _, p in _CURRENCIES) + r")\s*)?"
                    r"(?<![\d.,])(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)(?!\d|[.,]\d)"
                    r"(?:\s*(?P<post>" + "|".join(p for _, p in _CURRENCIES) + r"))?", re.I)


def as_money(raw: str) -> Optional[str]:
    """Tutar ve para birimi; birim yazılmamışsa None (birimsiz tutar karşılaştırılmaz)."""
    for m in _MONEY.finditer(raw or ""):
        unit = (m.group("pre") or m.group("post") or "").lower()
        code = next((c for c, p in _CURRENCIES if re.fullmatch(p, unit)), None)
        if code:
            return f"{m.group('num').replace('.', '').replace(',', '.')} {code}"
    return None


# Dönüştürücü değeri tanıdı ama karar için yetersiz bulduysa (ör. "elektronik imza", güvenli mi belli değil) bunu
# döndürür: alan "bulunamadı" kalır, konumu not edilir ve kural belirsiz der (tanınmayan ham değer gibi
# "karşılanmadı"ya gitmez).
UNDECIDED = object()


# Eşleşmeden hemen sonra (en fazla bir sözcük arayla) gelen olumsuzluk: "Evli değil", "Şirket ortağı değildir",
# "Müteselsil olmayan". Böyle bir eşleşme o kategoriyi göstermez; sıradaki kalıba geçilir.
_NEGATION = re.compile(r"\W*(?:[^\W\d_]+\s+)?(?:değil|olmayan|olmadığ)")


def one_of(choices: List[Tuple[str, str]]) -> Callable[[str], object]:
    """Değeri (kalıp, normal değer) listesinden ilk eşleşene çevirir. Hiçbiri eşleşmezse UNDECIDED: tanınmayan
    kategori değeri ("Rotatif kredi") ham metin olarak kalsaydı kural onu "uygulanmaz" ya da "karşılanmadı" sayardı;
    alan bulunamadı kalır ve müfettişe sorulur (M9.4c). Ardından olumsuzluk gelen eşleşme sayılmaz ("Evli değil"
    evli değildir; bekâr mı boşanmış mı bilinmediğinden müfettişe sorulur)."""
    def convert(raw: str) -> object:
        low = turkish_lower(raw)
        for pattern, value in choices:
            if any(not _NEGATION.match(low, m.end()) for m in re.finditer(pattern, low)):
                return value
        return UNDECIDED
    return convert


def _line_value(text: str, low: str, label: str, every: bool = False) -> Optional[Tuple[str, str]]:
    # Aynı etiket birden çok satırda olabilir (form başında boş, sonunda dolu; OCR sütun kayması): doluyu tercih et.
    # `every`: tekrar farklı kişileri gösteriyorsa (her uzmanın lisans no'su) biri boşsa alan boştur (M9.4i).
    found = list(re.finditer(r"(?m)^[ \t]*(?:[-•*\d.)]+\s*)?" + label + _LABEL_END + r"[ \t]*(.*)$", low))
    blank = [is_blank(_NEXT_LABEL.split(text[x.start(1):x.end(1)], maxsplit=1)[0].strip()) for x in found]
    m = next((x for x, b in zip(found, blank) if b == every), found[0] if found else None)
    if not m:
        # Aynı satırda birden çok etiket ("... Sicil No: Tescil Tarihi: 17.08.2021, Sicil No: X"): virgül / noktalı
        # virgülden, önceki etiketin iki noktasından ya da sütun boşluğundan sonra gelen etiket, değeri bir sonraki
        # virgüle kadar (M9.4c, M9.4h).
        m = re.search(r"(?m)(?:[,;]|[:：]|[ \t]{2,}|(?<=[_…])[ \t])[ \t]*" + label + r"[ \t]*[:：][ \t]*([^,;\n]*)", low)
    if not m:
        # İki noktasız form satırı: etiket, sütun boşluğu ve tarih kalıbı ("Sözleşme Tarihi      /   /", M9.4a)
        m = re.search(r"(?m)^[ \t]*" + label + r"[ \t]{3,}([\d \t.]*/[\d \t./]*)$", low)
    if not m:
        return None
    line_no = low.count("\n", 0, m.start()) + 1
    value = _NEXT_LABEL.split(text[m.start(1):m.start(1) + len(m.group(1))], maxsplit=1)[0].strip()
    where = f"satır {line_no}: {text[m.start():m.end()].strip()[:120]}"
    if not value:
        # Değer alt satıra yazılmış olabilir ("İmza:\nGül Soyuer"): kısa, etiketsiz, başlık olmayan satır (M9.4h).
        # Doldurma yeri ("____") boş değer olarak kalır; uzun cümle (bölüm metni) değer sayılmaz.
        # Yalnızca hemen alttaki satır (araya boş satır girerse başka bölümdür); sayfa / üst bilgi satırı değer değildir.
        below = text[m.end():].splitlines()
        nxt = below[1].strip() if len(below) > 1 else ""
        if (nxt and len(nxt) <= 40 and ":" not in nxt and not is_blank(nxt)
                and not re.match(r"(?i)(?:sayfa|page|\[|üst bilgi|alt bilgi)", nxt)
                and not (nxt.upper() == nxt and re.search(r"[A-ZÇĞİÖŞÜ]{3}", nxt))):
            return nxt, f"{where} / satır {line_no + 1}: {nxt[:60]}"
    return value, where


# Değerden sonra aynı satırda başlayan başka etiket ("Eş İmza: ____ Tarih: ____", "Ad Soyad : X    TC No : Y"):
# sütun boşluğundan, noktalama ya da doldurma yerinden sonra 1-4 kelime ve iki nokta (M9.4h). "No:19/1" gibi iki
# noktadan sonra boşluk gelmeyen değer parçaları etiket sayılmaz.
_NEXT_LABEL = re.compile(r"(?:[ \t]{2,}|(?<=[.;,_…\]])[ \t])(?=(?:[^\W\d_]+\.? ?){1,4}[ \t]*[:：](?:[ \t]|$))")


def is_blank(raw: str) -> bool:
    if _BLANK.match(raw) or _PLACEHOLDER.fullmatch(turkish_lower(raw)):
        return True
    if re.match(r"^\W*(?:boş|doldurulmamış|imzalanmamış|imza yok)\b", turkish_lower(raw)):
        return True  # "(BOŞ - İMZA VE METİN DOLDURULMAMIŞTIR)"
    # Köşeli parantezli yer tutucu: "[imza yeri]", "[kaşe / imza alanı]" (M9.4h; "[imza]" imzalı da demek olabilir)
    if re.fullmatch(r"\W*\[\s*(?:kaşe\W*)?imza (?:yeri|alanı)\s*\]\W*", turkish_lower(raw)):
        return True
    # Boş köşeli / normal parantez doldurma yeri: "[            ] TL", "(   )" (M9.4h)
    bracket = re.sub(r"[\[(]\s*[\])]", "", raw)
    if bracket != raw and _BLANK.match(_UNIT.sub("", bracket)):
        return True
    # Doldurma yeri ve yalnızca birim ("…… TL")
    return bool(re.search(r"[.…_]{2,}", raw)) and bool(_BLANK.match(_UNIT.sub("", raw)))


def _collected(text: str, low: str, patterns: List[str]) -> Tuple[List[str], str]:
    """Etiketlerin satır başındaki bütün dolu değerleri, belgedeki sırasıyla; konum: ilk satır."""
    hits = []
    for label in patterns:
        for m in re.finditer(r"(?m)^[ \t]*(?:[-•*\d.)]+\s*)?" + label + _LABEL_END + r"[ \t]*(.*)$", low):
            value = _NEXT_LABEL.split(text[m.start(1):m.end(1)], maxsplit=1)[0].strip()
            value = re.sub(r"[ \t]*[_.…]{3,}.*$", "", value).strip()  # adın yanındaki imza yeri
            where = f"satır {low.count(chr(10), 0, m.start()) + 1}: {text[m.start():m.end()].strip()[:120]}"
            if not is_blank(value):
                hits.append((m.start(), value, where))
                continue
            # Etiketin altında liste ("Temsilciler:\n1. A (Müdür)\n2. B (Müdür)"): listenin TAMAMI okunur; yalnızca
            # ilk satırı almak öbür yetkiliyi yetkisiz gösterirdi
            pos = m.end() + 1
            for line in text[pos:].split("\n")[:12]:
                item = re.match(r"[ \t]*(?:[-•*]|\d+[.)])?[ \t]*([^:：\n]{2,60}?)[ \t]*$", line)
                if not line.strip() or not item or is_blank(item.group(1)):
                    break
                hits.append((pos, item.group(1), where))
                pos += len(line) + 1
    hits.sort()
    return [v for _, v, _ in hits], (hits[0][2] if hits else "")


def extract_labeled_fields(text: str, labels: Dict[str, List[str]],
                           convert: Optional[Dict[str, Callable[[str], object]]] = None,
                           every: frozenset = frozenset(), collect: frozenset = frozenset()) -> Dict[str, FieldValue]:
    """Alan adı → FieldValue (etiketi bulunmayan alan yok). `convert` alanın değerini normalleştirir; tanınmayan
    değer ham metniyle dolu kalır. `every` içindeki alanlarda etiketin her tekrarı dolu olmalıdır. `collect` içindeki
    alanlarda bütün etiketlerin bütün dolu satırları birleşir ("Yetkili 1: A" + "Yetkili 2: B" → "A, B"; M10)."""
    text = text or ""
    low = turkish_lower(text)
    assert len(low) == len(text)  # konumlar küçük harfli metinden okunur
    convert = convert or {}
    out: Dict[str, FieldValue] = {}
    for field, patterns in labels.items():
        if field in collect:
            values, where = _collected(text, low, patterns)
            if values:
                fn = convert.get(field)
                raw = ", ".join(dict.fromkeys(values))
                out[field] = FieldValue.filled(fn(raw) if fn else raw, where)
                continue
        for label in patterns:
            hit = _line_value(text, low, label, field in every)
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
