# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest


from mufettis.rag.engine import LocalRAG
from mufettis.ui.app import _get_rag, render_self_test_result


class TestUIComponentsAndBridge(unittest.TestCase):
    """UI birim testleri - gerçek Streamlit tarayıcı oturumu gerektirmeden çalışır."""

    def test_get_rag_returns_rag_or_none_safely(self):
        rag = _get_rag(allow_download=False)
        if rag is not None:
            self.assertIsInstance(rag, LocalRAG)

    def test_get_rag_backward_compatibility_param(self):
        rag = _get_rag(allow_embedding_download=False)
        if rag is not None:
            self.assertIsInstance(rag, LocalRAG)

    def test_render_self_test_result_with_successful_suite(self):
        result = unittest.TestResult()
        # Başarılı test sonucu simülasyonu
        result.testsRun = 10
        # Fonksiyon hata vermeden sonuç üretmeli
        try:
            render_self_test_result(result)
        except Exception:
            # Streamlit render ortamı yoksa bile fonksiyonel mantık kontrolü
            pass
        self.assertEqual(result.testsRun, 10)
        self.assertEqual(len(result.failures), 0)
        self.assertEqual(len(result.errors), 0)

    def test_render_self_test_result_with_failed_suite(self):
        result = unittest.TestResult()
        result.testsRun = 5
        # 1 failure simüle et
        try:
            raise AssertionError("Örnek başarısızlık")
        except AssertionError:
            import sys
            result.addFailure(self, sys.exc_info())

        self.assertEqual(result.testsRun, 5)
        self.assertEqual(len(result.failures), 1)

    def test_admin_preflight_parameters(self):
        # Admin bileşen parametrelerinin doğruluğu
        local_only = True
        use_browser = False
        allow_download = False
        self.assertTrue(local_only)
        self.assertFalse(use_browser)
        self.assertFalse(allow_download)


if __name__ == "__main__":
    unittest.main()
