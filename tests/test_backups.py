# -*- coding: utf-8 -*-
"""Yedek saklama politikası (mufettis/common/backups.py)."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from mufettis.common import backups


class TestBackupRetention(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mufettis_backups_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _make(self, kind: str, stamp: str, manifest: bool = True) -> Path:
        d = backups.new_backup_dir(kind, stamp, self.root)
        (d / "store.sqlite3").write_bytes(b"x" * 10)
        if manifest:
            (d / "manifest.json").write_text(json.dumps({"kind": kind}), encoding="utf-8")
        return d

    def test_new_backup_dir_never_overwrites(self):
        self._make("rechunk", "20260927_210445")
        with self.assertRaises(FileExistsError):
            backups.new_backup_dir("rechunk", "20260927_210445", self.root)

    def test_keeps_newest_by_timestamp_across_kinds_and_keeps_manifests(self):
        self._make("rechunk", "20260926_205301")
        self._make("e7_legacy_recovery", "20260926_142223")
        self._make("version_links", "20260927_170102")
        self._make("rechunk", "20260927_210445")
        self._make("canonical_backfill", "20260927_194323", manifest=False)

        removed = backups.prune_backups(self.root, keep=2)

        self.assertEqual([d.name for d in removed],
                         ["e7_legacy_recovery_20260926_142223", "rechunk_20260926_205301",
                          "version_links_20260927_170102"])
        self.assertEqual([d.name for d in backups.backup_dirs(self.root)],
                         ["canonical_backfill_20260927_194323", "rechunk_20260927_210445"])
        manifests = sorted(p.name for p in (self.root / backups.MANIFEST_DIR_NAME).iterdir())
        self.assertEqual(manifests, [f"{d.name}.json" for d in removed])

    def test_unrelated_entries_untouched(self):
        (self.root / "notlar").mkdir()
        (self.root / "rechunk_elle_kopya").mkdir()
        (self.root / "README.txt").write_text("el ile", encoding="utf-8")
        for stamp in ("20260101_000000", "20260102_000000", "20260103_000000"):
            self._make("rechunk", stamp)

        backups.prune_backups(self.root, keep=1)

        self.assertTrue((self.root / "notlar").is_dir())
        self.assertTrue((self.root / "rechunk_elle_kopya").is_dir())
        self.assertTrue((self.root / "README.txt").is_file())
        self.assertEqual([d.name for d in backups.backup_dirs(self.root)], ["rechunk_20260103_000000"])

    def test_nothing_removed_within_limit_and_keep_must_be_positive(self):
        self._make("rechunk", "20260101_000000")
        self.assertEqual(backups.prune_backups(self.root, keep=2), [])
        self.assertEqual(backups.prune_backups(self.root / "yok", keep=2), [])
        with self.assertRaises(ValueError):
            backups.prune_backups(self.root, keep=0)


if __name__ == "__main__":
    unittest.main()
