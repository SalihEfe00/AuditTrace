# -*- coding: utf-8 -*-
"""Yönetim ekranından yüklenen dosya geçici bir ada ("upload_<uuid>_<ad>") yazılır. Kayıt başlığı
kullanıcının dosya adından türemeli; rastgele önek başlık tabanlı kimliği de bozuyordu (aynı dosyanın
iki yüklemesi farklı Work sayılırdı)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _Index:
    def __init__(self):
        self.data = {"indexed_items": {}}

    def upsert(self, key, value):
        self.data["indexed_items"][key] = value

    def get(self, key):
        return self.data["indexed_items"].get(key)


class TestUploadTitle(unittest.TestCase):
    def _ingest(self, original_name):
        from mufettis.rag import maintenance

        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp) / "upload_0123456789abcdef0123456789abcdef_Kart Kanunu.txt"
            temp.write_text("KART KANUNU\nMADDE 1 – Kart hamilleri korunur.\n" * 5, encoding="utf-8")
            fake = lambda rag, index, text, meta, **kw: {"status": "ingested", "count": 1, "meta": meta}
            with patch.object(maintenance, "versioned_ingest_text", side_effect=fake), \
                    patch.object(maintenance, "append_audit"):
                return maintenance.ingest_local_regulation_file(temp, rag=None, index=_Index(),
                                                                original_name=original_name)

    def test_title_and_identity_come_from_the_users_file_name(self):
        first = self._ingest("Kart Kanunu.txt")
        self.assertEqual(first["title"], "Kart Kanunu")
        self.assertEqual(first["file_name"], "Kart Kanunu.txt")
        self.assertEqual(first["local_path"], "")
        self.assertNotIn("upload_", first["work_id"])

    def test_without_original_name_the_path_is_used(self):
        meta = self._ingest(None)
        self.assertTrue(meta["title"].startswith("upload_"))
        self.assertTrue(meta["local_path"])




class TestLegacyUploadTitleBackfill(unittest.TestCase):
    def test_random_upload_prefix_is_removed_from_existing_records(self):
        import importlib.util

        script = Path(__file__).resolve().parent.parent / "scripts" / "backfill_canonical_metadata.py"
        spec = importlib.util.spec_from_file_location("backfill_canonical_metadata", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        rec = {"title": "upload_0123456789abcdef0123456789abcdef_Kart  Kanunu", "title_normalized": "x"}
        self.assertEqual(module.clean_legacy_title(rec)["title"], "Kart  Kanunu")
        self.assertEqual(module.clean_legacy_title(rec)["title_normalized"], "kart kanunu")
        plain = {"title": "upload_notes"}
        self.assertIs(module.clean_legacy_title(plain), plain)


if __name__ == "__main__":
    unittest.main()
