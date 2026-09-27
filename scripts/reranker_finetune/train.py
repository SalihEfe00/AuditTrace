# -*- coding: utf-8 -*-
"""Çapraz kodlayıcı ince ayarı: grup başına (1 doğru + N negatif) softmax çapraz entropi (listwise).
CPU, OMP_NUM_THREADS iş parçacığı (varsayılan 2). Tohumlar sabit.

Kullanım: python scripts/reranker_finetune/train.py <train.jsonl> <kaynak model klasörü> <çıktı klasörü> [epoch] [lr]
mufettis-reranker-ft1: kaynak = temel model (reranker.BASE_MODEL), 2 epoch, lr 2e-5, build_groups --drop-reviewed.
Bu makinede (i5, 2 iş parçacığı) 250 grup ~40 dk."""
import json
import os
import random
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "2")))
torch.manual_seed(0)
random.seed(0)
data_path, src, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
epochs = int(sys.argv[4]) if len(sys.argv) > 4 else 2
lr = float(sys.argv[5]) if len(sys.argv) > 5 else 2e-5
MAX_LEN, MAX_CHARS, GROUPS_PER_STEP = 384, 1500, 2

groups = [json.loads(l) for l in open(data_path, encoding="utf-8") if l.strip()]
tok = AutoTokenizer.from_pretrained(src)
model = AutoModelForSequenceClassification.from_pretrained(src)
model.train()
opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
steps = epochs * ((len(groups) + GROUPS_PER_STEP - 1) // GROUPS_PER_STEP)
sched = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
t0, step = time.time(), 0
for ep in range(epochs):
    random.shuffle(groups)
    total, hit = 0.0, 0
    for i in range(0, len(groups), GROUPS_PER_STEP):
        batch = groups[i:i + GROUPS_PER_STEP]
        q, p, sizes = [], [], []
        for g in batch:
            passages = [g["pos"]] + g["negs"]
            q += [g["claim"]] * len(passages)
            p += [x[:MAX_CHARS] for x in passages]
            sizes.append(len(passages))
        enc = tok(q, p, truncation="only_second", max_length=MAX_LEN, padding=True, return_tensors="pt")
        logits = model(**enc).logits.view(-1)
        loss, start = 0.0, 0
        for n in sizes:
            s = logits[start:start + n]
            loss = loss + torch.nn.functional.cross_entropy(s.unsqueeze(0), torch.zeros(1, dtype=torch.long))
            hit += int(s.argmax().item() == 0)
            start += n
        loss = loss / len(sizes)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step(); sched.step(); opt.zero_grad()
        total += loss.item() * len(sizes)
        step += 1
        if step % 20 == 0:
            print(f"ep{ep} adım {step}/{steps} kayıp {total / (i + len(batch)):.3f} isabet {hit / (i + len(batch)):.2f} "
                  f"{time.time() - t0:.0f}s", flush=True)
    print(f"== epoch {ep}: kayıp {total / len(groups):.3f}, eğitim isabeti {hit / len(groups):.2f}", flush=True)
out.mkdir(parents=True, exist_ok=True)
model.eval()
model.save_pretrained(out, safe_serialization=True)
tok.save_pretrained(out)
print("kaydedildi", out, f"{time.time() - t0:.0f}s")
