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

    def test_installed_model_matches_pinned_digest(self):
        from mufettis.config import RERANKER_MODEL_DIR

        if not (Path(RERANKER_MODEL_DIR) / "model.safetensors").is_file():
            self.skipTest("yeniden sıralayıcı modeli bu makinede kurulu değil")
        self.assertEqual(reranker.verify_model_dir(Path(RERANKER_MODEL_DIR)), [])


if __name__ == "__main__":
    unittest.main()
