# -*- coding: utf-8 -*-
"""
E7: eski şema parçaları yeniden yüklemeyi engellememeli.

LocalRAG.query yalnızca güncel CHUNK_SCHEMA_VERSION parçalarını döndürür. Önceden
ingest_text, bir hash için HERHANGİ bir parça varsa "skipped" dönüyordu; bu yüzden
yalnızca eski şema parçaları olan belgeler (ör. 5411 Bankacılık Kanunu) hem aranamaz
hem de yeniden yüklenemez durumda kalıyordu.
"""
from __future__ import annotations

import unittest

from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.rag.engine import LocalRAG

HASH = "b" * 64


class _FakeCollection:
    def __init__(self, records):
        self.records = dict(records)  # id -> metadata

    def get(self, where=None, include=None, ids=None):
        fh = (where or {}).get("file_hash")
        ids_ = [i for i, m in self.records.items() if fh is None or m.get("file_hash") == fh]
        return {"ids": ids_, "metadatas": [self.records[i] for i in ids_]}

    def delete(self, where=None, ids=None):
        if ids is not None:
            for i in ids:
                self.records.pop(i, None)
            return
        fh = (where or {}).get("file_hash")
        for i in [i for i, m in self.records.items() if m.get("file_hash") == fh]:
            self.records.pop(i)

    def upsert(self, ids, embeddings, documents, metadatas):
        for i, m in zip(ids, metadatas):
            self.records[i] = dict(m)


def _rag(records):
    rag = object.__new__(LocalRAG)
    rag.collection = _FakeCollection(records)
    rag._model = None
    rag.embed = lambda texts, batch_size=None: [[0.0, 0.0, 0.0, 1.0] for _ in texts]
    return rag


TEXT = "MADDE 1 - Amaç\n(1) Bu Kanunun amacı bankacılık sektörünün düzenlenmesidir.\nMADDE 2 - Kapsam\n(1) Tüm bankaları kapsar."
META = {"file_hash": HASH, "title": "Test Kanunu", "document_type": "Kanun"}


class TestLegacySchemaReingest(unittest.TestCase):
    def test_legacy_only_chunks_are_replaced_with_current_schema(self):
        rag = _rag({f"{HASH[:16]}-1-1": {"file_hash": HASH}, f"{HASH[:16]}-1-2": {"file_hash": HASH}})
        result = rag.ingest_text(TEXT, dict(META))
        self.assertNotEqual(result["status"], "skipped")
        metas = list(rag.collection.records.values())
        self.assertTrue(metas)
        self.assertTrue(all(str(m.get("chunk_schema_version")) == str(CHUNK_SCHEMA_VERSION) for m in metas))
        self.assertNotIn(f"{HASH[:16]}-1-1", rag.collection.records)

    def test_current_schema_chunks_are_still_skipped(self):
        rag = _rag({"x-1": {"file_hash": HASH, "chunk_schema_version": CHUNK_SCHEMA_VERSION}})
        self.assertEqual(rag.ingest_text(TEXT, dict(META))["status"], "skipped")
        self.assertEqual(list(rag.collection.records), ["x-1"])


if __name__ == "__main__":
    unittest.main()
