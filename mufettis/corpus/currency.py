# -*- coding: utf-8 -*-
"""Korpus güncelliği (M6.16): mevzuatın en son ne zaman resmî kaynaktan denetlendiği / yüklendiği.

Son güncelleme = `legislation_downloads/downloads.json` içindeki en geç indirme (`fetched_at`) ile
`import_runs.jsonl` içindeki en geç yükleme (`at`) zamanlarının büyüğü. Ağ kullanmaz; dosya yoksa None.
STALE_DAYS günden eskiyse arayüz uyarı gösterir: sonuçlar o tarihe kadarki mevzuata göredir.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

STALE_DAYS = 30
_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse(value) -> Optional[dt.datetime]:
    try:
        return dt.datetime.strptime(str(value)[:19], _FORMAT)
    except (TypeError, ValueError):
        return None


def last_corpus_update(data_dir: Path) -> Optional[dt.datetime]:
    folder = Path(data_dir) / "legislation_downloads"
    stamps = []
    try:
        records = json.loads((folder / "downloads.json").read_text(encoding="utf-8"))
        stamps += [_parse(r.get("fetched_at")) for r in records.values() if isinstance(r, dict)]
    except (OSError, ValueError):
        pass
    try:
        with open(folder / "import_runs.jsonl", encoding="utf-8") as fh:
            for line in fh:
                try:
                    stamps.append(_parse(json.loads(line).get("at")))
                except ValueError:
                    continue
    except OSError:
        pass
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def staleness_message(last: Optional[dt.datetime], now: Optional[dt.datetime] = None,
                      stale_days: int = STALE_DAYS) -> Optional[str]:
    """Uyarı metni; korpus güncelse None."""
    if last is None:
        return "Mevzuat korpusunun güncelleme tarihi bilinmiyor; sonuçların hangi tarihteki mevzuata göre olduğu belirsiz."
    age = ((now or dt.datetime.now()) - last).days
    if age <= stale_days:
        return None
    return (f"Mevzuat korpusu {age} gündür güncellenmedi (son: {last:%d.%m.%Y}). Sonuçlar bu tarihe kadarki mevzuata "
            f"göredir; güncelleme için internet gerekir: python scripts/update_legislation.py --production")


__all__ = ["last_corpus_update", "staleness_message", "STALE_DAYS"]
