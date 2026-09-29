# -*- coding: utf-8 -*-
"""M3.5: gömme modeli ve sıralayıcıyı ONNX'e çevirir, torch ile uyumu ölçer (docs/architecture/packaging.md).

Çıktılar: <klasör>/{embed,rerank}_fp32.onnx (torch ile birebir), *_int8.onnx (tam dinamik int8 — sıralayıcıyı bozar),
*_fused.onnx (ORT birleşimi) ve *_fusedg8.onnx (birleşim + yalnızca kelime tablosu int8 — tabanı koruyan varyant).
Eager dikkat uygulaması, ORT optimizasyon aracının katmanları birleştirebilmesi içindir.
Kullanım: python scripts/onnx_models/export.py <çıktı klasörü>
Ölçüm: eval_with_onnx.py (benchmark, modeller süreç içinde değiştirilir), measure_memory.py (torch'suz bellek / hız).
"""
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
torch.set_num_threads(2)
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)

from mufettis.config import EMBEDDING_MODEL  # noqa: E402
from mufettis.rag.reranker import MAX_LENGTH, default_model_dir  # noqa: E402
from sentence_transformers import CrossEncoder, SentenceTransformer  # noqa: E402
from onnxruntime.quantization import QuantType, quantize_dynamic  # noqa: E402
import onnxruntime as ort  # noqa: E402

SENTS = [
    "Bankam vadesiz hesabımdan habersiz hesap işletim ücreti kesti.",
    "Kredi kartı borcum yapılandırılmadan yasal takibe verildi, faiz oranı sözleşmeden yüksek.",
    "Şube müdürü müşteriye ait kimlik fotokopisini imzasız teslim almış.",
    "Madde 13 - Kredi kartı çıkaran kuruluşlar, kart hamillerine yapılacak bildirimleri yazılı olarak yapar.",
    "Asansör arızası nedeniyle şubeye girişte zorluk yaşandı.",
]


def export(model, inputs, names, path, out_name):
    torch.onnx.export(model, tuple(inputs[n] for n in names), str(path), input_names=list(names),
                      output_names=[out_name], opset_version=17, dynamo=False,
                      dynamic_axes={**{n: {0: "b", 1: "t"} for n in names}, out_name: {0: "b"}})


# --- gömme modeli
st = SentenceTransformer(EMBEDDING_MODEL, device="cpu", local_files_only=True, model_kwargs={"attn_implementation": "eager"})
tok = st.tokenizer
bert = st[0].auto_model.eval()
enc = tok(SENTS, padding=True, truncation=True, max_length=st.max_seq_length, return_tensors="pt")
names = [n for n in ("input_ids", "attention_mask", "token_type_ids") if n in enc]


class Last(torch.nn.Module):
    def __init__(self, m):
        super().__init__(); self.m = m

    def forward(self, *args):
        return self.m(**dict(zip(names, args))).last_hidden_state


emb_fp32 = OUT / "embed_fp32.onnx"
export(Last(bert), enc, names, emb_fp32, "last_hidden_state")
emb_int8 = OUT / "embed_int8.onnx"
quantize_dynamic(str(emb_fp32), str(emb_int8), weight_type=QuantType.QInt8)
ref = st.encode(SENTS, normalize_embeddings=True)


def onnx_embed(path):
    s = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    h = s.run(None, {n: enc[n].numpy() for n in names})[0]
    m = enc["attention_mask"].numpy()[..., None].astype(np.float32)
    v = (h * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


for p in (emb_fp32, emb_int8):
    v = onnx_embed(p)
    cos = (v * ref).sum(1)
    print(f"gömme {p.name}: {p.stat().st_size/2**20:.0f} MB, torch ile kosinüs en düşük {cos.min():.5f}")

# --- sıralayıcı
rdir = default_model_dir()
ce = CrossEncoder(str(rdir), device="cpu", max_length=MAX_LENGTH, model_kwargs={"attn_implementation": "eager"})
pairs = [(SENTS[0], SENTS[3]), (SENTS[1], SENTS[3]), (SENTS[2], SENTS[3]), (SENTS[4], SENTS[3]), (SENTS[1], SENTS[0])]
renc = ce.tokenizer([a for a, _ in pairs], [b for _, b in pairs], padding=True, truncation="longest_first",
                    max_length=MAX_LENGTH, return_tensors="pt")
rnames = [n for n in ("input_ids", "attention_mask", "token_type_ids") if n in renc]


class Logit(torch.nn.Module):
    def __init__(self, m):
        super().__init__(); self.m = m

    def forward(self, *args):
        return self.m(**dict(zip(rnames, args))).logits


ce.model.eval()
rr_fp32 = OUT / "rerank_fp32.onnx"
export(Logit(ce.model), renc, rnames, rr_fp32, "logits")
rr_int8 = OUT / "rerank_int8.onnx"
quantize_dynamic(str(rr_fp32), str(rr_int8), weight_type=QuantType.QInt8)
rref = np.asarray(ce.predict(pairs, show_progress_bar=False), dtype=np.float32)
for p in (rr_fp32, rr_int8):
    s = ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
    got = s.run(None, {n: renc[n].numpy() for n in rnames})[0][:, 0]
    print(f"sıralayıcı {p.name}: {p.stat().st_size/2**20:.0f} MB, torch {np.round(rref, 3)} onnx {np.round(got, 3)} "
          f"en büyük fark {np.abs(got - rref).max():.4f}")
print("tokenizer dosyaları:", st.tokenizer.name_or_path, rdir)

# --- ORT birleşimi + yalnızca kelime tablosu (Gather) int8
from onnxruntime.transformers import optimizer  # noqa: E402

for m in ("embed", "rerank"):
    fused = optimizer.optimize_model(str(OUT / f"{m}_fp32.onnx"), model_type="bert", num_heads=12, hidden_size=384,
                                     opt_level=1)
    print(m, "birleşimler:", {k: v for k, v in fused.get_fused_operator_statistics().items() if v})
    fused.save_model_to_file(str(OUT / f"{m}_fused.onnx"))
    quantize_dynamic(str(OUT / f"{m}_fused.onnx"), str(OUT / f"{m}_fusedg8.onnx"), weight_type=QuantType.QInt8,
                     op_types_to_quantize=["Gather"])
    print(m, "fusedg8:", round((OUT / f"{m}_fusedg8.onnx").stat().st_size / 2**20), "MB")
