# -*- coding: utf-8 -*-
"""SQLite tabanlı ingest index: eski JSON'dan geçiş, arayüz uyumluluğu, tutarlılık ve ölçek."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from mufettis.rag.index import IngestIndex, store_path


def _write_json(path: Path, items) -> None:
    path.write_text(json.dumps({"version": "2.0", "indexed_items": items}, ensure_ascii=False), encoding="utf-8")


class TestIngestIndexSqlite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # temizlik, açık dosya kilidi kalmadığını da doğrular
        self.json_path = Path(self.tmp.name) / "ingest_index.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_legacy_json_is_imported_once_and_reimported_when_replaced(self):
        _write_json(self.json_path, {"a": {"title": "A", "status": "active"}, "b": {"title": "B"}})
        index = IngestIndex(path=self.json_path)
        self.assertEqual(index.path, store_path(self.json_path))
        self.assertEqual(list(index.data["indexed_items"]), ["a", "b"])
        self.assertTrue(self.json_path.exists())  # eski dosya yedek olarak yerinde kalır

        index.upsert("c", {"title": "C"})
        self.assertIn("c", IngestIndex(path=self.json_path).data["indexed_items"])  # değişmeyen JSON yeniden alınmaz

        # Yedek JSON geri konursa (dosya değişti) içeriği indeksin yerine geçer.
        _write_json(self.json_path, {"z": {"title": "Z"}})
        os.utime(self.json_path, ns=(time.time_ns(), time.time_ns() + 10**9))
        self.assertEqual(list(IngestIndex(path=self.json_path).data["indexed_items"]), ["z"])

    def test_corrupt_legacy_json_is_an_error_not_an_empty_index(self):
        self.json_path.write_text("{bozuk", encoding="utf-8")
        with self.assertRaises(ValueError):
            IngestIndex(path=self.json_path)

    def test_mapping_interface_merge_order_and_lookup(self):
        index = IngestIndex(path=self.json_path)
        index.upsert("h1", {"title": "Örnek Yönetmelik", "title_normalized": "örnek yönetmelik",
                            "source_id": "bddk", "status": "active", "superseded_by": "x"})
        index.upsert("h2", {"title": "İkinci"})
        index.upsert("h1", {"superseded_by": ""})  # birleştirir, diğer alanlar korunur
        items = index.data.get("indexed_items", {})
        self.assertEqual(items["h1"]["title"], "Örnek Yönetmelik")
        self.assertEqual(items["h1"]["superseded_by"], "")
        self.assertEqual([h for h, _ in items.items()], ["h1", "h2"])  # güncelleme sırayı bozmaz
        self.assertEqual(len(items.values()), 2)
        self.assertEqual(items.get("yok"), None)
        self.assertNotIn("yok", items)
        self.assertEqual([r["title"] for r in index.find_by_title_and_source("Örnek  Yönetmelik", "bddk")],
                         ["Örnek Yönetmelik"])

        index.replace("h2", {"title": "Yeni"})
        self.assertEqual(index.get("h2"), {"title": "Yeni"})
        self.assertTrue(index.remove("h2"))
        self.assertFalse(index.remove("h2"))
        del items["h1"]
        self.assertEqual(index.count(), 0)

    def test_writes_during_iteration_and_between_instances(self):
        first, second = IngestIndex(path=self.json_path), IngestIndex(path=self.json_path)
        for i in range(600):  # birden fazla sayfa (PAGE_SIZE 500)
            first.upsert(f"h{i:04d}", {"status": "active"})
        for h, rec in second.data["indexed_items"].items():
            second.upsert(h, {"status": "superseded"})
        self.assertEqual({r["status"] for r in first.data["indexed_items"].values()}, {"superseded"})
        self.assertEqual(len(first.data["indexed_items"]), 600)

    def test_single_update_cost_does_not_grow_with_index_size(self):
        index = IngestIndex(path=self.json_path)
        for i in range(1000):
            index.upsert(f"{i:064x}", {"title": f"Belge {i}", "status": "active", "notes": "x" * 2000})
        start = time.perf_counter()
        for i in range(20):
            index.upsert(f"{i:064x}", {"status": "superseded"})
        self.assertLess((time.perf_counter() - start) / 20, 0.2)

    def test_backup_and_json_export(self):
        index = IngestIndex(path=self.json_path)
        index.upsert("a", {"title": "A"})
        copy = IngestIndex(path=index.backup(Path(self.tmp.name) / "yedek" / "ingest_index.sqlite3"))
        self.assertEqual(copy.get("a"), {"title": "A"})
        exported = index.export_json(Path(self.tmp.name) / "disari.json")
        self.assertEqual(json.loads(exported.read_text(encoding="utf-8"))["indexed_items"], {"a": {"title": "A"}})


if __name__ == "__main__":
    unittest.main()
