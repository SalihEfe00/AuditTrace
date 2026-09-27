# -*- coding: utf-8 -*-
"""İddia-kanıt yeniden sıralayıcısı (cross-encoder).

Gömme modeli sorguyu ve parçayı ayrı ayrı vektöre çevirir; cross-encoder ikisini birlikte okuyup
"bu parça bu cümleye cevap mı" puanı verir. Gerçek korpus ölçümünde (benchmarks/, iddia cümlesiyle
BM25+vektör RRF ilk 30 aday) beklenen hükmü ilk sıraya koyma: RRF 7/18 dev, 4/14 holdout;
cross-encoder ile 11/18 ve 13/14.

Model: cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 (çok dilli, ~470 MB, CPU'da 2 iş parçacığıyla
30 aday ~1.7 sn). İnternetten indirilmez: klasör config.RERANKER_MODEL_DIR'e (MUFETTIS_RERANKER_DIR)
elle konur. Klasör veya sentence-transformers yoksa load_reranker None döner ve analiz sözcüksel
sıralamayla sürer. Çıkarım deterministiktir (eval modu, sabit kırpma).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

# Parça metni bu kadar karakterle kırpılır (model zaten MAX_LENGTH token okur; kırpma hız içindir).
MAX_PASSAGE_CHARS = 1500
MAX_LENGTH = 384
BATCH_SIZE = 16
# Düşük donanım: çıkarım en fazla bu kadar CPU iş parçacığı kullanır.
MAX_THREADS = 2

# Model başına kabul eşiği: seçilen kanıtın puanı bunun altındaysa bulgu çıkmaz. Puan ölçeği modele
# özgü olduğundan eşik modelle birlikte tutulur ve benchmark ile ayarlanır (scripts/eval_accuracy.py,
# yalnızca dev vakaları; holdout doğrulama içindir). mmarco-mMiniLMv2: dev negatiflerinde seçilen
# kanıtın en yüksek puanı -2.57, dev doğrularının en düşüğü -1.74; eşik aradaki orta nokta.
# Ölçüm (48 vaka): dev 12/17 doğru, 0/11 yanlış pozitif; holdout 10/14 doğru, 0 yanlış dayanak, 0/6 yanlış pozitif.
MIN_SCORES = {
    "cross-encoder--mmarco-mMiniLMv2-L12-H384-v1": -2.15,
}


class CrossEncoderReranker:
    """score(sorgu, metinler) -> metin başına alaka puanı (büyük = ilgili; ölçek modele özgü)."""

    def __init__(self, model_dir: Path, min_score: Optional[float] = None):
        self.model_dir = Path(model_dir)
        self.name = self.model_dir.name
        # None: eşik yok (ayarı yapılmamış model; seçim yine sözcüksel kapıdan geçenler arasındadır).
        self.min_score = min_score
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                import torch
                from sentence_transformers import CrossEncoder

                if torch.get_num_threads() > MAX_THREADS:
                    torch.set_num_threads(MAX_THREADS)
                self._model = CrossEncoder(str(self.model_dir), device="cpu", max_length=MAX_LENGTH)
                self._model.model.eval()
            return self._model

    def score(self, query: str, passages: Sequence[str]) -> List[float]:
        if not passages:
            return []
        model = self._load()
        pairs = [(query, (p or "")[:MAX_PASSAGE_CHARS]) for p in passages]
        scores = model.predict(pairs, batch_size=BATCH_SIZE, show_progress_bar=False)
        return [float(s) for s in scores]


_DEFAULT: Optional[CrossEncoderReranker] = None
_DEFAULT_LOCK = threading.Lock()


def load_reranker(model_dir: Optional[Path] = None) -> Optional[CrossEncoderReranker]:
    """Model klasörü kuruluysa (paylaşılan) yeniden sıralayıcı, değilse None. Model ilk puanlamada yüklenir."""
    global _DEFAULT
    from mufettis.config import RERANKER_MODEL_DIR

    path = Path(model_dir or RERANKER_MODEL_DIR)
    if not (path / "config.json").is_file():
        return None
    try:
        import sentence_transformers  # noqa: F401
    except Exception as exc:
        logger.warning("Yeniden sıralayıcı kullanılamıyor (sentence-transformers): %s", exc)
        return None
    with _DEFAULT_LOCK:
        if _DEFAULT is None or _DEFAULT.model_dir != path:
            min_score = MIN_SCORES.get(path.name)
            if min_score is None:
                logger.warning("Yeniden sıralayıcı %s için ayarlı eşik yok; puan eşiği uygulanmayacak.", path.name)
            _DEFAULT = CrossEncoderReranker(path, min_score)
        return _DEFAULT


__all__ = ["CrossEncoderReranker", "load_reranker", "MAX_PASSAGE_CHARS", "MIN_SCORES"]
