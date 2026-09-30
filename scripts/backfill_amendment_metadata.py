# -*- coding: utf-8 -*-
"""Mevcut parçalara madde değişiklik tarihlerini ekler (M7.2; mufettis/rag/amendment_backfill.py).

Varsayılan kuru çalıştırmadır (kaç belge / parça değişecek). --apply yazar. Üretim `data/` dizininde yalnızca
--production ile (kullanıcı onayıyla).

Kullanım:
    MUFETTIS_DATA_DIR=<kopya> python scripts/backfill_amendment_metadata.py
    MUFETTIS_DATA_DIR=<kopya> python scripts/backfill_amendment_metadata.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OMP_NUM_THREADS", "2")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--production", action="store_true", help="üretim data/ dizininde çalış (kullanıcı onayıyla)")
    args = ap.parse_args(argv)
    if args.apply and not os.environ.get("MUFETTIS_DATA_DIR") and not args.production:
        print("MUFETTIS_DATA_DIR bir veri kopyasını göstermeli (üretim için --production, kullanıcı onayıyla).")
        return 2

    from mufettis.rag.amendment_backfill import apply_backfill, plan_backfill
    from mufettis.rag.engine import LocalRAG

    rag = LocalRAG(allow_download=False)
    plan = plan_backfill(rag)
    chunks = sum(len(v) for v in plan.values())
    print(f"değişecek: {len(plan)} belge, {chunks} parça")
    if args.apply and plan:
        print(f"yazıldı: {apply_backfill(rag, plan)} parça")
    return 0


if __name__ == "__main__":
    sys.exit(main())
