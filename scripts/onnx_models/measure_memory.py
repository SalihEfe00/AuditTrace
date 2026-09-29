# -*- coding: utf-8 -*-
# M3.5: torch'suz süreçte ONNX modellerin açılış süresi ve bellek payı.
# Kullanım: measure_memory.py <embed.onnx> <rerank.onnx> <gömme tokenizer.json> <sıralayıcı tokenizer.json>
# Ortam: ARENA=0 (bellek havuzu kapalı, önerilen), OPT=all|basic|none.
import ctypes
import ctypes.wintypes as wt
import sys
import time

k = ctypes.WinDLL("kernel32"); k.GetCurrentProcess.restype = wt.HANDLE


class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("a", wt.DWORD)] + [(n, ctypes.c_size_t) for n in
                                                      ("peak", "ws", "b", "c", "d", "e", "f", "g", "private")]


def mem():
    c = PMC(); c.cb = ctypes.sizeof(PMC)
    k.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PMC), wt.DWORD]
    k.K32GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return f"ws {c.ws/2**20:.0f} MB, özel {c.private/2**20:.0f} MB, en yüksek ws {c.peak/2**20:.0f} MB"


sys.stdout.reconfigure(encoding="utf-8")
t = time.perf_counter()
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
from tokenizers import Tokenizer  # noqa: E402
print(f"onnxruntime + tokenizers içe aktarma {time.perf_counter()-t:.2f} sn:", mem())
import os
o = ort.SessionOptions(); o.intra_op_num_threads = 2; o.inter_op_num_threads = 1
o.enable_cpu_mem_arena = os.environ.get("ARENA", "1") == "1"
o.graph_optimization_level = {"all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL, "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC, "none": ort.GraphOptimizationLevel.ORT_DISABLE_ALL}[os.environ.get("OPT", "all")]
print("arena", o.enable_cpu_mem_arena, "opt", os.environ.get("OPT", "all"))
emb_tok = Tokenizer.from_file(sys.argv[3]); emb_tok.enable_truncation(128); emb_tok.enable_padding()
rr_tok = Tokenizer.from_file(sys.argv[4]); rr_tok.enable_truncation(384); rr_tok.enable_padding()


def feed(sess, encs):
    names = [i.name for i in sess.get_inputs()]
    arr = {"input_ids": np.array([e.ids for e in encs], np.int64),
           "attention_mask": np.array([e.attention_mask for e in encs], np.int64),
           "token_type_ids": np.array([e.type_ids for e in encs], np.int64)}
    return {n: arr[n] for n in names}


t = time.perf_counter(); es = ort.InferenceSession(sys.argv[1], o, providers=["CPUExecutionProvider"])
es.run(None, feed(es, emb_tok.encode_batch(["deneme cümlesi"] * 32)))
print(f"+ gömme ({time.perf_counter()-t:.2f} sn):", mem())
t = time.perf_counter(); rs = ort.InferenceSession(sys.argv[2], o, providers=["CPUExecutionProvider"])
passage = "Madde 13 - Kredi kartı çıkaran kuruluşlar bildirimleri yazılı yapar. " * 20
rs.run(None, feed(rs, rr_tok.encode_batch([("iddia metni burada", passage)] * 16)))
print(f"+ sıralayıcı ({time.perf_counter()-t:.2f} sn):", mem())
t = time.perf_counter()
for _ in range(4):
    rs.run(None, feed(rs, rr_tok.encode_batch([("iddia metni burada", passage)] * 16)))
print(f"64 çift puanlama {time.perf_counter()-t:.2f} sn:", mem())
