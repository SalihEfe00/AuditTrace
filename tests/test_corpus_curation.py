# -*- coding: utf-8 -*-
"""Mevzuat olmayan kayıtların ayıklanması (M6.5): kural, silmeden ayıklama, geri alma, aramadan düşme."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.corpus.curation import exclude, find_non_legislation, non_legislation_reason, restore
from mufettis.legal.parser import PRESERVED_STATUSES, _chunk_is_retrievable
from mufettis.rag.index import IngestIndex

PORTAL = {"title": "Mükellef Girişi — e-Beyan Panel", "source_id": "mevzuat_org", "publisher_kind": "secondary",
          "article_count": 0, "structure_type": "document", "status": "active"}
TEST_FILE = {"title": "mufettis_ai_test_temiz", "source_id": "local_file", "publisher_kind": "user_supplied",
             "article_count": 0, "structure_type": "section", "status": "active"}
BDDK_GUIDE = {"title": "Rehber", "source_id": "bddk_official", "publisher_kind": "issuing_authority",
              "article_count": 0, "structure_type": "section", "status": "active"}
LAW_FILE = {"title": "55555", "source_id": "local_file", "publisher_kind": "user_supplied", "article_count": 8,
            "structure_type": "article", "status": "active"}
INTERNAL = {"title": "Toplantı notu", "publisher_kind": "internal", "article_count": 0, "status": "active"}


class _Rag:
    def __init__(self):
        self.updates = []

    def update_hash_metadata(self, file_hash, updates):
        self.updates.append((file_hash, dict(updates)))
        return 7


class TestRule(unittest.TestCase):
    def test_only_unofficial_records_without_articles(self):
        self.assertTrue(non_legislation_reason(PORTAL))
        self.assertTrue(non_legislation_reason(TEST_FILE))
        for record in (BDDK_GUIDE, LAW_FILE, INTERNAL, {**PORTAL, "status": "superseded"}):
            self.assertEqual(non_legislation_reason(record), "", record["title"])

    def test_local_files_are_only_proposed_for_review(self):
        found = find_non_legislation([("a", PORTAL), ("b", TEST_FILE), ("c", BDDK_GUIDE), ("d", LAW_FILE)])
        self.assertEqual([(f["file_hash"], f["action"]) for f in found], [("a", "exclude"), ("b", "review")])


class TestExcludeRestore(unittest.TestCase):
    def test_exclusion_is_reversible_and_not_a_deletion(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("mufettis.audit.service.append_audit") as audit:
            index = IngestIndex(Path(tmp) / "ingest_index.json")
            index.upsert("h1", {**PORTAL, "file_hash": "h1"})
            rag = _Rag()
            self.assertEqual(exclude(rag, index, "h1", "portal sayfası"), 7)
            record = index.get("h1")
            self.assertEqual((record["status"], record["status_before_exclusion"]), ("excluded", "active"))
            self.assertEqual(rag.updates[-1], ("h1", {"status": "excluded"}))
            self.assertEqual(restore(rag, index, "h1"), 7)
            self.assertEqual(index.get("h1")["status"], "active")
            self.assertEqual(rag.updates[-1], ("h1", {"status": "active"}))
            self.assertEqual([c.args[0] for c in audit.call_args_list],
                             ["corpus_record_excluded", "corpus_record_restored"])

    def test_excluded_chunks_are_not_retrievable_and_status_survives_reextraction(self):
        chunk = {"chunk_schema_version": str(CHUNK_SCHEMA_VERSION), "document_indexable": "True", "status": "active"}
        self.assertTrue(_chunk_is_retrievable(chunk, "x" * 300))
        self.assertFalse(_chunk_is_retrievable({**chunk, "status": "excluded"}, "x" * 300))
        self.assertIn("excluded", PRESERVED_STATUSES)


if __name__ == "__main__":
    unittest.main()
