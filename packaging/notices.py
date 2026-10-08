# -*- coding: utf-8 -*-
"""M17.7b: üçüncü taraf lisans bildirimi (THIRD_PARTY_NOTICES.txt) ve lisans denetimi.

MIT / BSD / Apache gibi lisanslar ikili dağıtımda telif bildirimini ve lisans metnini şart koşar; paket bu dosyayı
taşır (kurulum programı da). Pakete giren Python dağıtımları PyInstaller'ın derleme kaydından (PYZ / Analysis TOC)
ve `_internal` klasöründen bulunur; her birinin dist-info lisans dosyaları olduğu gibi eklenir.

Denetim: ücretli, ticari kullanımı yasaklayan ya da kaynak açma yükü uygulamanın kendi koduna yayılan bir lisans
(AGPL, SSPL, BUSL, "non-commercial", "proprietary", istisnasız GPL) pakete giren bir Python dağıtımında görülürse
derleme durur. LGPL ve "GPL + bağlama istisnası" (ör. GCC çalışma zamanı) ücretsizdir, ticari kullanıma izin verir;
yükümlülük bildirim ve kaynak erişimidir, bu dosyada yazılır.
"""
from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

OWN = {"mufettis", "repair", "launcher", "app"}
BLOCKED = re.compile(r"\bAGPL|Affero|\bSSPL\b|Server Side Public|\bBUSL\b|Business Source|non[- ]?commercial|"
                     r"proprietary|\bcommercial license\b", re.I)
GPL = re.compile(r"\bGPL\b|General Public License", re.I)
GPL_OK = re.compile(r"\bLGPL\b|Lesser General|Library General|exception", re.I)
LICENSE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice)[^/]*$", re.I)

# Tesseract (paketçi: UB Mannheim, MSYS2/MinGW-w64 derlemesi) ve OCR için gerçekten yüklenen DLL'ler. Lisanslar üst
# kaynak projelerin lisansıdır (kaynak: ilgili proje ve https://packages.msys2.org); sürümler `tesseract --version`.
TESSERACT_COMPONENTS: Tuple[Tuple[str, str, str], ...] = (
    ("tesseract.exe, libtesseract-5.dll", "Tesseract OCR 5.5.3", "Apache-2.0 (tesseract/LICENSE)"),
    ("libleptonica-6.dll", "Leptonica 1.87.0", "BSD-2-Clause"),
    ("libarchive-13.dll", "libarchive 3.8.8", "BSD-2-Clause"),
    ("libb2-1.dll", "BLAKE2 (libb2)", "CC0-1.0 / OpenSSL / Apache-2.0 (seçmeli)"),
    ("libbrotlicommon.dll, libbrotlidec.dll", "Brotli 1.2.0", "MIT"),
    ("libbz2-1.dll", "bzip2 1.0.8", "bzip2-1.0.6 (BSD benzeri)"),
    ("libcurl-4.dll", "curl 8.21.0", "curl (MIT benzeri)"),
    ("libdeflate.dll", "libdeflate", "MIT"),
    ("libexpat-1.dll", "Expat 2.8.2", "MIT"),
    ("libgcc_s_seh-1.dll, libstdc++-6.dll", "GCC çalışma zamanı", "GPL-3.0 + GCC Runtime Library Exception"),
    ("libgif-7.dll", "giflib 5.2.2", "MIT"),
    ("libiconv-2.dll", "GNU libiconv", "LGPL-2.1-or-later"),
    ("libidn2-0.dll", "GNU libidn2 2.3.8", "LGPL-3.0-or-later / GPL-2.0-or-later (seçmeli)"),
    ("libintl-8.dll", "GNU gettext çalışma zamanı", "LGPL-2.1-or-later"),
    ("libjbig-0.dll", "JBIG-KIT (libtiff kullanır)", "GPL-2.0-or-later"),
    ("libjpeg-8.dll", "libjpeg-turbo 3.1.3", "IJG + BSD-3-Clause + Zlib"),
    ("libLerc.dll", "LERC", "Apache-2.0"),
    ("liblz4.dll", "LZ4 1.10.0", "BSD-2-Clause"),
    ("liblzma-5.dll", "XZ Utils 5.8.3", "0BSD"),
    ("libopenjp2-7.dll", "OpenJPEG 2.5.4", "BSD-2-Clause"),
    ("libpng16-16.dll", "libpng 1.6.58", "libpng-2.0"),
    ("libpsl-5.dll", "libpsl 0.21.5", "MIT"),
    ("libsharpyuv-0.dll, libwebp-7.dll, libwebpmux-3.dll", "libwebp 1.6.0", "BSD-3-Clause"),
    ("libssh2-1.dll", "libssh2 1.11.1", "BSD-3-Clause"),
    ("libtiff-6.dll", "libtiff 4.7.2", "libtiff (BSD benzeri)"),
    ("libunistring-5.dll", "GNU libunistring", "LGPL-3.0-or-later / GPL-2.0-or-later (seçmeli)"),
    ("libwinpthread-1.dll", "MinGW-w64 winpthreads", "MIT / BSD-3-Clause"),
    ("libzstd.dll", "Zstandard 1.5.7", "BSD-3-Clause / GPL-2.0 (seçmeli)"),
    ("zlib1.dll", "zlib 1.3.1", "Zlib"),
)

MODELS = (
    ("models/paraphrase-multilingual-MiniLM-L12-v2-onnx",
     "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (ONNX'e çevrildi)", "Apache-2.0 (model kartı)"),
    ("models/mufettis-reranker-ft1-onnx",
     "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 üzerine bu projede ince ayar (ONNX'e çevrildi)",
     "Apache-2.0 (model kartı). Not: temel model mMARCO ile eğitildi (kartı Apache-2.0); mMARCO, kullanım şartları "
     "ticari olmayan araştırmayla sınırlı MS MARCO'nun çevirisidir. Ticari satış düşünülürse ayrıca değerlendirilmeli."),
)


def license_label(meta) -> str:
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    text = (meta.get("License") or "").strip()
    first = text.splitlines()[0][:120] if text else ""
    classifiers = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    return "; ".join(x for x in [first] + classifiers if x) or "(dağıtımda lisans adı yok; metin aşağıda)"


def problem(label: str, texts: Iterable[str] = ()) -> Optional[str]:
    """Lisansta derlemeyi durduracak bir şey varsa nedeni; yoksa None. Lisans adı ve metinlerin başı taranır."""
    head = " ".join([label] + [t[:3000] for t in texts])
    m = BLOCKED.search(head)
    if m:
        return f"ücretli / ticari kısıtlı ya da kaynak açma yükü yayılan lisans: {m.group(0)!r}"
    if GPL.search(label) and not GPL_OK.search(label):
        return "istisnasız GPL"
    return None


def bundled_top_levels(build_dir: Path, internal: Path) -> set:
    """Pakete giren üst düzey modül adları: PyInstaller TOC'leri (saf modüller) + _internal (eklentiler, veriler)."""
    names = set()
    for toc in list(build_dir.glob("PYZ-*.toc")) + list(build_dir.glob("Analysis-*.toc")):
        text = toc.read_text(encoding="utf-8", errors="replace")
        names.update(m.split(".")[0] for m in re.findall(r"\('([A-Za-z_][\w.]*)',", text))
    if internal.is_dir():
        for p in internal.iterdir():
            names.add(p.name.split(".")[0].split("-")[0])
    return {n for n in names if n and not n.startswith("_") or n in ("_cffi_backend", "_yaml")}


def bundled_distributions(build_dir: Path, internal: Path) -> List[md.Distribution]:
    top = bundled_top_levels(build_dir, internal)
    owners: Dict[str, List[str]] = md.packages_distributions()
    names = sorted({d for t in top for d in owners.get(t, [])} - OWN, key=str.lower)
    out = []
    for name in names:
        try:
            out.append(md.distribution(name))
        except md.PackageNotFoundError:
            continue
    return out


def license_texts(dist: md.Distribution) -> List[Tuple[str, str]]:
    texts = []
    for f in dist.files or []:
        if ".dist-info/" in str(f).replace("\\", "/") and LICENSE_FILE.search(str(f).replace("\\", "/")):
            try:
                texts.append((str(f), dist.locate_file(f).read_text(encoding="utf-8", errors="replace").strip()))
            except OSError:
                continue
    return texts


def write_notices(dist_dir: Path, build_dir: Path, webview2_version: str = "") -> dict:
    """Bildirim dosyasını yazar; denetim sorunu varsa SystemExit. Özet döner."""
    dists = bundled_distributions(build_dir, dist_dir / "_internal")
    problems, parts = [], []
    for d in dists:
        label = license_label(d.metadata)
        texts = license_texts(d)
        why = problem(label, [t for _, t in texts])
        if why:
            problems.append(f"{d.metadata['Name']} {d.version}: {why}")
        body = "\n\n".join(f"--- {name} ---\n{text}" for name, text in texts) or "(dağıtımda lisans dosyası yok)"
        parts.append(f"{d.metadata['Name']} {d.version}\nLisans: {label}\n\n{body}\n")
    if problems:
        raise SystemExit("lisans denetimi: " + "; ".join(problems))
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    sep = "\n" + "=" * 78 + "\n"
    head = (
        "AuditTrace - üçüncü taraf bileşenler ve lisansları / third-party notices\n\n"
        "Bu dosya AuditTrace paketinde dağıtılan üçüncü taraf yazılımların telif bildirimlerini ve lisanslarını "
        "içerir. Hiçbiri ücret gerektirmez. GPL / LGPL bileşenler yalnız ayrı bir program olarak çağrılan Tesseract "
        "klasöründedir (AuditTrace'in kendi koduna bağlanmaz); kaynak kodları ilgili projelerden ve "
        "https://packages.msys2.org adresinden alınabilir.\n")
    sections = [head,
                "1. Python çalışma zamanı\n\nPython " + sys.version.split()[0] + " - PSF License\n\n"
                + (py_license.read_text(encoding="utf-8", errors="replace") if py_license.is_file() else ""),
                "2. Python kütüphaneleri (" + str(len(dists)) + ")\n\n" + sep.join(parts),
                "3. Microsoft Edge WebView2 SDK (Microsoft.Web.WebView2.Core.dll, Microsoft.Web.WebView2.WinForms.dll, "
                "WebView2Loader.dll)\n\nCopyright (C) Microsoft Corporation. BSD-3-Clause tarzı lisans: "
                "https://www.nuget.org/packages/Microsoft.Web.WebView2/License"
                + (f"\nWebView2 çalışma zamanı (bu makinede {webview2_version}) Windows'un parçasıdır; pakete girmez."
                   if webview2_version else ""),
                "4. Tesseract OCR ve yardımcı kütüphaneleri (tesseract/)\n\n"
                + "\n".join(f"- {files}: {what} - {lic}" for files, what, lic in TESSERACT_COMPONENTS)
                + "\n\nKaynak: https://github.com/tesseract-ocr/tesseract ve https://packages.msys2.org "
                  "(GPL / LGPL bileşenlerin kaynak kodu oradan alınabilir).",
                "5. Modeller (models/)\n\n" + "\n".join(f"- {path}: {what} - {lic}" for path, what, lic in MODELS),
                "6. Yazı tipleri\n\nSource Serif 4, Source Sans 3, Source Code Pro (Adobe) - SIL Open Font License 1.1; "
                "Streamlit ile gelir."]
    out = dist_dir / "THIRD_PARTY_NOTICES.txt"
    out.write_text(sep.join(sections) + "\n", encoding="utf-8")
    return {"dosya": str(out), "python_dagitimlari": len(dists), "boyut_kb": round(out.stat().st_size / 1024)}


__all__ = ["MODELS", "TESSERACT_COMPONENTS", "bundled_distributions", "license_label", "problem", "write_notices"]
