# -*- coding: utf-8 -*-
"""Yerel dil modeli sağlık denetimi modelin kurulu olduğunu da doğrular (ağ taklit edilir)."""
from __future__ import annotations

import unittest
from unittest import mock

from mufettis.llm import client


def _tags(*names):
    response = mock.Mock(status_code=200)
    response.json.return_value = {"models": [{"name": n} for n in names]}
    return response


class TestOllamaHealth(unittest.TestCase):
    def test_missing_model_is_not_healthy(self):
        with mock.patch.object(client.requests, "get", return_value=_tags("qwen2.5:7b")):
            health = client.ollama_health("llama3.2:3b")
        self.assertFalse(health["ok"])
        self.assertIn("kurulu değil", health["error"])
        self.assertIn("qwen2.5:7b", health["error"])

    def test_installed_model_and_latest_tag(self):
        with mock.patch.object(client.requests, "get", return_value=_tags("qwen2.5:7b", "llama3.2:latest")):
            self.assertTrue(client.ollama_health("qwen2.5:7b")["ok"])
            self.assertTrue(client.ollama_health("llama3.2")["ok"])
            self.assertTrue(client.ollama_health(None)["ok"])

    def test_verification_stays_off_when_no_model_installed(self):
        from mufettis.analysis.analysis_presenter import AnalysisPresenter

        presenter = AnalysisPresenter(audit_sink=lambda *a, **k: None)
        with mock.patch.object(client.requests, "get", return_value=_tags()):
            problem = presenter.configure_llm_verification(True)
        self.assertIsNone(presenter.engine.claim_verifier)
        self.assertIn("kurulu değil", problem)

    def test_verification_uses_other_installed_model(self):
        """M15.2: varsayılan model kurulu değilse kurulu olan metin modeli kullanılır."""
        from mufettis.analysis.analysis_presenter import AnalysisPresenter

        presenter = AnalysisPresenter(audit_sink=lambda *a, **k: None)
        with mock.patch.object(client, "DEFAULT_LLM", "llama3.2:3b"),                 mock.patch.dict(client.os.environ, {"MUFETTIS_LLM_MODEL": ""}),                 mock.patch.object(client.requests, "get", return_value=_tags("qwen2.5:7b")):
            problem = presenter.configure_llm_verification(True)
        self.assertIsNone(problem)
        self.assertIsNotNone(presenter.engine.claim_verifier)


class TestActiveModel(unittest.TestCase):
    def setUp(self):
        client.set_model(None)
        self.addCleanup(client.set_model, None)
        env = mock.patch.dict(client.os.environ, {"MUFETTIS_LLM_MODEL": ""})
        default = mock.patch.object(client, "DEFAULT_LLM", "llama3.2:3b")
        env.start(), default.start()
        self.addCleanup(env.stop)
        self.addCleanup(default.stop)

    def test_default_kept_when_installed(self):
        self.assertEqual(client.active_model(["qwen2.5:7b", "llama3.2:3b"]), "llama3.2:3b")

    def test_falls_back_to_first_text_model(self):
        self.assertEqual(client.active_model(["nomic-embed-text:latest", "qwen2.5:7b"]), "qwen2.5:7b")

    def test_nothing_installed_returns_default(self):
        self.assertEqual(client.active_model([]), "llama3.2:3b")
        self.assertEqual(client.active_model(["bge-m3:latest"]), "llama3.2:3b")

    def test_user_choice_wins(self):
        client.set_model("mistral:7b")
        self.assertEqual(client.active_model(["qwen2.5:7b", "mistral:7b", "llama3.2:3b"]), "mistral:7b")

    def test_choice_not_installed_is_kept_and_reported(self):
        client.set_model("mistral:7b")
        self.assertEqual(client.active_model(["qwen2.5:7b"]), "mistral:7b")
        with mock.patch.object(client.requests, "get", return_value=_tags("qwen2.5:7b")):
            health = client.ollama_health()
        self.assertFalse(health["ok"])
        self.assertIn("mistral:7b", health["error"])

    def test_explicit_env_model_not_replaced(self):
        with mock.patch.dict(client.os.environ, {"MUFETTIS_LLM_MODEL": "llama3.2:3b"}):
            self.assertEqual(client.active_model(["qwen2.5:7b"]), "llama3.2:3b")

    def test_health_reports_model_in_use(self):
        with mock.patch.object(client.requests, "get", return_value=_tags("qwen2.5:7b")):
            health = client.ollama_health()
        self.assertTrue(health["ok"])
        self.assertEqual(health["model"], "qwen2.5:7b")

    def test_unreachable_server(self):
        with mock.patch.object(client.requests, "get", side_effect=OSError("kapalı")):
            self.assertIsNone(client.installed_models())
            self.assertFalse(client.ollama_health()["ok"])


if __name__ == "__main__":
    unittest.main()
