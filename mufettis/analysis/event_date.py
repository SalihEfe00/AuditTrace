# -*- coding: utf-8 -*-
"""Olay tarihi (M7.1): belgenin anlattığı işlemin / olayın tarihi, belgenin düzenlenme tarihinden ayrı.

Dayanak seçimi için kullanılır (M7.4): eski bir işlem, olay tarihinde yürürlükte olan metinle değerlendirilir.
Mevzuat sürümleri yıllar içinde değiştiğinden birkaç günlük sapma sürüm seçimini değiştirmez; bu yüzden amaç
olayın döneminin doğru tarihi, kusurlu işlemin gününün birebir bulunması değil.

Her tarih alanına bağlamından bir rol verilir:
* olay: etiketli işlem tarihleri ("İşlem Tarihi:", "Kullandırım tarihi:", "Tebliğ Tarihi:"), düzyazıda
  "… tarihinde" ile anlatılan eylem, işlem tablosunun satır tarihi;
* düzenlenme: belgenin kendi tarihi ("Tarih:", "Rapor Tarihi:", "Teklif Tarihi:");
* diğer: vade, son ödeme, doğum, sorgu, dönem sınırı, "… tarihli" başka bir belgeye atıf, "… itibarıyla".
Olay tarihi en erken olay rolündeki tarihtir; yoksa belgenin düzenlenme tarihi; o da yoksa bilinmiyor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from mufettis.analysis.models import ExtractedField
from mufettis.analysis.turkish_text import turkish_lower

LABEL_WINDOW = 40         # tarihin solunda etiket aranan karakter sayısı (aynı satırda)
PROSE_WINDOW = 25         # tarihin sağında "tarihinde" / "tarihli" aranan karakter sayısı

# Etiketteki sözcük başları (Türkçe ekler için başlangıçla eşleşir).
_EVENT_LABELS = ("işlem", "kullandırım", "olay", "tespit", "harcama", "ödeme tarihi", "tahsil", "sayım", "tebliğ",
                 "başvuru", "kapanış", "açılış", "valör", "transfer", "havale", "eft", "çekim", "yatırma", "teslim",
                 "imza tarihi", "kayıt tarihi", "gerçekleş")
_ISSUE_LABELS = ("tarih", "düzenleme", "düzenlenme", "rapor tarihi", "teklif tarihi", "yazı tarihi", "belge tarihi",
                 "form tarihi", "tutanak tarihi")
_OTHER_LABELS = ("vade", "son ödeme", "doğum", "geçerlilik", "bitiş", "sorgu", "dönem", "aksiyon", "tescil",
                 "nüfus", "sözleşme tarihi", "kuruluş")
_TABLE_ROW = re.compile(r"^\s*\|?\s*(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})\s*[,;|]")


@dataclass(frozen=True)
class EventDate:
    value: Optional[str]      # YYYY-MM-DD ya da None
    basis: str                # etiket | düzyazı | işlem tablosu | düzenlenme tarihi | yok
    issue_date: Optional[str] = None


def _line_bounds(text: str, start: int, end: int):
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return line_start, (len(text) if line_end < 0 else line_end)


def _last_label(left: str) -> str:
    """Tarihin solundaki en yakın etiket: son iki noktalı ifade ("Kullandırım tarihi:") ya da tire / virgülle
    ayrılmış son parça."""
    parts = re.split(r"[—–|,;]|\s-\s", left)
    return turkish_lower(parts[-1].strip()) if parts else ""


def date_role(text: str, field: ExtractedField) -> str:
    """olay | düzenlenme | diğer | belirsiz."""
    line_start, line_end = _line_bounds(text, field.start, field.end)
    line = text[line_start:line_end]
    if _TABLE_ROW.match(line) and field.start - line_start < 15:
        return "olay"  # işlem tablosu satırı
    right = turkish_lower(text[field.end:min(line_end, field.end + PROSE_WINDOW)])
    if re.match(r"\s*(?:[-–]\s*\S+\s*)?tarihli", right):
        return "diğer"  # başka bir belgeye atıf ("07.03.2024 tarihli başvurunuz")
    if re.match(r"\s*itibar", right):
        return "diğer"
    label = _last_label(text[max(line_start, field.start - LABEL_WINDOW):field.start])
    if not label or re.fullmatch(r"[\d./\s]*", label):
        # Yakın etiket yoksa (ör. aralığın ikinci tarihi) satır başındaki etiket: "Dönem: 01.01 – 31.01".
        head = re.match(r"\s*([^:\n]{2,40}):", line)
        label = turkish_lower(head.group(1).strip()) if head and line_start + head.end() <= field.start else ""
    if label:
        if label.startswith(_OTHER_LABELS) or any(f" {w}" in f" {label}" for w in ("vade", "sorgu", "dönem")):
            return "diğer"
        if any(w in label for w in _EVENT_LABELS):
            return "olay"
        if label.startswith(_ISSUE_LABELS) or label.endswith("tarih:") or label.endswith("tarihi:"):
            return "düzenlenme"
    if re.match(r"\s*(?:[-–]\s*\S+\s*)?tarih(?:in|ler)", right):
        return "olay"  # "05.03.2024 tarihinde ... çekildi", "26.07.2019 – 09.08.2019 tarihleri arasında"
    return "belirsiz"


def extract_event_date(text: str, fields: Sequence[ExtractedField], issue_date: Optional[str] = None) -> EventDate:
    dates = [f for f in fields if f.kind == "date"]
    roles = [(f, date_role(text, f)) for f in dates]
    # Belgenin kendi tarihi başlıktadır: ilk düzenlenme etiketi (gövdedeki "Tarih: …" başka bir kaydındır).
    issue = issue_date or next((f.value for f, r in roles if r == "düzenlenme"), None)
    events: List[tuple] = []
    for f, role in roles:
        if role != "olay":
            continue
        line_start, line_end = _line_bounds(text, f.start, f.end)
        basis = "işlem tablosu" if _TABLE_ROW.match(text[line_start:line_end]) else (
            "düzyazı" if re.match(r"\s*(?:[-–]\s*\S+\s*)?tarih", turkish_lower(text[f.end:f.end + PROSE_WINDOW]))
            else "etiket")
        events.append((f.value, basis))
    if events:
        value, basis = min(events)
        return EventDate(value, basis, issue)
    if issue:
        return EventDate(issue, "düzenlenme tarihi", issue)
    return EventDate(None, "yok", None)


__all__ = ["EventDate", "extract_event_date", "date_role"]
