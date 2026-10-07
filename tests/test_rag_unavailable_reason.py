# -*- coding: utf-8 -*-
"""RAG kurulamadığında sebep kaybolmaz: motor hatasında, günlükte ve arayüz mesajında görünür (M3.3'te temiz
makinede eksik bir DLL torch'u yükletmemiş, arayüz yalnızca "RAG hazır değil" demişti)."""
from __future__ import annotations

import types
import unittest
from unittest import mock

from mufettis.rag import engine, onnx_backend
from mufettis.ui import admin, app


class TestRagUnavailableReason(unittest.TestCase):
    REASON = "ImportError: DLL load failed while importing torch_cpu"

    def test_engine_error_carries_import_reason(self):
        # torch yolu sınanır: kurulu bir ONNX modeli (M17.1) torch'u gereksiz kılar.
        with mock.patch.object(engine, "SentenceTransformer", None), \
                mock.patch.object(onnx_backend, "onnx_dir", lambda name: None), \
                mock.patch.object(engine, "SENTENCE_TRANSFORMERS_ERROR", self.REASON):
            with self.assertRaises(RuntimeError) as ctx:  # hata dizin oluşturmadan önce atılır; diske yazılmaz
                engine.LocalRAG(store="sqlite")
        self.assertIn(self.REASON, str(ctx.exception))

    def test_ui_records_logs_and_shows_reason(self):
        fake_st = types.SimpleNamespace(session_state={})
        with mock.patch.object(engine, "SentenceTransformer", None), \
                mock.patch.object(onnx_backend, "onnx_dir", lambda name: None), \
                mock.patch.object(engine, "SENTENCE_TRANSFORMERS_ERROR", self.REASON), \
                mock.patch.object(app, "st", fake_st), mock.patch.object(admin, "st", fake_st), \
                self.assertLogs("mufettis.ui.app", level="WARNING") as logs:
            self.assertIsNone(app._get_rag())
            message = admin.rag_not_ready()
        self.assertIn(self.REASON, fake_st.session_state["rag_error"])
        self.assertIn(self.REASON, message)
        self.assertTrue(message.startswith("RAG hazır değil. Sebep:"))
        self.assertIn(self.REASON, "\n".join(logs.output))

    def test_message_without_reason(self):
        with mock.patch.object(admin, "st", types.SimpleNamespace(session_state={})):
            self.assertEqual(admin.rag_not_ready(), "RAG hazır değil.")


if __name__ == "__main__":
    unittest.main()
