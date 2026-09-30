# -*- coding: utf-8 -*-
"""İndirilmiş mevzuat listesini korpusa yükler (M6.4; mufettis/importers/legislation_importer.py).

Önce scripts/fetch_legislation.py ile indirilmiş olmalı. Yüklenmiş belge atlanır (kaldığı yerden sürer).
Üretim `data/` dizinine yalnızca --production ile (kullanıcı onayından sonra, M6.10) yazılır.

Kullanım:
    MUFETTIS_DATA_DIR=<kopya> python scripts/import_legislation.py --priority 1
    MUFETTIS_DATA_DIR=<kopya> python scripts/import_legislation.py --only 1.5.5549
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OMP_NUM_THREADS", "2")

from mufettis.corpus.legislation_sources import load_sources, select_sources  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--priority", type=int, default=1, choices=(1, 2))
    ap.add_argument("--include-in-corpus", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--production", action="store_true", help="üretim data/ dizinine yaz (kullanıcı onayıyla)")
    args = ap.parse_args(argv)
    if not os.environ.get("MUFETTIS_DATA_DIR") and not args.production:
        print("MUFETTIS_DATA_DIR bir veri kopyasını göstermeli (üretim için --production, kullanıcı onayıyla).")
        return 2

    from mufettis.config import DATA_DIR
    from mufettis.importers.legislation_importer import import_downloads
    from mufettis.rag.engine import LocalRAG
    from mufettis.rag.index import IngestIndex

    sources = load_sources()
    chosen = select_sources(sources, args.priority, args.include_in_corpus)
    if args.only:
        chosen = [s for s in sources if s.key in set(args.only)]
    download_dir = DATA_DIR / "legislation_downloads"
    records_file = download_dir / "downloads.json"
    records = json.loads(records_file.read_text(encoding="utf-8")) if records_file.exists() else {}

    started = time.monotonic()
    rag = LocalRAG(allow_download=False)
    index = IngestIndex()

    def progress(i, n, row):
        print(f"[{i}/{n}] {row['status']:<22} {row['key']:<16} {row.get('chunks', 0):5d} parça "
              f"{row['seconds']:6.1f} sn {row.get('peak_working_set_mb', 0):7.0f} MB tepe  {row.get('reason', '')}",
              flush=True)

    rows = import_downloads(rag, index, chosen, records, download_dir, progress=progress)
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("özet:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
          f"| toplam {time.monotonic() - started:.0f} sn")
    ok = {"indexed", "repaired_and_indexed", "skipped"}
    return 0 if all(row["status"] in ok for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
