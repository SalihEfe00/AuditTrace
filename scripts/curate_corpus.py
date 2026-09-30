# -*- coding: utf-8 -*-
"""Mevzuat olmayan kayıtların ayıklanması (M6.5; mufettis/corpus/curation.py).

Varsayılan kuru çalıştırmadır: adayları ve gerekçelerini yazar, hiçbir şeyi değiştirmez. Ayıklama silme
değildir (durum `excluded`, --restore ile geri alınır). Üretim `data/` dizininde yalnızca --production ile
(kullanıcı onayından sonra, M6.10) çalışır.

Kullanım:
    MUFETTIS_DATA_DIR=<kopya> python scripts/curate_corpus.py                  # kuru çalıştırma
    MUFETTIS_DATA_DIR=<kopya> python scripts/curate_corpus.py --apply          # ikincil kaynak adaylarını ayıkla
    MUFETTIS_DATA_DIR=<kopya> python scripts/curate_corpus.py --exclude <hash> # kullanıcının onayladığı yerel dosya
    MUFETTIS_DATA_DIR=<kopya> python scripts/curate_corpus.py --restore <hash>
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
    ap.add_argument("--apply", action="store_true", help="ikincil kaynak adaylarını ayıkla")
    ap.add_argument("--exclude", nargs="*", default=[], help="bu kayıtları ayıkla (inceleme sonrası)")
    ap.add_argument("--restore", nargs="*", default=[], help="ayıklamayı geri al")
    ap.add_argument("--production", action="store_true", help="üretim data/ dizininde çalış (kullanıcı onayıyla)")
    args = ap.parse_args(argv)
    writes = args.apply or args.exclude or args.restore
    if writes and not os.environ.get("MUFETTIS_DATA_DIR") and not args.production:
        print("MUFETTIS_DATA_DIR bir veri kopyasını göstermeli (üretim için --production, kullanıcı onayıyla).")
        return 2

    from mufettis.corpus.curation import exclude, find_non_legislation, restore
    from mufettis.rag.index import IngestIndex

    index = IngestIndex()
    candidates = find_non_legislation(index.iter_records())
    for c in candidates:
        print(f"{c['action']:<8} {c['file_hash'][:12]}  {c['source_id']:<12} {c['title'][:60]}  ({c['reason']})")
    print(f"{len(candidates)} aday ({sum(c['action'] == 'exclude' for c in candidates)} ayıklanabilir, "
          f"{sum(c['action'] == 'review' for c in candidates)} incelenecek)")
    if not writes:
        return 0

    from mufettis.rag.engine import LocalRAG

    rag = LocalRAG(allow_download=False)
    by_prefix = {c["file_hash"]: c for c in candidates}

    def resolve(prefix):
        hits = [h for h in by_prefix if h.startswith(prefix)] or [h for h, _ in index.iter_records() if h.startswith(prefix)]
        if len(hits) != 1:
            raise SystemExit(f"{prefix}: {len(hits)} kayıt eşleşti")
        return hits[0]

    targets = [c["file_hash"] for c in candidates if c["action"] == "exclude"] if args.apply else []
    targets += [resolve(p) for p in args.exclude]
    for h in dict.fromkeys(targets):
        reason = by_prefix.get(h, {}).get("reason") or "kullanıcı kararı"
        print(f"ayıklandı {h[:12]}: {exclude(rag, index, h, reason)} parça")
    for p in args.restore:
        h = resolve(p)
        print(f"geri alındı {h[:12]}: {restore(rag, index, h)} parça")
    return 0


if __name__ == "__main__":
    sys.exit(main())
