# -*- coding: utf-8 -*-
"""M17.3: ürün paketini kurar. Kaynaklar yalnızca okunur; çıktı dist/AuditTrace.

1. PyInstaller (`audittrace.spec`, her zaman --clean: M3.3'te önbellek eski modülleri pakete koymuştu).
2. models/: kurulu ONNX modelleri (manifest özetleri doğrulanır; torch modelleri girmez).
3. tesseract/: tesseract.exe, DLL'leri ve tessdata (tur, osd) — taranmış PDF'ler için OCR.
4. data/: `scripts/build_package_data.py` ile seçilmiş mevzuat kütüphanesi (kullanıcı kayıtları girmez).
5. Denetim: pakette torch / transformers yok; System32 dışından beklenen her DLL pakette (M3.3: eksik
   vcruntime140_threads.dll temiz Windows'ta torch'u yükletmemişti); boyut raporu dist/AuditTrace_icerik.json.

6. --installer: Inno Setup ile tek kurulum dosyası (M17.5, `packaging/audittrace.iss`; dist/AuditTrace_Kurulum_<sürüm>).

Kullanım: python packaging/build.py --data <veri dizini> [--tesseract C:\\mufettis_ai] [--skip-pyinstaller]
          [--installer] | python packaging/build.py --installer-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DIST = ROOT / "dist" / "AuditTrace"
FORBIDDEN = ("torch", "transformers", "sentence_transformers", "sklearn", "scipy", "chromadb")
TESSDATA = ("tur.traineddata", "osd.traineddata")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def size_mb(path: Path) -> float:
    if path.is_file():
        return round(path.stat().st_size / 2**20, 1)
    return round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 2**20, 1)


def pyinstaller() -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "audittrace.spec"),
                    "--noconfirm", "--clean", "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build")],
                   check=True, cwd=ROOT)


def copy_models() -> None:
    from mufettis.config import EMBEDDING_MODEL
    from mufettis.rag.onnx_backend import onnx_dir
    from mufettis.rag.reranker import FINETUNED_MODEL

    for name in (EMBEDDING_MODEL, FINETUNED_MODEL.name):
        src = onnx_dir(name)
        if src is None:
            raise SystemExit(f"ONNX modeli kurulu değil: {name} (scripts/onnx_models/export.py + install.py)")
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        if sha256(src / "model.onnx") != manifest["onnx_sha256"]:
            raise SystemExit(f"ONNX özeti uyuşmuyor: {src}")
        shutil.copytree(src, DIST / "models" / src.name, dirs_exist_ok=True)


def copy_tesseract(src: Path) -> None:
    target = DIST / "tesseract"
    (target / "tessdata").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "tesseract.exe", target / "tesseract.exe")
    for dll in src.glob("*.dll"):
        shutil.copy2(dll, target / dll.name)
    for name in TESSDATA:
        shutil.copy2(src / "tessdata" / name, target / "tessdata" / name)
    for lic in ("LICENSE", "doc/LICENSE", "doc/AUTHORS"):
        if (src / lic).is_file():
            shutil.copy2(src / lic, target / Path(lic).name)


def copy_data(src: Path) -> None:
    from scripts.build_package_data import build

    if (DIST / "data").exists():
        shutil.rmtree(DIST / "data")
    build(src, DIST / "data")


def audit() -> dict:
    internal = DIST / "_internal"
    present = [n for n in FORBIDDEN if (internal / n).exists()]
    if present:
        raise SystemExit(f"pakette olmaması gereken kütüphaneler: {present}")
    missing = missing_dlls(DIST)
    if missing:
        raise SystemExit(f"pakette eksik DLL'ler: {missing}")
    report = {p.name: size_mb(p) for p in sorted(DIST.iterdir())}
    report["toplam"] = size_mb(DIST)
    (DIST.parent / "AuditTrace_icerik.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                        encoding="utf-8")
    return report


def missing_dlls(root: Path) -> list:
    """Paketteki PE dosyalarının içe aktardığı DLL'lerden ne pakette ne de her Windows'ta hazır olanlar."""
    import pefile

    have = {p.name.lower() for p in root.rglob("*.dll")} | {p.name.lower() for p in root.rglob("*.pyd")}
    missing = set()
    for path in list(root.rglob("*.dll")) + list(root.rglob("*.pyd")) + list(root.rglob("*.exe")):
        try:
            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        except Exception:
            continue
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
            name = entry.dll.decode("ascii", "ignore").lower()
            if name not in have and not system_dll(name):
                missing.add(f"{path.relative_to(root)} -> {name}")
        pe.close()
    return sorted(missing)


def system_dll(name: str) -> bool:
    """Temiz Windows 10/11'de hazır gelen DLL'ler (api-ms-win-* kümeleri ve System32'deki çekirdek DLL'ler).
    Visual C++ çalışma zamanı (vcruntime140*, msvcp140*) hazır SAYILMAZ: temiz Windows'ta olmayabilir."""
    if name.startswith(("api-ms-win-", "ext-ms-win-")):
        return True
    if name.startswith(("vcruntime", "msvcp", "concrt", "vcomp")):
        return False
    return (Path(r"C:\Windows\System32") / name).is_file()


def iscc_path() -> Path:
    """Inno Setup derleyicisi (kullanıcı başına ya da sistem kurulumu)."""
    import os

    bases = (Path(os.environ.get("LOCALAPPDATA", ".")) / "Programs", Path(os.environ.get("ProgramFiles(x86)", ".")),
             Path(os.environ.get("ProgramFiles", ".")))
    for base in bases:
        if (base / "Inno Setup 6" / "ISCC.exe").is_file():
            return base / "Inno Setup 6" / "ISCC.exe"
    raise SystemExit("Inno Setup 6 bulunamadı (https://jrsoftware.org/isdl.php)")


def installer() -> Path:
    from mufettis.config import APP_VERSION

    if not (DIST / "AuditTrace.exe").is_file():
        raise SystemExit("önce paket derlenmeli: dist/AuditTrace yok")
    subprocess.run([str(iscc_path()), f"/DAppVersion={APP_VERSION}", "/Qp", str(ROOT / "packaging" / "audittrace.iss")],
                   check=True, cwd=ROOT / "packaging")
    return DIST.parent / f"AuditTrace_Kurulum_{APP_VERSION}.exe"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path)
    parser.add_argument("--tesseract", type=Path, default=Path(r"C:\mufettis_ai"))
    parser.add_argument("--skip-pyinstaller", action="store_true")
    parser.add_argument("--installer", action="store_true")
    parser.add_argument("--installer-only", action="store_true")
    args = parser.parse_args()
    if args.installer_only:
        print(installer())
        return 0
    if args.data is None:
        parser.error("--data gerekli")
    if not args.skip_pyinstaller:
        pyinstaller()
    copy_models()
    copy_tesseract(args.tesseract)
    copy_data(args.data)
    print(json.dumps(audit(), ensure_ascii=False, indent=1))
    if args.installer:
        print(installer())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
