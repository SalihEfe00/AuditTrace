# -*- coding: utf-8 -*-
"""Mevzuat metnindeki değişiklik notları (M7.2): hangi madde / fıkra ne zaman, neyle değişti.

İki biçim:
* Kanun: "(Değişik üçüncü fıkra: 24/6/2009-5915/1 md.)", "(Ek cümle: 13/6/2012-6327/43 md.)", "(Mülga: …)" —
  tarih değiştiren kanunun kabul tarihi, dayanak kanun numarası.
* Yönetmelik / tebliğ: "(Değişik:RG-25/9/2020-31255)", "(Ek:RG-…)", "(Mülga:RG-…)" — tarih Resmî Gazete yayım
  tarihi, dayanak Resmî Gazete sayısı.

Not yürürlük tarihi değildir (yürürlük değiştiren metnin yürürlük maddesindedir); olay tarihinden sonra
değişmiş hükmü işaretlemek için (M7.5) yeterince yakındır: değişiklik bu tarihten önce yürürlüğe girmez.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence

_KINDS = {"değişik": "değişik", "ek": "ek", "mülga": "mülga", "iptal": "iptal"}
# Genel biçim: "(<tür> [ne değişti]: [dayanak türü -] <G/A/YYYY> [- numara ...])". Dayanak tarihin önünde ("RG-",
# "Kurul Kararı-") ya da arkasında ("-5915/1 md.", "- KHK - 646/10 md.", "-2014/6381 K.") yazılır.
_NOTE = re.compile(
    r"\((Değişik|Ek|Mülga|İptal)[^():]{0,80}?:\s*([^()\d]{0,30}?)"
    r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})([^()]{0,60})\)", re.I)


def _basis(before: str, after: str) -> str:
    before = re.sub(r"[\s\-–]+", " ", before).strip()
    after = re.sub(r"\s+", " ", re.sub(r"\s*[–-]\s+|\s+[–-]\s*", " ", after)).strip(" -–")
    number = re.search(r"\d+(?:/\d+)?", after)
    if before.upper().startswith("RG"):
        return f"RG {number.group(0) if number else ''}".strip()
    law = re.fullmatch(r"(\d{3,5})(?:\s*/\s*\d+)?\s*(?:md\.?)?", after)
    if not before and law:
        return f"kanun {law.group(1)}"
    return re.sub(r"\s+", " ", f"{before} {after}").strip(" -–")


@dataclass(frozen=True)
class Amendment:
    kind: str       # değişik | ek | mülga | iptal
    date: str       # YYYY-MM-DD (kanunda kabul, yönetmelikte Resmî Gazete tarihi)
    basis: str      # "kanun 5915" | "RG 31255" | "KHK 646/10 md." | "Kurul Kararı 11559" | "2014/6381 K."


def _kind(word: str) -> str:
    lowered = word.lower().replace("i̇", "i")
    for prefix, kind in _KINDS.items():
        if lowered.startswith(prefix[:3]):
            return kind
    return lowered


def amendment_notes(text: str) -> List[Amendment]:
    """Metindeki değişiklik notları, metindeki sırayla."""
    out = []
    for m in _NOTE.finditer(text or ""):
        day, month, year = int(m.group(3)), int(m.group(4)), int(m.group(5))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        out.append(Amendment(_kind(m.group(1)), f"{year:04d}-{month:02d}-{day:02d}", _basis(m.group(2), m.group(6))))
    return out


def amendment_metadata(chunks: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    """Parça başına meta veri: `amended_on` (parçadaki en geç not), `amendments` ("değişik 2009-06-24 kanun 5915;
    …"), `article_amended_on` (aynı maddenin bütün parçalarındaki en geç not). chunks: split_legal_chunks çıktısı."""
    per_chunk = [amendment_notes(ch.get("text", "")) for ch in chunks]
    article_latest: Dict[tuple, str] = {}
    for ch, notes in zip(chunks, per_chunk):
        key = (ch.get("article_prefix") or "", ch.get("article_no") or "")
        if key[1] and notes:
            article_latest[key] = max([article_latest.get(key, "")] + [n.date for n in notes])
    out = []
    for ch, notes in zip(chunks, per_chunk):
        key = (ch.get("article_prefix") or "", ch.get("article_no") or "")
        out.append({
            "amended_on": max((n.date for n in notes), default=""),
            "amendments": "; ".join(f"{n.kind} {n.date} {n.basis}" for n in notes),
            "article_amended_on": article_latest.get(key, "") if key[1] else "",
        })
    return out


__all__ = ["Amendment", "amendment_notes", "amendment_metadata"]
