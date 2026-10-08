# -*- coding: utf-8 -*-
"""M17.3: pakete girecek veri klasörünü bir veri dizininden seçerek kopyalar (kaynak yalnızca okunur).

Girer: mevzuat kütüphanesi (parça deposu, sözcük dizini, alım dizini, kaynak önbelleği), terim istatistikleri ve
sıralayıcı eşiği. Girmez: kullanıcının kendi kayıtları (denetim günlükleri, inceleme kayıtları, dışa aktarımlar,
dil modeli günlükleri, ayarlar, yetki tablosu, onaylar), ham indirmeler, yedekler, karantina ve çalışma klasörleri.
Kullanım: python scripts/build_package_data.py <kaynak veri dizini> <hedef klasör>
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

INCLUDE = ("corpus_store", "source_cache", "ingest_index.json", "ingest_index.sqlite3", "term_stats.json",
           "reranker_calibration.json")
INCLUDE_GLOBS = ("mevzuat_db_lexical_*.sqlite3",)


def selected(src: Path):
    for name in INCLUDE:
        if (src / name).exists():
            yield src / name
    for pattern in INCLUDE_GLOBS:
        yield from sorted(src.glob(pattern))


def build(src: Path, dst: Path) -> dict:
    if dst.exists():
        raise SystemExit(f"hedef zaten var: {dst}")
    dst.mkdir(parents=True)
    sizes = {}
    for path in selected(src):
        target = dst / path.name
        if path.is_dir():
            shutil.copytree(path, target)
            sizes[path.name] = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        else:
            shutil.copy2(path, target)
            sizes[path.name] = target.stat().st_size
    missing = [n for n in INCLUDE if not (dst / n).exists()]
    if missing or not any(dst.glob(INCLUDE_GLOBS[0])):
        raise SystemExit(f"eksik veri: {missing or INCLUDE_GLOBS}")
    return sizes


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    result = build(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps({k: round(v / 2**20, 1) for k, v in result.items()}, ensure_ascii=False, indent=1))
    print("toplam MB:", round(sum(result.values()) / 2**20))
