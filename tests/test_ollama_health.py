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

    def test_verification_stays_off_when_model_missing(self):
        from mufettis.analysis.analysis_presenter import AnalysisPresenter

        presenter = AnalysisPresenter(audit_sink=lambda *a, **k: None)
        with mock.patch.object(client.requests, "get", return_value=_tags("baska:1b")):
            problem = presenter.configure_llm_verification(True)
        self.assertIsNone(presenter.engine.claim_verifier)
        self.assertIn("kurulu değil", problem)


if __name__ == "__main__":
    unittest.main()
