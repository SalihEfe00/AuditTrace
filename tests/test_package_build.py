# -*- coding: utf-8 -*-
"""M17.3: paket betikleri — veri seçimi kullanıcı kayıtlarını almaz; DLL denetimi VC++ çalışma zamanını hazır saymaz;
spec torch ailesini dışarıda bırakır, kural kataloğu ve örnek dosyayı içeri alır."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from scripts.build_package_data import build

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("audittrace_build", ROOT / "packaging" / "build.py")
package = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(package)


class PackageDataTests(unittest.TestCase):
    def test_selects_library_and_skips_user_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "data", Path(tmp) / "out"
            for d in ("corpus_store", "source_cache", "audit", "exports", "backups", "legislation_downloads"):
                (src / d).mkdir(parents=True)
                (src / d / "x.txt").write_text("x", encoding="utf-8")
            for f in ("ingest_index.json", "ingest_index.sqlite3", "term_stats.json", "reranker_calibration.json",
                      "mevzuat_db_lexical_v2.sqlite3", "review_registry.json", "settings.json",
                      "authority_table.json"):
                (src / f).write_text("x", encoding="utf-8")
            build(src, dst)
            self.assertEqual(sorted(p.name for p in dst.iterdir()), sorted([
                "corpus_store", "source_cache", "ingest_index.json", "ingest_index.sqlite3", "term_stats.json",
                "reranker_calibration.json", "mevzuat_db_lexical_v2.sqlite3"]))

    def test_refuses_existing_target_and_missing_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                build(Path(tmp), Path(tmp))
            with self.assertRaises(SystemExit):
                build(Path(tmp), Path(tmp) / "out")


class DllAuditTests(unittest.TestCase):
    def test_vc_runtime_is_not_assumed_present(self):
        for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "vcomp140.dll"):
            self.assertFalse(package.system_dll(name), name)

    def test_api_sets_and_core_dlls_are_present(self):
        self.assertTrue(package.system_dll("api-ms-win-crt-runtime-l1-1-0.dll"))
        self.assertTrue(package.system_dll("kernel32.dll"))
        self.assertFalse(package.system_dll("libtesseract-5.dll"))


class SpecTests(unittest.TestCase):
    def test_spec_excludes_torch_family_and_includes_app_files(self):
        spec = (ROOT / "packaging" / "audittrace.spec").read_text(encoding="utf-8")
        for name in ("torch", "transformers", "sentence_transformers", "sklearn", "scipy", "chromadb"):
            self.assertIn(f'"{name}"', spec)
        for part in ('"rules" / "catalog"', '"ui" / "samples"', '"ui" / "brand"', 'name="AuditTrace"', "icon.ico"):
            self.assertIn(part, spec)


class InstallerScriptTests(unittest.TestCase):
    """M17.5: kullanıcı başına kurulum; AppId sabit (güncelleme üstüne kurar); kullanıcı verisi silinmez; ısıtma."""

    def setUp(self):
        self.iss = (ROOT / "packaging" / "audittrace.iss").read_text(encoding="utf-8")

    def test_per_user_without_admin(self):
        self.assertIn("PrivilegesRequired=lowest", self.iss)
        self.assertIn(r"DefaultDirName={autopf}\{#AppName}", self.iss)

    def test_app_id_is_fixed(self):
        self.assertIn("AppId={{E92EE63D-AAEC-41D8-98DB-AE008C9663B4}", self.iss)

    def test_user_data_survives_update_and_uninstall(self):
        deleting = [line for line in self.iss.splitlines() if line.startswith("Type: filesandordirs")]
        self.assertTrue(deleting)
        for line in deleting:
            self.assertNotIn(r"AuditTrace\data", line)
            self.assertTrue(r"{app}" in line or r"{localappdata}\AuditTrace\edge_profile" in line
                            or r"{localappdata}\AuditTrace\logs" in line, line)

    def test_warm_up_and_shortcuts(self):
        self.assertIn("--selftest", self.iss)
        self.assertIn(r"{autoprograms}\{#AppName}", self.iss)
        self.assertIn(r"{autodesktop}\{#AppName}", self.iss)
        self.assertIn("Turkish.isl", self.iss)


if __name__ == "__main__":
    unittest.main()
