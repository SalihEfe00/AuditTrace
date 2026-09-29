# -*- coding: utf-8 -*-
"""Müfettiş AI deneme paketi başlatıcısı (M3.2). Paketin (PyInstaller) giriş noktasıdır; kaynaktan da çalışır.

Kipler:
    launcher.exe              tarayıcı: Streamlit sunucusu + varsayılan tarayıcı
    launcher.exe --window     masaüstü penceresi: sunucu alt süreçte, arayüz pywebview (Edge WebView2) penceresinde
    launcher.exe --serve P    yalnızca sunucu, P portunda (pencere kipinin alt süreci)

Veri ve modeller paketin yanındaki klasörlerden okunur (varsa): data/ -> MUFETTIS_DATA_DIR,
models/ -> MUFETTIS_MODELS_DIR, hf/ -> HF_HOME. Yoksa ortam değişkenleri / varsayılanlar geçerlidir.
Açılış süreleri launcher.log'a yazılır (ölçüm için).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

T0 = time.perf_counter()
FROZEN = getattr(sys, "frozen", False)
HOME = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
BUNDLE = Path(getattr(sys, "_MEIPASS", HOME))
LOG = HOME / "launcher.log"


def log(msg: str) -> None:
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%H:%M:%S')} +{time.perf_counter() - T0:6.2f}s pid {os.getpid()} {msg}\n")


def capture_output() -> None:
    """Konsolsuz pakette sys.stdout / sys.stderr None'dır; onlara yazan kütüphaneler (ör. tqdm) çöker ve hatalar
    görünmez. Çıktı paket yanındaki launcher_output.log'a gider."""
    if sys.stdout is None or sys.stderr is None:
        stream = open(HOME / "launcher_output.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream


def environment() -> None:
    for folder, var in (("data", "MUFETTIS_DATA_DIR"), ("models", "MUFETTIS_MODELS_DIR"), ("hf", "HF_HOME")):
        if (HOME / folder).is_dir():
            os.environ.setdefault(var, str(HOME / folder))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "2")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float = 180.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def serve(port: int, open_browser: bool) -> None:
    from streamlit.web import bootstrap

    log(f"sunucu başlıyor, port {port}")
    # Anahtarlar CLI bayrak biçiminde (bölüm_ayar); `streamlit run` gibi önce yüklenmeleri gerekir.
    # Yalnızca 127.0.0.1: belgeler yerel ağdaki başka makinelerden açılamasın.
    flags = {
        "server_port": port,
        "server_address": "127.0.0.1",
        "server_headless": not open_browser,
        "server_fileWatcherType": "none",
        "server_runOnSave": False,
        "global_developmentMode": False,
        "browser_gatherUsageStats": False,
        "browser_serverAddress": "127.0.0.1",
    }
    bootstrap.load_config_options(flag_options=flags)
    bootstrap.run(str(BUNDLE / "app.py"), False, [], flags)


def window() -> int:
    import webview

    port = free_port()
    cmd = [sys.executable, "--serve", str(port)] if FROZEN else [sys.executable, __file__, "--serve", str(port)]
    server = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        if not wait_for_port(port):
            log("sunucu açılmadı")
            return 1
        log("sunucu hazır; pencere açılıyor")
        webview.create_window("Müfettiş AI", f"http://127.0.0.1:{port}", width=1280, height=860)
        webview.start()
        log("pencere kapandı")
        return 0
    finally:
        server.terminate()


def main() -> int:
    capture_output()
    environment()
    args = sys.argv[1:]
    log(f"başlatıldı {args}")
    if os.environ.get("MUFETTIS_TRACE_AFTER"):
        # Teşhis: verilen saniye sonra tüm iş parçacıklarının yığınını çıktı günlüğüne yaz.
        import faulthandler

        faulthandler.dump_traceback_later(float(os.environ["MUFETTIS_TRACE_AFTER"]), repeat=False, file=sys.stderr)
    if args[:1] == ["--serve"]:
        serve(int(args[1]), open_browser=False)
        return 0
    if args[:1] == ["--window"]:
        return window()
    serve(free_port(), open_browser=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
