#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""İddia-kanıt yeniden sıralayıcı modellerini (cross-encoder, ~490 MB (≈470 MiB)) kurar ve doğrular.

Model analiz doğruluğunun temelidir (bkz. mufettis/rag/reranker.py, benchmarks/baseline.json);
yoksa uygulama çalışır ama eşleştirme yalnızca sözcükseldir ve analiz sonucunda uyarı gösterilir.
Uygulama modeli kendiliğinden indirmez: kurulum bu betikle, açık bir komutla yapılır.

Kullanım:
  python scripts/install_reranker_model.py                         # durum: hangi modeller kurulu, dosyalar doğru mu
  python scripts/install_reranker_model.py --download              # temel modeli Hugging Face'ten sabit sürümle indir
  python scripts/install_reranker_model.py --from D:/model         # klasörden kopyala (varsayılan: ince ayarlı model)
  python scripts/install_reranker_model.py --from D:/m --model cross-encoder--mmarco-mMiniLMv2-L12-H384-v1

Modeller ve tercih sırası: mufettis.rag.reranker.MODELS. İnce ayarlı model indirilemez; dağıtım paketinden ya da
scripts/reranker_finetune/ ile üretilen klasörden --from ile kurulur. Hedef: config.RERANKER_MODELS_ROOT/<ad>
(MUFETTIS_RERANKER_DIR verilmişse o klasör). Dosyalar önce geçici klasöre alınır, özetleri doğrulanınca yerine taşınır.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.config import RERANKER_MODEL_DIR, RERANKER_MODELS_ROOT  # noqa: E402
from mufettis.rag.reranker import (  # noqa: E402
    BASE_MODEL, FINETUNED_MODEL, MODEL_FILES, MODELS, RerankerModel, verify_model_dir,
)


def _download(staging: Path, model: Optional[RerankerModel]) -> None:
    from huggingface_hub import snapshot_download

    # local_dir: Windows'ta sembolik bağlantı izni gerektirmez (HF önbellek düzeni gerektirir).
    model = model or BASE_MODEL
    snapshot_download(repo_id=model.repo, revision=model.revision, local_dir=str(staging),
                      allow_patterns=list(model.files))


def _copy_from(source: Path, staging: Path, model: Optional[RerankerModel]) -> None:
    for name in (model.files if model else MODEL_FILES):
        if (source / name).is_file():
            shutil.copy2(source / name, staging / name)


def target_dir(model: RerankerModel) -> Path:
    return Path(RERANKER_MODEL_DIR) if RERANKER_MODEL_DIR is not None else Path(RERANKER_MODELS_ROOT) / model.name


def install(target: Path, source: Optional[Path] = None, model: Optional[RerankerModel] = None) -> int:
    """model None: temel modelin dosya listesi ve özeti (reranker.MODEL_FILES / MODEL_WEIGHTS_SHA256)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=target.name + ".", dir=target.parent))
    try:
        if source is not None:
            _copy_from(source, staging, model)
        else:
            _download(staging, model)
        problems = verify_model_dir(staging, model)
        if problems:
            print("Model doğrulanamadı, kurulmadı:\n  " + "\n  ".join(problems))
            return 1
        old = target.with_name(target.name + ".eski")
        if target.exists():
            if old.exists():
                shutil.rmtree(old)
            target.rename(old)
        staging.rename(target)
        # Yeni model doğrulandı ve yerinde; önceki kopya yalnızca taşıma sırasında korunur.
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        print(f"Kuruldu ve doğrulandı: {target}")
        return 0
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--download", action="store_true", help="temel modeli Hugging Face'ten indir (internet gerekir)")
    group.add_argument("--from", dest="source", type=Path, help="model dosyalarını bu klasörden kopyala")
    parser.add_argument("--model", choices=list(MODELS), help="kurulacak model (varsayılan: --download için temel, "
                        "--from için ince ayarlı model)")
    args = parser.parse_args()

    if args.download or args.source:
        model = MODELS[args.model] if args.model else (BASE_MODEL if args.download else FINETUNED_MODEL)
        if args.download and not model.repo:
            parser.error(f"{model.name} indirilemez; --from ile kurulur")
        if args.source and not args.source.is_dir():
            parser.error(f"klasör bulunamadı: {args.source}")
        return install(target_dir(model), args.source, model)

    installed = 0
    for model in MODELS.values():
        target = target_dir(model)
        problems = verify_model_dir(target, model) if target.is_dir() else ["kurulu değil"]
        installed += not problems
        source = f"{model.repo} @ {model.revision[:12]}" if model.repo else "yerel ince ayar (--from ile kurulur)"
        print(f"Model: {model.name} — {source}\n  Klasör: {target}\n  Durum: "
              + ("kurulu ve doğrulandı" if not problems else "; ".join(problems)))
    return 0 if installed else 1

if __name__ == "__main__":
    sys.exit(main())
