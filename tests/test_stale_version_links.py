# -*- coding: utf-8 -*-
"""Aktif belgelerde kalmış yanlış sürüm bağlarının temizlenmesi (scripts/clear_stale_version_links.py)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


class FakeCollection:
    def __init__(self, metas: Dict[str, Dict[str, Any]]):
        self.metas = metas

    def get(self, where=None, include=None, limit=None, offset=0, ids=None, **kw):
        keys = sorted(self.metas)
        if where and "file_hash" in where:
            keys = [k for k in keys if self.metas[k]["file_hash"] == where["file_hash"]]
        page = keys[offset:offset + limit] if limit is not None else keys
        return {"ids": page, "documents": [""] * len(page), "metadatas": [dict(self.metas[k]) for k in page]}

    def update(self, ids, metadatas):
        for i, meta in zip(ids, metadatas):
            self.metas[i] = dict(meta)


class FakeRag:
    def __init__(self, metas):
        self.collection = FakeCollection(metas)

    def update_hash_metadata(self, file_hash: str, updates: Dict[str, Any]) -> int:
        found = [k for k, m in self.collection.metas.items() if m["file_hash"] == file_hash]
        for k in found:
            self.collection.metas[k].update(updates)
        return len(found)


class FakeIndex:
    def __init__(self, items):
        self.data = {"indexed_items": items}

    def get(self, h):
        return self.data["indexed_items"].get(h)

    def upsert(self, h, meta):  # IngestIndex.upsert gibi birleştirir
        self.data["indexed_items"][h] = {**self.data["indexed_items"].get(h, {}), **meta}


def _rec(key: str, **over: Any) -> Dict[str, Any]:
    return {"title": key, "document_key": key, "source_id": "bddk_official", "status": "active",
            "timestamp": "2026-09-01", **over}


class TestClearStaleVersionLinks(unittest.TestCase):
    def setUp(self):
        self.items = {
            # E8 sonrası kalıntı: farklı anahtara işaret eden bağ, index'te ve parçalarda.
            "a": _rec("943", superseded_by="b", valid_to="2026-09-05"),
            "b": _rec("1043"),
            # Gerçek sürüm zinciri: aynı anahtar, eski sürüm "superseded" kalmalı.
            "old": _rec("877", status="superseded", superseded_by="new", valid_to="2026-09-02"),
            "new": _rec("877", timestamp="2026-09-02"),
        }
        self.chunks = {
            "a-1": {"file_hash": "a", "superseded_by": "b", "valid_to": "2026-09-05"},
            "b-1": {"file_hash": "b"},
            "old-1": {"file_hash": "old", "superseded_by": "new", "valid_to": "2026-09-02"},
            "new-1": {"file_hash": "new", "superseded_by": "", "valid_to": ""},
        }

    def test_plan_apply_verify(self):
        import clear_stale_version_links as script

        index, rag = FakeIndex(self.items), FakeRag(self.chunks)
        the_plan = script.plan(index, rag)
        self.assertEqual([i["hash"] for i in the_plan["index_links"]], ["a"])
        self.assertEqual([(c["hash"], c["chunks"]) for c in the_plan["chunk_links"]], [("a", 1)])
        self.assertEqual(the_plan["pending_supersede"], [])

        script.apply(index, rag, the_plan)
        self.assertEqual((index.get("a")["superseded_by"], index.get("a")["valid_to"]), ("", ""))
        self.assertEqual(rag.collection.metas["a-1"]["valid_to"], "")
        # Gerçek zincire dokunulmaz.
        self.assertEqual(index.get("old")["superseded_by"], "new")
        self.assertEqual(rag.collection.metas["old-1"]["superseded_by"], "new")
        after = script.plan(index, rag)
        self.assertEqual(after["index_links"] + after["chunk_links"], [])


if __name__ == "__main__":
    unittest.main()
