# -*- coding: utf-8 -*-
"""Tam mevzuat korpusunu bir veri dizinine uygular (M6.10): yedek → ayıklama → indirme → yükleme → değişiklik
notları → eşik.

Adımlar veri kopyasında (datacopy_m6) ayrı ayrı denenip ölçüldü (docs/architecture/legislation_corpus.md). Bu
betik aynı adımları tek seferde ve M2 yedekleme politikasıyla yapar:

1. Yedek: dizin (ingest_index) ve parça deposu `BACKUP_DIR/legislation_corpus_<zaman>/` altına (manifest.json).
2. Ayıklama: ikincil sitelerin madde yapısız sayfaları; `--exclude` ile verilen kayıtlar (kullanıcı kararı).
3. İndirme: listedeki belgeler (öncelik 1 + 2), doğrulamayla.
4. Yükleme: belge belge; yüklenmiş olan atlanır (yarıda kalırsa yeniden çalıştırılabilir).
   4b. Madde değişiklik notları önceden yüklenmiş belgelerin parçalarına da eklenir (M7.2).
5. Eşik: tam korpus için dev'den seçilen sıralayıcı eşiği `DATA_DIR/reranker_calibration.json` dosyasına (M6.1).

Üretim `data/` dizininde yalnızca --production ile, kullanıcı onayından sonra çalışır. Sonrasında taban dosyaları
yeni korpusa göre bilinçli olarak yeniden yazılır (bkz. dokümandaki "M6.10" bölümü).

Kullanım:
    MUFETTIS_DATA_DIR=<kopya> python scripts/apply_legislation_corpus.py --exclude 64b009bc9ab3
    python scripts/apply_legislation_corpus.py --production --exclude 64b009bc9ab3     # kullanıcı onayıyla
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OMP_NUM_THREADS", "2")

# M6.1: tam mevzuat korpusu (45 928 parça) için dev'den seçilen eşik; eski korpusta modelin varsayılanı (-4.48) geçerli.
FULL_CORPUS_CALIBRATION = {
    "mufettis-reranker-ft1": {
        "min_score": -2.95,
        "note": "M6.1 (2026-09-30): tam mevzuat korpusu, dev'den seçildi (doğru - yanlış dayanak - yanlış pozitif en "
                "büyük); aday havuzu derinliği 30",
    },
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exclude", nargs="*", default=[], help="ayrıca ayıklanacak kayıtlar (özet öneki; kullanıcı kararı)")
    ap.add_argument("--production", action="store_true", help="üretim data/ dizininde çalış (kullanıcı onayıyla)")
    ap.add_argument("--skip-fetch", action="store_true", help="indirme adımını atla (dosyalar zaten indirilmişse)")
    args = ap.parse_args(argv)
    if not os.environ.get("MUFETTIS_DATA_DIR") and not args.production:
        print("MUFETTIS_DATA_DIR bir veri kopyasını göstermeli (üretim için --production, kullanıcı onayıyla).")
        return 2

    from mufettis.common.backups import new_backup_dir, prune_backups
    from mufettis.config import DATA_DIR
    from mufettis.corpus.curation import exclude, find_non_legislation
    from mufettis.corpus.legislation_sources import load_sources, select_sources, validate_sources
    from mufettis.harvesting.official_legislation import OfficialLegislationFetcher
    from mufettis.importers.legislation_importer import import_downloads
    from mufettis.rag.engine import LocalRAG
    from mufettis.rag.index import IngestIndex
    from mufettis.rag.reranker import CALIBRATION_FILE
    from mufettis.rag.store import backup_vector_store

    started = time.monotonic()
    sources = select_sources(load_sources(), 2)
    problems = validate_sources(sources)
    if problems:
        print("liste hatalı:", problems)
        return 2
    print(f"veri dizini: {DATA_DIR}")

    # 1. Yedek
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = new_backup_dir("legislation_corpus", stamp)
    index = IngestIndex()
    index.backup(target / "ingest_index.sqlite3")
    backup_vector_store(target)
    manifest = {"kind": "legislation_corpus", "stamp": stamp, "data_dir": str(DATA_DIR), "steps": []}
    print(f"1. yedek: {target}")

    # 2. Ayıklama
    rag = LocalRAG(allow_download=False)
    candidates = find_non_legislation(index.iter_records())
    wanted = [c for c in candidates if c["action"] == "exclude"]
    for prefix in args.exclude:
        hits = [h for h, _ in index.iter_records() if h.startswith(prefix)]
        if len(hits) != 1:
            print(f"--exclude {prefix}: {len(hits)} kayıt eşleşti; durduruldu (yedek: {target})")
            return 1
        wanted.append({"file_hash": hits[0], "reason": "kullanıcı kararı (M6.10)"})
    excluded = [(c["file_hash"], exclude(rag, index, c["file_hash"], c["reason"])) for c in wanted]
    manifest["steps"].append({"step": "curation", "excluded": [h for h, _ in excluded]})
    print(f"2. ayıklama: {len(excluded)} kayıt, {sum(n for _, n in excluded)} parça")

    # 3. İndirme
    download_dir = DATA_DIR / "legislation_downloads"
    fetcher = OfficialLegislationFetcher(download_dir)
    if not args.skip_fetch:
        fetched = fetcher.fetch_all(sources)
        counts = {}
        for r in fetched:
            counts[r.status] = counts.get(r.status, 0) + 1
        manifest["steps"].append({"step": "fetch", "counts": counts})
        print(f"3. indirme: {counts}")

    # 4. Yükleme
    rows = import_downloads(rag, index, sources, fetcher.records, download_dir,
                            progress=lambda i, n, r: print(f"   [{i}/{n}] {r['status']:<22} {r['key'][:60]}", flush=True))
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    manifest["steps"].append({"step": "import", "counts": counts})
    print(f"4. yükleme: {counts}")

    # 4b. Değişiklik notları (M7.2): önceden yüklenmiş belgelerin parçaları; yenileri yüklemede aldı.
    from mufettis.rag.amendment_backfill import apply_backfill, plan_backfill

    written = apply_backfill(rag, plan_backfill(rag))
    manifest["steps"].append({"step": "amendment_backfill", "chunks": written})
    print(f"4b. değişiklik notları: {written} parça")

    # 5. Eşik
    calibration = Path(DATA_DIR) / CALIBRATION_FILE
    calibration.write_text(json.dumps(FULL_CORPUS_CALIBRATION, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    manifest["steps"].append({"step": "calibration", "file": str(calibration)})
    print(f"5. eşik: {calibration}")

    manifest["seconds"] = round(time.monotonic() - started)
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [r for r in rows if r["status"] not in ("indexed", "repaired_and_indexed", "skipped", "quarantined")]
    if failed:
        print(f"yüklenemeyen {len(failed)} belge; yedek korunuyor: {target}")
        return 1
    for old in prune_backups():  # saklama politikası: mufettis/common/backups.py
        print(f"eski yedek temizlendi: {old.name}")
    print(f"bitti ({manifest['seconds']} sn). Sonraki adım: tabanları yeni korpusa göre yeniden yaz (doküman, M6.10).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
