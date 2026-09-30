# -*- coding: utf-8 -*-
"""Geçerlilik (yürürlük) ekseni.

Kayıtta saklanmaz; her okumada `status` ve tarih alanlarından hesaplanır. Sürüm zinciri bir
belgeyi sonradan "superseded" yaptığında (mark_hash_superseded) ayrıca güncellenecek türetilmiş
bir alan kalmaz. Geçerlilik alaka puanına eklenmez: retrieval'da ayrı bir sıralama katmanıdır.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, Optional

from mufettis.corpus.model import Validity

NOT_IN_FORCE_STATUSES = frozenset({"superseded", "mülga", "mulga", "repealed", "repealed_or_changed"})
# Mevzuat olmadığı için korpustan ayıklanan kayıt (M6.5): silinmez, aramaya ve istatistiklere girmez; geri alınabilir.
EXCLUDED_STATUSES = frozenset({"excluded"})
# Kanunun önceki (tarih aralıklı) metni (M7.3): bugünkü aramaya girmez; olay tarihi aralığına düşen analizde
# (M7.4) dayanak olabilir. Güncel sürümün yerini almaz (superseded değildir).
HISTORICAL_STATUSES = frozenset({"historical"})
# Etkin korpusa sayılmayan kayıtlar (terim istatistiği, yeniden parçalama, atıf çözümü).
INACTIVE_STATUSES = frozenset({"superseded", "quarantined"}) | EXCLUDED_STATUSES | HISTORICAL_STATUSES

# Sıralama katmanı: küçük önce gelir. Yürürlükte / durumu bilinmeyen > henüz yürürlüğe
# girmemiş > yürürlükten kalkmış.
VALIDITY_TIER = {
    Validity.IN_FORCE: 0,
    Validity.UNKNOWN: 0,
    Validity.NOT_YET_IN_FORCE: 1,
    Validity.NOT_IN_FORCE: 2,
}


def parse_date(value: Any) -> Optional[dt.date]:
    text = str(value or "").strip()
    if not text:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$", text)
        if not m:
            return None
        d, mo, y = (int(x) for x in m.groups())
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def validity(meta: Dict[str, Any], as_of: Optional[dt.date] = None) -> str:
    as_of = as_of or dt.date.today()
    status = str(meta.get("status", "") or "").strip().lower()
    if status in NOT_IN_FORCE_STATUSES:
        return Validity.NOT_IN_FORCE
    end = parse_date(meta.get("effective_to") or meta.get("valid_to"))
    if end and end <= as_of:
        return Validity.NOT_IN_FORCE
    start = parse_date(meta.get("effective_from") or meta.get("valid_from") or meta.get("effective_date"))
    if start and start > as_of:
        return Validity.NOT_YET_IN_FORCE
    if status in HISTORICAL_STATUSES:
        # Önceki metin yalnızca geçerlilik aralığı içindeki tarihte yürürlüktedir; aralık bilinmiyorsa değil.
        return Validity.IN_FORCE if start and end else Validity.NOT_IN_FORCE
    return Validity.IN_FORCE if status == "active" else Validity.UNKNOWN


def validity_tier(meta: Dict[str, Any], as_of: Optional[dt.date] = None) -> int:
    return VALIDITY_TIER[validity(meta, as_of)]


__all__ = [
    "NOT_IN_FORCE_STATUSES",
    "EXCLUDED_STATUSES",
    "HISTORICAL_STATUSES",
    "INACTIVE_STATUSES",
    "VALIDITY_TIER",
    "parse_date",
    "validity",
    "validity_tier",
]
