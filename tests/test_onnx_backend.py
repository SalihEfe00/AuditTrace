# -*- coding: utf-8 -*-
"""M17.1: ONNX arka ucu. Kurulu ONNX modeli varsa gömme ve sıralayıcı torch'suz çalışır; MUFETTIS_MODEL_BACKEND=torch
ONNX'i yok sayar. Gerçek modeller kuruluysa torch ile birebir aynı sonucu verdiği ölçülür (eşikler ortaktır)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mufettis.rag import engine, onnx_backend, reranker


def fake_model_dir(root: Path, name: str) -> Path:
    path = root / (name.split("/")[-1] + onnx_backend.ONNX_SUFFIX)
    path.mkdir(parents=True)
    (path / "model.onnx").write_bytes(b"x")
    (path / "manifest.json").write_text(json.dumps({"max_length": 8}), encoding="utf-8")
    return path


class TestOnnxDir(unittest.TestCase):
    def test_found_only_with_manifest_and_model(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("mufettis.config.RERANKER_MODELS_ROOT", Path(tmp)), \
                mock.patch.dict(os.environ, {"MUFETTIS_MODEL_BACKEND": ""}):
            self.assertIsNone(onnx_backend.onnx_dir("org/model-a"))
            path = fake_model_dir(Path(tmp), "org/model-a")
            self.assertEqual(onnx_backend.onnx_dir("org/model-a"), path)
            self.assertEqual(onnx_backend.onnx_dir("model-a"), path)
            (path / "manifest.json").unlink()
            self.assertIsNone(onnx_backend.onnx_dir("model-a"))

    def test_torch_override(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("mufettis.config.RERANKER_MODELS_ROOT", Path(tmp)), \
                mock.patch.dict(os.environ, {"MUFETTIS_MODEL_BACKEND": "torch"}):
            fake_model_dir(Path(tmp), "model-a")
            self.assertIsNone(onnx_backend.onnx_dir("model-a"))


class TestEngineUsesOnnx(unittest.TestCase):
    def test_no_torch_needed_when_onnx_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = fake_model_dir(Path(tmp), "emb")
            with mock.patch.object(onnx_backend, "onnx_dir", lambda name: model_dir), \
                    mock.patch.object(engine, "SentenceTransformer", None), \
                    mock.patch.object(onnx_backend, "OnnxEmbedder") as embedder:
                self.assertIsNone(engine.embedding_backend_error())
                rag = engine.LocalRAG(db_path=Path(tmp) / "db", store="sqlite")
                self.assertIs(rag._load_model(), embedder.return_value)
                embedder.assert_called_once_with(model_dir)

    def test_reason_when_neither_backend(self):
        with mock.patch.object(onnx_backend, "onnx_dir", lambda name: None), \
                mock.patch.object(engine, "SentenceTransformer", None), \
                mock.patch.object(engine, "SENTENCE_TRANSFORMERS_ERROR", "ImportError: torch"):
            self.assertIn("ImportError: torch", engine.embedding_backend_error())


class TestRerankerUsesOnnx(unittest.TestCase):
    def test_backend_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = fake_model_dir(Path(tmp), reranker.FINETUNED_MODEL.name)
            with mock.patch.object(onnx_backend, "onnx_dir", lambda name: model_dir):
                self.assertEqual(reranker.CrossEncoderReranker(Path(tmp) / "m").backend, "onnx")
            with mock.patch.object(onnx_backend, "onnx_dir", lambda name: None):
                self.assertEqual(reranker.CrossEncoderReranker(Path(tmp) / "m").backend, "torch")

    def test_onnx_only_install_loads_with_shared_threshold(self):
        """Pakette yalnızca ONNX kopyası vardır (config.json yok, sentence-transformers yok); eşik ortaktır."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = fake_model_dir(root, reranker.FINETUNED_MODEL.name)
            (root / reranker.CALIBRATION_FILE).write_text(json.dumps({
                reranker.FINETUNED_MODEL.name: {"min_score": -2.95}}), encoding="utf-8")
            with mock.patch.object(onnx_backend, "onnx_dir", lambda name: model_dir), \
                    mock.patch("mufettis.config.DATA_DIR", root), mock.patch.object(reranker, "_DEFAULT", None):
                rr = reranker.load_reranker(root / reranker.FINETUNED_MODEL.name)
            self.assertEqual((rr.backend, rr.min_score), ("onnx", -2.95))


@unittest.skipUnless(onnx_backend.onnx_dir(engine.EMBEDDING_MODEL) and reranker.default_model_dir()
                     and onnx_backend.onnx_dir(reranker.default_model_dir().name), "ONNX modelleri kurulu değil")
class TestParityWithTorch(unittest.TestCase):
    TEXTS = ["Kefalet sözleşmesinde eşin rızası",
             "Madde 584 - Eşlerden biri, ancak diğerinin yazılı rızasıyla kefil olabilir.",
             "Kefil, borçlunun borcunu ödememesi hâlinde sorumlu olur. " * 60, ""]

    def test_embeddings_and_scores_close_to_torch(self):
        try:
            from sentence_transformers import CrossEncoder, SentenceTransformer
        except Exception:
            self.skipTest("torch yok")
        import numpy as np

        got = onnx_backend.OnnxEmbedder(onnx_backend.onnx_dir(engine.EMBEDDING_MODEL)).encode(self.TEXTS)
        ref = SentenceTransformer(engine.EMBEDDING_MODEL, local_files_only=True).encode(self.TEXTS,
                                                                                       normalize_embeddings=True)
        self.assertGreater(float((got * ref).sum(1).min()), 0.9999)
        rdir = reranker.default_model_dir()
        pairs = [(self.TEXTS[0], t) for t in self.TEXTS[1:]]
        g = onnx_backend.OnnxCrossEncoder(onnx_backend.onnx_dir(rdir.name)).predict(pairs)
        r = np.asarray(CrossEncoder(str(rdir), device="cpu", max_length=reranker.MAX_LENGTH).predict(pairs))
        self.assertLess(float(np.abs(g - r).max()), 1e-3)  # fp32 + birleşim: birebir (eşikler ortak)


if __name__ == "__main__":
    unittest.main()
