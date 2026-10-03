# -*- coding: utf-8 -*-
"""Korpus güncelliği (M6.16): mevzuatın en son ne zaman resmî kaynaktan denetlendiği / yüklendiği.

Son güncelleme = `legislation_downloads/downloads.json` içindeki en geç indirme (`fetched_at`) ile
`import_runs.jsonl` içindeki en geç yükleme (`at`) zamanlarının büyüğü. Ağ kullanmaz; dosya yoksa None.
STALE_DAYS günden eskiyse arayüz uyarı gösterir: sonuçlar o tarihe kadarki mevzuata göredir.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

STALE_DAYS = 30
_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse(value) -> Optional[dt.datetime]:
    try:
        return dt.datetime.strptime(str(value)[:19], _FORMAT)
    except (TypeError, ValueError):
        return None


def last_corpus_update(data_dir: Path) -> Optional[dt.datetime]:
    folder = Path(data_dir) / "legislation_downloads"
    stamps = []
    try:
        records = json.loads((folder / "downloads.json").read_text(encoding="utf-8"))
        stamps += [_parse(r.get("fetched_at")) for r in records.values() if isinstance(r, dict)]
    except (OSError, ValueError):
        pass
    try:
        with open(folder / "import_runs.jsonl", encoding="utf-8") as fh:
            for line in fh:
                try:
                    stamps.append(_parse(json.loads(line).get("at")))
                except ValueError:
                    continue
    except OSError:
        pass
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def staleness_message(last: Optional[dt.datetime], now: Optional[dt.datetime] = None,
                      stale_days: int = STALE_DAYS) -> Optional[str]:
    """Uyarı metni; korpus güncelse None."""
    if last is None:
        return "Mevzuat korpusunun güncelleme tarihi bilinmiyor; sonuçların hangi tarihteki mevzuata göre olduğu belirsiz."
    age = ((now or dt.datetime.now()) - last).days
    if age <= stale_days:
        return None
    return (f"Mevzuat korpusu {age} gündür güncellenmedi (son: {last:%d.%m.%Y}). Sonuçlar bu tarihe kadarki mevzuata "
            f"göredir; kenar çubuğundaki 'Mevzuatı güncelle' düğmesi resmî kaynaklardan günceller (internet gerekir).")


LOCK_NAME = "update_legislation.lock"
LOG_NAME = "update_legislation.log"


def update_command(project_root: Path, production: bool, days: int = 7) -> List[str]:
    """Arayüz düğmesinin çalıştırdığı komut (M6.16 c): M6.8 aracı, son `days` günün Resmî Gazetesi."""
    cmd = [sys.executable, str(Path(project_root) / "scripts" / "update_legislation.py"), "--days", str(days)]
    return cmd + (["--production"] if production else [])


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def running_update(data_dir: Path) -> Optional[int]:
    """Süren güncellemenin süreç numarası; yoksa (ya da kilit bayatsa) None."""
    try:
        pid = int((Path(data_dir) / LOCK_NAME).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def start_update(data_dir: Path, project_root: Path, production: bool, days: int = 7,
                 popen: Callable = subprocess.Popen) -> Tuple[bool, str]:
    """Güncellemeyi arka planda, düşük öncelikle başlatır; çevrimdışı kullanım bunu beklemez.
    Aynı anda ikinci güncelleme başlatılmaz (kilit dosyası). Dönüş: (başladı mı, mesaj)."""
    data_dir = Path(data_dir)
    pid = running_update(data_dir)
    if pid:
        return False, f"Güncelleme zaten sürüyor (süreç {pid}); kayıt: {data_dir / LOG_NAME}"
    flags = 0x00000040 | 0x08000000 if os.name == "nt" else 0  # IDLE_PRIORITY_CLASS | CREATE_NO_WINDOW
    log = open(data_dir / LOG_NAME, "a", encoding="utf-8")
    try:
        proc = popen(update_command(project_root, production, days), stdout=log, stderr=subprocess.STDOUT,
                     cwd=str(project_root), creationflags=flags)
    except OSError as exc:
        return False, f"Güncelleme başlatılamadı: {exc}"
    finally:
        log.close()
    (data_dir / LOCK_NAME).write_text(str(proc.pid), encoding="utf-8")
    return True, f"Güncelleme başladı (süreç {proc.pid}); internet gerekir. Kayıt: {data_dir / LOG_NAME}"


__all__ = ["last_corpus_update", "staleness_message", "STALE_DAYS", "update_command", "start_update",
           "running_update", "LOCK_NAME", "LOG_NAME"]
