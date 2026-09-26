# -*- coding: utf-8 -*-
"""Chroma koleksiyonunu SQLite parça deposuna (mufettis/rag/store.py) kopyalar ve doğrular.

Kaynağa yazmaz. Hedef boş olmalıdır (yarım kalmış bir kopyanın üzerine yazılmaz; --replace ile
hedef koleksiyon silinip baştan kurulur). Sonunda parça sayısı, sıra, metin, metadata ve vektörler
kaynakla karşılaştırılır.

Kullanım:
    python scripts/migrate_chroma_to_sqlite.py                      # data/mevzuat_db → data/corpus_store
    python scripts/migrate_chroma_to_sqlite.py --source X --target Y [--replace]
Uygulama yeni depoyu MUFETTIS_VECTOR_STORE=sqlite ile kullanır.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.config import CHROMA_COLLECTION, CORPUS_STORE_PATH, DB_PATH  # noqa: E402
from mufettis.rag.store import SqliteStoreClient  # noqa: E402

PAGE = 1000
VECTOR_TOLERANCE = 2e-3  # float16 saklama hatası (birim vektörlerde ~5e-4)


def _pages(collection, include):
    offset = 0
    while True:
        got = collection.get(include=list(include), limit=PAGE, offset=offset)
        if not got["ids"]:
            return
        yield got
        offset += len(got["ids"])


def migrate(source: Path, target: Path, name: str = CHROMA_COLLECTION, replace: bool = False) -> dict:
    import chromadb

    src = chromadb.PersistentClient(path=str(source)).get_collection(name=name)
    client = SqliteStoreClient(target)
    if replace:
        try:
            client.delete_collection(name)
        except ValueError:
            pass
    dst = client.get_or_create_collection(name)
    if dst.count():
        raise SystemExit(f"Hedef boş değil ({dst.count()} parça): {target} (--replace ile baştan kurulur)")

    copied = 0
    for got in _pages(src, ("documents", "metadatas", "embeddings")):
        dst.upsert(ids=got["ids"], embeddings=np.asarray(got["embeddings"], dtype=np.float32),
                   documents=got["documents"], metadatas=got["metadatas"])
        copied += len(got["ids"])
        print(f"  {copied}/{src.count()} parça", end="\r", flush=True)
    print()
    return verify(src, dst)


def verify(src, dst) -> dict:
    problems = []
    if src.count() != dst.count():
        problems.append(f"parça sayısı {src.count()} ≠ {dst.count()}")
    max_err = 0.0
    offset = 0
    for got in _pages(src, ("documents", "metadatas", "embeddings")):
        mine = dst.get(limit=PAGE, offset=offset, include=["documents", "metadatas", "embeddings"])
        offset += len(got["ids"])
        if mine["ids"] != got["ids"]:
            problems.append(f"sıra/kimlik farkı (ofset {offset})")
            break
        if mine["documents"] != got["documents"]:
            problems.append(f"metin farkı (ofset {offset})")
        if mine["metadatas"] != got["metadatas"]:
            problems.append(f"metadata farkı (ofset {offset})")
        err = float(np.max(np.abs(np.asarray(mine["embeddings"]) - np.asarray(got["embeddings"], dtype=np.float32)),
                           initial=0.0))
        max_err = max(max_err, err)
    if max_err > VECTOR_TOLERANCE:
        problems.append(f"vektör farkı {max_err:.2e}")
    return {"chunks": dst.count(), "max_vector_error": max_err, "problems": problems}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DB_PATH)
    ap.add_argument("--target", type=Path, default=CORPUS_STORE_PATH)
    ap.add_argument("--name", default=CHROMA_COLLECTION)
    ap.add_argument("--replace", action="store_true")
    args = ap.parse_args()
    result = migrate(args.source, args.target, args.name, args.replace)
    print(result)
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
