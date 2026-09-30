# -*- coding: utf-8 -*-
"""Resmî kaynak indiricisi (M6.3): mevzuat listesindeki (corpus/legislation_sources.json) belgeleri indirir.

Kurallar: dürüst kullanıcı kimliği, robots.txt, istekler arası bekleme, yalnızca listedeki resmî alan adları.
mevzuat.gov.tr'de PDF'i olmayan belgelerde (yönetmelik, tebliğ, karar) aynı sitenin resmî metin sayfası (HTML)
alınır. İndirilen dosya PDF imzası (ya da HTML metni) ve ilk sayfalarında listedeki resmî adın (ve varsa numaranın) geçmesiyle doğrulanır;
yanlış kod başka bir belgeyi indirdiyse dosya kabul edilmez. Dosyalar `DATA_DIR/legislation_downloads/` altına
yazılır ve `downloads.json` kaydı tutulur; yeniden çalıştırmada doğrulanmış dosya tekrar indirilmez.
"""
from __future__ import annotations

import json
import re
import time
import urllib.robotparser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.common.utils import atomic_write_json, now_iso, sha256_bytes
from mufettis.corpus.legislation_sources import OFFICIAL_HOSTS, LegislationSource

USER_AGENT = "MufettisAI/0.13 (+local-decision-support)"
MIN_DELAY_SECONDS = 3.0
REQUEST_TIMEOUT = 60
MAX_BYTES = 60 * 1024 * 1024
VERIFY_PAGES = 3
MIN_TITLE_COVERAGE = 0.8
RECORD_FILE = "downloads.json"

_FOLD = str.maketrans({"â": "a", "î": "i", "û": "u"})


def _words(text: str) -> List[str]:
    # Harf ve rakam ayrı kelimedir: başlığa bitişik dipnot işareti ("KANUNU1") kelimeyi bozmasın.
    return re.findall(r"[a-zçğıöşü]+|[0-9]+", turkish_lower(text or "").translate(_FOLD))


def title_coverage(title: str, text: str) -> float:
    """Resmî addaki anlamlı kelimelerin (≥3 harf) metinde geçme oranı."""
    wanted = {w for w in _words(title) if len(w) >= 3}
    if not wanted:
        return 0.0
    have = set(_words(text))
    return len(wanted & have) / len(wanted)


def verify_document(source: LegislationSource, pages: List[str]) -> Optional[str]:
    """Sorun varsa açıklaması, yoksa None. İlk sayfalarda resmî ad ve (varsa) numara aranır."""
    head = "\n".join(pages[:VERIFY_PAGES])
    if not head.strip():
        return "metin çıkarılamadı"
    coverage = title_coverage(source.title, head)
    if coverage < MIN_TITLE_COVERAGE:
        return f"resmî ad ilk sayfalarda bulunamadı (kapsam {coverage:.2f})"
    if source.instrument == "kanun" and source.number not in re.findall(r"\d+", head):
        return f"kanun numarası {source.number} ilk sayfalarda yok"
    return None


HTML_VERIFY_CHARS = 6000


def html_text(raw: bytes) -> str:
    """Resmî metin sayfasının düz metni (toplu yükleyicinin HTML okumasıyla aynı kural)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
    for el in soup(["script", "style", "header", "footer", "nav"]):
        el.extract()
    return re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip()


def _default_pages(pdf_bytes: bytes) -> List[str]:
    from mufettis.extraction.pdf import _extract_pdfium_pages

    return _extract_pdfium_pages(pdf_bytes)


@dataclass
class FetchResult:
    key: str
    status: str            # downloaded | cached | failed | rejected | robots_blocked
    path: str = ""         # indirme klasörüne göre dosya adı (veri dizini kopyalanınca da geçerli)
    sha256: str = ""
    text_sha256: str = ""  # çıkarılan metnin özeti: güncellik denetimi baytla değil metinle (M6.8)
    size: int = 0
    pages: int = 0
    reason: str = ""
    fetched_at: str = ""
    seconds: float = 0.0


class OfficialLegislationFetcher:
    def __init__(self, out_dir: Path, session=None, delay: float = MIN_DELAY_SECONDS,
                 page_extractor: Callable[[bytes], List[str]] = _default_pages,
                 sleep: Callable[[float], None] = time.sleep):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.session = session if session is not None else self._build_session()
        self.delay = delay
        self.page_extractor = page_extractor
        self.sleep = sleep
        self._last_request = 0.0
        self._robots: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self.record_path = self.out_dir / RECORD_FILE
        self.records: Dict[str, dict] = (json.loads(self.record_path.read_text(encoding="utf-8"))
                                         if self.record_path.exists() else {})

    @staticmethod
    def _build_session():
        import requests
        from urllib3.util.retry import Retry

        from mufettis.common.http import SystemTrustAdapter

        session = requests.Session()
        retry = Retry(total=3, backoff_factor=2.0, status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=frozenset(["GET"]), raise_on_status=False)
        adapter = SystemTrustAdapter(max_retries=retry)
        session.mount("https://", adapter)
        return session

    def _get(self, url: str):
        wait = self.delay - (time.monotonic() - self._last_request)
        if self._last_request and wait > 0:
            self.sleep(wait)
        try:
            return self.session.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        finally:
            self._last_request = time.monotonic()

    def robots_allows(self, url: str) -> bool:
        """robots.txt izin vermiyorsa False; alınamazsa izinli sayılır (resmî yayın, kişisel veri yok)."""
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            parser: Optional[urllib.robotparser.RobotFileParser] = None
            try:
                response = self._get(base + "/robots.txt")
                if response.status_code == 200:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(response.text.splitlines())
            except Exception:
                parser = None
            self._robots[base] = parser
        parser = self._robots[base]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    def _file_for(self, source: LegislationSource) -> Path:
        return self.out_dir / (re.sub(r"[^0-9A-Za-z._-]", "_", source.key.split("//")[-1]) + ".pdf")  # uzantı sonra

    def _save_record(self, result: FetchResult) -> None:
        self.records[result.key] = asdict(result)
        atomic_write_json(self.record_path, self.records)

    def fetch(self, source: LegislationSource, force: bool = False) -> FetchResult:
        started = time.monotonic()
        url = source.download_url
        path = self._file_for(source)
        previous = self.records.get(source.key)
        kept = self.out_dir / Path(previous["path"]).name if previous and previous.get("path") else None
        if (not force and previous and previous.get("status") in ("downloaded", "cached") and kept and kept.exists()
                and sha256_bytes(kept.read_bytes()) == previous.get("sha256")):
            return FetchResult(**{**previous, "status": "cached"})
        if urlparse(url).hostname not in OFFICIAL_HOSTS:
            return self._finish(FetchResult(source.key, "rejected", reason=f"resmî olmayan alan adı: {url}"), started)
        if not self.robots_allows(url):
            return self._finish(FetchResult(source.key, "robots_blocked", reason="robots.txt izin vermiyor"), started)
        try:
            response = self._get(url)
        except Exception as exc:
            return self._finish(FetchResult(source.key, "failed", reason=f"istek hatası: {exc}"), started)
        if response.status_code != 200:
            return self._finish(FetchResult(source.key, "failed", reason=f"HTTP {response.status_code}"), started)
        data, suffix = response.content or b"", ".pdf"
        pages, problem = self._check(source, data, suffix)
        if problem and source.html_url:
            # PDF yok (yönetmelik, tebliğ) ya da PDF'in metni doğrulanamıyor (ör. kararın yalnızca imza sayfası
            # metin, eki görüntü): aynı sitenin resmî metin sayfası denenir.
            try:
                response = self._get(source.html_url)
            except Exception as exc:
                return self._finish(FetchResult(source.key, "failed",
                                                reason=f"PDF: {problem}; metin sayfası: istek hatası: {exc}"), started)
            if response.status_code != 200:
                return self._finish(FetchResult(source.key, "failed",
                                                reason=f"PDF: {problem}; metin sayfası: HTTP {response.status_code}"),
                                    started)
            html_data = response.content or b""
            html_pages, html_problem = self._check(source, html_data, ".html")
            if html_problem:
                problem = f"PDF: {problem}; metin sayfası: {html_problem}"
            else:
                data, suffix, pages, problem = html_data, ".html", html_pages, None
        if problem:
            return self._finish(FetchResult(source.key, "rejected", size=len(data), pages=len(pages), reason=problem),
                                started)
        path = path.with_suffix(suffix)
        path.write_bytes(data)
        text = "\n".join(pages) if suffix == ".pdf" else html_text(data)
        return self._finish(FetchResult(source.key, "downloaded", path=path.name, sha256=sha256_bytes(data),
                                        text_sha256=sha256_bytes(re.sub(r"\s+", " ", text).strip().encode("utf-8")),
                                        size=len(data), pages=len(pages)), started)

    def _check(self, source: LegislationSource, data: bytes, suffix: str):
        """(sayfalar, sorun); sorun yoksa None."""
        if len(data) > MAX_BYTES:
            return [], f"dosya çok büyük ({len(data)} bayt)"
        if suffix == ".pdf":
            if not data.startswith(b"%PDF"):
                return [], "PDF değil"
            pages = self.page_extractor(data)
        else:
            pages = [html_text(data)[:HTML_VERIFY_CHARS]]
        return pages, verify_document(source, pages)

    def _finish(self, result: FetchResult, started: float) -> FetchResult:
        result.fetched_at = now_iso()
        result.seconds = round(time.monotonic() - started, 2)
        self._save_record(result)
        return result

    def fetch_all(self, sources: List[LegislationSource], force: bool = False,
                  progress: Optional[Callable[[int, int, FetchResult], None]] = None) -> List[FetchResult]:
        results = []
        for i, source in enumerate(sources, 1):
            result = self.fetch(source, force=force)
            results.append(result)
            if progress:
                progress(i, len(sources), result)
        return results


__all__ = ["OfficialLegislationFetcher", "FetchResult", "verify_document", "title_coverage", "html_text", "USER_AGENT"]
