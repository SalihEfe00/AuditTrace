# -*- coding: utf-8 -*-
"""Bakım betiklerinin işlem öncesi yedekleri ve saklama politikası.

Yedek klasörü: BACKUP_DIR/<tür>_<YYYYMMDD_HHMMSS>/ (ör. rechunk_20260927_210445). Politika: en yeni
BACKUP_KEEP yedek tam tutulur; daha eskilerin içeriği silinir, ne yapıldığının kaydı olan manifest.json
BACKUP_DIR/_manifests/<klasör adı>.json olarak kalır. Bu adla eşleşmeyen hiçbir şeye dokunulmaz.
Temizlik, betik işlemini doğruladıktan sonra çağrılır; böylece en yeni yedek her zaman son işlemin öncesidir.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import List, Optional

from mufettis.config import BACKUP_DIR, BACKUP_KEEP

MANIFEST_DIR_NAME = "_manifests"
_BACKUP_NAME = re.compile(r"^[a-z0-9_]+_(\d{8}_\d{6})$")


def new_backup_dir(kind: str, stamp: str, root: Optional[Path] = None) -> Path:
    """Yeni ve boş yedek klasörü oluşturur; aynı adla varsa hata verir (yedek üzerine yazılmaz)."""
    target = Path(root or BACKUP_DIR) / f"{kind}_{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def backup_dirs(root: Optional[Path] = None) -> List[Path]:
    """Politikanın yönettiği yedek klasörleri, eskiden yeniye (addaki zaman damgasına göre)."""
    root = Path(root or BACKUP_DIR)
    if not root.is_dir():
        return []
    found = [(m.group(1), d) for d in root.iterdir() if d.is_dir() and (m := _BACKUP_NAME.match(d.name))]
    return [d for _, d in sorted(found, key=lambda t: (t[0], t[1].name))]


def prune_backups(root: Optional[Path] = None, keep: int = BACKUP_KEEP) -> List[Path]:
    """En yeni `keep` yedek dışındakileri siler (manifest.json korunur). Silinen klasörleri döndürür."""
    if keep < 1:
        raise ValueError("en az bir yedek tutulmalı")
    root = Path(root or BACKUP_DIR)
    old = backup_dirs(root)[:-keep]
    for d in old:
        manifest = d / "manifest.json"
        if manifest.is_file():
            (root / MANIFEST_DIR_NAME).mkdir(exist_ok=True)
            shutil.copy2(manifest, root / MANIFEST_DIR_NAME / f"{d.name}.json")
        shutil.rmtree(d)
    return old
