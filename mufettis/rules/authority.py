# -*- coding: utf-8 -*-
"""Yetki tablosu (M10.4, isteğe bağlı): kullanıcının girdiği basit tablo, onay mercii → onaylayabileceği azami tutar.

Banka içi düzenlemedir (kanun değil): kural iç kontrol olarak gösterilir ve yalnızca onay belgesinde merci okunursa
uygulanır. Kredi onayındaki merci tablodaki satırla Türkçe harfler sadeleştirilerek eşleşir (tablodaki ad merci
metninde geçmeli); birden çok satır eşleşirse en uzun (en belirgin) ad seçilir ("Kredi Komitesi" ile "Bölge Kredi
Komitesi"). Eşleşen satır yoksa sınır "bulunamadı" kalır: kural belirsizdir, müfettişe sınır sorulur.

Tablo veri klasöründe saklanır (`<veri>/rules/yetki_tablosu.json`); testler yolu kendisi verir.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.rules.catalog import AUTHORITY_KIND
from mufettis.rules.engine import FILLED, FieldValue
from mufettis.rules.fields import as_money

_ASCII = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def table_path(data_dir: Optional[Path] = None) -> Path:
    if data_dir is None:
        from mufettis.config import DATA_DIR
        data_dir = DATA_DIR
    return Path(data_dir) / "rules" / "yetki_tablosu.json"


def _key(name: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", turkish_lower(str(name or "")).translate(_ASCII)))


def normalise(rows) -> List[Tuple[str, str]]:
    """(merci, tutar) satırları → (merci, "<tutar> <para birimi>"); boş ya da tutarı okunamayan satır atlanır,
    birimsiz tutar TL sayılır."""
    out = []
    for name, amount in rows:
        money = as_money(str(amount or "")) or as_money(f"{amount} TL")
        if _key(name) and money:
            out.append((str(name).strip(), money))
    return out


def load_table(path: Optional[Path] = None) -> List[Tuple[str, str]]:
    path = Path(path) if path else table_path()
    if not path.exists():
        return []
    return normalise((r.get("merci"), r.get("tutar")) for r in json.loads(path.read_text(encoding="utf-8")))


def save_table(rows, path: Optional[Path] = None) -> List[Tuple[str, str]]:
    rows = normalise(rows)
    path = Path(path) if path else table_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"merci": n, "tutar": m} for n, m in rows], ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return rows


def match(table, authority: str) -> Optional[Tuple[str, str]]:
    """Onay merciine uyan tablo satırı: tablodaki ad merci metninin içinde geçer; en uzun ad. Tersi eşleşmez:
    onaydaki genel "Kredi Komitesi" tablodaki daha dar "Bölge Kredi Komitesi"nin sınırını almamalı."""
    target = _key(authority)
    if not target:
        return None
    hits = [(name, money) for name, money in table
            if _key(name) and f" {_key(name)} " in f" {target} "]
    return max(hits, key=lambda r: len(_key(r[0]))) if hits else None


def authority_fields(approval_fields: Dict[str, FieldValue], table) -> Dict[str, FieldValue]:
    """Dosyaya eklenecek sanal alan: onay merciinin tablodaki sınırı ("yetki_tablosu.limit")."""
    authority = approval_fields.get("onay_mercii")
    if not table or authority is None or authority.state != FILLED:
        return {}
    hit = match(table, authority.value)
    if hit is None:
        return {}
    return {f"{AUTHORITY_KIND}.limit": FieldValue.filled(hit[1], f"yetki tablosu: {hit[0]}")}


__all__ = ["table_path", "load_table", "save_table", "normalise", "match", "authority_fields"]
