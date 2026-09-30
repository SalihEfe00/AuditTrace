# -*- coding: utf-8 -*-
"""Müfettiş AI deneme paketi başlatıcısı (M3.2). Paketin (PyInstaller) giriş noktasıdır; kaynaktan da çalışır.

Kipler:
    launcher.exe              uygulama penceresi: sunucu alt süreçte, arayüz Edge'in --app penceresinde (sekmesiz,
                              ayrı profil); pencere kapanınca sunucu da kapanır. Edge yoksa --browser'a düşer.
    launcher.exe --browser    Streamlit sunucusu + varsayılan tarayıcı (M3.3: temiz Windows'ta varsayılan tarayıcı
                              atanmamış olabilir; "http bağlantısını açamıyoruz")
    launcher.exe --window     pywebview penceresi (Edge WebView2; M3.3: temiz Windows'ta WebView2 yok, pywebview eski
                              MSHTML motoruna düşüyor ve Streamlit boş sayfa kalıyor)
    launcher.exe --serve P    yalnızca sunucu, P portunda (pencere kipinin alt süreci)
    launcher.exe --measure D  ölçüm (M3.3): sunucunun hazır olma süresi, uygulama içe aktarma, örnek belgelerle analiz
                              süreleri ve bellek; sonuç D/olcum.json (tıklamasız, ör. Windows Sandbox içinde)

Veri ve modeller paketin yanındaki klasörlerden okunur (varsa): data/ -> MUFETTIS_DATA_DIR,
models/ -> MUFETTIS_MODELS_DIR, hf/ -> HF_HOME. Yoksa ortam değişkenleri / varsayılanlar geçerlidir.
Açılış süreleri launcher.log'a yazılır (pakette exe'nin yanında, kaynakta %TEMP%/mufettis_launcher).
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
if not FROZEN and str(HOME) not in sys.path:
    sys.path.insert(0, str(HOME))  # kaynaktan: mufettis paketi repo kökünde
# Günlükler: pakette exe'nin yanında; kaynaktan çalışırken repo kirlenmesin diye geçici klasörde.
LOG_DIR = HOME if FROZEN else Path(os.environ.get("TEMP", "/tmp")) / "mufettis_launcher"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG = LOG_DIR / "launcher.log"


def log(msg: str) -> None:
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%H:%M:%S')} +{time.perf_counter() - T0:6.2f}s pid {os.getpid()} {msg}\n")


def capture_output() -> None:
    """Konsolsuz pakette sys.stdout / sys.stderr None'dır; onlara yazan kütüphaneler (ör. tqdm) çöker ve hatalar
    görünmez. Çıktı paket yanındaki launcher_output.log'a gider."""
    if sys.stdout is None or sys.stderr is None:
        stream = open(LOG_DIR / "launcher_output.log", "a", encoding="utf-8", buffering=1)
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


def start_server() -> tuple:
    """Sunucuyu alt süreçte başlatır: (süreç, port)."""
    port = free_port()
    cmd = [sys.executable, "--serve", str(port)] if FROZEN else [sys.executable, __file__, "--serve", str(port)]
    return subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)), port


def window() -> int:
    import webview

    server, port = start_server()
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


def edge_path():
    """Windows 10/11'de hazır gelen Edge (kurulum yeri: sistem ya da kullanıcı)."""
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base and (Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe").is_file():
            return Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    return None


def edge_app() -> int:
    """Arayüz Edge --app penceresinde. Ayrı profil klasörü Edge'i ayrı süreç olarak açar; böylece pencere
    kapanınca beklemeden çıkılır ve sunucu kapatılır (kullanıcının açık Edge oturumuna bağlanmaz)."""
    edge = edge_path()
    if edge is None:
        log("Edge bulunamadı; varsayılan tarayıcı kipine geçiliyor")
        serve(free_port(), open_browser=True)
        return 0
    server, port = start_server()
    try:
        if not wait_for_port(port):
            log("sunucu açılmadı")
            return 1
        log("sunucu hazır; Edge uygulama penceresi açılıyor")
        window_proc = subprocess.Popen([
            str(edge), f"--app=http://127.0.0.1:{port}", f"--user-data-dir={LOG_DIR / 'edge_profile'}",
            "--no-first-run", "--no-default-browser-check", "--window-size=1280,860"])
        window_proc.wait()
        log("pencere kapandı")
        return 0
    finally:
        server.terminate()


def _memory_mb() -> dict:
    import ctypes
    import ctypes.wintypes as wt

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("faults", wt.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("peak_ws", "ws", "a", "b", "c", "d", "e", "f", "private")]

    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(Counters), wt.DWORD]
    c = Counters(); c.cb = ctypes.sizeof(Counters)
    k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return {"ws_mb": round(c.ws / 2**20), "peak_ws_mb": round(c.peak_ws / 2**20), "private_mb": round(c.private / 2**20)}


def _webview2_version() -> str:
    import winreg

    key = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, key) as k:
                return str(winreg.QueryValueEx(k, "pv")[0])
        except OSError:
            continue
    return ""


def measure(out_dir: Path) -> int:
    """Tıklamasız ölçüm: sunucu alt süreçte hazır olana dek süre, sonra bu süreçte analizler."""
    import json
    import platform
    import shutil

    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [{"asama": "başlatıcı", "sn": round(time.perf_counter() - T0, 2), **_memory_mb()}]
    result = {"makine": platform.node(), "windows": platform.platform(), "cpu_sayisi": os.cpu_count(),
              "webview2": _webview2_version(), "asamalar": rows}

    def mark(stage: str, started: float, **extra) -> None:
        rows.append({"asama": stage, "sn": round(time.perf_counter() - started, 2), **_memory_mb(), **extra})
        log(f"ölçüm {rows[-1]}")

    port = free_port()
    t = time.perf_counter()
    cmd = [sys.executable, "--serve", str(port)] if FROZEN else [sys.executable, __file__, "--serve", str(port)]
    server = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        mark("sunucu hazır (alt süreç)", t, hazir=wait_for_port(port))
    finally:
        server.terminate()
    try:
        t = time.perf_counter()
        from mufettis.analysis.analysis_presenter import AnalysisPresenter
        from mufettis.rag.engine import LocalRAG
        from mufettis.ui.analysis_view import SAMPLE_COMPLAINT_TEXT, SAMPLE_INTERNAL_TEXT
        mark("uygulama içe aktarma", t)
        rag, presenter = LocalRAG(allow_download=False), AnalysisPresenter()
        for stage, text in (("analiz 1: şikâyet (soğuk)", SAMPLE_COMPLAINT_TEXT),
                            ("analiz 2: şikâyet (ılık)", SAMPLE_COMPLAINT_TEXT),
                            ("analiz 3: iç denetim notu", SAMPLE_INTERNAL_TEXT)):
            t = time.perf_counter()
            r = presenter.execute_analysis(text=text, rag_client=rag)
            mark(stage, t, basarili=bool(r.success), hata=str(r.error_message or ""))
        result["tamam"] = all(row.get("basarili", True) for row in rows)
    except Exception as exc:  # ölçüm her durumda yazılsın
        result["tamam"], result["hata"] = False, repr(exc)
    result["toplam_sn"] = round(time.perf_counter() - T0, 2)
    (out_dir / "olcum.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for name in ("launcher.log", "launcher_output.log"):
        if (LOG_DIR / name).is_file():
            shutil.copy2(LOG_DIR / name, out_dir / name)
    return 0 if result["tamam"] else 1


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
    if args[:1] == ["--browser"]:
        serve(free_port(), open_browser=True)
        return 0
    if args[:1] == ["--measure"]:
        return measure(Path(args[1]))
    return edge_app()


if __name__ == "__main__":
    raise SystemExit(main())
