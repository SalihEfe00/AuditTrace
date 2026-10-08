# -*- mode: python ; coding: utf-8 -*-
# M17.3 ürün paketi (onedir). Derleme packaging/build.py ile (PyInstaller --clean, ardından modeller, Tesseract ve
# veri paketin yanına konur). Doğrudan: python -m PyInstaller packaging/audittrace.spec --noconfirm --clean
# Model çalışma zamanı ONNX'tir (M17.1): torch, transformers, sentence-transformers, sklearn, scipy pakete girmez.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "app.py"), ".")]
# Uygulamanın kendi veri dosyaları: kural kataloğu (M9) ve örnek kredi dosyası (M15.9).
datas += [(str(p), "mufettis/rules/catalog") for p in (ROOT / "mufettis" / "rules" / "catalog").glob("*.json")]
datas += [(str(p), str(p.parent.relative_to(ROOT)).replace("\\", "/"))
          for p in (ROOT / "mufettis" / "ui" / "samples").rglob("*") if p.is_file()]
datas += collect_data_files("streamlit")
datas += copy_metadata("streamlit")
for pkg in ("onnxruntime", "tokenizers", "numpy", "pandas", "pydantic", "requests", "packaging", "python-docx",
            "pypdf", "pdfplumber", "pypdfium2", "openpyxl"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass
datas += collect_data_files("pypdfium2_raw")

binaries = collect_dynamic_libs("onnxruntime")

hiddenimports = collect_submodules("mufettis", filter=lambda n: ".tests.test_" not in n)
hiddenimports += collect_submodules("repair")
hiddenimports += collect_submodules("streamlit")
hiddenimports += ["onnxruntime", "tokenizers"]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["torch", "torchvision", "torchaudio", "transformers", "sentence_transformers", "sklearn", "scipy",
              "sympy", "networkx", "chromadb", "kubernetes", "grpc", "playwright", "pytest", "ruff", "vulture",
              "PyInstaller", "IPython", "matplotlib", "tkinter", "onnx", "onnxruntime.transformers",
              "onnxruntime.quantization", "huggingface_hub", "webview"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AuditTrace", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="AuditTrace")
