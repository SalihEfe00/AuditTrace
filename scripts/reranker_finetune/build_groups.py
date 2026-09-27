# -*- coding: utf-8 -*-
"""Eğitim grupları: (iddia, doğru madde metni, N_NEG yanıltıcı negatif) -> <çıktı>/train.jsonl.

- Madde metinleri korpustan (MUFETTIS_DATA_DIR) okunur; korpusa yazılmaz.
- Ölçüm dayanakları (benchmarks/*cases*.json) eğitimde hiç görünmez: o maddeye ait iddia atılır,
  aday listesinde çıkarlarsa negatif yapılmaz.
- Sızıntı süzgeci: benchmark metni cümlelerine MiniLM kosinüsü >= LEAK_COS olan iddia atılır.
- Negatifler: hattın iddia adayları (LocalRAG.claim_candidates, ilk 30) içinden doğru madde dışındakiler,
  TEMEL modelin (reranker.BASE_MODEL) puanına göre en zorları; eksikse aynı kanunun komşu maddeleri.

Kullanım: python scripts/reranker_finetune/build_groups.py <çıktı klasörü> [--drop-reviewed] [--limit N]
mufettis-reranker-ft1 --drop-reviewed ile üretildi; o sürümde dışlanan ölçüm kümeleri real_corpus_cases ve
lay_cases_ho5 idi (bu betik benchmarks/ altındaki tüm vaka dosyalarını dışlar, yani daha katıdır).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from common import LAWS, all_cases, eval_specs, load_claims, unit_key

import numpy as np

LEAK_COS = 0.85
N_NEG = 7
CANDIDATES = 30


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--drop-reviewed", action="store_true", help="incelemede ZAYIF/YANLIŞ işaretli iddiaları at")
    parser.add_argument("--limit", type=int, default=0, help="yalnızca ilk N iddia (deneme)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.out.mkdir(parents=True, exist_ok=True)

    from mufettis.rag.engine import LocalRAG
    from mufettis.rag.paging import iter_pages
    from mufettis.config import RERANKER_MODELS_ROOT
    from mufettis.rag.reranker import BASE_MODEL, load_reranker

    rag = LocalRAG()
    rer = load_reranker(RERANKER_MODELS_ROOT / BASE_MODEL.name)
    if rer is None:
        parser.error("temel sıralayıcı modeli kurulu değil (scripts/install_reranker_model.py --download)")

    parts = defaultdict(list)
    for _ids, docs, metas in iter_pages(rag.collection, include=("documents", "metadatas")):
        for d, m in zip(docs, metas):
            m = m or {}
            if str(m.get("law_number") or "") in LAWS and m.get("article_no"):
                parts[unit_key(m)].append((int(m.get("chunk_no") or 0), int(m.get("subchunk_no") or 0), d))
    texts = {k: "\n".join(t for *_, t in sorted(v, key=lambda p: p[:2])) for k, v in parts.items()}

    specs = eval_specs()
    bench_keys = {f"{s.law_number}||{s.article}" for s in specs if s.law_number and s.article}

    def is_bench(meta):
        return any(s.matches(meta) for s in specs)

    def emb(items):
        v = np.asarray(rag.embed(items), dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    claims = [(k, c) for k, c in load_claims(args.drop_reviewed) if k in texts and k not in bench_keys]
    bench = [s.strip() for case in all_cases() for s in re.split(r"(?<=[.!?])\s+|\n+", case.text) if len(s.strip()) > 25]
    sims = emb([c for _, c in claims]) @ emb(bench).T
    kept = [kc for kc, row in zip(claims, sims) if row.max() < LEAK_COS]
    print(f"iddia {len(claims)}, sızıntı süzgeciyle atılan {len(claims) - len(kept)}")
    if args.limit:
        kept = kept[:args.limit]

    def neighbours(key):
        law, pre, no = key.split("|")
        if not no.isdigit():
            return []
        near = (f"{law}|{pre}|{int(no) + d}" for d in (1, -1, 2, -2, 3, -3))
        return [texts[k] for k in near if k in texts and k not in bench_keys]

    groups, n_bench = [], 0
    for i, (key, claim) in enumerate(kept):
        negs = []
        for x in rag.claim_candidates(claim, CANDIDATES, None):
            meta = x.get("metadata") or {}
            if unit_key(meta) == key or not x.get("document"):
                continue
            if is_bench(meta):
                n_bench += 1
                continue
            negs.append(x["document"])
        if negs:
            scores = rer.score(claim, negs)
            negs = [n for _, n in sorted(zip(scores, negs), key=lambda t: -t[0])]
        negs += neighbours(key)
        groups.append({"key": key, "claim": claim, "pos": texts[key], "negs": negs[:N_NEG]})
        if i % 50 == 0:
            print(i, flush=True)
    with open(args.out / "train.jsonl", "w", encoding="utf-8") as fh:
        for g in groups:
            fh.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(f"grup {len(groups)}, ölçüm dayanağı olduğu için atılan negatif {n_bench}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
