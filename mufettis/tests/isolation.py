# -*- coding: utf-8 -*-
"""
Test veri izolasyonu yardımcıları.

Yalnızca standart kütüphaneye bağlıdır ve hiçbir `mufettis` modülünü import
etmez; böylece `mufettis.config` yüklenmeden önce (ör. conftest içinde)
dosya yolundan doğrudan yüklenebilir.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

Snapshot = Dict[str, Tuple[int, int]]


def snapshot(root: Path) -> Snapshot:
    """Dosya yolu (root'a göre) -> (boyut, mtime_ns). Dizin yoksa boş sözlük."""
    snap: Snapshot = {}
    root = Path(root)
    if not root.exists():
        return snap
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            snap[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return snap


def diff(before: Snapshot, after: Snapshot) -> List[str]:
    """İki snapshot arasındaki farkları '+ eklenen', '- silinen', '~ değişen' satırları olarak döndürür."""
    changes = [f"+ {p}" for p in sorted(after.keys() - before.keys())]
    changes += [f"- {p}" for p in sorted(before.keys() - after.keys())]
    changes += [f"~ {p}" for p in sorted(before.keys() & after.keys()) if before[p] != after[p]]
    return changes


def create_isolated_data_copy(source_data_dir: Path, prefix: str = "mufettis_test_data_") -> Tuple[Path, Path]:
    """source_data_dir'i geçici bir köke kopyalar; (geçici_kök, veri_dizini) döndürür.

    Kaynak yoksa boş bir veri dizini oluşturulur. Temizlik çağırana aittir
    (`shutil.rmtree(geçici_kök)`).
    """
    temp_root = Path(tempfile.mkdtemp(prefix=prefix))
    data_dir = temp_root / "data"
    source = Path(source_data_dir)
    if source.is_dir():
        shutil.copytree(source, data_dir)
    else:
        data_dir.mkdir(parents=True)
    return temp_root, data_dir
