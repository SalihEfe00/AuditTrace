#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""İddia-kanıt yeniden sıralayıcı modelini (cross-encoder, ~490 MB) kurar ve doğrular.

Model analiz doğruluğunun temelidir (bkz. mufettis/rag/reranker.py, benchmarks/baseline.json);
yoksa uygulama çalışır ama eşleştirme yalnızca sözcükseldir ve analiz sonucunda uyarı gösterilir.
Uygulama modeli kendiliğinden indirmez: kurulum bu betikle, açık bir komutla yapılır.

Kullanım:
  python scripts/install_reranker_model.py                 # durum: kurulu mu, dosyalar doğru mu
  python scripts/install_reranker_model.py --download      # Hugging Face'ten sabit sürümü indir
  python scripts/install_reranker_model.py --from D:/model # internetsiz makine: klasörden kopyala

Hedef klasör: config.RERANKER_MODEL_DIR (MUFETTIS_RERANKER_DIR ile değiştirilebilir). İndirilen
veya kopyalanan dosyalar önce geçici klasöre alınır, özetleri doğrulanınca yerine taşınır.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.config import RERANKER_MODEL_DIR  # noqa: E402
from mufettis.rag.reranker import MODEL_FILES, MODEL_REPO, MODEL_REVISION, verify_model_dir  # noqa: E402


def _download(staging: Path) -> None:
    from huggingface_hub import snapshot_download

    # local_dir: Windows'ta sembolik bağlantı izni gerektirmez (HF önbellek düzeni gerektirir).
    snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION, local_dir=str(staging),
                      allow_patterns=list(MODEL_FILES))


def _copy_from(source: Path, staging: Path) -> None:
    for name in MODEL_FILES:
        if (source / name).is_file():
            shutil.copy2(source / name, staging / name)


def install(target: Path, source: Optional[Path] = None) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=target.name + ".", dir=target.parent))
    try:
        if source is not None:
            _copy_from(source, staging)
        else:
            _download(staging)
        problems = verify_model_dir(staging)
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
    group.add_argument("--download", action="store_true", help="Hugging Face'ten indir (internet gerekir)")
    group.add_argument("--from", dest="source", type=Path, help="model dosyalarını bu klasörden kopyala")
    args = parser.parse_args()

    target = Path(RERANKER_MODEL_DIR)
    if args.download or args.source:
        if args.source and not args.source.is_dir():
            parser.error(f"klasör bulunamadı: {args.source}")
        return install(target, args.source)

    problems = verify_model_dir(target) if target.is_dir() else ["kurulu değil"]
    print(f"Model: {MODEL_REPO} @ {MODEL_REVISION[:12]}\nKlasör: {target}")
    print("Durum: kurulu ve doğrulandı" if not problems else "Durum: " + "; ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
