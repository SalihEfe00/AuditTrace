# -*- coding: utf-8 -*-
"""Şube belge türü sınıflandırıcısı (M5.6).

Etiket kümesindeki (document_taxonomy.LABELS) 15 türden birini ya da "bilinmiyor"u seçer. Deterministiktir:
işaret tablosundaki (document_taxonomy.SIGNALS) başlık ifadeleri belgenin ilk satırlarında güçlü, gövde
ifadeleri her yerde zayıf işaret sayılır; tablo yapısı (hesap hareketleri) ve mevcut şikâyet / iç yazışma
sınıflandırıcısının kararı ek işarettir. En yüksek puan eşiği geçmez ya da ikinciyle farkı küçükse tür
"bilinmiyor" olur: yanlış tür, türsüzlükten kötüdür (yanlış şart listesi uygulanır).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from mufettis.analysis import document_taxonomy as tax
from mufettis.analysis.turkish_text import turkish_lower

TITLE_LINES = 8          # başlık bölgesi: ilk boş olmayan satırlar
TITLE_WEIGHT = 3.0
BODY_WEIGHT = 1.0
MAX_BODY_HITS = 6        # uzun belgede gövde işaretleri tek başına baskın olmasın
MIN_SCORE = 4.0
MIN_MARGIN = 1.5
TABLE_BONUS = 4.0        # tarih + tutar sütunlu, çok satırlı tablo → hesap hareketleri
# Mevcut sınıflandırıcı şikâyet / iç yazışma dediyse bu tek başına türü taşır (o karar kendi eşiğini geçmiştir);
# başka bir türün belirgin başlık / gövde işaretleri varsa o kazanır.
ROUTING_BONUS = MIN_SCORE
HEADING_MAX_CHARS = 90


@dataclass
class BranchTypeDecision:
    code: str
    score: float
    runner_up: str = ""
    runner_up_score: float = 0.0
    signals: List[str] = field(default_factory=list)


def _has(phrase: str, text: str) -> bool:
    return re.search(r"(?<![0-9a-zçğıöşüâîû])" + re.escape(phrase), text) is not None


def _looks_like_transaction_table(lines: List[str]) -> bool:
    """En az 5 satırı tarih ile başlayıp ayırıcıyla sürüp sayısal değer taşıyan tablo (CSV / noktalı virgül / |)."""
    rows = [ln for ln in lines if re.match(r"\s*\|?\s*(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})\s*[,;|]", ln)
            and re.search(r"\d[.,]\d{2}", ln)]
    return len(rows) >= 5


def classify_branch_type(text: str, routing_type: Optional[str] = None) -> BranchTypeDecision:
    """routing_type: mevcut sınıflandırıcının kararı (complaint / internal_correspondence / unknown)."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    lower = turkish_lower(text or "")
    # Başlık bölgesi: ilk satırlardan başlık gibi görünenler (kısa, cümle sonu noktalaması yok). Kısa bir
    # dilekçenin tamamı ilk satırlara sığar; düzyazı cümleleri başlık sayılmaz.
    headings = [ln for ln in lines[:TITLE_LINES]
                if len(ln.strip()) <= HEADING_MAX_CHARS and not ln.rstrip().endswith((".", "?", "!"))]
    title = turkish_lower("\n".join(headings))
    scores: Dict[str, float] = {}
    signals: Dict[str, List[str]] = {}
    for code, table in tax.SIGNALS.items():
        title_hits = [p for p in table["title"] if _has(p, title)]
        body_hits = [p for p in table["body"] if _has(p, lower)][:MAX_BODY_HITS]
        scores[code] = TITLE_WEIGHT * len(title_hits) + BODY_WEIGHT * len(body_hits)
        signals[code] = [f"başlık:{p}" for p in title_hits] + [f"gövde:{p}" for p in body_hits]
    if _looks_like_transaction_table(lines):
        scores["islem_listesi"] += TABLE_BONUS
        signals["islem_listesi"].append("yapı:işlem tablosu")
    routed = tax.ENGINE_TYPE_MAP.get(routing_type or "")
    if routed and routed != tax.UNKNOWN:
        scores[routed] += ROUTING_BONUS
        signals[routed].append(f"yönlendirme:{routing_type}")
    ranked: List[Tuple[str, float]] = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    (best, best_score), (second, second_score) = ranked[0], ranked[1]
    if best_score < MIN_SCORE or best_score - second_score < MIN_MARGIN:
        return BranchTypeDecision(tax.UNKNOWN, best_score, best, second_score, signals[best])
    return BranchTypeDecision(best, best_score, second, second_score, signals[best])


__all__ = ["BranchTypeDecision", "classify_branch_type", "TITLE_LINES", "MIN_SCORE", "MIN_MARGIN"]
