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

# Eşiği geçen ama bu puanın altında kalan bulgu "düşük güvenli" gösterilir: denetçi önce atıf yapılan
# hükmün iddiayla ilgili olduğunu doğrulamalıdır. Eşik yanlış dayanakları ayıramıyor (dev'de yanlış
# dayanak puanları -1.91 ve -0.74, doğruların bir kısmı daha düşük); ayırmak yerine işaretlenir.
# Sınır ayarlanmadı: çapraz kodlayıcının logit'i, 0 = modelin "yüzde elli ilgili" noktası.
# Ölçüm (2026-09-27, üretim korpusu): dev'de 2/2 yanlış dayanak ve 12 doğrunun 5'i bu bantta.
CONFIDENT_SCORES = {
    "cross-encoder--mmarco-mMiniLMv2-L12-H384-v1": 0.0,
}

# Kurulum (scripts/install_reranker_model.py): sabit sürüm ve dosya özetleri. Ağırlık dosyası
# değişirse eşik (MIN_SCORES) ve benchmarks/baseline.json yeniden ölçülmelidir.
MODEL_REPO = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
MODEL_REVISION = "1427fd652930e4ba29e8149678df786c240d8825"
MODEL_FILES = (
    "config.json", "model.safetensors", "sentencepiece.bpe.model",
    "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
)
MODEL_WEIGHTS_SHA256 = {"model.safetensors": "5daeca2481a76b5976a2bdc32f0a78532b6716da4f8cd3ff59460ef8d2f359b4"}
MISSING_MODEL_WARNING = (
    "İddia-kanıt yeniden sıralayıcı modeli kurulu değil; eşleştirme yalnızca sözcüksel sıralamayla yapıldı "
    "ve daha az isabetlidir (ölçümde yanlış dayanak ve yanlış pozitif belirgin biçimde artıyor). Kurulum: "
    "python scripts/install_reranker_model.py --download (ya da --from <klasör>)."
)


def verify_model_dir(model_dir: Path) -> List[str]:
    """Model klasöründeki sorunlar (boşsa kurulu ve özetleri doğru)."""
    import hashlib

    model_dir = Path(model_dir)
    problems = [f"eksik dosya: {name}" for name in MODEL_FILES if not (model_dir / name).is_file()]
    for name, expected in MODEL_WEIGHTS_SHA256.items():
        path = model_dir / name
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            problems.append(f"özet uyuşmuyor: {name}")
    return problems


class CrossEncoderReranker:
    """score(sorgu, metinler) -> metin başına alaka puanı (büyük = ilgili; ölçek modele özgü)."""

    def __init__(self, model_dir: Path, min_score: Optional[float] = None, confident_score: Optional[float] = None):
        self.model_dir = Path(model_dir)
        self.name = self.model_dir.name
        # None: eşik yok (ayarı yapılmamış model; seçim yine sözcüksel kapıdan geçenler arasındadır).
        self.min_score = min_score
        # None: güven bandı yok (ayarı yapılmamış model; bulgular güven etiketi taşımaz).
        self.confident_score = confident_score
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
            _DEFAULT = CrossEncoderReranker(path, min_score, CONFIDENT_SCORES.get(path.name))
        return _DEFAULT


__all__ = [
    "CrossEncoderReranker", "load_reranker", "verify_model_dir", "MAX_PASSAGE_CHARS", "MIN_SCORES", "CONFIDENT_SCORES",
    "MODEL_REPO", "MODEL_REVISION", "MODEL_FILES", "MODEL_WEIGHTS_SHA256", "MISSING_MODEL_WARNING",
]
