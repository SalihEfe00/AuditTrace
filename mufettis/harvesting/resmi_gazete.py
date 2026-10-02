# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import io
import re
import time
import urllib.robotparser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from urllib3.util.retry import Retry
from pypdf import PdfReader

from mufettis.rag.engine import LocalRAG
from mufettis.config import (
    SCRAPER_MIN_DELAY,
    WEB_SOURCE_MAX_DEPTH,
    WEB_SOURCE_MAX_PAGES,
)
from mufettis.common.http import SystemTrustAdapter
from mufettis.common.utils import (
    enrich_source_meta,
    now_iso,
    sha256_bytes,
)
from mufettis.extraction.pdf import (
    _extract_pdf_with_pdfplumber_pages,
    _extract_pdfium_pages,
    _extract_pdfium_spatial_pages,
    _join_pdf_pages,
    _normalize_pdf_candidate_pages,
    _pdf_quality_gate,
    _select_pdf_engine_b3,
)
from mufettis.rag.index import IngestIndex

# Metin katmanı bundan kısa olan sayfa, metni olan bir PDF'te taranmış görüntü sayılır (karışık PDF, K2a.1).
IMAGE_PAGE_MAX_CHARS = 40


class ResmiGazeteScraper:
    """
    Resmî Gazete scraper.
    - urllib.robotparser ile robots.txt değerlendirmesi
    - Retry/backoff
    - Minimum istek aralığı
    - Günlük fihrist filtreleme
    - PDF/HTML indirme
    - PDF -> OCR fallback
    - İçerik hash ve değişiklik tespiti
    """

    BASE_URL = "https://www.resmigazete.gov.tr"

    def __init__(self, index: Optional[IngestIndex] = None, target_keywords: Optional[List[str]] = None, local_only: bool = False):
        self.index = index or IngestIndex()
        self.target_keywords = target_keywords or [
            "vergi", "masak", "bddk", "spk", "tebliğ", "yönetmelik",
            "kanun", "maliye", "hazine", "banka", "mülga", "anomali",
            "finans", "sermaye", "kara para", "yükümlülük",
            "bankacılık", "banka", "kredi", "mevduat", "ödeme", "elektronik para",
            "finansal kiralama", "faktoring", "finansman", "tasarruf finansman",
            "sermaye yeterliliği", "likidite", "risk grubu", "kredi kartı", "kurul kararı"
        ]
        self.local_only = local_only
        self.headers = {
            "User-Agent": "MufettisAI/0.10 (+local-audit-decision-support; contact: admin)"
        }
        self.session = self._build_session()
        self.robot_parser: Optional[urllib.robotparser.RobotFileParser] = None
        self._last_request = 0.0
        self._last_pdf_diagnostic: Dict[str, Any] = {}

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry_kwargs = {
            "total": 4,
            "connect": 4,
            "read": 4,
            "status": 4,
            "backoff_factor": 1.0,
            "status_forcelist": [429, 500, 502, 503, 504],
            "raise_on_status": False,
        }
        try:
            retry = Retry(**retry_kwargs, allowed_methods=frozenset(["GET"]))
        except TypeError:  # urllib3 eski sürüm
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

    def _get(self, url: str, timeout: int = 15) -> Optional[requests.Response]:
        if self.local_only:
            return None
        self._respect_rate_limit()
        res = self.session.get(url, headers=self.headers, timeout=timeout)
        self._last_request = time.monotonic()
        return res

    def is_robots_allowed(self, target_url: str) -> bool:
        if self.local_only:
            return False
        try:
            if self.robot_parser is None:
                robots_url = urljoin(self.BASE_URL, "/robots.txt")
                rp = urllib.robotparser.RobotFileParser()
                rp.set_url(robots_url)
                res = self._get(robots_url, timeout=8)
                if res is None or res.status_code != 200:
                    # robots.txt alınamadığında kesin izin sonucu iddia etmiyoruz.
                    # Politika olarak erişimi tutucu biçimde durduruyoruz.
                    return False
                rp.parse(res.text.splitlines())
                self.robot_parser = rp
            return bool(self.robot_parser.can_fetch(self.headers["User-Agent"], target_url))
        except Exception as exc:
            print(f"[robots.txt] Kontrol başarısız: {exc}")
            return False

    def fetch_daily_indices(self, target_date: Optional[dt.date] = None, filter_keywords: bool = True) -> List[Dict[str, Any]]:
        if target_date is None:
            target_date = dt.date.today()
        date_str = target_date.strftime("%Y%m%d")
        year_month = target_date.strftime("%Y/%m")
        url = f"{self.BASE_URL}/eskiler/{year_month}/{date_str}.htm"
        if not self.is_robots_allowed(url):
            return []
        try:
            res = self._get(url, timeout=15)
            if res is None or res.status_code != 200:
                return []
            # Fihrist sayfaları kodlamasını meta etiketinde beyan eder (windows-1254); bayt verilince
            # BeautifulSoup bu beyana uyar. UTF-8'e zorlamak Türkçe harfleri bozuyordu ("Su�lar�").
            soup = BeautifulSoup(res.content, "html.parser")
            docs: List[Dict[str, Any]] = []
            seen = set()
            for a in soup.find_all("a", href=True):
                title = a.get_text(" ", strip=True)
                href = a.get("href", "")
                if not title or not href:
                    continue
                title_l = title.lower()
                relevance = sum(1 for k in self.target_keywords if k in title_l)
                if filter_keywords and relevance <= 0:
                    continue
                full_url = href if href.startswith("http") else urljoin(url, href)
                key = (title, full_url)
                if key in seen:
                    continue
                seen.add(key)
                docs.append({
                    "title": title,
                    "url": full_url,
                    "date": target_date.isoformat(),
                    "source": "Resmî Gazete",
                    "relevance_score": relevance,
                })
            return docs
        except Exception as exc:
            print(f"[Scraper] Fihrist hatası: {exc}")
            return []

    def _ocr_image_pages(self, pdf_bytes: bytes, pages: List[str]) -> Tuple[List[str], bool]:
        """Karışık PDF (K2a.1): metin katmanı olan belgede metni neredeyse boş sayfalar taranmış görüntüdür; yalnızca
        onlar tek tek OCR'lanır. Her sayfada yalnızca kısa bir etiket varsa ("Yargıtay Film 1": mikrofilmden taranmış
        eski RG) kalite kapısı bunu metin sayar ve belge OCR yoluna düşmez; o durumda da bütün sayfalar OCR'lanır (K2b.3)."""
        empty = [i for i, page in enumerate(pages) if len(page.strip()) < IMAGE_PAGE_MAX_CHARS]
        if not empty:
            return pages, False
        from mufettis.extraction.ocr import ocr_pdf_pages

        filled, used = list(pages), False
        for index, text in zip(empty, ocr_pdf_pages(pdf_bytes, only=empty)):
            if len(text.strip()) > len(filled[index].strip()):
                filled[index], used = text, True
        self._last_pdf_diagnostic["image_pages_ocr"] = {"pages": len(empty), "filled": used}
        return filled, used

    def _ocr_pdf(self, pdf_bytes: bytes) -> str:
        from mufettis.extraction.ocr import ocr_pdf_pages

        return "\n".join(ocr_pdf_pages(pdf_bytes)).strip()

    def _extract_pdf_text(self, pdf_bytes: bytes) -> Tuple[str, int, bool]:
        """pypdfium2 Fast Pass + page Quality Gate + conditional fallback."""
        self._last_pdf_diagnostic = {
            "pipeline": "pypdfium2 -> quality gate -> conditional fallback -> OCR",
            "fast_pass": {},
            "candidates": [],
            "selected_engine": "",
            "selected_score": 0.0,
            "selected_status": "",
            "ocr_used": False,
        }

        fast_pages = _extract_pdfium_pages(pdf_bytes)
        page_count = len(fast_pages)
        fast_gate = _pdf_quality_gate(fast_pages)
        self._last_pdf_diagnostic["fast_pass"] = fast_gate

        # Temiz Fast Pass: ikinci extractor çalıştırılmaz.
        if fast_pages and fast_gate["status"] == "HIGH_CONFIDENCE":
            self._last_pdf_diagnostic.update({
                "selected_engine": "pypdfium2",
                "selected_score": float(fast_gate.get("score", 0.0)),
                "selected_status": str(fast_gate.get("status", "")),
            })
            pages, ocr_used = self._ocr_image_pages(pdf_bytes, fast_pages)
            return _join_pdf_pages(_normalize_pdf_candidate_pages(pages)), page_count, ocr_used

        candidates: List[Tuple[str, List[str]]] = []
        if fast_pages:
            candidates.append(("pypdfium2", fast_pages))

        # Fast Pass şüpheliyse aynı engine'in fiziksel A1-A4 çıktısını da aday olarak değerlendir.
        if fast_pages and fast_gate.get("status") != "HIGH_CONFIDENCE":
            try:
                spatial_pages = _extract_pdfium_spatial_pages(pdf_bytes)
                if spatial_pages:
                    page_count = max(page_count, len(spatial_pages))
                    candidates.append(("pypdfium2_spatial", spatial_pages))
            except Exception as exc:
                self._last_pdf_diagnostic["pypdfium2_spatial_error"] = str(exc)

        # Yalnızca şüpheli/boş belgelerde pahalı ikinci motorlar devreye girer.
        plumber_pages = _extract_pdf_with_pdfplumber_pages(pdf_bytes)
        if plumber_pages:
            page_count = max(page_count, len(plumber_pages))
            candidates.append(("pdfplumber", plumber_pages))

        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pypdf_pages: List[str] = []
            for page in reader.pages:
                try:
                    pypdf_pages.append(page.extract_text() or "")
                except Exception:
                    pypdf_pages.append("")
            if pypdf_pages:
                page_count = max(page_count, len(pypdf_pages))
                candidates.append(("pypdf", pypdf_pages))
        except Exception as exc:
            self._last_pdf_diagnostic["pypdf_error"] = str(exc)
            print(f"[PDF] PyPDF differential hatası: {exc}")

        decision = _select_pdf_engine_b3(candidates)
        self._last_pdf_diagnostic["engine_decision"] = {
            key: value for key, value in decision.items() if key != "selected_pages"
        }
        self._last_pdf_diagnostic["candidates"] = decision.get("candidates", [])

        if not decision.get("ocr_required"):
            pages, ocr_used = self._ocr_image_pages(pdf_bytes, decision.get("selected_pages", []))
            repaired_pages = _normalize_pdf_candidate_pages(pages)
            text = _join_pdf_pages(repaired_pages)
            if text:
                self._last_pdf_diagnostic.update({
                    "selected_engine": decision.get("selected_engine", ""),
                    "selected_score": float(decision.get("selected_score", 0.0)),
                    "selected_status": decision.get("selected_status", ""),
                })
                return text, page_count, ocr_used

        text = self._ocr_pdf(pdf_bytes)
        self._last_pdf_diagnostic.update({
            "selected_engine": "OCR",
            "selected_score": 0.0,
            "selected_status": "OCR",
            "ocr_used": bool(text),
        })
        return text, page_count, bool(text)


    def fetch_document_content(self, doc_info: Dict[str, Any], force: bool = False) -> Tuple[str, Dict[str, Any]]:
        url = doc_info["url"]
        if self.local_only or not self.is_robots_allowed(url):
            return "", {}
        try:
            res = self._get(url, timeout=20)
            if res is None or res.status_code != 200:
                return "", {}
            content_bytes = res.content
            is_pdf_url = url.lower().endswith(".pdf") or "application/pdf" in res.headers.get("Content-Type", "").lower()
            final_bytes = content_bytes
            extracted_text = ""
            is_pdf = False
            page_count = 0
            ocr_used = False

            if is_pdf_url:
                extracted_text, page_count, ocr_used = self._extract_pdf_text(final_bytes)
                is_pdf = True
            else:
                soup = BeautifulSoup(res.text, "html.parser")
                pdf_a = soup.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
                if pdf_a and pdf_a.get("href"):
                    pdf_url = urljoin(url, pdf_a["href"])
                    if self.is_robots_allowed(pdf_url):
                        pdf_res = self._get(pdf_url, timeout=20)
                        if pdf_res is not None and pdf_res.status_code == 200:
                            final_bytes = pdf_res.content
                            extracted_text, page_count, ocr_used = self._extract_pdf_text(final_bytes)
                            is_pdf = True
                if not extracted_text:
                    for el in soup(["script", "style", "header", "footer", "nav"]):
                        el.extract()
                    extracted_text = re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip()

            if not extracted_text:
                return "", {}

            file_hash = sha256_bytes(final_bytes)
            previous = self.index.get(file_hash)
            status = "unchanged" if previous and not force else "new"
            if previous and not force:
                previous["last_seen"] = now_iso()
                self.index.upsert(file_hash, previous)
                return "", {"file_hash": file_hash, "status": "skipped", **previous}

            meta = enrich_source_meta({
                "file_hash": file_hash,
                "title": doc_info.get("title", "Bilinmeyen belge"),
                "url": url,
                "source": "Resmî Gazete",
                "resmi_gazete_date": doc_info.get("date"),
                "is_pdf": is_pdf,
                "page_count": page_count,
                "ocr_used": ocr_used,
                "timestamp": now_iso(),
                "status": status,
                "content_size": len(final_bytes),
            }, "resmi_gazete")
            meta["raw_bytes"] = final_bytes
            return extracted_text, meta
        except Exception as exc:
            print(f"[İçerik] {url}: {exc}")
            return "", {}

def versioned_ingest_text(*args, **kwargs):
    from mufettis.rag.maintenance import versioned_ingest_text as _fn
    return _fn(*args, **kwargs)

def sync_web_source(source_id: str, rag: LocalRAG, use_browser: bool = False, max_depth: int = WEB_SOURCE_MAX_DEPTH, max_pages: int = WEB_SOURCE_MAX_PAGES) -> Dict[str, Any]:
    from mufettis.harvesting.web_harvester import (
        BrowserWebSourceHarvester,
        WebSourceHarvester,
        sync_playwright,
    )
    # Playwright kurulu değilse tarayıcı yolu her çalıştırmada hata verip kaynağı hiç
    # toplamıyordu; bootstrapper'daki gibi HTTP toplayıcıya geçilir ve sonuçta belirtilir.
    browser_fallback = bool(use_browser and sync_playwright is None)
    Harvester = BrowserWebSourceHarvester if use_browser and not browser_fallback else WebSourceHarvester
    harvester = Harvester(source_id, index=IngestIndex(), local_only=False)
    candidates = harvester.collect_candidates(max_depth=max_depth, max_pages=max_pages)
    result = {"source": source_id, "found": len(candidates), "ingested": 0, "skipped": 0, "errors": 0, "versions_changed": 0}
    if browser_fallback:
        result["browser_fallback"] = "Playwright kurulu değil; HTTP toplayıcı kullanıldı."
    for candidate in candidates:
        try:
            payload = harvester.fetch_candidate(candidate)
            if not payload:
                continue
            version_result = versioned_ingest_text(
                rag,
                harvester.index,
                payload["text"],
                payload["meta"],
                force=False,
                raw_bytes=payload.get("raw_bytes"),
            )
            meta = version_result.get("meta", payload["meta"])
            harvester.index.upsert(meta["file_hash"], meta)
            if version_result.get("status") == "skipped":
                result["skipped"] += 1
            else:
                result["ingested"] += 1
                if version_result.get("diff", {}).get("changed"):
                    result["versions_changed"] += 1
        except Exception:
            result["errors"] += 1
    return result

def sync_resmi_gazete_for_date(target_date: dt.date, rag: LocalRAG, filter_keywords: bool = False, max_docs: int = 120) -> Dict[str, Any]:
    index = IngestIndex()
    scraper = ResmiGazeteScraper(index=index, local_only=False)
    docs = scraper.fetch_daily_indices(target_date, filter_keywords=filter_keywords)[:max_docs]
    result = {"source": "resmi_gazete", "date": target_date.isoformat(), "found": len(docs), "ingested": 0, "skipped": 0, "errors": 0, "versions_changed": 0}
    for doc_info in docs:
        try:
            text, meta = scraper.fetch_document_content(doc_info)
            if not text or not meta:
                continue
            version_result = versioned_ingest_text(
                rag,
                index,
                text,
                meta,
                force=False,
                raw_bytes=meta.get("raw_bytes"),
            )
            if version_result.get("status") == "skipped":
                result["skipped"] += 1
            else:
                result["ingested"] += 1
                if version_result.get("diff", {}).get("changed"):
                    result["versions_changed"] += 1
        except Exception:
            result["errors"] += 1
    return result


__all__ = [
    "ResmiGazeteScraper",
    "versioned_ingest_text",
    "sync_web_source",
    "sync_resmi_gazete_for_date",
]
