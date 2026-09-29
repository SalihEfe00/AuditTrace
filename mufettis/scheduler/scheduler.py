# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
import threading
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from mufettis.common.utils import (
    atomic_write_json,
    canonical_source_id,
    check_disk_health,
    now_iso,
    safe_json,
)
from mufettis.config import (
    SCHEDULER_HOUR,
    SCHEDULER_LOG_FILE,
    SCHEDULER_MINUTE,
    SCHEDULER_STATE_FILE,
    SCHEDULER_TIMEZONE,
    WEB_SOURCE_MAX_DEPTH,
    WEB_SOURCE_MAX_PAGES,
)
from mufettis.audit.service import append_audit
from mufettis.harvesting.resmi_gazete import (
    sync_resmi_gazete_for_date,
    sync_web_source,
)
from mufettis.rag.engine import LocalRAG

class BackgroundUpdateScheduler:
    """Uygulama açıkken günlük mevzuat senkronizasyonunu arka planda çalıştırır.

    Varsayılan saat 00:05 Europe/Istanbul'dur. Süreç kapanırsa scheduler durur;
    gerçek OS-seviyesi sürekli çalışma için aynı `--update-now` komutu Windows
    Task Scheduler/cron üzerinden ayrıca çalıştırılabilir.
    """
    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self, sources: Optional[List[str]] = None, use_browser_for_bddk: bool = True):
        self.sources = list(sources) if sources is not None else ["bddk_official", "resmi_gazete"]
        self.use_browser_for_bddk = use_browser_for_bddk
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.running = False

    @classmethod
    def get_or_create(cls, sources: Optional[List[str]] = None, use_browser_for_bddk: bool = True):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(sources=sources, use_browser_for_bddk=use_browser_for_bddk)
            else:
                if sources is not None:
                    cls._instance.sources = list(sources)
                cls._instance.use_browser_for_bddk = use_browser_for_bddk
            return cls._instance

    def _now_local(self) -> dt.datetime:
        return dt.datetime.now(ZoneInfo(SCHEDULER_TIMEZONE))

    def seconds_until_next_run(self) -> float:
        now = self._now_local()
        next_run = now.replace(hour=SCHEDULER_HOUR, minute=SCHEDULER_MINUTE, second=0, microsecond=0)
        if next_run <= now:
            next_run += dt.timedelta(days=1)
        return max(1.0, (next_run - now).total_seconds())

    def start(self) -> bool:
        if self.running and self.thread and self.thread.is_alive():
            return False
        self.stop_event.clear()
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="MufettisAI-MevzuatScheduler", daemon=True)
        self.thread.start()
        atomic_write_json(SCHEDULER_STATE_FILE, {"running": True, "started_at": now_iso(), "next_run_seconds": self.seconds_until_next_run(), "sources": self.sources})
        return True

    def stop(self) -> None:
        self.running = False
        self.stop_event.set()
        atomic_write_json(SCHEDULER_STATE_FILE, {"running": False, "stopped_at": now_iso(), "sources": self.sources})

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            wait_seconds = min(self.seconds_until_next_run(), 300.0)
            self.stop_event.wait(wait_seconds)
            if self.stop_event.is_set():
                break
            if self.seconds_until_next_run() > 30:
                continue
            try:
                result = run_automatic_update(self.sources, use_browser_for_bddk=self.use_browser_for_bddk)
                atomic_write_json(SCHEDULER_STATE_FILE, {"running": True, "last_run": now_iso(), "last_result": result, "next_run_seconds": self.seconds_until_next_run(), "sources": self.sources})
            except Exception as exc:
                err = {"timestamp": now_iso(), "error": str(exc)}
                atomic_write_json(SCHEDULER_STATE_FILE, {"running": True, "last_run": now_iso(), "last_error": str(exc), "next_run_seconds": self.seconds_until_next_run(), "sources": self.sources})
                with SCHEDULER_LOG_FILE.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(err, ensure_ascii=False) + "\n")
def run_automatic_update(sources: Optional[List[str]] = None, use_browser_for_bddk: bool = True) -> Dict[str, Any]:
    sources = [canonical_source_id(x) for x in sources] if sources is not None else ["bddk_official", "resmi_gazete"]
    health = check_disk_health()
    if not health.get("ok"):
        raise RuntimeError(f"Disk sağlık kontrolü başarısız: {health.get('error')}")
    rag = LocalRAG(allow_download=False)
    started = now_iso()
    results = []
    if "bddk_official" in sources:
        try:
            results.append(sync_web_source("bddk_official", rag, use_browser=use_browser_for_bddk, max_depth=WEB_SOURCE_MAX_DEPTH, max_pages=WEB_SOURCE_MAX_PAGES))
        except Exception as exc:
            results.append({"source": "bddk_official", "error": str(exc)})
    if "resmi_gazete" in sources:
        # Gece çalışırken bugün + dün taranır; tekrarlar hash ile elenir.
        local_today = dt.datetime.now(ZoneInfo(SCHEDULER_TIMEZONE)).date()
        for d in [local_today, local_today - dt.timedelta(days=1)]:
            try:
                results.append(sync_resmi_gazete_for_date(d, rag, filter_keywords=False, max_docs=120))
            except Exception as exc:
                results.append({"source": "resmi_gazete", "date": d.isoformat(), "error": str(exc)})
    result = {"started_at": started, "finished_at": now_iso(), "sources": sources, "results": results}
    with SCHEDULER_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(safe_json(result), ensure_ascii=False) + "\n")
    append_audit("automatic_regulation_update_completed", result)
    return result


__all__ = [
    "BackgroundUpdateScheduler",
    "run_automatic_update",
]
