# -*- coding: utf-8 -*-
"""ONNX arka ucu (M17.1): gömme modeli ve yeniden sıralayıcı torch / transformers / sentence-transformers'sız,
`onnxruntime` + `tokenizers` ile çalışır. Pakette torch yoktur (M3.4 kararı, docs/architecture/packaging.md).

Modeller `scripts/onnx_models/install.py` ile RERANKER_MODELS_ROOT altına kurulur; klasör adı kaynak modelin adı +
ONNX_SUFFIX'tir. Klasörde model.onnx, tokenizer.json ve manifest.json bulunur. manifest kaynak ağırlıkların ve ONNX
dosyasının özetini, en uzun token sayısını ve havuzlamayı taşır. Klasör varsa uygulama bu arka ucu kullanır (torch
kurulu olsa da): geliştirme ortamı ile paket aynı sonucu verir. Varyant "eager + birleşim, fp32"dir: torch ile birebir
(puan farkı ~1e-5), eşikler ortak. M3.5'in "gather8" varyantı (kelime tablosu int8, model başına 275 MB daha küçük) tam
korpusta bir dev vakasında iki adayın sırasını çevirdi (doğru → yanlış dayanak, M17.1); kullanılmaz.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

ONNX_SUFFIX = "-onnx"
MAX_THREADS = 2


def onnx_dir(model_name: str) -> Optional[Path]:
    """Kurulu ONNX model klasörü (yoksa None). model_name HF adı ("org/ad") ya da klasör adı olabilir.
    MUFETTIS_MODEL_BACKEND=torch ONNX'i yok sayar (torch ile karşılaştırma ölçümleri için)."""
    import os

    from mufettis.config import RERANKER_MODELS_ROOT

    if os.environ.get("MUFETTIS_MODEL_BACKEND", "").strip().lower() == "torch":
        return None
    path = Path(RERANKER_MODELS_ROOT) / (model_name.split("/")[-1] + ONNX_SUFFIX)
    return path if (path / "manifest.json").is_file() and (path / "model.onnx").is_file() else None


def _session(model_dir: Path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = MAX_THREADS
    opts.inter_op_num_threads = 1
    # Bellek havuzu en yüksek kullanımı geri vermez (M3.5: kapalıyken 0,94 GB, açıkken 2,07 GB).
    opts.enable_cpu_mem_arena = False
    return ort.InferenceSession(str(model_dir / "model.onnx"), sess_options=opts, providers=["CPUExecutionProvider"])


def _tokenizer(model_dir: Path, max_length: int, strategy: str = "longest_first"):
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    tok.enable_truncation(max_length=max_length, strategy=strategy)
    pad_id = tok.token_to_id("<pad>")
    tok.enable_padding(pad_id=1 if pad_id is None else pad_id, pad_token="<pad>")
    return tok


class _Model:
    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)
        self.manifest: Dict = json.loads((self.model_dir / "manifest.json").read_text(encoding="utf-8"))
        self.max_length = int(self.manifest["max_length"])
        self.device = "cpu"
        self._sess = None
        self._tok = None
        self._names: List[str] = []
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._sess is None:
                self._tok = _tokenizer(self.model_dir, self.max_length)
                self._sess = _session(self.model_dir)
                self._names = [i.name for i in self._sess.get_inputs()]
        return self._sess, self._tok

    def _feed(self, encodings) -> Dict[str, np.ndarray]:
        cols = {"input_ids": [e.ids for e in encodings], "attention_mask": [e.attention_mask for e in encodings],
                "token_type_ids": [e.type_ids for e in encodings]}
        return {n: np.asarray(cols[n], dtype=np.int64) for n in self._names}


class OnnxEmbedder(_Model):
    """SentenceTransformer.encode yerine: ortalama havuzlama + isteğe bağlı L2 normalleştirme."""

    def encode(self, texts: Sequence[str], batch_size: int = 32, normalize_embeddings: bool = True,
               show_progress_bar: bool = False) -> np.ndarray:
        sess, tok = self._load()
        out = []
        for start in range(0, len(texts), max(1, int(batch_size))):
            enc = tok.encode_batch([t or "" for t in texts[start:start + batch_size]])
            feed = self._feed(enc)
            hidden = sess.run(None, feed)[0]
            mask = feed["attention_mask"][..., None].astype(np.float32)
            vec = (hidden * mask).sum(1) / np.clip(mask.sum(1), 1e-9, None)
            if normalize_embeddings:
                vec = vec / np.clip(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12, None)
            out.append(vec.astype(np.float32))
        return np.concatenate(out) if out else np.zeros((0, 0), dtype=np.float32)


class OnnxCrossEncoder(_Model):
    """CrossEncoder.predict yerine: (sorgu, metin) çiftleri için ham logit (etkinleştirme yok, torch ile aynı ölçek)."""

    def predict(self, pairs: Sequence[tuple], batch_size: int = 16, show_progress_bar: bool = False) -> np.ndarray:
        sess, tok = self._load()
        out = []
        for start in range(0, len(pairs), max(1, int(batch_size))):
            enc = tok.encode_batch([(a or "", b or "") for a, b in pairs[start:start + batch_size]])
            out.append(sess.run(None, self._feed(enc))[0][:, 0].astype(np.float32))
        return np.concatenate(out) if out else np.zeros((0,), dtype=np.float32)


__all__ = ["ONNX_SUFFIX", "OnnxEmbedder", "OnnxCrossEncoder", "onnx_dir"]
