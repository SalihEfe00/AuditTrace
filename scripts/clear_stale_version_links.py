#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aktif belgelerde kalmış yanlış sürüm bağlarını (superseded_by / valid_to) temizler.

E8 onarımı, farklı belge anahtarına işaret eden superseded_by bağlarını Chroma'da boşaltmış ama
ingest index'te bırakmıştı (IngestIndex.upsert mevcut kayıtla birleştirir; alanı kayıttan çıkarmak
onu silmez). Belge yeniden indekslendiğinde parçalar bu bağı index'ten alır ve belge "yürürlükten
kalkmış" sayılıp sıralamada geriye düşer. Karar repair_e8_corpus.plan_version_chains ile aynıdır:
aynı (kaynak, belge anahtarı) grubunun en yeni kaydı aktiftir ve sürüm bağı taşımaz.

Varsayılan: yalnızca plan. --apply: yedek + uygula + doğrula.
Veri dizini MUFETTIS_DATA_DIR ile değiştirilebilir (ör. production'ın bir kopyası).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mufettis.config import DATA_DIR
from mufettis.common.backups import new_backup_dir, prune_backups

CLEARED = {"status": "active", "superseded_by": "", "valid_to": ""}


def plan(index, rag) -> Dict[str, Any]:
    from mufettis.rag.paging import iter_pages
    from repair_e8_corpus import plan_version_chains

    items = index.data.get("indexed_items", {})
    chains = plan_version_chains(items, {h: str(r.get("document_key", "")) for h, r in items.items()})
    index_links = chains["clear_links"]
    keep = {item["hash"] for item in index_links}
    # Chroma tarafı: aktif (grubun en yenisi) belgelerin parçalarında kalmış bağlar.
    active = {h for h, r in items.items() if str(r.get("status", "")) == "active"}
    active -= {item["hash"] for item in chains["supersede"]}
    chunk_links: Dict[str, int] = {}
    for _, _, metas in iter_pages(rag.collection, include=("metadatas",)):
        for meta in metas:
            meta = meta or {}
            h = str(meta.get("file_hash", ""))
            if h in active and (meta.get("superseded_by") or meta.get("valid_to")):
                chunk_links[h] = chunk_links.get(h, 0) + 1
    return {
        "index_links": index_links,
        "chunk_links": [{"hash": h, "title": str(items.get(h, {}).get("title", ""))[:60], "chunks": n,
                         "also_in_index": h in keep} for h, n in sorted(chunk_links.items())],
        "pending_supersede": chains["supersede"],
    }


def backup(stamp: str) -> Path:

    target = new_backup_dir("version_links", stamp)
    from mufettis.rag.index import IngestIndex

    IngestIndex().backup(target / "ingest_index.sqlite3")  # tutarlı SQLite yedeği
    from mufettis.rag.store import backup_vector_store

    backup_vector_store(target)  # yalnızca etkin parça deposu (SQLite veya Chroma)
    return target


def apply(index, rag, the_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = []
    hashes = {item["hash"] for item in the_plan["index_links"]} | {item["hash"] for item in the_plan["chunk_links"]}
    for h in sorted(hashes):
        index.upsert(h, CLEARED)
        updated = rag.update_hash_metadata(h, {"superseded_by": "", "valid_to": ""})
        results.append({"hash": h, "title": str(index.get(h).get("title", ""))[:60], "chunks_updated": updated})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Yedek al, uygula ve doğrula")
    args = parser.parse_args()

    from mufettis.rag.engine import LocalRAG
    from mufettis.rag.index import IngestIndex

    index = IngestIndex()
    rag = LocalRAG(allow_download=False)
    the_plan = plan(index, rag)
    print(json.dumps({"data_dir": str(DATA_DIR), "plan": the_plan}, ensure_ascii=False, indent=2))
    if the_plan["pending_supersede"]:
        print("\nBekleyen sürüm zinciri kararları var; önce repair_e8_corpus.py çalıştırılmalı. Hiçbir şey yazılmadı.")
        return 1
    if not args.apply:
        print("\nPLAN (hiçbir şey yazılmadı). Uygulamak için --apply")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup(stamp)
    results = apply(index, rag, the_plan)
    after = plan(IngestIndex(), rag)
    remaining = after["index_links"] + after["chunk_links"]
    report = {"results": results, "remaining": remaining, "backup": str(backup_dir)}
    (backup_dir / "manifest.json").write_text(json.dumps({"plan": the_plan, "report": report},
                                                         ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not remaining:
        for old in prune_backups():  # saklama politikası: mufettis/common/backups.py
            print(f"Eski yedek silindi (manifest korundu): {old.name}")
    print(f"\nUYGULANDI. Doğrulama: {'BAŞARILI' if not remaining else 'KONTROL GEREKİYOR'}. Yedek: {backup_dir}")
    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())
