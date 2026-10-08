# -*- coding: utf-8 -*-
"""AuditTrace (eski adı Müfettiş AI) paket başlatıcısı (M3.2). Paketin (PyInstaller) giriş noktasıdır; kaynaktan da çalışır.

Kipler:
    launcher.exe              uygulama penceresi: sunucu alt süreçte; arayüz WebView2 varsa kendi penceresinde
                              (pywebview, M17.7a: Microsoft hesabına oturum açmaz), yoksa Edge'in --app penceresinde
                              (sekmesiz, ayrı profil); pencere kapanınca sunucu da kapanır. Edge de yoksa --browser.
    launcher.exe --browser    Streamlit sunucusu + varsayılan tarayıcı (M3.3: temiz Windows'ta varsayılan tarayıcı
                              atanmamış olabilir; "http bağlantısını açamıyoruz")
    launcher.exe --window     yalnız WebView2 penceresi (yedeksiz; M3.3: temiz Windows'ta WebView2 yok, pywebview
                              eski MSHTML motoruna düşüyor ve Streamlit boş sayfa kalıyor)
    launcher.exe --serve P    yalnızca sunucu, P portunda (pencere kipinin alt süreci)
    launcher.exe --measure D  ölçüm (M3.3): sunucunun hazır olma süresi, uygulama içe aktarma, örnek belgelerle analiz
                              süreleri ve bellek; sonuç D/olcum.json (tıklamasız, ör. Windows Sandbox içinde)

Yerler (M17.2): program ve modeller kurulum klasöründe (salt okunur olabilir); yazılan her şey kullanıcı klasöründe
(%LOCALAPPDATA%/AuditTrace): veri (data/ -> MUFETTIS_DATA_DIR; kurulum programı ilk kurulumda buraya koyar, güncellemede
dokunmaz), günlükler (logs/) ve Edge profili. Kullanıcı klasöründe veri yoksa kurulum klasörünün yanındaki data/
kullanılır (M3 deneme paketi düzeni). models/ -> MUFETTIS_MODELS_DIR, hf/ -> HF_HOME (varsa). Kaynaktan çalışırken
kullanıcı klasörü %TEMP%/mufettis_launcher'dır (repo kirlenmesin).
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


def user_dir(frozen: bool, env) -> Path:
    """Yazılabilir kullanıcı klasörü: pakette %LOCALAPPDATA%/AuditTrace (M3.3: kurulum klasörü salt okunur olabilir)."""
    if frozen and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "AuditTrace"
    return Path(env.get("TEMP", "/tmp")) / "mufettis_launcher"


def data_dir(home: Path, user: Path):
    """Uygulama verisi: kullanıcı klasöründeki data/ (kurulumun yeri), yoksa kurulum klasörünün yanındaki data/."""
    for path in (user / "data", home / "data"):
        if path.is_dir():
            return path
    return None


USER = user_dir(FROZEN, os.environ)
LOG_DIR = USER / "logs" if FROZEN else USER
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
    data = data_dir(HOME, USER)
    if data is not None:
        os.environ.setdefault("MUFETTIS_DATA_DIR", str(data))
    for folder, var in (("models", "MUFETTIS_MODELS_DIR"), ("hf", "HF_HOME")):
        if (HOME / folder).is_dir():
            os.environ.setdefault(var, str(HOME / folder))
    if (HOME / "tesseract" / "tesseract.exe").is_file():  # M17.3: taranmış PDF'ler için pakete gömülü OCR
        os.environ.setdefault("MUFETTIS_TESSERACT_CMD", str(HOME / "tesseract" / "tesseract.exe"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "2")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float = 180.0, proc=None) -> bool:
    """Port açılana dek bekler; `proc` (sunucu alt süreci) erken kapanırsa beklemeden False (M17.4)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        if proc is not None and proc.poll() is not None:
            log(f"sunucu alt süreci kapandı (çıkış {proc.returncode})")
            return False
        time.sleep(0.2)
    return False


# --- M17.4: süreç güvenliği, tek örnek ve hata kutusu (yalnız Windows; başka sistemde etkisiz) ---------------------

def kill_on_close_job():
    """Windows Job Object: başlatıcı kapanınca (çökse ya da Görev Yöneticisi'nden kapatılsa da) içine konan
    süreçler de kapanır; sunucu öksüz kalıp portu ve belleği tutmaz. Windows dışında None."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9: genişletilmiş bilgi
        k32.CloseHandle(job)
        return None
    return job


def add_to_job(job, proc) -> bool:
    if not job:
        return False
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    return bool(k32.AssignProcessToJobObject(job, int(proc._handle)))


INSTANCE_NAME = "Local\\AuditTrace.tek_ornek"
_INSTANCE_MUTEX = None  # süreç boyunca açık kalmalı (kapanınca yeni açılış serbest)


def first_instance(name: str = INSTANCE_NAME) -> bool:
    """İlk örnek mi? Değilse açık AuditTrace penceresi öne getirilir (ikinci çift tıklama yeni sunucu açmaz)."""
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    _INSTANCE_MUTEX = k32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return True
    bring_to_front("AuditTrace")
    return False


def bring_to_front(title_prefix: str) -> bool:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.startswith(title_prefix):
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, 0)
    if found:
        user32.ShowWindow(found[0], 9)  # SW_RESTORE
        user32.SetForegroundWindow(found[0])
    return bool(found)


def error_text(reason: str) -> str:
    return (f"AuditTrace açılamadı: {reason}.\n\nAyrıntılar şu günlük dosyasında:\n{LOG}\n\n"
            "Bilgisayarı yeniden başlatıp tekrar deneyin. Sorun sürerse bu dosyayı paylaşın.")


def error_box(reason: str) -> None:
    """Kullanıcıya Türkçe hata kutusu (konsolsuz pakette hata başka türlü görünmez); günlüğe de yazılır."""
    log(f"HATA: {reason}")
    if os.name == "nt" and not os.environ.get("AUDITTRACE_NO_DIALOG"):
        import ctypes

        ctypes.WinDLL("user32").MessageBoxW(None, error_text(reason), "AuditTrace", 0x10 | 0x40000)  # hata, en üstte


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
        "client_toolbarMode": "minimal",  # M15.9: Deploy düğmesi görünmez
    }
    from mufettis.ui.theme import theme_flags

    flags.update(theme_flags())  # M16.3: AuditTrace teması (kaynaktan çalıştırmada .streamlit/config.toml)
    bootstrap.load_config_options(flag_options=flags)
    bootstrap.run(str(BUNDLE / "app.py"), False, [], flags)


_JOB = None


def start_server() -> tuple:
    """Sunucuyu alt süreçte başlatır: (süreç, port). Süreç başlatıcının Job Object'ine bağlanır (M17.4)."""
    global _JOB
    port = free_port()
    cmd = [sys.executable, "--serve", str(port)] if FROZEN else [sys.executable, __file__, "--serve", str(port)]
    proc = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _JOB = _JOB or kill_on_close_job()
    if not add_to_job(_JOB, proc):
        log("sunucu Job Object'e bağlanamadı (başlatıcı çökerse elle kapatılması gerekebilir)")
    return proc, port


# M17.7a: pywebview pencere metinleri (dosya kaydetme penceresi vb.) Türkçe
WEBVIEW_TEXTS = {"global.quitConfirmation": "Çıkmak istiyor musunuz?", "global.ok": "Tamam", "global.quit": "Çık",
                 "global.cancel": "Vazgeç", "global.saveFile": "Dosyayı kaydet",
                 "windows.fileFilter.allFiles": "Tüm dosyalar", "windows.fileFilter.otherFiles": "Diğer dosya türleri"}


def window() -> int:
    """Arayüz WebView2 penceresinde (pywebview). Edge'in --app penceresinden farkı: Windows'taki Microsoft hesabıyla
    kendiliğinden oturum açmaz, eşitleme ve çeviri sorusu yok (M17.7). Metin seçilebilir; Word indirme "kaydet"
    penceresiyle; tarayıcı verisi kullanıcı klasöründe (webview/)."""
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    server, port = start_server()
    try:
        if not wait_for_port(port, proc=server):
            error_box("uygulama sunucusu başlamadı")
            return 1
        log("sunucu hazır; WebView2 penceresi açılıyor")
        webview.create_window("AuditTrace", f"http://127.0.0.1:{port}", width=1280, height=860, min_size=(1024, 640),
                              background_color="#F7F5F0", text_select=True, zoomable=True)
        # Gizli kipte pywebview %TEMP%'te her açılışta yeni klasör açıyor ve her zaman silemiyor; bu yüzden uygulamaya
        # ait kalıcı klasör (kaldırmada silinir). WebView2 burada da Microsoft hesabına oturum açmaz (denendi).
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(USER / "webview"),
                      localization=WEBVIEW_TEXTS)
        log("pencere kapandı")
        return 0
    finally:
        server.terminate()


def app_window() -> int:
    """Varsayılan kip (M17.7a): WebView2 çalışma zamanı varsa kendi pencere, yoksa ya da pencere açılamazsa Edge --app
    (M3.3: temiz Windows'ta WebView2 olmayabilir; pywebview o zaman boş sayfa gösteren eski motora düşer)."""
    version = _webview2_version()
    if version:
        log(f"WebView2 {version}")
        try:
            return window()
        except Exception:
            import traceback

            log("WebView2 penceresi açılamadı; Edge penceresine geçiliyor\n" + traceback.format_exc())
    else:
        log("WebView2 yok; Edge penceresi")
    return edge_app()


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
        if not wait_for_port(port, proc=server):
            error_box("uygulama sunucusu başlamadı")
            return 1
        log("sunucu hazır; Edge uygulama penceresi açılıyor")
        window_proc = subprocess.Popen([
            str(edge), f"--app=http://127.0.0.1:{port}", f"--user-data-dir={USER / 'edge_profile'}",
            "--no-first-run", "--no-default-browser-check", "--window-size=1280,860"])
        add_to_job(_JOB, window_proc)  # M17.4: başlatıcı ölürse sunucusuz kalan pencere de kapanır (ayrı profil)
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

    # Makine geneli kurulum WOW6432Node altında, kullanıcı başına kurulum HKCU'da (M17.7a); boş ya da 0.0.0.0 = yok
    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{client}"),
                      (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{client}"),
                      (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\WOW6432Node\{client}")):
        try:
            with winreg.OpenKey(hive, key) as k:
                version = str(winreg.QueryValueEx(k, "pv")[0])
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return version
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


def selftest() -> dict:
    """M17.3 öz-test: paketin kendi modelleri (ONNX), gömülü OCR ve örnek kredi dosyası kontrolü gerçekten çalışıyor mu.
    Kaynaktan çalıştırılınca da aynı sonucu vermeli (kural sonuçları karşılaştırılır)."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    from mufettis.config import EMBEDDING_MODEL, RERANKER_MODELS_ROOT
    from mufettis.extraction import ocr
    from mufettis.rag.onnx_backend import onnx_dir
    from mufettis.rag.reranker import load_reranker
    from mufettis.rules.catalog import load_catalog
    from mufettis.rules.credit_file import CreditFile, evaluate_file, load_document
    from mufettis.ui.analysis_view import SAMPLE_DIR, load_uploaded_document

    out = {"modeller_klasoru": str(RERANKER_MODELS_ROOT)}
    embed = onnx_dir(EMBEDDING_MODEL)
    out["gomme_onnx"] = str(embed) if embed else None
    if embed:
        from mufettis.rag.onnx_backend import OnnxEmbedder

        vec = OnnxEmbedder(embed).encode(["kredi teminatı ipotek"])
        out["gomme_boyut"] = int(vec.shape[1])
    reranker = load_reranker()
    out["yeniden_siralayici"] = getattr(reranker, "backend", None)
    if reranker is not None:
        out["yeniden_siralayici_esik"] = getattr(reranker, "min_score", None)

    out["ocr_komutu"] = ocr.tesseract_cmd()
    img = Image.new("L", (1400, 300), 255)
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((40, 100), "IPOTEK BELGESI 2026", fill=0, font=font)
    pdf = io.BytesIO()
    img.save(pdf, format="PDF", resolution=150)
    pages = ocr.ocr_pdf_pages(pdf.getvalue())
    out["ocr_metni"] = " ".join(pages).strip()

    documents = []
    for path in sorted(SAMPLE_DIR.glob("*.docx")):
        loaded = load_uploaded_document(path.name, path.read_bytes())
        documents.append(load_document(path.name, loaded.text))
    out["ornek_belge_turleri"] = {d.name: d.kind for d in documents}
    results = evaluate_file(load_catalog(), CreditFile(documents))
    out["ornek_kural_sonuclari"] = sorted(f"{r.result.rule_id}={r.result.status}" for r in results)
    return out


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
    if args[:1] == ["--selftest"]:
        import json

        try:
            report = selftest()
        except Exception as exc:  # rapor her durumda yazılsın
            import traceback

            report = {"hata": repr(exc), "iz": traceback.format_exc()}
        Path(args[1]).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return 1 if "hata" in report else 0
    if not first_instance():
        log("ikinci açılış: açık pencere öne getirildi")
        return 0
    try:
        return app_window()
    except Exception as exc:  # konsolsuz pakette görünmeyen hatalar kullanıcıya söylensin
        import traceback

        log(traceback.format_exc())
        error_box(f"beklenmeyen hata ({type(exc).__name__})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
