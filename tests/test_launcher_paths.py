# -*- coding: utf-8 -*-
"""M17.2: paket yazılan her şeyi kullanıcı klasörüne (%LOCALAPPDATA%/AuditTrace) koyar; kurulum klasörü salt okunur
olabilir (M3.3'te başlatıcı exe'nin yanına günlük yazmaya çalışırken çökmüştü)."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mufettis_launcher", ROOT / "packaging" / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class TestLauncherPaths(unittest.TestCase):
    def test_package_writes_under_localappdata(self):
        env = {"LOCALAPPDATA": r"C:\Users\x\AppData\Local", "TEMP": r"C:\t"}
        self.assertEqual(launcher.user_dir(True, env), Path(env["LOCALAPPDATA"]) / "AuditTrace")

    def test_source_run_uses_temp(self):
        env = {"LOCALAPPDATA": r"C:\Users\x\AppData\Local", "TEMP": r"C:\t"}
        self.assertEqual(launcher.user_dir(False, env), Path(r"C:\t") / "mufettis_launcher")

    def test_data_prefers_user_folder_then_install_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, user = Path(tmp) / "kurulum", Path(tmp) / "kullanici"
            self.assertIsNone(launcher.data_dir(home, user))
            (home / "data").mkdir(parents=True)
            self.assertEqual(launcher.data_dir(home, user), home / "data")
            (user / "data").mkdir(parents=True)
            self.assertEqual(launcher.data_dir(home, user), user / "data")

    def test_logs_not_next_to_exe_in_package(self):
        source = (ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8")
        self.assertIn('LOG_DIR = USER / "logs" if FROZEN else USER', source)
        self.assertIn("USER / 'edge_profile'", source)


if __name__ == "__main__":
    unittest.main()
