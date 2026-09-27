# -*- coding: utf-8 -*-
"""Yeniden sıralayıcı modelinin kurulumu ve doğrulaması (scripts/install_reranker_model.py).

Ağ kullanılmaz: kurulum --from yolu sahte dosyalarla sınanır; özet sabiti test içinde değiştirilir.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.install_reranker_model as installer
from mufettis.rag import reranker


def _fake_model(directory: Path, weights: bytes = b"agirliklar") -> str:
    directory.mkdir(parents=True, exist_ok=True)
    for name in reranker.MODEL_FILES:
        (directory / name).write_bytes(weights if name == "model.safetensors" else b"{}")
    return hashlib.sha256(weights).hexdigest()


class TestVerifyAndInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_verify_reports_missing_and_tampered_files(self):
        digest = _fake_model(self.tmp / "m")
        with mock.patch.dict(reranker.MODEL_WEIGHTS_SHA256, {"model.safetensors": digest}):
            self.assertEqual(reranker.verify_model_dir(self.tmp / "m"), [])
            (self.tmp / "m" / "model.safetensors").write_bytes(b"degistirilmis")
            self.assertEqual(reranker.verify_model_dir(self.tmp / "m"), ["özet uyuşmuyor: model.safetensors"])
            (self.tmp / "m" / "tokenizer.json").unlink()
            self.assertIn("eksik dosya: tokenizer.json", reranker.verify_model_dir(self.tmp / "m"))

    def test_install_from_folder_verifies_before_replacing(self):
        digest = _fake_model(self.tmp / "kaynak")
        target = self.tmp / "hedef" / "model"
        with mock.patch.dict(reranker.MODEL_WEIGHTS_SHA256, {"model.safetensors": digest}):
            self.assertEqual(installer.install(target, self.tmp / "kaynak"), 0)
            self.assertEqual(reranker.verify_model_dir(target), [])
            # Bozuk kaynak: kurulu model yerinde kalır, geçici klasör temizlenir.
            (self.tmp / "kaynak" / "model.safetensors").write_bytes(b"bozuk")
            self.assertEqual(installer.install(target, self.tmp / "kaynak"), 1)
            self.assertEqual(reranker.verify_model_dir(target), [])
            self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["model"])

    def test_installed_models_match_pinned_digests(self):
        from mufettis.config import RERANKER_MODELS_ROOT

        installed = [m for m in reranker.MODELS.values()
                     if (Path(RERANKER_MODELS_ROOT) / m.name / "model.safetensors").is_file()]
        if not installed:
            self.skipTest("yeniden sıralayıcı modeli bu makinede kurulu değil")
        for model in installed:
            self.assertEqual(reranker.verify_model_dir(Path(RERANKER_MODELS_ROOT) / model.name), [], model.name)

    def test_default_model_prefers_finetuned_and_falls_back(self):
        root = self.tmp / "modeller"
        with mock.patch("mufettis.config.RERANKER_MODELS_ROOT", root), \
                mock.patch("mufettis.config.RERANKER_MODEL_DIR", None):
            self.assertIsNone(reranker.default_model_dir())
            _fake_model(root / reranker.BASE_MODEL.name)
            self.assertEqual(reranker.default_model_dir(), root / reranker.BASE_MODEL.name)
            _fake_model(root / reranker.FINETUNED_MODEL.name)
            self.assertEqual(reranker.default_model_dir(), root / reranker.FINETUNED_MODEL.name)
        with mock.patch("mufettis.config.RERANKER_MODEL_DIR", self.tmp / "zorla"):
            self.assertEqual(reranker.default_model_dir(), self.tmp / "zorla")

    def test_finetuned_model_verified_with_its_own_file_list(self):
        model = reranker.FINETUNED_MODEL
        source = self.tmp / "ft"
        source.mkdir()
        for name in model.files:
            (source / name).write_bytes(b"agirliklar" if name == "model.safetensors" else b"{}")
        spec = reranker.RerankerModel(model.name, model.min_score, model.confident_score, model.files,
                                      hashlib.sha256(b"agirliklar").hexdigest())
        self.assertEqual(installer.install(self.tmp / "hedef" / model.name, source, spec), 0)
        self.assertEqual(reranker.verify_model_dir(self.tmp / "hedef" / model.name, spec), [])

if __name__ == "__main__":
    unittest.main()
