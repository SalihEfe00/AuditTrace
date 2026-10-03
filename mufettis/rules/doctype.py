# -*- coding: utf-8 -*-
"""Teminat belgesinin türü (M9.3 / M9.5): metnin başlığından ve ayırt edici ifadelerinden, belirlenimci.

TBK 603: kefaletin şekline ve eşin rızasına ilişkin hükümler, gerçek kişilerin başka ad altında verdiği kişisel
güvence sözleşmelerine de uygulanır. Bu yüzden gerçek kişinin "garanti sözleşmesi", "taahhütname", "kişisel
güvence" belgesi `kefalet_sozlesmesi` kurallarına bağlanır (`via_603=True`). Tüzel kişinin (banka teminat mektubu,
şirket garantisi) belgesi bu eşlemeye girmez.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from mufettis.analysis.turkish_text import turkish_lower

# (tür, ayırt edici kalıp); sıra önemli: daha özgül olan önce
_PATTERNS = [
    ("dask", r"zorunlu deprem sigortası|\bdask\b"),
    ("ekspertiz", r"(?:ekspertiz|değerleme) raporu|gayrimenkul değerleme"),
    ("ipotek", r"ipotek (?:akit tablosu|sözleşmesi|belgesi)|resmi senet.{0,40}ipotek"),
    ("rehin", r"(?:taşınır )?rehin sözleşmesi|rehinli taşınır sicili"),
    ("kefalet_sozlesmesi", r"kefalet sözleşmesi|müteselsil kefil|kefaletname"),
    ("senet_gks", r"genel kredi sözleşmesi|bono|emre muharrer senet|kredi sözleşmesi"),
]
_PERSONAL_GUARANTEE = r"garanti sözleşmesi|taahhütname|kişisel güvence|şahsi teminat"
_LEGAL_ENTITY = r"(?:\ba\.ş\.|\bltd\b\.?|limited şirketi|anonim şirketi).{0,80}(?:garantör|garanti eden|taahhüt eden)|teminat mektubu"


@dataclass(frozen=True)
class DocType:
    document_type: Optional[str]
    via_603: bool = False
    reason: str = ""


def detect_collateral_type(text: str) -> DocType:
    head = turkish_lower((text or "")[:3000])
    for doc_type, pattern in _PATTERNS:
        m = re.search(pattern, head)
        if m:
            return DocType(doc_type, False, f"ifade: {m.group(0)}")
    m = re.search(_PERSONAL_GUARANTEE, head)
    if m and not re.search(_LEGAL_ENTITY, head):
        return DocType("kefalet_sozlesmesi", True,
                       f"ifade: {m.group(0)}; gerçek kişinin kişisel güvencesi (TBK 603: kefalet şekil ve eş rızası hükümleri uygulanır)")
    return DocType(None, False, "teminat belgesi ifadesi bulunamadı")


__all__ = ["DocType", "detect_collateral_type"]
