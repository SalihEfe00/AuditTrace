# -*- mode: python ; coding: utf-8 -*-
# M3.2 deneme paketi (onedir). Kurulum: python -m PyInstaller packaging/mufettis_trial.spec --noconfirm --clean
# --clean zorunlu: önbellek değişen uygulama modüllerini yenilemeyebiliyor (M3.3: pakette eski admin/app/engine kaldı).
# Veri, modeller ve HF önbelleği pakete girmez; dist klasörünün yanına data/, models/, hf/ olarak konur (launcher.py).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "app.py"), ".")]
datas += collect_data_files("streamlit")
datas += copy_metadata("streamlit")
for pkg in ("sentence_transformers", "transformers", "tokenizers", "huggingface_hub", "safetensors", "torch",
            "numpy", "pandas", "pydantic", "requests", "tqdm", "regex", "filelock", "packaging", "pyyaml"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

hiddenimports = collect_submodules("mufettis", filter=lambda n: ".tests.test_" not in n)
hiddenimports += collect_submodules("mufettis.repair")
hiddenimports += collect_submodules("streamlit")

# PyInstaller System32'deki DLL'leri toplamaz. torch_cpu.dll, Visual C++ 2022 çalışma zamanının
# vcruntime140_threads.dll'ine bağlıdır; temiz Windows'ta yoktur (M3.3'te Sandbox'ta torch yüklenemedi).
# Uygulamayla birlikte dağıtılabilir (Visual C++ yeniden dağıtılabilir paket lisansı, uygulama yerel dağıtım).
binaries = [(r"C:\Windows\System32\vcruntime140_threads.dll", "torch/lib")]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["chromadb", "kubernetes", "grpc", "playwright", "pytest", "ruff", "vulture", "PyInstaller",
              "IPython", "matplotlib", "tkinter", "torchvision", "torchaudio", "onnx"],
    noarchive=False,
    # transformers 5 içe aktarma yapısını kendi .py dosyalarını diskte tarayarak kurar; arşivdeyken açılış
    # sonsuza dek bekler (M3.2'de görüldü). Kaynak dosyalar diske konur.
    module_collection_mode={"transformers": "py"},
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MufettisAI", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="MufettisAI")
