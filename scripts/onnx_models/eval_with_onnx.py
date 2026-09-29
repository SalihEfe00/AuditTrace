# -*- coding: utf-8 -*-
# M3.5: eval_accuracy'yi ONNX modelleriyle çalıştır (ürün koduna dokunmadan, bu süreçte yama ile).
# Ortam: M35_EMBED=<embed onnx|boş>, M35_RERANK=<rerank onnx|boş>. Argümanlar eval_accuracy'ye aynen geçer.
import os
import runpy
import sys
import threading

import numpy as np

sys.path.insert(0, ".")
import onnxruntime as ort  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

from mufettis.config import EMBEDDING_MODEL  # noqa: E402
from mufettis.rag import engine, reranker  # noqa: E402

OPTS = ort.SessionOptions(); OPTS.intra_op_num_threads = 2; OPTS.inter_op_num_threads = 1


def session(path):
    return ort.InferenceSession(path, OPTS, providers=["CPUExecutionProvider"])


class OnnxEmbedder:
    device = "cpu"

    def __init__(self, path):
        self.s = session(path)
        self.tok = AutoTokenizer.from_pretrained(EMBEDDING_MODEL, local_files_only=True)
        self.names = [i.name for i in self.s.get_inputs()]

    def encode(self, texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False, **_):
        out = []
        for i in range(0, len(texts), batch_size):
            enc = self.tok(list(texts[i:i + batch_size]), padding=True, truncation=True, max_length=128, return_tensors="np")
            h = self.s.run(None, {n: enc[n].astype(np.int64) for n in self.names})[0]
            m = enc["attention_mask"][..., None].astype(np.float32)
            v = (h * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            if normalize_embeddings:
                v = v / np.linalg.norm(v, axis=1, keepdims=True)
            out.append(v.astype(np.float32))
        return np.concatenate(out) if out else np.zeros((0, 384), np.float32)


class OnnxCrossEncoder:
    def __init__(self, path, model_dir):
        self.s = session(path)
        self.tok = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
        self.names = [i.name for i in self.s.get_inputs()]

    def predict(self, pairs, batch_size=16, show_progress_bar=False, **_):
        out = []
        for i in range(0, len(pairs), batch_size):
            chunk = pairs[i:i + batch_size]
            enc = self.tok([a for a, _ in chunk], [b for _, b in chunk], padding=True, truncation="longest_first",
                           max_length=reranker.MAX_LENGTH, return_tensors="np")
            out.append(self.s.run(None, {n: enc[n].astype(np.int64) for n in self.names})[0][:, 0])
        return np.concatenate(out) if out else np.zeros(0, np.float32)


if os.environ.get("M35_EMBED"):
    _emb = {}
    _lock = threading.Lock()

    def _load_model(self):
        with _lock:
            if "m" not in _emb:
                _emb["m"] = OnnxEmbedder(os.environ["M35_EMBED"])
        return _emb["m"]
    engine.LocalRAG._load_model = _load_model
    print("gömme: ONNX", os.environ["M35_EMBED"], flush=True)

if os.environ.get("M35_RERANK"):
    def _load(self):
        with self._lock:
            if self._model is None:
                self._model = OnnxCrossEncoder(os.environ["M35_RERANK"], self.model_dir)
            return self._model
    reranker.CrossEncoderReranker._load = _load
    print("sıralayıcı: ONNX", os.environ["M35_RERANK"], flush=True)

sys.argv = ["scripts/eval_accuracy.py"] + sys.argv[1:]
runpy.run_path("scripts/eval_accuracy.py", run_name="__main__")
