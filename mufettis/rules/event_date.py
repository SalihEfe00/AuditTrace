# -*- coding: utf-8 -*-
"""Olay tarihi (M7 / M9.4b): kuralların hangi kanun metnine göre değerlendirileceği belgenin kendi tarihinden seçilir.

Arayüz önce bugünün tarihini kullanıyordu; 2013 öncesi kefalet ya da 6750 öncesi rehin gibi eski tarihli belgelerde
eski metin kuralları hiç devreye girmiyordu. Belge türüne göre öncelikli tarih alanı; hiçbiri okunamazsa bugün
(ve çağıran bunu gösterir).
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, Optional, Tuple

from mufettis.rules.engine import FILLED, FieldValue

# Belge türü → olay tarihi sayılacak alanlar (öncelik sırasıyla)
EVENT_DATE_FIELDS = {
    # Kefalet tarihi yoksa kredi sözleşmesinin tarihi yalnızca yürürlükteki metni seçmek için yaklaşık olay tarihidir
    "kefalet_sozlesmesi": ("kefalet_tarihi", "sozlesme_tarihi", "kredi_sozlesme_tarihi"),
    "ipotek": ("tescil_tarihi",),
    "rehin": ("sozlesme_tarihi", "sicil_tescil_tarihi"),
    "ekspertiz": ("rapor_tarihi",),
    "dask": ("islem_tarihi", "baslangic_tarihi"),
    "bono": ("duzenleme_tarihi",),
    "senet_gks": ("duzenleme_tarihi",),
}


def document_event_date(document_type: str, fields: Dict[str, FieldValue],
                        today: Optional[dt.date] = None) -> Tuple[dt.date, str]:
    """(olay tarihi, kaynak alan adı ya da "bugün")."""
    for name in EVENT_DATE_FIELDS.get(document_type, ()):
        fv = fields.get(name)
        if fv is not None and fv.state == FILLED and isinstance(fv.value, dt.date):
            return fv.value, name
    return today or dt.date.today(), "bugün"


__all__ = ["EVENT_DATE_FIELDS", "document_event_date"]
