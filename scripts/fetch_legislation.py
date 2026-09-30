# -*- coding: utf-8 -*-
"""Mevzuat listesindeki belgeleri resmî kaynaktan indirir (M6.3; mufettis/harvesting/official_legislation.py).

Dosyalar MUFETTIS_DATA_DIR/legislation_downloads/ altına yazılır. Üretim `data/` dizinine yalnızca
--production ile (kullanıcı onayından sonra, M6.10) yazılır; bu olmadan MUFETTIS_DATA_DIR zorunludur.

Kullanım:
    MUFETTIS_DATA_DIR=<kopya> python scripts/fetch_legislation.py --priority 1
    MUFETTIS_DATA_DIR=<kopya> python scripts/fetch_legislation.py --priority 2 --include-in-corpus
    MUFETTIS_DATA_DIR=<kopya> python scripts/fetch_legislation.py --only 1.5.5549 --force
    python scripts/fetch_legislation.py --list                  # indirmeden listeyi gösterir
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.corpus.legislation_sources import load_sources, select_sources, validate_sources  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--priority", type=int, default=1, choices=(1, 2), help="bu önceliğe kadar olan kayıtlar")
    ap.add_argument("--include-in-corpus", action="store_true", help="korpusta zaten olanları da indir")
    ap.add_argument("--only", nargs="*", default=None, help="yalnızca bu kodlar")
    ap.add_argument("--force", action="store_true", help="doğrulanmış kopya olsa da yeniden indir")
    ap.add_argument("--list", action="store_true", help="yalnızca seçilen kayıtları yaz")
    ap.add_argument("--production", action="store_true", help="üretim data/ dizinine yaz (kullanıcı onayıyla)")
    args = ap.parse_args(argv)

    sources = load_sources()
    problems = validate_sources(sources)
    if problems:
        for key, problem in problems:
            print(f"liste hatası {key}: {problem}")
        return 2
    chosen = select_sources(sources, args.priority, args.include_in_corpus)
    if args.only:
        chosen = [s for s in sources if s.key in set(args.only)]
    if args.list:
        for s in chosen:
            print(f"{s.priority} {s.key:<16} {s.instrument:<10} {s.title}")
        print(f"{len(chosen)} kayıt")
        return 0
    if not os.environ.get("MUFETTIS_DATA_DIR") and not args.production:
        print("MUFETTIS_DATA_DIR bir veri kopyasını göstermeli (üretim için --production, kullanıcı onayıyla).")
        return 2

    from mufettis.config import DATA_DIR
    from mufettis.harvesting.official_legislation import OfficialLegislationFetcher

    fetcher = OfficialLegislationFetcher(DATA_DIR / "legislation_downloads")

    def progress(i, n, r):
        print(f"[{i}/{n}] {r.status:<14} {r.key:<16} {r.size / 1024:8.0f} KB {r.pages:4d} s  {r.seconds:5.1f} sn"
              f"  {r.reason}", flush=True)

    results = fetcher.fetch_all(chosen, force=args.force, progress=progress)
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print("özet:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 0 if all(r.status in ("downloaded", "cached") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
