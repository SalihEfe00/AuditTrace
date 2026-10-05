# -*- coding: utf-8 -*-
"""M15.3 hızlı açılış: arayüz modülü ağır kütüphaneleri (torch, chromadb, sentence-transformers) yüklemez;
mevzuat kütüphanesi sayfa açılırken değil, ilk gerektiğinde kurulur ve oturum boyunca açık kalır."""
from __future__ import annotations

import contextlib
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from mufettis.ui import app

ROOT = Path(__file__).resolve().parents[1]


class TestHeavyLibrariesNotImportedAtOpen(unittest.TestCase):
    def test_app_import_skips_heavy_libraries(self):
        code = ("import sys, app; heavy = ['torch', 'chromadb', 'sentence_transformers', 'transformers'];"
                "print(','.join(m for m in heavy if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "", "")


class TestRagWhenNeeded(unittest.TestCase):
    def setUp(self):
        self.fake_st = types.SimpleNamespace(session_state={}, spinner=lambda *_a, **_k: contextlib.nullcontext())
        patcher = mock.patch.object(app, "st", self.fake_st)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.get_rag = mock.Mock(return_value="rag")
        getter = mock.patch.object(app, "_get_rag", self.get_rag)
        getter.start()
        self.addCleanup(getter.stop)

    def test_not_built_until_needed(self):
        self.assertIsNone(app._rag_when_needed(False))
        self.get_rag.assert_not_called()

    def test_built_when_needed_and_kept_for_session(self):
        self.assertEqual(app._rag_when_needed(False, needed=True), "rag")
        self.assertTrue(self.fake_st.session_state["rag_requested"])
        self.assertEqual(app._rag_when_needed(False), "rag")  # sonraki çizimlerde yeniden istemeye gerek yok
        self.assertEqual(self.get_rag.call_count, 2)  # kurulum _get_rag içinde önbellekli

    def test_unavailable_reason_still_reported(self):
        self.get_rag.return_value = None
        self.assertIsNone(app._rag_when_needed(False, needed=True))
        self.get_rag.assert_called_once_with(allow_download=False)


if __name__ == "__main__":
    unittest.main()
