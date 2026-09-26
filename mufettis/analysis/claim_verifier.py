# -*- coding: utf-8 -*-
"""İddia-kanıt doğrulaması için isteğe bağlı dil modeli katmanı.

Sözcüksel eşleştirici, iddiayla ortak terimi olmayan doğru hükmü (ör. "aidat ücreti" iddiası ↔
"sözleşmede yer almayan … masraf … talep edilemez") bulamaz; uzun cümlelerdeki tesadüfi kök
örtüşmelerini de eleyemez. Bu katman dil modeline yalnızca retrieval'ın getirdiği adayları gösterir
ve bir seçim ister. Güvenceler:

* Model yeni kaynak üretemez: seçim aday numarasıdır.
* Seçilen hükümden yapılan alıntı kanıt metninde (boşluk farkı gözetilmeden) birebir geçmelidir;
  geçmezse karar geçersizdir ve çağıran sözcüksel eşleştiriciye döner.
* Model erişilemezse veya yanıt çözümlenemezse None döner (davranış değişmez).
* İstem kişisel veriler maskelenerek gönderilir (execute_structured_llm_call).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional

from mufettis.analysis.llm_support import execute_structured_llm_call
from mufettis.analysis.models import Evidence

MAX_CANDIDATES = 5
CANDIDATE_CHARS = 900
MIN_QUOTE_CHARS = 20

PROMPT_TEMPLATE = """Sen bir banka müfettişine yardım eden mevzuat analistisin.
Aşağıdaki İDDİA bir müşteri şikâyetinden veya iç yazışmadan alınmıştır. ADAY HÜKÜMLER yerel
mevzuat veritabanından getirilmiştir. Görevin: iddia edilen davranışı doğrudan düzenleyen (yasaklayan,
şarta bağlayan veya yükümlülük getiren) hükmü seçmektir. Konu benzerliği yetmez; hüküm iddiadaki
davranışa uygulanabilir olmalıdır. Hiçbiri uygun değilse 0 seç.

İDDİA:
{claim}

ADAY HÜKÜMLER:
{candidates}

Yalnızca şu JSON'u döndür:
{{"secim": <aday numarası veya 0>, "alinti": "<seçtiğin hükümden birebir kopyalanmış tek cümle>", "gerekce": "<en fazla iki cümle>"}}"""


@dataclass(frozen=True)
class Verdict:
    """evidence_index None ise model hiçbir adayı uygun bulmadı."""
    evidence_index: Optional[int]
    quote: str
    reason: str


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


class LLMClaimVerifier:
    def __init__(self, generate_fn: Callable[[str], str], max_candidates: int = MAX_CANDIDATES):
        self.generate_fn = generate_fn
        self.max_candidates = max_candidates

    def build_prompt(self, claim: str, candidates: List[Evidence]) -> str:
        blocks = [
            f"[{i}] {ev.source_document} — {ev.chunk_reference}\n{_squash(ev.text_span)[:CANDIDATE_CHARS]}"
            for i, ev in enumerate(candidates, 1)
        ]
        return PROMPT_TEMPLATE.format(claim=_squash(claim), candidates="\n\n".join(blocks))

    def choose(self, claim: str, candidates: List[Evidence]) -> Optional[Verdict]:
        candidates = candidates[: self.max_candidates]
        if not candidates:
            return None
        parsed = execute_structured_llm_call(
            self.build_prompt(claim, candidates), self.generate_fn, fallback_factory=lambda: {}
        )
        try:
            choice = int(parsed.get("secim"))
        except (TypeError, ValueError):
            return None
        reason = _squash(parsed.get("gerekce", ""))[:400]
        if choice == 0:
            return Verdict(evidence_index=None, quote="", reason=reason)
        if not 1 <= choice <= len(candidates):
            return None
        quote = _squash(parsed.get("alinti", ""))
        # Alıntı, gösterilen kısaltılmış metinde değil kanıtın tamamında aranır.
        if len(quote) < MIN_QUOTE_CHARS or quote not in _squash(candidates[choice - 1].text_span):
            return None
        return Verdict(evidence_index=choice - 1, quote=quote, reason=reason)


__all__ = ["LLMClaimVerifier", "MAX_CANDIDATES", "Verdict"]
