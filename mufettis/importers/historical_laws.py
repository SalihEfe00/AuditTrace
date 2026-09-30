# -*- coding: utf-8 -*-
"""Kanunların önceki metinleri (M7.3): olay tarihinde yürürlükte olan metin.

mevzuat.gov.tr her kanun için "X ile değişik hali" metinlerini yayımlar
(`EskiKanunlar/EskiKanunlarList?kanunSayisi=N&mevzuatTertip=T`, JSON). Her metin o değiştiren işlemin işlendiği
derlenmiş hâldir; değiştiren işlem kanun, KHK ya da Anayasa Mahkemesi kararı ("2012205" = K. 2012/205) olabilir,
ilk kayıt kanunun kendisi (ilk metin) olabilir. İndirmede doğrulanır: metnin başında değiştiren işlemin numarası
geçer, metinde ondan sonraki bir değişiklik notu yoktur.

Geçerlilik aralığı:
* başlangıç: değiştiren işlemin Resmî Gazete tarihi (kabulden önce yürürlüğe giremez);
* bitiş: bir sonraki değişikliğin tarihi (kabul; kaynakta kabul yayımdan sonra görünüyorsa yayım) — listedeki bir sonraki sürüm ya da güncel metindeki, bu
  sürümden sonraki en erken değişiklik notu (hangisi önceyse). Değişiklikler listede her zaman ayrı sürüm
  olarak yer almaz; güncel metnin notları aradaki değişikliği yakalar. Kabul, yürürlükten önce gelir: aralık
  gerçeğinden kısa kalabilir, hiçbir zaman uzun değil (olay tarihi boşluğa düşerse güncel metin + uyarı, M7.5).

Metinler "historical" durumuyla yüklenir: varsayılan aramaya girmez, güncel sürümün yerini almaz
(`versioned_ingest_text(link_versions=False)`), yalnızca olay tarihine göre aday olur (M7.4).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import gc
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from mufettis.common.utils import now_iso, sha256_bytes
from mufettis.corpus.legislation_sources import OFFICIAL_HOSTS, LegislationSource
from mufettis.importers.legislation_importer import RUN_LOG, document_text, listed_meta, process_memory_mb
from mufettis.legal.amendments import amendment_notes

VERSIONS_URL = "https://www.mevzuat.gov.tr/EskiKanunlar/EskiKanunlarList?kanunSayisi={no}&mevzuatTertip={tertip}"
HISTORICAL_STATUS = "historical"
VERSIONS_FILE = "historical_versions.json"
HEAD_CHARS = 800


def _date(value: str) -> str:
    """"13.06.2012" → "2012-06-13"; biçim dışıysa boş."""
    try:
        return dt.datetime.strptime(str(value or "").strip(), "%d.%m.%Y").date().isoformat()
    except ValueError:
        return ""


@dataclass(frozen=True)
class HistoricalVersion:
    code: str            # güncel kanunun mevzuat kodu (1.5.5464)
    amending_law: str    # metni bu hâle getiren kanun (6327)
    accepted_on: str     # değiştiren işlemin kabul (karar) tarihi (YYYY-MM-DD)
    published_on: str    # değiştiren işlemin Resmî Gazete tarihi
    pdf_url: str

    @property
    def key(self) -> str:
        return f"{self.code}@{self.accepted_on}"

    @property
    def changed_on(self) -> str:
        """Değişikliğin en erken tarihi (notlar kabul tarihini yazar). Kaynakta kabulün yayımdan sonra göründüğü
        kayıtlar var (Anayasa Mahkemesi kararları); o zaman yayım."""
        return min(self.accepted_on, self.published_on)


@dataclass(frozen=True)
class VersionWindow:
    version: HistoricalVersion
    valid_from: str
    valid_to: str        # boşsa aralık bilinmiyor: metin hiçbir tarihte yürürlükte sayılmaz


def versions_url(source: LegislationSource) -> str:
    """Yalnızca mevzuat.gov.tr kodu olan kanunlar; diğerlerinde boş."""
    if source.instrument != "kanun" or not source.code:
        return ""
    _tur, tertip, no = source.code.split(".")
    return VERSIONS_URL.format(no=no, tertip=tertip)


def parse_versions(source: LegislationSource, payload: Any) -> List[HistoricalVersion]:
    """Uç noktanın JSON listesi → kabul tarihine göre sıralı sürümler (resmî alan dışı / tarihsiz kayıt atlanır)."""
    out = []
    for row in payload if isinstance(payload, list) else []:
        pdf_url = str((row or {}).get("pdfUrl") or "")
        accepted, published = _date(row.get("kabulTarih")), _date(row.get("resmiGazeteTarihi"))
        if not pdf_url or urlparse(pdf_url).hostname not in OFFICIAL_HOSTS or not accepted or not published:
            continue
        out.append(HistoricalVersion(source.code, str(row.get("mevzuatNo") or "").strip(), accepted, published,
                                     pdf_url))
    return sorted(set(out), key=lambda v: (v.published_on, v.changed_on, v.amending_law))


def version_windows(versions: Sequence[HistoricalVersion], current_text: str) -> List[VersionWindow]:
    note_dates = sorted({n.date for n in amendment_notes(current_text)})
    out = []
    for i, version in enumerate(versions):
        ends = [d for d in note_dates if d > version.changed_on]
        if i + 1 < len(versions):
            ends.append(versions[i + 1].changed_on)
        valid_to = min(ends) if ends else ""
        if valid_to and valid_to <= version.published_on:
            valid_to = ""  # bir sonraki değişiklik yayımdan önce kabul edilmiş: aralık belirsiz, yürürlükte sayılmaz
        out.append(VersionWindow(version, version.published_on, valid_to))
    return out


def check_version_text(version: HistoricalVersion, text: str) -> Optional[str]:
    """Metin gerçekten değiştiren işlemle işlenmiş hâl mi? Sorun yoksa None."""
    dates = [n.date for n in amendment_notes(text)]
    if dates and max(dates) > max(version.accepted_on, version.published_on):
        return f"metinde sonraki değişiklik notu var ({max(dates)} > {version.accepted_on})"
    # Başlık bloğu: "6327 sayılı Kanun ile değişik hali", "… K.: 2012/205 sayılı Kararı ile değişik hali",
    # ilk metinde "Kanun Numarası : 5411". Karar numarasının eğik çizgisi kaynağın numarasında yok.
    head = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "", (text or "")[:HEAD_CHARS])
    if version.amending_law and not re.search(rf"(?<!\d){re.escape(version.amending_law)}(?!\d)", head):
        return f"metnin başında değiştiren işlem ({version.amending_law}) geçmiyor"
    return None


def version_label(text: str) -> str:
    """Metnin kendi başlık satırından sürüm adı: "6327 sayılı Kanun", "Anayasa Mahkemesi'nin 27/12/2012 tarihli ve
    E.: 2011/139, K.: 2012/205 sayılı Kararı"; satır yoksa (ilk metin) boş."""
    m = re.search(r"(\S[^\n]*(?:\n[^\n]*?)?)\s+ile\s+de[ğg]i[şs]ik\s+hali", (text or "")[:HEAD_CHARS], re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def version_source(source: LegislationSource, version: HistoricalVersion) -> LegislationSource:
    """İndirici için kaynak: başlık ve kanun numarası doğrulaması güncel kanunla aynı, adres sürümün PDF'i."""
    return dataclasses.replace(source, code="", url=version.pdf_url)


def historical_meta(source: LegislationSource, window: VersionWindow, path: Path, raw: bytes, pages: int,
                    ocr_used: bool, text: str = "") -> Dict[str, Any]:
    version = window.version
    meta = listed_meta(version_source(source, version), path, raw, pages, ocr_used)
    label = version_label(text)
    meta.update({
        "title": f"{source.title} ({label + ' ile değişik hali' if label else 'ilk metin'})",
        "amended_by": label or "ilk metin",
        "legislation_code": source.key,
        # Güncel metinden ayrı eser sürümü: kimlik kanun numarasına düşmez, güncel sürümle eşleşmez.
        "listed_document_key": f"mevzuat:{version.key}",
        "declared_work_id": f"mevzuat:{version.key}",
        "historical_of": f"mevzuat:{source.key}",
        "amended_by_law": version.amending_law,
        "status": HISTORICAL_STATUS,
        "valid_from": window.valid_from,
        "valid_to": window.valid_to,
    })
    return meta


def current_law_text(records: Iterable[Tuple[str, Dict[str, Any]]], law_number: str,
                     load_text: Callable[[str], str]) -> str:
    """Kanunun korpustaki güncel metni (aralık bitişindeki değişiklik notları için): numarası tutan etkin kayıt.
    records: (file_hash, kayıt) çiftleri (IngestIndex.items()); load_text: özet → metin (kaynak önbelleği)."""
    for file_hash, record in records:
        record = record or {}
        if str(record.get("law_number", "")) == law_number and str(record.get("status", "active")) == "active":
            text = load_text(file_hash)
            if text:
                return text
    return ""


def fetch_version_lists(get_json: Callable[[str], Any], sources: Sequence[LegislationSource]
                        ) -> Dict[str, List[HistoricalVersion]]:
    """Kanun kodu → sürümler. get_json: adres → JSON (istek aralığını çağıran korur)."""
    out: Dict[str, List[HistoricalVersion]] = {}
    for source in sources:
        url = versions_url(source)
        if url:
            out[source.key] = parse_versions(source, get_json(url))
    return out


def save_version_lists(path: Path, lists: Dict[str, List[HistoricalVersion]]) -> None:
    path.write_text(json.dumps({k: [dataclasses.asdict(v) for v in vs] for k, vs in lists.items()},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def load_version_lists(path: Path) -> Dict[str, List[HistoricalVersion]]:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {k: [HistoricalVersion(**v) for v in vs] for k, vs in data.items()}


def import_versions(rag, index, sources: Sequence[LegislationSource], lists: Dict[str, List[HistoricalVersion]],
                    fetcher, current_text: Callable[[LegislationSource], str], log_dir: Path,
                    progress: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
                    ingest: Optional[Callable[..., Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Her sürümü indirir (fetcher: OfficialLegislationFetcher), doğrular ve "historical" olarak yükler.
    current_text: güncel kanun metni (aralık bitişi için); yoksa boş dizge."""
    if ingest is None:
        from mufettis.rag.maintenance import versioned_ingest_text as ingest
    jobs = [(s, w) for s in sources for w in version_windows(lists.get(s.key, []), current_text(s))]
    rows: List[Dict[str, Any]] = []
    for i, (source, window) in enumerate(jobs, 1):
        started = time.monotonic()
        version = window.version
        row: Dict[str, Any] = {"key": version.key, "title": source.title, "amending_law": version.amending_law,
                               "valid_from": window.valid_from, "valid_to": window.valid_to, "at": now_iso()}
        try:
            result = fetcher.fetch(version_source(source, version))
            path = Path(fetcher.out_dir) / Path(result.path or "-").name
            if result.status not in ("downloaded", "cached") or not path.exists():
                row.update(status="not_downloaded", reason=result.reason or result.status)
            else:
                raw = path.read_bytes()
                if sha256_bytes(raw) != result.sha256:
                    raise ValueError("dosya indirme kaydıyla uyuşmuyor")
                text, pages, ocr_used = document_text(path, raw)
                problem = check_version_text(version, text)
                if problem:
                    row.update(status="rejected", reason=problem)
                else:
                    meta = historical_meta(source, window, path, raw, pages, ocr_used, text)
                    out = ingest(rag, index, text, meta, raw_bytes=raw, source_path=str(path), link_versions=False)
                    row.update(status=out.get("status", ""), chunks=int(out.get("count", 0) or 0), chars=len(text),
                               reason=str(out.get("error") or out.get("reason") or ""))
        except Exception as exc:  # bir sürümün hatası çalışmayı durdurmaz
            row.update(status="error", reason=f"{type(exc).__name__}: {exc}")
        gc.collect()
        row["seconds"] = round(time.monotonic() - started, 2)
        row.update(process_memory_mb())
        rows.append(row)
        with (Path(log_dir) / RUN_LOG).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**row, "kind": "historical"}, ensure_ascii=False) + "\n")
        if progress:
            progress(i, len(jobs), row)
    return rows


def fetch_and_import(rag, index, sources: Sequence[LegislationSource], download_dir: Path, fetcher=None,
                     list_only: bool = False, log: Callable[[str], None] = print) -> List[Dict[str, Any]]:
    """Listedeki kanunların sürüm listelerini alır (download_dir/historical_versions.json), sonra indirip yükler.
    fetcher: OfficialLegislationFetcher (varsayılan download_dir üzerinde)."""
    from mufettis.harvesting.official_legislation import OfficialLegislationFetcher
    from mufettis.rag.maintenance import load_source_cache

    fetcher = fetcher or OfficialLegislationFetcher(download_dir)
    laws = [s for s in sources if versions_url(s)]
    lists = fetch_version_lists(fetcher.fetch_json, laws)
    save_version_lists(Path(download_dir) / VERSIONS_FILE, lists)
    log(f"sürüm listesi: {len(laws)} kanun, {sum(len(v) for v in lists.values())} önceki metin")
    if list_only or index is None:
        return []
    return import_versions(
        rag, index, laws, lists, fetcher,
        lambda s: current_law_text(index.iter_records(), s.number, load_source_cache), download_dir,
        progress=lambda i, n, r: log(f"   [{i}/{n}] {r['status']:<22} {r['key']:<24} {r['valid_from']}…"
                                     f"{r['valid_to'] or '?':<10} {r.get('chunks', 0):4d} parça  {r.get('reason', '')}"))


__all__ = ["HistoricalVersion", "VersionWindow", "VERSIONS_URL", "HISTORICAL_STATUS", "VERSIONS_FILE",
           "versions_url", "parse_versions", "version_windows", "check_version_text", "version_source",
           "historical_meta", "version_label", "current_law_text", "fetch_version_lists", "save_version_lists", "load_version_lists", "import_versions",
           "fetch_and_import"]
