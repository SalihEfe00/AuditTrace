# -*- coding: utf-8 -*-
"""M17.4: başlatıcı sağlamlığı — sunucu başlatıcıyla birlikte kapanır (Job Object), ikinci açılış yeni sunucu açmaz,
sunucu çökerse beklemeden Türkçe hata verilir, Streamlit dosya izleyicisi kapalı."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audittrace_launcher_robust", ROOT / "packaging" / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "Windows'a özgü")
class JobObjectTests(unittest.TestCase):
    def test_child_dies_when_job_handle_closes(self):
        import ctypes

        job = launcher.kill_on_close_job()
        self.assertTrue(job)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self.assertTrue(launcher.add_to_job(job, child))
            self.assertIsNone(child.poll())
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))  # başlatıcının ölmesiyle aynı
            child.wait(timeout=10)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()


@unittest.skipUnless(WINDOWS, "Windows'a özgü")
class SingleInstanceTests(unittest.TestCase):
    def test_second_instance_is_refused_and_brings_window_forward(self):
        name = f"Local\\AuditTrace.test_{uuid.uuid4().hex}"
        with mock.patch.object(launcher, "bring_to_front") as front:
            self.assertTrue(launcher.first_instance(name))
            first = launcher._INSTANCE_MUTEX
            self.assertFalse(launcher.first_instance(name))
            front.assert_called_once_with("AuditTrace")
        launcher._INSTANCE_MUTEX = first


class FailureTests(unittest.TestCase):
    def test_dead_server_is_noticed_without_waiting_for_timeout(self):
        dead = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
        dead.wait(timeout=30)
        started = time.monotonic()
        self.assertFalse(launcher.wait_for_port(launcher.free_port(), timeout=60, proc=dead))
        self.assertLess(time.monotonic() - started, 5)

    def test_error_text_is_turkish_and_points_to_the_log(self):
        text = launcher.error_text("uygulama sunucusu başlamadı")
        self.assertIn("AuditTrace açılamadı", text)
        self.assertIn(str(launcher.LOG), text)

    def test_error_box_logs_without_dialog_in_tests(self):
        with mock.patch.dict(os.environ, {"AUDITTRACE_NO_DIALOG": "1"}), mock.patch.object(launcher, "log") as log:
            launcher.error_box("deneme")
        log.assert_called_once_with("HATA: deneme")

    def test_file_watcher_is_off(self):
        source = (ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8")
        self.assertIn('"server_fileWatcherType": "none"', source)


if __name__ == "__main__":
    unittest.main()
