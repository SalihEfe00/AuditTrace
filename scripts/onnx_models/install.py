# -*- coding: utf-8 -*-
"""M17.1: export.py çıktısından ürünün kullandığı ONNX modellerini RERANKER_MODELS_ROOT altına kurar.

Kurulan varyant: *_fused.onnx (eager + birleşim, fp32; torch ile birebir). gather8 (kelime tablosu int8) tam korpusta
bir dev vakasının sonucunu değiştirdiği için kullanılmaz (M17.1).
Klasörler: <gömme modeli adı>-onnx ve <sıralayıcı adı>-onnx; her birinde model.onnx, tokenizer.json, manifest.json.
Kullanım: python scripts/onnx_models/install.py <export.py çıktı klasörü>
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from mufettis.config import EMBEDDING_MODEL, RERANKER_MODELS_ROOT  # noqa: E402
from mufettis.rag.onnx_backend import ONNX_SUFFIX  # noqa: E402
from mufettis.rag.reranker import MAX_LENGTH, MODELS, default_model_dir  # noqa: E402

BUILD = Path(sys.argv[1])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def hf_snapshot(repo: str) -> Path:
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(repo, local_files_only=True))


def install(kind: str, source_name: str, source_dir: Path, max_length: int) -> None:
    target = Path(RERANKER_MODELS_ROOT) / (source_name.split("/")[-1] + ONNX_SUFFIX)
    target.mkdir(parents=True, exist_ok=True)
    onnx_src = BUILD / f"{kind}_fused.onnx"
    shutil.copy2(onnx_src, target / "model.onnx")
    shutil.copy2(source_dir / "tokenizer.json", target / "tokenizer.json")
    manifest = {
        "kind": kind, "source": source_name, "variant": "eager+fused fp32 (M17.1)",
        "source_weights_sha256": sha256(source_dir / "model.safetensors"),
        "onnx_sha256": sha256(target / "model.onnx"), "tokenizer_sha256": sha256(target / "tokenizer.json"),
        "max_length": max_length, "pooling": "mean" if kind == "embed" else "none",
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{target}: {round((target / 'model.onnx').stat().st_size / 2**20)} MB")


emb_dir = hf_snapshot(EMBEDDING_MODEL)
emb_max = json.loads((emb_dir / "sentence_bert_config.json").read_text(encoding="utf-8"))["max_seq_length"]
install("embed", EMBEDDING_MODEL, emb_dir, int(emb_max))
rdir = default_model_dir()
expected = MODELS[rdir.name].weights_sha256 if rdir.name in MODELS else None
if expected and sha256(rdir / "model.safetensors") != expected:
    raise SystemExit(f"sıralayıcı ağırlık özeti beklenenden farklı: {rdir}")
install("rerank", rdir.name, rdir, MAX_LENGTH)
