# -*- coding: utf-8 -*-
"""Listedeki kanunların önceki metinlerini indirir ve tarih aralığıyla yükler (M7.3;
mufettis/importers/historical_laws.py).

mevzuat.gov.tr'nin resmî "X sayılı Kanun ile değişik hali" metinleri; dürüst kullanıcı aracısı, robots.txt,
istekler arası 3 sn. Metinler "historical" durumunda yüklenir: bugünkü aramaya girmez, güncel metnin yerini
almaz; olay tarihi aralığına düşen analizde (M7.4) aday olur. Yüklenmiş sürüm atlanır (kaldığı yerden sürer).
Üretim `data/` dizinine yalnızca --production ile (kullanıcı onayından sonra) yazılır.

Kullanım:
    MUFETTIS_DATA_DIR=<kopya> python scripts/fetch_historical_laws.py
    MUFETTIS_DATA_DIR=<kopya> python scripts/fetch_historical_laws.py --only 1.5.5464 --list-only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OMP_NUM_THREADS", "2")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None, help="yalnızca bu kodlar (1.5.5464 …)")
    ap.add_argument("--list-only", action="store_true", help="yalnızca sürüm listelerini al, indirme / yükleme yok")
    ap.add_argument("--production", action="store_true", help="üretim data/ dizinine yaz (kullanıcı onayıyla)")
    args = ap.parse_args(argv)
    if not os.environ.get("MUFETTIS_DATA_DIR") and not args.production:
        print("MUFETTIS_DATA_DIR bir veri kopyasını göstermeli (üretim için --production, kullanıcı onayıyla).")
        return 2

    from mufettis.config import DATA_DIR
    from mufettis.corpus.legislation_sources import load_sources, select_sources

    sources = select_sources(load_sources(), 2, include_in_corpus=True)
    if args.only:
        sources = [s for s in sources if s.key in set(args.only)]
    download_dir = DATA_DIR / "legislation_downloads" / "historical"
    download_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    rag = index = None
    if not args.list_only:
        from mufettis.rag.engine import LocalRAG
        from mufettis.rag.index import IngestIndex

        rag, index = LocalRAG(allow_download=False), IngestIndex()
    from mufettis.importers.historical_laws import fetch_and_import

    rows = fetch_and_import(rag, index, sources, download_dir, list_only=args.list_only,
                            log=lambda m: print(m, flush=True))
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("özet:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "-",
          f"| toplam {time.monotonic() - started:.0f} sn")
    ok = {"indexed", "repaired_and_indexed", "skipped"}
    return 0 if all(row["status"] in ok for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
