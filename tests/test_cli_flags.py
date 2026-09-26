# -*- coding: utf-8 -*-
"""E5: app.py CLI bayrak davranışı (onarım dalgası kipi ve rollback tekrar koruması)."""
from __future__ import annotations

import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

APP = Path(__file__).resolve().parent.parent / "app.py"


def _run_app(argv):
    with patch.object(sys, "argv", ["app.py", *argv]):
        try:
            runpy.run_path(str(APP), run_name="__main__")
        except SystemExit as exc:
            return exc.code
    return None


class TestRepairWaveModeFlags(unittest.TestCase):
    def test_dry_run_and_execute_are_mutually_exclusive(self):
        proc = subprocess.run(
            [sys.executable, str(APP), "--repair-wave", "--repair-wave-dry-run", "--repair-wave-execute"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(APP.parent),
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not allowed with argument", proc.stderr)


class TestRollbackForceFlag(unittest.TestCase):
    def _captured_force(self, extra_args):
        fake_result = SimpleNamespace(
            status="ROLLBACK_ALREADY_RESTORED", cache_status="PASS", index_status="PASS",
            chroma_status="PASS", fingerprint_status="PASS", failures=[], warnings=[],
        )
        with patch("repair.rollback.rollback_commit", return_value=fake_result) as rollback, \
                patch("mufettis.rag.engine.LocalRAG", MagicMock()), \
                patch("mufettis.rag.index.IngestIndex", MagicMock()):
            code = _run_app(["--repair-rollback", "commit_abc", *extra_args])
        self.assertEqual(code, 0)
        return rollback.call_args.kwargs["force"]

    def test_rollback_keeps_idempotency_guard_by_default(self):
        self.assertIs(self._captured_force([]), False)

    def test_force_flag_is_explicit_opt_in(self):
        self.assertIs(self._captured_force(["--repair-rollback-force"]), True)


class TestBrowserFallback(unittest.TestCase):
    def test_sync_web_source_falls_back_to_http_without_playwright(self):
        from mufettis.harvesting import resmi_gazete, web_harvester

        used = []

        def fake_collect(self, max_depth=0, max_pages=0):
            used.append(type(self).__name__)
            return []

        with patch.object(web_harvester, "sync_playwright", None), \
                patch.object(web_harvester.WebSourceHarvester, "collect_candidates", fake_collect), \
                patch.object(web_harvester.BrowserWebSourceHarvester, "collect_candidates", fake_collect):
            result = resmi_gazete.sync_web_source("bddk_official", rag=MagicMock(), use_browser=True)

        self.assertEqual(used, ["WebSourceHarvester"])
        self.assertIn("browser_fallback", result)


if __name__ == "__main__":
    unittest.main()
