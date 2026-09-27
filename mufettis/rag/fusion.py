# -*- coding: utf-8 -*-
"""Sözcüksel (BM25) ve anlamsal (vektör) aramanın sıralama birleştirmesi: ağırlıklı Reciprocal Rank
Fusion. Puanlar farklı ölçeklerde olduğundan (BM25 sınırsız, vektör uzaklığı 0..4) toplanmaz;
her aday, her listedeki SIRASI üzerinden puan alır: sum(ağırlık / (RRF_K + sıra)).

Ağırlıklar gerçek korpus ölçümüyle seçildi (benchmarks/, scripts/eval_accuracy.py): mevcut gömme
modeli (paraphrase-multilingual-MiniLM-L12-v2, 128 token) iddia cümlesiyle beklenen hükmü ilk 10'da
dev vakalarının 7/18'inde, holdout vakalarının 4/14'ünde buluyordu; Türkçe kök anahtarlı BM25
13/18 ve 12/14. BM25 birincil, vektör ikincil sinyal (1.0 / 0.5, RRF_K=10) ilk 10'da 13/18 ve
13/14 verdi; 0.25-0.5 aralığı ve RRF_K 10-60 benzer sonuç veriyor. Gömme modeli değişirse ağırlıklar
aynı ölçümle yeniden seçilmelidir.
"""
from __future__ import annotations

from typing import Dict, Hashable, Sequence, Tuple

RRF_K = 10
LEXICAL_WEIGHT = 1.0
SEMANTIC_WEIGHT = 0.5


def reciprocal_rank_fusion(
    rankings: Sequence[Tuple[Sequence[Hashable], float]],
    k: int = RRF_K,
) -> Dict[Hashable, float]:
    """rankings: (en iyiden kötüye kimlik listesi, ağırlık). Bir listede tekrar eden kimliğin
    yalnızca ilk sırası sayılır. Dönen puan büyükse aday iyidir."""
    scores: Dict[Hashable, float] = {}
    for ranking, weight in rankings:
        seen = set()
        for rank, key in enumerate(ranking):
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank + 1)
    return scores


__all__ = ["RRF_K", "LEXICAL_WEIGHT", "SEMANTIC_WEIGHT", "reciprocal_rank_fusion"]
