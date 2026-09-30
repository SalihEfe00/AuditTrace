# -*- coding: utf-8 -*-
"""Mevzuat listesini güncel tutar (M6.8; mufettis/corpus/legislation_updates.py).

Varsayılan: son N günün Resmî Gazete fihristinde listedeki belgeleri anan yayımlar aranır, anılan belgeler
yeniden indirilir; metni değişen belge yeni sürüm olarak yüklenir. --all ile listedeki bütün belgeler denetlenir
(torba kanunlar başlıkta değiştirdiği kanunları saymadığı için dönem dönem gerekli). --dry-run yalnızca raporlar.
Üretim `data/` dizininde yalnızca --production ile (kullanıcı onayıyla) çalışır.

Kullanım:
    MUFETTIS_DATA_DIR=<kopya> python scripts/update_legislation.py --days 7
    MUFETTIS_DATA_DIR=<kopya> python scripts/update_legislation.py --all --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OMP_NUM_THREADS", "2")

from mufettis.corpus.legislation_sources import load_sources, select_sources  # noqa: E402
from mufettis.corpus.legislation_updates import amended_sources, check_sources  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7, help="taranacak Resmî Gazete günü")
    ap.add_argument("--all", action="store_true", help="listedeki bütün belgeleri denetle")
    ap.add_argument("--dry-run", action="store_true", help="yükleme yapma, yalnızca raporla")
    ap.add_argument("--production", action="store_true", help="üretim data/ dizininde çalış (kullanıcı onayıyla)")
    args = ap.parse_args(argv)
    if not os.environ.get("MUFETTIS_DATA_DIR") and not args.production:
        print("MUFETTIS_DATA_DIR bir veri kopyasını göstermeli (üretim için --production, kullanıcı onayıyla).")
        return 2

    from mufettis.config import DATA_DIR
    from mufettis.harvesting.official_legislation import OfficialLegislationFetcher

    sources = select_sources(load_sources(), 2)
    if args.all:
        targets = sources
    else:
        from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper

        scraper = ResmiGazeteScraper()
        items = []
        today = dt.date.today()
        for back in range(args.days):
            items += scraper.fetch_daily_indices(today - dt.timedelta(days=back), filter_keywords=False)
        hits = amended_sources(items, sources)
        for key, titles in hits.items():
            print(f"Resmî Gazete: {key} ← {titles[0][:100]}")
        print(f"{args.days} gün, {len(items)} yayım; listeden anılan {len(hits)} belge")
        targets = [s for s in sources if s.key in hits]
    if not targets:
        return 0

    download_dir = DATA_DIR / "legislation_downloads"
    if args.dry_run:
        for s in targets:
            print(f"denetlenecek: {s.key}  {s.title[:80]}")
        return 0
    fetcher = OfficialLegislationFetcher(download_dir)
    checks = check_sources(fetcher, targets)
    for c in checks:
        print(f"{c.status:<10} {c.key:<16} {c.reason}")
    to_import = [s for s in targets if any(c.key == s.key and c.status in ("changed", "new") for c in checks)]
    if to_import:
        from mufettis.importers.legislation_importer import import_downloads
        from mufettis.rag.engine import LocalRAG
        from mufettis.rag.index import IngestIndex

        rows = import_downloads(LocalRAG(allow_download=False), IngestIndex(), to_import, fetcher.records, download_dir)
        for row in rows:
            print(f"yüklendi {row['status']:<22} {row['key']:<16} {row.get('chunks', 0)} parça")
    return 1 if any(c.status == "error" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
