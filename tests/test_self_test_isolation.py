# -*- coding: utf-8 -*-
"""
B2: Uygulama içi self-test (`--self-test` ve UI butonu) veri izolasyonu testleri.

run_self_tests() testleri ayrı bir süreçte, veri dizininin geçici bir kopyası
üzerinde çalıştırmalı ve kaynak veri dizinine hiçbir şey yazmamalıdır.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from mufettis.config import DATA_DIR
from mufettis.tests import runner
from mufettis.tests.isolation import create_isolated_data_copy, diff, snapshot


class TestIsolationHelpers(unittest.TestCase):
    def test_diff_reports_added_removed_and_changed(self):
        before = {"a.txt": (1, 1), "b.txt": (2, 2), "c.txt": (3, 3)}
        after = {"a.txt": (1, 1), "c.txt": (3, 4), "d.txt": (5, 5)}
        self.assertEqual(diff(before, after), ["+ d.txt", "- b.txt", "~ c.txt"])
        self.assertEqual(diff(before, dict(before)), [])

    def test_create_isolated_copy_does_not_share_files(self):
        import shutil
        import tempfile

        src_root = Path(tempfile.mkdtemp(prefix="iso_src_"))
        try:
            (src_root / "sub").mkdir()
            (src_root / "sub" / "x.json").write_text("{}", encoding="utf-8")
            temp_root, data_dir = create_isolated_data_copy(src_root)
            try:
                before = snapshot(src_root)
                (data_dir / "sub" / "x.json").write_text('{"changed": true}', encoding="utf-8")
                (data_dir / "new.txt").write_text("x", encoding="utf-8")
                self.assertEqual(diff(before, snapshot(src_root)), [])
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)
        finally:
            shutil.rmtree(src_root, ignore_errors=True)

    def test_backup_dirs_are_not_copied(self):
        import shutil
        import tempfile

        src_root = Path(tempfile.mkdtemp(prefix="iso_src_"))
        try:
            for name in ("backups", "audit_logs_backup", "audit_logs"):
                (src_root / name).mkdir()
                (src_root / name / "x.jsonl").write_text("{}", encoding="utf-8")
            (src_root / "audit_logs" / "backups").mkdir()  # yalnızca kök düzeyi hariç tutulur
            temp_root, data_dir = create_isolated_data_copy(src_root)
            try:
                self.assertFalse((data_dir / "backups").exists())
                self.assertFalse((data_dir / "audit_logs_backup").exists())
                self.assertTrue((data_dir / "audit_logs" / "x.jsonl").exists())
                self.assertTrue((data_dir / "audit_logs" / "backups").is_dir())
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)
        finally:
            shutil.rmtree(src_root, ignore_errors=True)

    def test_missing_source_creates_empty_data_dir(self):
        import shutil

        temp_root, data_dir = create_isolated_data_copy(Path("__olmayan_veri_dizini__"))
        try:
            self.assertTrue(data_dir.is_dir())
            self.assertEqual(list(data_dir.iterdir()), [])
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


class TestIsolatedSelfTestRunner(unittest.TestCase):
    def test_self_test_runs_in_isolated_copy_without_touching_source(self):
        before = snapshot(DATA_DIR)
        result = runner.run_self_tests(["mufettis.tests.test_pipeline.TestMufettisAI"])

        self.assertGreater(result.testsRun, 0)
        self.assertTrue(result.wasSuccessful(), [t for t, _ in result.failures + result.errors])
        self.assertEqual(result.production_changes, [])
        self.assertEqual(diff(before, snapshot(DATA_DIR)), [])
        self.assertIn(f"Ran {result.testsRun} test", result.output)

        isolated = Path(result.isolated_data_dir)
        self.assertNotEqual(isolated.resolve(), DATA_DIR.resolve())
        self.assertFalse(isolated.exists(), "Geçici self-test veri dizini temizlenmeli")

    def test_subprocess_failure_is_reported_as_error(self):
        result = runner.run_self_tests(["mufettis.tests.olmayan_modul.OlmayanTest"])
        self.assertFalse(result.wasSuccessful())
        self.assertGreaterEqual(len(result.errors), 1)

    def test_missing_result_file_is_reported_as_error(self):
        fake = subprocess.CompletedProcess(args=[], returncode=3, stdout="", stderr="çöktü")
        with patch.object(runner.subprocess, "run", return_value=fake):
            result = runner.run_self_tests()

        self.assertFalse(result.wasSuccessful())
        ids = [str(test) for test, _ in result.errors]
        self.assertIn("self_test_subprocess", ids)
        detail = dict((str(t), d) for t, d in result.errors)["self_test_subprocess"]
        self.assertIn("3", detail)
        self.assertIn("çöktü", detail)

    def test_source_mutation_is_detected_and_reported(self):
        rogue = DATA_DIR / "self_test_rogue_write.txt"

        def rogue_run(*args, **kwargs):
            rogue.write_text("izolasyon ihlali", encoding="utf-8")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        try:
            with patch.object(runner.subprocess, "run", side_effect=rogue_run):
                result = runner.run_self_tests()
        finally:
            rogue.unlink(missing_ok=True)

        self.assertIn("+ self_test_rogue_write.txt", result.production_changes)
        ids = [str(test) for test, _ in result.errors]
        self.assertIn("production_data_isolation", ids)
        self.assertFalse(result.wasSuccessful())


if __name__ == "__main__":
    unittest.main()
