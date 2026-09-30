# -*- coding: utf-8 -*-
"""Mevzuat listesinin güncel tutulması (M6.8).

İki sinyal:
1. Resmî Gazete günlük fihristi: listedeki bir belgenin adını taşıyan yayım ("… Yönetmelikte Değişiklik
   Yapılmasına Dair Yönetmelik", aynı adla yeniden çıkarılan düzenleme) o belgeyi denetime alır.
2. Tam denetim: torba kanunların başlığı değiştirdiği kanunları saymaz ("Bazı Kanunlarda Değişiklik…"); bu
   yüzden listedeki belgeler dönem dönem yeniden indirilip metinleri karşılaştırılır.

Değişiklik çıkarılan metnin özetiyle ölçülür (FetchResult.text_sha256): aynı metnin yeniden üretilmiş dosyası
yeni sürüm sayılmaz. Metni değişen belge yeniden yüklenir; sürüm anahtarı (mevzuat:<kod>) aynı olduğundan eski
sürüm `superseded` olur (rag.maintenance.versioned_ingest_text).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.corpus.legislation_sources import LegislationSource

# Ad eşleşmesinde sayılmayan genel kelimeler (tür adları, bağlaçlar); kök başlarıyla karşılaştırılır.
_GENERIC = {"hakkında", "ilişkin", "dair", "yönetmelik", "yönetmeliği", "kanun", "kanunu", "tebliğ", "tebliği",
            "genelge", "genelgesi", "karar", "kararı", "usul", "usûl", "esaslar", "esasları", "değişiklik",
            "yapılmasına", "ile", "olarak", "sıra", "no"}
STEM = 6                  # Türkçe ekler için kelimenin ilk 6 harfi karşılaştırılır ("yönetmelikte" ~ "yönetmelik")
MIN_NAME_COVERAGE = 0.8


def _stems(text: str) -> List[str]:
    words = re.findall(r"[a-zçğıöşüâîû]+", turkish_lower(text or ""))
    return [w[:STEM] for w in words if len(w) >= 3 and w not in _GENERIC]


def mentions(source: LegislationSource, gazette_title: str) -> bool:
    """Resmî Gazete başlığı listedeki belgeyi anıyor mu (adının anlamlı kelimelerinin çoğu geçiyor)."""
    wanted = set(_stems(source.title))
    if not wanted:
        return False
    have = set(_stems(gazette_title))
    return len(wanted & have) / len(wanted) >= MIN_NAME_COVERAGE


def amended_sources(gazette_items: Iterable[Dict[str, str]], sources: List[LegislationSource]) -> Dict[str, List[str]]:
    """Belge anahtarı → onu anan Resmî Gazete başlıkları."""
    out: Dict[str, List[str]] = {}
    for item in gazette_items:
        title = str(item.get("title") or "")
        for source in sources:
            if mentions(source, title):
                out.setdefault(source.key, []).append(title)
    return out


@dataclass
class UpdateCheck:
    key: str
    status: str        # changed | unchanged | new | error
    reason: str = ""


def check_sources(fetcher, sources: List[LegislationSource]) -> List[UpdateCheck]:
    """Belgeleri yeniden indirir, metin özetini önceki kayıtla karşılaştırır."""
    out = []
    for source in sources:
        before = (fetcher.records.get(source.key) or {}).get("text_sha256", "")
        result = fetcher.fetch(source, force=True)
        if result.status != "downloaded":
            out.append(UpdateCheck(source.key, "error", f"{result.status}: {result.reason}"))
        elif not before:
            out.append(UpdateCheck(source.key, "new", "önceki metin özeti yok"))
        else:
            out.append(UpdateCheck(source.key, "changed" if result.text_sha256 != before else "unchanged"))
    return out


__all__ = ["mentions", "amended_sources", "check_sources", "UpdateCheck"]
