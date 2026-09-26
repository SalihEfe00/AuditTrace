# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import html
import io
import json
import re
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from urllib3.util.retry import Retry

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

from mufettis.rag.index import IngestIndex
from mufettis.audit.service import append_audit
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper

from mufettis.config import (
    BROWSER_DOCUMENT_TIMEOUT_MS,
    BROWSER_MAX_PAGES,
    BROWSER_TIMEOUT_MS,
    DATA_DIR,
    SCRAPER_MIN_DELAY,
    SOURCE_DEFINITIONS,
    SOURCE_REQUEST_TIMEOUT,
    WEB_FAST_REQUEST_DELAY,
    WEB_FAST_WORKERS,
    WEB_HTTP_CACHE_DIR,
    WEB_SOURCE_MAX_DEPTH,
    WEB_SOURCE_MAX_PAGES,
)
from mufettis.common.http import SystemTrustAdapter
from mufettis.common.utils import (
    atomic_write_json,
    canonical_source_id,
    enrich_source_meta,
    load_web_cache_meta,
    now_iso,
    save_web_cache_meta,
    sha256_bytes,
    source_is_allowed,
    source_profile,
    web_cache_key,
)
from mufettis.extraction.pdf import (
    _extract_pdfium_pages,
    _join_pdf_pages,
    _normalize_pdf_candidate_pages,
    _pdf_quality_gate,
)
from mufettis.extraction.text_repair import normalize_extracted_text, repair_mojibake
from mufettis.legal.parser import detect_doc_type, extract_legal_metadata

class WebSourceHarvester:
    """Kaynak profilinden web sayfalarını/PDF bağlantılarını kontrollü biçimde toplar."""

    def __init__(self, source_id: str, index: Optional[IngestIndex] = None, local_only: bool = False):
        if source_id not in SOURCE_DEFINITIONS and not source_profile(source_id):
            raise ValueError(f"Bilinmeyen kaynak: {source_id}")
        self.source_id = source_id
        self.profile = source_profile(source_id)
        self.index = index or IngestIndex()
        self.local_only = local_only
        self.session = self._build_session()
        self.user_agent = "MufettisAI/0.13 (+local-decision-support)"
        self._last_request = 0.0
        self.robot_parser: Optional[urllib.robotparser.RobotFileParser] = None

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry_kwargs = {
            "total": 4, "connect": 4, "read": 4, "status": 4,
            "backoff_factor": 1.0, "status_forcelist": [429, 500, 502, 503, 504],
            "raise_on_status": False,
        }
        try:
            retry = Retry(**retry_kwargs, allowed_methods=frozenset(["GET"]))
        except TypeError:
            retry = Retry(**retry_kwargs, method_whitelist=frozenset(["GET"]))
        # Resmî sitelerin eksik ara sertifikası işletim sistemi güven deposuyla tamamlanır (bkz. mufettis.common.http).
        adapter = SystemTrustAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _respect_rate_limit(self) -> None:
        delta = time.monotonic() - self._last_request
        if delta < SCRAPER_MIN_DELAY:
            time.sleep(SCRAPER_MIN_DELAY - delta)

    def _get(self, url: str, timeout: int = SOURCE_REQUEST_TIMEOUT) -> Optional[requests.Response]:
        if self.local_only:
            return None
        if not source_is_allowed(url, self.source_id):
            return None
        self._respect_rate_limit()
        response = self.session.get(url, headers={"User-Agent": self.user_agent}, timeout=timeout)
        self._last_request = time.monotonic()
        return response

    def robots_decision(self, target_url: str) -> Optional[bool]:
        """robots.txt için üç durumlu karar: True=izinli, False=engelli, None=bilinmiyor.

        robots.txt alınamazsa erişimi otomatik olarak "yasak" varsaymak yerine bilinmiyor
        döndürürüz; böylece resmi sitelerde robots servisindeki geçici erişim sorunları
        bootstrap'ı yanlışlıkla sıfıra düşürmez. Açık bir Disallow kuralı her zaman engellenir.
        """
        if self.local_only or not source_is_allowed(target_url, self.source_id):
            return False
        try:
            if self.robot_parser is None:
                robots_url = urljoin(self.profile["base_url"], "/robots.txt")
                response = self._get(robots_url, timeout=8)
                if response is None or response.status_code != 200:
                    return None
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(response.text.splitlines())
                self.robot_parser = rp
            return bool(self.robot_parser.can_fetch(self.user_agent, target_url))
        except Exception:
            return None

    def is_robots_allowed(self, target_url: str) -> bool:
        decision = self.robots_decision(target_url)
        return decision is not False

    def _looks_like_pdf(self, url: str, content_type: str = "") -> bool:
        return url.lower().split("?")[0].endswith(".pdf") or "application/pdf" in content_type.lower()

    def _extract_links(self, page_url: str, html: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: List[str] = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                continue
            full = urljoin(page_url, href)
            if source_is_allowed(full, self.source_id):
                links.append(full.split("#", 1)[0])
        # sıra koruyarak unique
        return list(dict.fromkeys(links))

    def _fetch_html_for_discovery(
        self,
        url: str,
        depth: int,
    ) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
        if self.local_only or not source_is_allowed(url, self.source_id):
            return url, None, "kaynak erişimi engelli"

        if not self.is_robots_allowed(url):
            return url, None, "robots.txt engelliyor"

        session=self._build_session()
        try:
            time.sleep(WEB_FAST_REQUEST_DELAY)
            response=session.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=SOURCE_REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                return url, None, f"HTTP {response.status_code}"

            content_type=str(response.headers.get("Content-Type",""))
            if self._looks_like_pdf(url,content_type):
                candidate={
                    "url":url,
                    "title":Path(urlparse(url).path).name or url,
                    "depth":depth,
                    "kind":"pdf",
                }
                return url,candidate,None

            html_text=response.text
            soup=BeautifulSoup(html_text,"html.parser")
            title=soup.title.get_text(" ",strip=True) if soup.title else url
            candidate={
                "url":url,
                "title":title,
                "depth":depth,
                "kind":"html",
            }
            candidate["_links"]=self._extract_links(url,html_text)
            return url,candidate,None
        except Exception as exc:
            return url,None,str(exc)
        finally:
            session.close()

    def collect_candidates(
        self,
        max_depth: int=WEB_SOURCE_MAX_DEPTH,
        max_pages: int=WEB_SOURCE_MAX_PAGES,
        progress_callback: Optional[Any]=None,
    ) -> List[Dict[str,Any]]:
        """HTTP-first paralel keşif. Aynı kaynak için BFS sırası korunur."""
        if self.local_only:
            return []

        seeds=list(self.profile.get("seed_urls",[]))
        queue: List[Tuple[str,int]]= [(u,0) for u in seeds]
        visited=set()
        candidates=[]
        seen_candidates=set()

        while queue and len(visited)<max_pages:
            current_level=[]
            next_queue=[]

            while queue and len(visited)+len(current_level)<max_pages:
                current_level.append(queue.pop(0))

            allowed=[]
            for url,depth in current_level:
                if url in visited or depth>max_depth:
                    continue
                visited.add(url)
                allowed.append((url,depth))

            if not allowed:
                continue

            with ThreadPoolExecutor(max_workers=min(WEB_FAST_WORKERS,len(allowed))) as executor:
                futures=[
                    executor.submit(self._fetch_html_for_discovery,url,depth)
                    for url,depth in allowed
                ]
                results=[future.result() for future in futures]

            # Deterministic order: submit sırası.
            for idx,(url,candidate,error) in enumerate(results,1):
                if progress_callback is not None:
                    try:
                        progress_callback(
                            len(visited),
                            max_pages,
                            url,
                            "hızlı HTTP keşfi" if candidate else "keşif hatası",
                        )
                    except Exception:
                        pass

                if not candidate:
                    continue

                if url not in seen_candidates:
                    candidate=dict(candidate)
                    candidate.pop("_links",None)
                    candidates.append(candidate)
                    seen_candidates.add(url)

                # Reconstruct links only for HTML pages.
                if candidate.get("kind")=="html":
                    # Re-fetching HTML here defeats the purpose, so use a
                    # second lightweight result lookup from the same batch.
                    _,raw_candidate,_=results[idx-1]
                    # raw_candidate's private links were stripped above;
                    # retrieve from the original result object by re-reading
                    # current tuple source.
                    original_candidate=results[idx-1][1]
                    if original_candidate and "_links" in original_candidate and allowed[idx-1][1] < max_depth:
                        for link in original_candidate["_links"]:
                            if link not in visited:
                                next_queue.append((link,allowed[idx-1][1]+1))

            queue.extend(next_queue)

        return candidates


    def fetch_candidate(self, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = candidate["url"]
        if not self.is_robots_allowed(url):
            return None
        res = self._get(url)
        if res is None or res.status_code != 200:
            return None
        raw = res.content
        content_type = res.headers.get("Content-Type", "")
        is_pdf = self._looks_like_pdf(url, content_type)
        text = ""
        pages = 0
        ocr_used = False
        if is_pdf:
            scraper = ResmiGazeteScraper(index=self.index, local_only=True)
            text, pages, ocr_used = scraper._extract_pdf_text(raw)
        else:
            soup = BeautifulSoup(res.text, "html.parser")
            for el in soup(["script", "style", "header", "footer", "nav"]):
                el.extract()
            text = re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip()
        if not text.strip():
            return None
        file_hash = sha256_bytes(raw)
        profile = self.profile
        meta = enrich_source_meta({
            "file_hash": file_hash,
            "title": candidate.get("title") or url,
            "url": url,
            "timestamp": now_iso(),
            "page_count": pages,
            "ocr_used": ocr_used,
            "is_pdf": is_pdf,
            "content_size": len(raw),
            "ingest_origin": "official_web_source" if profile.get("source_type") == "official" else "secondary_web_source",
        }, self.source_id)
        meta = extract_legal_metadata(meta["title"], text, meta)
        previous = self.index.get(file_hash)
        if previous:
            meta["status"] = "unchanged"
            meta["previous_hash"] = previous.get("previous_hash", "")
        else:
            meta["status"] = "new"
        return {"text": text, "meta": meta, "raw_bytes": raw}

    def fetch_candidate_http_fast(
        self,
        candidate: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Thread-safe HTTP fetch. Conditional GET cache ile değişmeyen belgeyi atlar."""
        url=str(candidate.get("url","")).strip()
        if not url or self.local_only or not source_is_allowed(url,self.source_id):
            return None,"kaynak erişimi engelli"
        if not self.is_robots_allowed(url):
            return None,"robots.txt engelliyor"

        session=self._build_session()
        cache=load_web_cache_meta(url)
        headers={"User-Agent":self.user_agent}
        if cache.get("etag"):
            headers["If-None-Match"]=str(cache["etag"])
        if cache.get("last_modified"):
            headers["If-Modified-Since"]=str(cache["last_modified"])

        try:
            time.sleep(WEB_FAST_REQUEST_DELAY)
            response=session.get(
                url,
                headers=headers,
                timeout=SOURCE_REQUEST_TIMEOUT,
            )

            if response.status_code==304:
                cached_hash=str(cache.get("file_hash",""))
                cached_meta=cache.get("meta",{})
                if cached_hash and self.index.get(cached_hash):
                    return {
                        "status":"not_modified",
                        "meta":cached_meta,
                    },None
                # Cache ile index tutarlı değilse tam GET dene.
                response=session.get(
                    url,
                    headers={"User-Agent":self.user_agent,"Cache-Control":"no-cache"},
                    timeout=SOURCE_REQUEST_TIMEOUT,
                )

            if response.status_code!=200:
                return None,f"HTTP {response.status_code}"

            raw=response.content
            content_type=str(response.headers.get("Content-Type",""))
            is_pdf=self._looks_like_pdf(url,content_type)

            if is_pdf:
                scraper=ResmiGazeteScraper(index=self.index,local_only=True)
                text_value,pages,ocr_used=scraper._extract_pdf_text(raw)
            else:
                soup=BeautifulSoup(response.text,"html.parser")
                for el in soup(["script","style","header","footer","nav"]):
                    el.extract()
                text_value=re.sub(
                    r"\n\s*\n","\n",
                    soup.get_text(separator="\n"),
                ).strip()
                pages=0
                ocr_used=False

            if not text_value.strip():
                return None,"metin boş"

            file_hash=sha256_bytes(raw)
            profile=self.profile
            meta=enrich_source_meta({
                "file_hash":file_hash,
                "title":candidate.get("title") or (
                    Path(urlparse(url).path).name if is_pdf else url
                ),
                "url":url,
                "timestamp":now_iso(),
                "page_count":pages,
                "ocr_used":ocr_used,
                "is_pdf":is_pdf,
                "content_size":len(raw),
                "ingest_origin":(
                    "official_fast_http"
                    if profile.get("source_type")=="official"
                    else "secondary_fast_http"
                ),
                "collector":"http_fast_path",
                "https_certificate_bypass":False,
                "tls_verification":"enforced",
                "etag":response.headers.get("ETag",""),
                "last_modified":response.headers.get("Last-Modified",""),
            },self.source_id)
            meta=extract_legal_metadata(meta["title"],text_value,meta)

            save_web_cache_meta(url,{
                "url":url,
                "file_hash":file_hash,
                "etag":response.headers.get("ETag",""),
                "last_modified":response.headers.get("Last-Modified",""),
                "meta":meta,
                "verified_at":now_iso(),
            })

            return {"status":"fetched","text":text_value,"meta":meta,"raw_bytes":raw},None
        except Exception as exc:
            return None,str(exc)
        finally:
            session.close()

    def fetch_candidates_batch_fast(
        self,
        candidates: List[Dict[str,Any]],
        progress_callback: Optional[Any]=None,
    ) -> Iterator[Tuple[Dict[str,Any],Optional[Dict[str,Any]],Optional[str]]]:
        """Kontrollü paralel HTTP fetch; tarayıcı gerektirenler alt sınıfta fallback eder."""
        total=len(candidates)
        if not candidates:
            return

        def worker(candidate):
            return self.fetch_candidate_http_fast(candidate)

        with ThreadPoolExecutor(max_workers=min(WEB_FAST_WORKERS,total)) as executor:
            futures={executor.submit(worker,c):c for c in candidates}
            completed=0
            results={}
            for future in as_completed(futures):
                candidate=futures[future]
                try:
                    payload,error=future.result()
                except Exception as exc:
                    payload,error=None,str(exc)
                completed+=1
                results[candidate.get("url","")]=(candidate,payload,error)
                if progress_callback:
                    try:
                        progress_callback(
                            completed,total,
                            candidate.get("title") or candidate.get("url",""),
                            "HTTP işlendi" if payload else "HTTP başarısız",
                            {"error":error or ""},
                        )
                    except Exception:
                        pass

        # Caller order deterministic.
        for candidate in candidates:
            yield results.get(
                candidate.get("url",""),
                (candidate,None,"işlenemedi"),
            )
class BrowserWebSourceHarvester(WebSourceHarvester):
    """Playwright destekli kaynak toplayıcı.

    Özellikle BDDK gibi istemci tarafında bağlantıları oluşturan sayfalarda
    render edilmiş DOM'u tarar. robots.txt açıkça engelliyorsa atlar; robots
    alınamıyorsa işlemi gereksiz yere sıfıra düşürmez. Belge indirme için Playwright Page navigasyonu kullanır; requests/API katmanına geri dönmez.
    """

    def _should_visit(self, url: str) -> bool:
        if not source_is_allowed(url, self.source_id):
            return False
        if self.source_id == "bddk_official":
            path = urlparse(url).path.lower()
            return path == "/mevzuat" or path.startswith("/mevzuat/")
        return True

    def _extract_browser_discovery_items(self, page: Any) -> List[Dict[str, str]]:
        """Render edilmiş DOM'dan gerçek bağlantı adaylarını çıkarır.

        BDDK'nin bazı mevzuat listelerinde belge URL'si klasik <a href>
        olarak değil data-url/data-href veya onclick içinde tutulabiliyor.
        Sadece href okumak bu nedenle 0 aday üretebiliyor. Burada URL
        üretme ihtimali olan attribute'lar birlikte okunur; sonrasında
        collect_candidates yalnızca izin verilen hukuki belge endpoint'lerini
        aday olarak kabul eder.
        """
        script = """els => els.map(el => ({
            href: el.href || el.getAttribute('href') || '',
            dataHref: el.getAttribute('data-href') || '',
            dataUrl: el.getAttribute('data-url') || '',
            onclick: el.getAttribute('onclick') || '',
            text: (el.innerText || el.textContent || '').trim()
        }))"""
        try:
            raw_items = page.eval_on_selector_all(
                "a[href], [data-href], [data-url], [onclick]",
                script,
            )
        except Exception:
            raw_items = []
        items: List[Dict[str, str]] = []
        seen = set()
        for raw in raw_items or []:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text", ""))
            values = [
                raw.get("href", ""),
                raw.get("dataHref", ""),
                raw.get("dataUrl", ""),
            ]
            onclick = str(raw.get("onclick", ""))
            # onclick içindeki mutlak/relative URL'leri yakala.
            values.extend(re.findall(r"(?:https?://[^\"'\s)]+|/[^\"'\s)]+)", onclick))
            for value in values:
                value = str(value or "").strip()
                if not value or value.startswith(("javascript:", "mailto:", "tel:")):
                    continue
                absolute = urljoin(page.url, value).split("#", 1)[0]
                if not source_is_allowed(absolute, self.source_id):
                    continue
                if absolute not in seen:
                    seen.add(absolute)
                    items.append({"url": absolute, "text": text})
        return items

    def collect_candidates(
        self,
        max_depth: int = WEB_SOURCE_MAX_DEPTH,
        max_pages: int = BROWSER_MAX_PAGES,
        progress_callback: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Playwright ile BDDK mevzuat adaylarını keşfeder.

        Kritik davranış: ``max_pages`` ziyaret edilen keşif sayfası bütçesidir;
        bir sayfanın içindeki doğrudan PDF/DokumanGetir/Detay bağlantıları
        ayrıca aday havuzuna çıkarılır. Böylece ``max_pages=1`` ile bile seed
        sayfasında görünür olan gerçek belge bağlantıları 0 aday olarak kaybolmaz.
        Liste sayfaları ise belge gibi indekslenmez; yalnızca BFS kuyruğuna alınır.
        """
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright kurulu değil. `pip install playwright` ve ardından "
                "`python -m playwright install chromium` çalıştırılmalı."
            )
        if self.local_only:
            return []

        seeds = self.profile.get("seed_urls", [])
        queue: List[Tuple[str, int]] = [(u, 0) for u in seeds]
        visited = set()
        candidates: List[Dict[str, Any]] = []
        candidate_seen = set()

        def add_candidate(url: str, title: str, depth: int, rendered: bool = True) -> None:
            url = str(url).split("#", 1)[0]
            if not url or url in candidate_seen or not source_is_allowed(url, self.source_id):
                return
            path = urlparse(url).path.lower()
            is_pdf = path.endswith(".pdf")
            is_document = (
                "/mevzuat/detay/" in path
                or "/dokumangetir/" in path
                or is_pdf
            )
            if not is_document:
                return
            candidate_seen.add(url)
            candidates.append({
                "url": url,
                "title": title or Path(urlparse(url).path).name or url,
                "depth": depth,
                "kind": "pdf" if is_pdf else "html",
                "rendered": rendered,
                "relevance_score": sum(
                    1 for k in self.profile.get("keywords", [])
                    if k.lower() in (title or "").lower()
                ),
            })

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=self.user_agent,
                ignore_https_errors=False,
                accept_downloads=True,
            )
            page = context.new_page()
            try:
                while queue and len(visited) < max(1, int(max_pages)):
                    url, depth = queue.pop(0)
                    if url in visited or depth > max_depth or not self._should_visit(url):
                        continue
                    visited.add(url)
                    if progress_callback is not None:
                        try:
                            progress_callback(len(visited), max_pages, url, "sayfa keşfi")
                        except Exception:
                            pass
                    robot = self.robots_decision(url)
                    if robot is False:
                        continue
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
                        try:
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                        # SPA/JS listelerinin DOM'a eklenmesi için kısa ve bounded bekleme.
                        try:
                            page.wait_for_timeout(400)
                        except Exception:
                            pass

                        title = page.title() or url
                        current_path = urlparse(page.url or url).path.lower()
                        if (
                            "/mevzuat/detay/" in current_path
                            or "/dokumangetir/" in current_path
                            or current_path.endswith(".pdf")
                        ):
                            add_candidate(page.url or url, title, depth)

                        for item in self._extract_browser_discovery_items(page):
                            link = item["url"]
                            text = item.get("text", "")
                            lpath = urlparse(link).path.lower()
                            link_title = text or Path(urlparse(link).path).name or link

                            # Doğrudan belgeyi, mevcut sayfa sayfa bütçesinden bağımsız
                            # aday havuzuna al. Böylece seed sayfası tek başına işe yarar.
                            if (
                                "/mevzuat/detay/" in lpath
                                or "/dokumangetir/" in lpath
                                or lpath.endswith(".pdf")
                            ):
                                add_candidate(link, link_title, depth + 1)
                                continue

                            # Liste/Mevzuat sayfaları sadece sonraki keşif seviyesine girer.
                            if depth < max_depth and self._should_visit(link):
                                if link not in visited and ("/mevzuat" in lpath):
                                    queue.append((link, depth + 1))

                    except Exception as exc:
                        append_audit(
                            "browser_page_error",
                            {"source": self.source_id, "url": url, "error": str(exc)},
                        )
            finally:
                context.close()
                browser.close()

        return candidates

    def _fetch_candidate_on_page(
        self,
        page: Any,
        candidate: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Tek bir açık Playwright page üzerinde aday belgeyi getir.

        BDDK'nın /Mevzuat/DokumanGetir/* uçları çoğu zaman sayfa navigasyonu
        yerine dosya download başlatır. Bu nedenle bu URL'lerde page.goto()
        tek başına kullanılmaz; expect_download ile indirme olayı yakalanır.
        """
        url = candidate["url"]

        if not source_is_allowed(url, self.source_id):
            return None

        robot = self.robots_decision(url)
        if robot is False:
            return None

        path_lower = urlparse(url).path.lower()
        is_download_endpoint = (
            "/dokumangetir/" in path_lower
            or str(candidate.get("kind", "")).lower() == "pdf"
            or path_lower.endswith(".pdf")
        )

        raw = b""
        text_value = ""
        pages = 0
        ocr_used = False
        current_url = url
        content_type = ""

        if is_download_endpoint:
            download = None
            goto_response = None
            goto_error: Optional[Exception] = None

            try:
                # BDDK dosya endpoint'i download başlattığında Playwright
                # page.goto() bir exception üretebilir. expect_download olayı
                # yine de yakalar; exception'ı yalnızca download yoksa fatal say.
                with page.expect_download(timeout=BROWSER_DOCUMENT_TIMEOUT_MS) as download_info:
                    try:
                        goto_response = page.goto(
                            url,
                            wait_until="commit",
                            timeout=BROWSER_DOCUMENT_TIMEOUT_MS,
                        )
                    except Exception as exc:
                        goto_error = exc
                download = download_info.value
            except Exception as exc:
                # Download olayı alınamadıysa, gerçek navigasyon hatasını geri bildir.
                if goto_error is not None:
                    raise goto_error
                raise exc

            if download is None:
                if goto_error is not None:
                    raise goto_error
                return None

            try:
                failure = download.failure()
            except Exception:
                failure = None
            if failure:
                append_audit(
                    "browser_download_failure",
                    {"source": self.source_id, "url": url, "error": failure},
                )
                return None

            try:
                download_path = download.path()
                if download_path:
                    raw = Path(download_path).read_bytes()
            except Exception as exc:
                append_audit(
                    "browser_download_read_error",
                    {"source": self.source_id, "url": url, "error": str(exc)},
                )
                return None

            if not raw:
                return None

            current_url = url
            content_type = "application/pdf" if (path_lower.endswith(".pdf") or "/dokumangetir/" in path_lower) else ""
            is_pdf = True

        else:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=BROWSER_DOCUMENT_TIMEOUT_MS,
            )

            if response is None:
                return None

            if response.status >= 400:
                append_audit(
                    "browser_fetch_http_error",
                    {
                        "source": self.source_id,
                        "url": url,
                        "status": response.status,
                    },
                )
                return None

            try:
                page.wait_for_load_state("networkidle", timeout=1500)
            except Exception:
                pass

            current_url = page.url or url
            response_headers = response.headers or {}
            content_type = str(response_headers.get("content-type", ""))
            is_pdf = self._looks_like_pdf(current_url, content_type)

            if is_pdf:
                try:
                    raw = response.body()
                except Exception as exc:
                    append_audit(
                        "browser_pdf_body_error",
                        {
                            "source": self.source_id,
                            "url": current_url,
                            "error": str(exc),
                        },
                    )
                    return None
                if not raw:
                    return None
            else:
                html = page.content()
                raw = html.encode("utf-8", errors="replace")

                soup = BeautifulSoup(html, "html.parser")
                for el in soup(["script", "style", "header", "footer", "nav"]):
                    el.extract()

                text_value = re.sub(
                    r"\n\s*\n",
                    "\n",
                    soup.get_text(separator="\n"),
                ).strip()

        if is_pdf:
            scraper = ResmiGazeteScraper(index=self.index, local_only=True)
            text_value, pages, ocr_used = scraper._extract_pdf_text(raw)

        if not text_value.strip():
            return None

        file_hash = sha256_bytes(raw)
        profile = self.profile

        meta = enrich_source_meta(
            {
                "file_hash": file_hash,
                "title": (
                    candidate.get("title")
                    or (page.title() if not is_pdf else "")
                    or current_url
                ),
                "url": current_url,
                "timestamp": now_iso(),
                "page_count": pages,
                "ocr_used": ocr_used,
                "is_pdf": is_pdf,
                "content_size": len(raw),
                "ingest_origin": (
                    "official_browser_source"
                    if profile.get("source_type") == "official"
                    else "secondary_browser_source"
                ),
                "collector": "playwright_download_aware",
                "https_certificate_bypass": False,
                "tls_verification": "enforced",
                "download_endpoint": is_download_endpoint,
            },
            self.source_id,
        )

        meta = extract_legal_metadata(meta["title"], text_value, meta)
        return {"text": text_value, "meta": meta}
    def fetch_candidates_batch(
        self,
        candidates: List[Dict[str, Any]],
        progress_callback: Optional[Any] = None,
    ) -> Iterator[Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[str]]]:
        """Tek browser/context ile birden fazla belgeyi sırayla getirir.

        Her aday için yeni Chromium açılmaz. Böylece bootstrap sırasında
        yüzlerce kez browser başlatma maliyeti ve uzun beklemeler azaltılır.
        Hatalı bir belge tüm batch'i durdurmaz.
        """
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright kurulu değil. `pip install playwright` ve "
                "`python -m playwright install chromium` çalıştırılmalı."
            )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=self.user_agent,
                ignore_https_errors=False,
                accept_downloads=True,
            )
            page = context.new_page()

            total = len(candidates)
            try:
                for i, candidate in enumerate(candidates, 1):
                    payload = None
                    error_text = None

                    try:
                        payload = self._fetch_candidate_on_page(page, candidate)
                    except Exception as exc:
                        error_text = str(exc)
                        append_audit(
                            "browser_batch_item_error",
                            {
                                "source": self.source_id,
                                "url": candidate.get("url", ""),
                                "error": error_text,
                            },
                        )

                    if progress_callback is not None:
                        try:
                            progress_callback(
                                i,
                                total,
                                candidate.get("title") or candidate.get("url", ""),
                                "işlendi" if payload else "atlandı",
                                {"error": error_text or ""},
                            )
                        except Exception:
                            pass

                    yield candidate, payload, error_text

            finally:
                context.close()
                browser.close()

    def fetch_candidates_batch_fast(
        self,
        candidates: List[Dict[str, Any]],
        progress_callback: Optional[Any] = None,
    ) -> Iterator[Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[str]]]:
        """Önce paralel HTTP; yalnız başarısız adaylar Playwright fallback."""
        candidates=list(candidates)
        total=len(candidates)
        if not candidates:
            return

        http_results={}
        with ThreadPoolExecutor(max_workers=min(WEB_FAST_WORKERS,total)) as executor:
            future_map={
                executor.submit(self.fetch_candidate_http_fast,c):c
                for c in candidates
            }
            done=0
            for future in as_completed(future_map):
                candidate=future_map[future]
                try:
                    payload,error=future.result()
                except Exception as exc:
                    payload,error=None,str(exc)
                http_results[candidate.get("url","")]=(payload,error)
                done+=1
                if progress_callback:
                    try:
                        progress_callback(
                            done,total,
                            candidate.get("title") or candidate.get("url",""),
                            "HTTP işlendi" if payload else "Playwright'a ayrıldı",
                            {"error":error or ""},
                        )
                    except Exception:
                        pass

        fallback=[
            c for c in candidates
            if not http_results.get(c.get("url",""),(None,""))[0]
        ]

        browser_results={}
        if fallback:
            # Mevcut stabil tek-browser/tek-context fallback.
            for candidate,payload,error in self.fetch_candidates_batch(fallback):
                browser_results[candidate.get("url","")]=(payload,error)

        for candidate in candidates:
            url=candidate.get("url","")
            payload,error=http_results.get(url,(None,"HTTP sonucu yok"))
            if payload:
                if payload.get("status")=="not_modified":
                    # Değişmeyen belge: yeniden embed etme.
                    meta=payload.get("meta",{})
                    yield candidate,{"status":"not_modified","meta":meta},None
                else:
                    yield candidate,payload,None
            else:
                bp,be=browser_results.get(url,(None,error))
                yield candidate,bp,be


    def fetch_candidate(self, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Uyumluluk için tek aday erişimi; batch implementasyonunu kullanır."""
        for _, payload, _ in self.fetch_candidates_batch([candidate]):
            return payload
        return None


__all__ = [k for k in dict(globals()).keys() if not k.startswith('__')]
