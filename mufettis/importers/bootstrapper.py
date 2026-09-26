# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from zoneinfo import ZoneInfo

from mufettis.common.utils import atomic_write_json, canonical_source_id, load_json, now_iso
from mufettis.config import (
    BOOTSTRAP_BDDK_DEPTH,
    BOOTSTRAP_MAX_BDDK_PAGES,
    BOOTSTRAP_RESMI_DAYS,
    BOOTSTRAP_RESMI_MAX_DOCS_PER_DAY,
    BOOTSTRAP_STATE_FILE,
    SCHEDULER_TIMEZONE,
)
from mufettis.audit.service import append_audit
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper
from mufettis.harvesting.web_harvester import (
    BrowserWebSourceHarvester,
    WebSourceHarvester,
    sync_playwright,
)
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex
from mufettis.rag.maintenance import versioned_ingest_text

class RegulationBootstrapper:
    """İlk çalıştırmada kullanıcıya dosya toplatma işini ortadan kaldıran kurulum motoru.

    Öncelik:
    1) BDDK resmi mevzuat ağacı (güncel çekirdek bankacılık düzenlemeleri)
    2) Resmî Gazete son N gün (değişiklik/yeni yayın başlangıç snapshot'ı)
    3) İsteğe bağlı mevzuat.org (ikincil/yedek kaynak)

    Ağ erişimi kullanıcı tarafından başlatılan bir işlemdir; uygulama sessizce
    binlerce belge indirmez. Başarılı kayıtlar mevcut hash/version mekanizmasına
    bağlanır ve tekrar çalıştırmada atlanır.
    """
    def __init__(self, rag: Optional[LocalRAG], index: Optional[IngestIndex] = None):
        self.rag = rag
        self.index = index or IngestIndex()
        self.state = load_json(BOOTSTRAP_STATE_FILE, {
            "version": "1.0",
            "completed": False,
            "completed_at": None,
            "last_run": None,
            "sources": {},
        })
        if not isinstance(self.state, dict):
            self.state = {"version": "1.0", "completed": False, "completed_at": None, "last_run": None, "sources": {}}

    def _save(self) -> None:
        atomic_write_json(BOOTSTRAP_STATE_FILE, self.state)

    def status(self) -> Dict[str, Any]:
        return {
            "completed": bool(self.state.get("completed")),
            "completed_at": self.state.get("completed_at"),
            "last_run": self.state.get("last_run"),
            "sources": self.state.get("sources", {}),
        }

    @staticmethod
    def _candidate_is_relevant(source_id: str, candidate: Dict[str, Any]) -> bool:
        source_id = canonical_source_id(source_id)
        url = str(candidate.get("url", ""))
        title = str(candidate.get("title", ""))
        path = urlparse(url).path.lower()
        if source_id == "bddk_official":
            return ("/mevzuat/detay/" in path) or path.endswith(".pdf") or "/dokumangetir/" in path
        if source_id == "mevzuat_org":
            return bool(title or url)
        if source_id == "resmi_gazete":
            return True
        return True

    def _ingest_candidates(
        self,
        source_id: str,
        candidates: List[Dict[str, Any]],
        use_browser: bool = False,
        max_items: Optional[int] = None,
    ) -> Dict[str, Any]:
        if self.rag is None:
            raise RuntimeError("Bootstrap için LocalRAG hazır olmalı")

        filtered = [
            c for c in candidates
            if self._candidate_is_relevant(source_id, c)
        ]

        unique: Dict[str, Dict[str, Any]] = {}
        for c in filtered:
            url = str(c.get("url", "")).strip()
            if url:
                unique[url] = c

        work = list(unique.values())
        if max_items is not None:
            work = work[:max_items]

        stats = {
            "found": len(candidates),
            "relevant": len(filtered),
            "selected": len(work),
            "ingested": 0,
            "skipped": 0,
            "errors": 0,
            "versions_changed": 0,
        }

        if use_browser:
            harvester = BrowserWebSourceHarvester(
                source_id,
                index=self.index,
                local_only=False,
            )
            iterator = harvester.fetch_candidates_batch_fast(work)
            for candidate, payload, error_text in iterator:
                if not payload:
                    if error_text:
                        stats["errors"] += 1
                    continue

                try:
                    if payload.get("status") == "not_modified":
                        stats["skipped"] += 1
                        continue
                    vr = versioned_ingest_text(
                        self.rag,
                        self.index,
                        payload["text"],
                        payload["meta"],
                        force=False,
                        raw_bytes=payload.get("raw_bytes"),
                    )
                    meta = vr.get("meta", payload["meta"])
                    meta["bootstrap_source"] = source_id
                    meta["bootstrap_ingested_at"] = now_iso()
                    self.index.upsert(meta["file_hash"], meta)

                    if vr.get("status") == "skipped":
                        stats["skipped"] += 1
                    else:
                        stats["ingested"] += 1
                        if vr.get("diff", {}).get("changed"):
                            stats["versions_changed"] += 1

                except Exception as exc:
                    stats["errors"] += 1
                    append_audit(
                        "bootstrap_item_error",
                        {
                            "source": source_id,
                            "url": candidate.get("url"),
                            "error": str(exc),
                        },
                    )

            return stats

        # Requests/BeautifulSoup yolu browser'sız kaynaklar için korunur.
        harvester = WebSourceHarvester(
            source_id,
            index=self.index,
            local_only=False,
        )

        for candidate in work:
            try:
                payload = harvester.fetch_candidate(candidate)
                if not payload:
                    continue

                vr = versioned_ingest_text(
                    self.rag,
                    self.index,
                    payload["text"],
                    payload["meta"],
                    force=False,
                    raw_bytes=payload.get("raw_bytes"),
                )
                meta = vr.get("meta", payload["meta"])
                meta["bootstrap_source"] = source_id
                meta["bootstrap_ingested_at"] = now_iso()
                self.index.upsert(meta["file_hash"], meta)

                if vr.get("status") == "skipped":
                    stats["skipped"] += 1
                else:
                    stats["ingested"] += 1
                    if vr.get("diff", {}).get("changed"):
                        stats["versions_changed"] += 1

            except Exception as exc:
                stats["errors"] += 1
                append_audit(
                    "bootstrap_item_error",
                    {
                        "source": source_id,
                        "url": candidate.get("url"),
                        "error": str(exc),
                    },
                )

        return stats

    def _bootstrap_bddk(self, use_browser: bool = True) -> Dict[str, Any]:
        source_id = "bddk_official"
        collector_errors: List[str] = []
        candidates: List[Dict[str, Any]] = []
        collector = "requests"
        if use_browser and sync_playwright is not None:
            try:
                harvester = BrowserWebSourceHarvester(source_id, index=self.index, local_only=False)
                candidates = harvester.collect_candidates(max_depth=BOOTSTRAP_BDDK_DEPTH, max_pages=BOOTSTRAP_MAX_BDDK_PAGES)
                collector = "playwright"
            except Exception as exc:
                collector_errors.append(f"Playwright: {exc}")
                append_audit("bddk_browser_fallback", {"error": str(exc)})
        if not candidates:
            try:
                harvester = WebSourceHarvester(source_id, index=self.index, local_only=False)
                candidates = harvester.collect_candidates(max_depth=BOOTSTRAP_BDDK_DEPTH, max_pages=BOOTSTRAP_MAX_BDDK_PAGES)
                collector = "requests_fallback"
            except Exception as exc:
                collector_errors.append(f"Requests: {exc}")
        result = self._ingest_candidates(source_id, candidates, use_browser=(collector == "playwright"), max_items=BOOTSTRAP_MAX_BDDK_PAGES)
        result["collector"] = collector
        if collector_errors:
            result["collector_errors"] = collector_errors
            result["errors"] += len(collector_errors)
        return result

    def _bootstrap_resmi_gazete(self, days: int = BOOTSTRAP_RESMI_DAYS) -> Dict[str, Any]:
        if self.rag is None:
            raise RuntimeError("Bootstrap için LocalRAG hazır olmalı")
        index = self.index
        scraper = ResmiGazeteScraper(index=index, local_only=False)
        result = {"days": days, "found": 0, "ingested": 0, "skipped": 0, "errors": 0, "versions_changed": 0}
        today = dt.datetime.now(ZoneInfo(SCHEDULER_TIMEZONE)).date()
        for offset in range(max(1, int(days))):
            target = today - dt.timedelta(days=offset)
            try:
                docs = scraper.fetch_daily_indices(target, filter_keywords=False)
                docs = docs[:BOOTSTRAP_RESMI_MAX_DOCS_PER_DAY]
                result["found"] += len(docs)
                for doc_info in docs:
                    try:
                        text, meta = scraper.fetch_document_content(doc_info)
                        if not text or not meta:
                            continue
                        vr = versioned_ingest_text(
                            self.rag,
                            index,
                            text,
                            meta,
                            force=False,
                            raw_bytes=meta.get("raw_bytes"),
                        )
                        m = vr.get("meta", meta)
                        index.upsert(m["file_hash"], m)
                        if vr.get("status") == "skipped":
                            result["skipped"] += 1
                        else:
                            result["ingested"] += 1
                            if vr.get("diff", {}).get("changed"):
                                result["versions_changed"] += 1
                    except Exception as exc:
                        result["errors"] += 1
                        append_audit("bootstrap_item_error", {"source": "resmi_gazete", "date": target.isoformat(), "error": str(exc)})
            except Exception as exc:
                result["errors"] += 1
                append_audit("bootstrap_day_error", {"source": "resmi_gazete", "date": target.isoformat(), "error": str(exc)})
        return result

    def _bootstrap_secondary(self, use_browser: bool = False) -> Dict[str, Any]:
        if self.rag is None:
            raise RuntimeError("Bootstrap için LocalRAG hazır olmalı")
        harvester_cls = BrowserWebSourceHarvester if use_browser and sync_playwright is not None else WebSourceHarvester
        harvester = harvester_cls("mevzuat_org", index=self.index, local_only=False)
        candidates = harvester.collect_candidates(max_depth=1, max_pages=80)
        return self._ingest_candidates("mevzuat_org", candidates, use_browser=False, max_items=80)

    def run(self, use_browser_for_bddk: bool = True, include_resmi_gazete: bool = True, include_secondary: bool = False, progress_callback: Optional[Any] = None) -> Dict[str, Any]:
        if self.rag is None:
            raise RuntimeError("İlk mevzuat kurulumu için embedding modeli + Chroma hazır olmalı")
        started = now_iso()
        result: Dict[str, Any] = {"started_at": started, "completed": False, "partial": False, "sources": {}, "errors": 0}
        steps = [("bddk_official", True), ("resmi_gazete", include_resmi_gazete), ("mevzuat_org", include_secondary)]
        total_steps = sum(1 for _, enabled in steps if enabled)
        done = 0
        for source_id, enabled in steps:
            if not enabled:
                continue
            if progress_callback:
                progress_callback(done, total_steps, source_id, "başlıyor", result)
            try:
                if source_id == "bddk_official":
                    src_result = self._bootstrap_bddk(use_browser=use_browser_for_bddk)
                elif source_id == "resmi_gazete":
                    src_result = self._bootstrap_resmi_gazete()
                else:
                    src_result = self._bootstrap_secondary(use_browser=False)
                result["sources"][source_id] = src_result
                result["errors"] += int(src_result.get("errors", 0) or 0)
            except Exception as exc:
                result["sources"][source_id] = {"found": 0, "selected": 0, "ingested": 0, "skipped": 0, "errors": 1, "fatal_error": str(exc)}
                result["errors"] += 1
                append_audit("bootstrap_source_error", {"source": source_id, "error": str(exc)})
            done += 1
            if progress_callback:
                progress_callback(done, total_steps, source_id, "tamamlandı", result)

        meaningful = 0
        for src in result["sources"].values():
            meaningful += int(src.get("selected", 0) or 0) + int(src.get("ingested", 0) or 0) + int(src.get("skipped", 0) or 0)
        result["finished_at"] = now_iso()
        result["meaningful_items"] = meaningful
        # Gerçek veri bulunmadan "başarılı" işaretleme: bu kez 0/0 durumunda kurulum bitmiş sayılmıyor.
        result["completed"] = meaningful > 0
        result["partial"] = bool(result["errors"]) and meaningful > 0
        if result["completed"]:
            self.state["completed"] = True
            self.state["completed_at"] = result["finished_at"]
        else:
            self.state["completed"] = False
            self.state["completed_at"] = None
        self.state["last_run"] = result
        self.state["sources"] = result["sources"]
        self._save()
        append_audit("regulation_bootstrap_completed" if result["completed"] else "regulation_bootstrap_failed_no_data", result)
        return result
def should_offer_bootstrap(rag: Optional[LocalRAG]) -> bool:
    state = load_json(BOOTSTRAP_STATE_FILE, {})
    if bool(state.get("completed")):
        return False
    if rag is None:
        return True
    try:
        return rag.count() == 0
    except Exception:
        return True


__all__ = [
    "RegulationBootstrapper",
    "should_offer_bootstrap",
]
