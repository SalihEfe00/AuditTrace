# -*- coding: utf-8 -*-
"""Mevcut parçalara değişiklik notu meta verisinin eklenmesi (M7.2).

Yeni yüklenen belgeler bu alanları yüklemede alır (LocalRAG.ingest_text). Önceden yüklenmiş belgelerin parçaları
yeniden gömülmeden, depodaki parça metninden tamamlanır: `amended_on`, `amendments`, `article_amended_on`.
Parça metni ve madde alanları değişmez; yalnızca bu üç alan yazılır. Yeniden çalıştırmak güvenlidir.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from mufettis.config import CHROMA_UPSERT_BATCH_SIZE, CHUNK_SCHEMA_VERSION
from mufettis.legal.amendments import amendment_metadata
from mufettis.rag.paging import iter_pages

FIELDS = ("amended_on", "amendments", "article_amended_on")


def plan_backfill(rag: Any) -> Dict[str, List[tuple]]:
    """Belge başına güncellenecek (parça kimliği, yeni meta) listesi; yalnızca değişecek parçalar."""
    by_document: Dict[str, List[tuple]] = defaultdict(list)
    for ids, docs, metas in iter_pages(rag.collection, where={"chunk_schema_version": CHUNK_SCHEMA_VERSION}):
        for cid, doc, meta in zip(ids, docs, metas):
            by_document[str((meta or {}).get("file_hash", ""))].append((cid, doc or "", dict(meta or {})))
    plan: Dict[str, List[tuple]] = {}
    for file_hash, rows in by_document.items():
        rows.sort(key=lambda r: int(r[2].get("chunk_no") or 0))
        chunks = [{"text": doc, "article_prefix": m.get("article_prefix", ""), "article_no": m.get("article_no", "")}
                  for _, doc, m in rows]
        updates = []
        for (cid, _, meta), fields in zip(rows, amendment_metadata(chunks)):
            if any(str(meta.get(k, "")) != v for k, v in fields.items()):
                updates.append((cid, {**meta, **fields}))
        if updates:
            plan[file_hash] = updates
    return plan


def apply_backfill(rag: Any, plan: Dict[str, List[tuple]]) -> int:
    rows = [row for updates in plan.values() for row in updates]
    for start in range(0, len(rows), CHROMA_UPSERT_BATCH_SIZE):
        batch = rows[start:start + CHROMA_UPSERT_BATCH_SIZE]
        rag.collection.update(ids=[cid for cid, _ in batch],
                              metadatas=[{k: str(v) for k, v in meta.items()} for _, meta in batch])
    return len(rows)


__all__ = ["plan_backfill", "apply_backfill", "FIELDS"]
