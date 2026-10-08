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
# M16: amblem, simge ve favicon (arayüz başlığı ve Word raporu kullanır).
datas += [(str(p), "mufettis/ui/brand") for p in (ROOT / "mufettis" / "ui" / "brand").iterdir() if p.is_file()]
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
# M17.7a: WebView2 penceresi (pywebview + pythonnet); kanca webview/lib'i toplar, aşağıda süzülür.
hiddenimports += ["webview", "webview.platforms.winforms", "webview.platforms.edgechromium", "clr", "clr_loader"]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["torch", "torchvision", "torchaudio", "transformers", "sentence_transformers", "sklearn", "scipy",
              "sympy", "networkx", "chromadb", "kubernetes", "grpc", "playwright", "pytest", "ruff", "vulture",
              "PyInstaller", "IPython", "matplotlib", "tkinter", "onnx", "onnxruntime.transformers",
              "onnxruntime.quantization", "huggingface_hub", "webview.platforms.cocoa", "webview.platforms.gtk",
              "webview.platforms.qt", "webview.platforms.android", "webview.platforms.cef"],
    noarchive=False,
)
# M17.7a: yalnız Windows x64 WebView2 DLL'leri (Microsoft imzalı) kalır; imzasız MSHTML yedeği (WebBrowserInterop),
# Android dosyası ve x86 / arm64 kopyaları pakete girmez.
_DROP = ("webbrowserinterop", "pywebview-android", "win-x86", "win-arm64")
a.binaries = [b for b in a.binaries if not any(d in b[0].replace("\\", "/").lower() for d in _DROP)]
a.datas = [d for d in a.datas if not any(x in d[0].replace("\\", "/").lower() for x in _DROP)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AuditTrace", console=False,
          icon=str(ROOT / "mufettis" / "ui" / "brand" / "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="AuditTrace")
