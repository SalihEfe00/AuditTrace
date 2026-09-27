# -*- coding: utf-8 -*-
"""İddia-kanıt yeniden sıralayıcısı (cross-encoder).

Gömme modeli sorguyu ve parçayı ayrı ayrı vektöre çevirir; cross-encoder ikisini birlikte okuyup
"bu parça bu cümleye cevap mı" puanı verir. Gerçek korpus ölçümünde (benchmarks/, iddia cümlesiyle
BM25+vektör RRF ilk 30 aday) beklenen hükmü ilk sıraya koyma: RRF 7/18 dev, 4/14 holdout;
cross-encoder ile 11/18 ve 13/14 (ilk 14 pozitifli holdout; güncel sayılar benchmarks/baseline.json).

Modeller (MODELS, tercih sırasıyla): mufettis-reranker-ft1 — mmarco-mMiniLMv2'nin Türkçe bankacılık mevzuatı
üzerinde ince ayarlanmış hali (scripts/reranker_finetune/, docs/architecture/claim_matching.md); yoksa temel model
cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 (çok dilli). İkisi de ~490 MB (≈470 MiB), CPU'da 2 iş parçacığıyla
30 aday ~1.7 sn. Model analiz sırasında indirilmez; config.RERANKER_MODELS_ROOT/<ad> klasörüne
scripts/install_reranker_model.py ile kurulur (--download yalnızca temel model, --from her model). Kurulu model
veya sentence-transformers yoksa load_reranker None döner ve analiz sözcüksel sıralamayla sürer. Çıkarım
deterministiktir (eval modu, sabit kırpma).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Parça metni bu kadar karakterle kırpılır (model zaten MAX_LENGTH token okur; kırpma hız içindir).
MAX_PASSAGE_CHARS = 1500
MAX_LENGTH = 384
BATCH_SIZE = 16
# Düşük donanım: çıkarım en fazla bu kadar CPU iş parçacığı kullanır.
MAX_THREADS = 2

@dataclass(frozen=True)
class RerankerModel:
    """Kurulabilir bir yeniden sıralayıcı: klasör adı, eşikler ve dosya özetleri.

    min_score: seçilen kanıtın puanı bunun altındaysa bulgu çıkmaz. Puan ölçeği modele özgü olduğundan eşik
    modelle birlikte tutulur ve YALNIZCA dev vakalarıyla seçilir (scripts/eval_accuracy.py --min-score none;
    holdout ve mühürlü kümeler doğrulama içindir).
    confident_score: eşiği geçen ama bunun altında kalan bulgu "düşük güvenli" gösterilir; denetçi önce atfın
    iddiayla ilgili olduğunu doğrulamalıdır.
    Ağırlık dosyası değişirse eşikler ve benchmarks/baseline.json yeniden ölçülmelidir."""
    name: str
    min_score: float
    confident_score: float
    files: Tuple[str, ...]
    weights_sha256: str
    repo: str = ""        # indirilebilir kaynak (boşsa yalnızca --from ile kurulur)
    revision: str = ""


# Temel model. Eşik: ilk ölçümde dev negatiflerinde seçilen kanıtın en yüksek puanı -2.57, dev doğrularının
# en düşüğü -1.74; orta nokta (temiz korpusla dev'de yeniden doğrulandı). Güven sınırı 0 = logit'in "yüzde elli
# ilgili" noktası. Ölçüm (82 vaka): dev 12/18 doğru, 2 yanlış dayanak, 0/10 yanlış pozitif; holdout 22/34 doğru,
# 4 yanlış dayanak, 0/20 yanlış pozitif; yanlış dayanakların 5/6'sı, doğruların 8/34'ü düşük güven bandında.
BASE_MODEL = RerankerModel(
    name="cross-encoder--mmarco-mMiniLMv2-L12-H384-v1",
    min_score=-2.15,
    confident_score=0.0,
    files=("config.json", "model.safetensors", "sentencepiece.bpe.model",
           "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"),
    weights_sha256="5daeca2481a76b5976a2bdc32f0a78532b6716da4f8cd3ff59460ef8d2f359b4",
    repo="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    revision="1427fd652930e4ba29e8149678df786c240d8825",
)

# İnce ayarlı model (2026-09-28, pilot 3): temel modelden listwise softmax ile, 5411/5464/6361 maddeleri için
# yazılmış ve incelemede temiz bulunan 250 sentetik iddia grubu (1 doğru + 7 zor negatif); ölçüm kümelerinin
# dayanakları eğitimde ne pozitif ne negatif. Eşik dev'de (doğru − yanlış dayanak − yanlış pozitif) en büyük
# olacak biçimde seçildi; güven sınırı dev'deki tek yanlış dayanak (-1.26) ile üstündeki doğru (1.09) arası.
# Ölçüm (datacopy, 82 vaka): dev 10/17 doğru, 1 yanlış dayanak, 0/11 YP; holdout 22/34, 0, 0/20
# (temel model: 34 doğru / 6 yanlış dayanak toplamda). Mühürlü gündelik dil kümesi (lay_cases_ho6, bir kez):
# 4/16 doğru, 2 yanlış dayanak, 0/4 YP (temel model 3/16, 4, 0).
FINETUNED_MODEL = RerankerModel(
    name="mufettis-reranker-ft1",
    min_score=-4.48,
    confident_score=0.0,
    files=("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json"),
    weights_sha256="05fc36d6306a4323a3b84d8a26982a24eec76dda042e4998ec6f00add4cf0ae0",
)

# Tercih sırası: kurulu olanlardan ilki kullanılır.
MODELS: Dict[str, RerankerModel] = {m.name: m for m in (FINETUNED_MODEL, BASE_MODEL)}
MIN_SCORES = {m.name: m.min_score for m in MODELS.values()}
CONFIDENT_SCORES = {m.name: m.confident_score for m in MODELS.values()}

# Temel modelin kurulum sabitleri (geriye uyum; indirme yalnızca temel model için).
MODEL_REPO = BASE_MODEL.repo
MODEL_REVISION = BASE_MODEL.revision
MODEL_FILES = BASE_MODEL.files
MODEL_WEIGHTS_SHA256 = {"model.safetensors": BASE_MODEL.weights_sha256}
MISSING_MODEL_WARNING = (
    "İddia-kanıt yeniden sıralayıcı modeli kurulu değil; eşleştirme yalnızca sözcüksel sıralamayla yapıldı "
    "ve daha az isabetlidir (ölçümde yanlış dayanak ve yanlış pozitif belirgin biçimde artıyor). Kurulum: "
    "python scripts/install_reranker_model.py --download (ya da --from <klasör>)."
)


def verify_model_dir(model_dir: Path, model: Optional[RerankerModel] = None) -> List[str]:
    """Model klasöründeki sorunlar (boşsa kurulu ve özetleri doğru). model verilmezse klasör adından bulunur;
    tanınmayan adda temel modelin dosya listesi ve özeti beklenir."""
    import hashlib

    model_dir = Path(model_dir)
    model = model or MODELS.get(model_dir.name)
    files = model.files if model else MODEL_FILES
    digests = {"model.safetensors": model.weights_sha256} if model else MODEL_WEIGHTS_SHA256
    problems = [f"eksik dosya: {name}" for name in files if not (model_dir / name).is_file()]
    for name, expected in digests.items():
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


def default_model_dir() -> Optional[Path]:
    """config.RERANKER_MODEL_DIR (zorlanmışsa) ya da RERANKER_MODELS_ROOT altında kurulu ilk model (MODELS sırası)."""
    from mufettis.config import RERANKER_MODEL_DIR, RERANKER_MODELS_ROOT

    if RERANKER_MODEL_DIR is not None:
        return Path(RERANKER_MODEL_DIR)
    for name in MODELS:
        if (Path(RERANKER_MODELS_ROOT) / name / "config.json").is_file():
            return Path(RERANKER_MODELS_ROOT) / name
    return None


def load_reranker(model_dir: Optional[Path] = None) -> Optional[CrossEncoderReranker]:
    """Model klasörü kuruluysa (paylaşılan) yeniden sıralayıcı, değilse None. Model ilk puanlamada yüklenir."""
    global _DEFAULT
    path = Path(model_dir) if model_dir else default_model_dir()
    if path is None or not (path / "config.json").is_file():
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
    "CrossEncoderReranker", "RerankerModel", "MODELS", "BASE_MODEL", "FINETUNED_MODEL", "default_model_dir",
    "load_reranker", "verify_model_dir", "MAX_PASSAGE_CHARS", "MIN_SCORES", "CONFIDENT_SCORES",
    "MODEL_REPO", "MODEL_REVISION", "MODEL_FILES", "MODEL_WEIGHTS_SHA256", "MISSING_MODEL_WARNING",
]
