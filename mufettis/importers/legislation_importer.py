# -*- coding: utf-8 -*-
"""Mevzuat listesindeki indirilmiş belgelerin korpusa yüklenmesi (M6.4).

Belge kimliği (tür, kanun / belge numarası, sürüm anahtarı, ad, kurum) mevzuat listesinden gelir ve
`listed_*` alanlarıyla taşınır; başlık sezgisi bunları ezmez (legal.parser.extract_legal_metadata). Belgeler
tek tek işlenir: yüklenmiş olan (aynı özet) atlanır, böylece kesilen çalışma kaldığı yerden sürer. Her belge
için süre ve işlem belleği kaydedilir (`legislation_downloads/import_runs.jsonl`).
"""
from __future__ import annotations

import gc
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Callable, Dict, List, Optional

from mufettis.common.utils import enrich_source_meta, now_iso, sha256_bytes
from mufettis.corpus.legislation_sources import LegislationSource
from mufettis.harvesting.official_legislation import non_pdf_text

SOURCE_ID = "mevzuat_gov"
# mevzuat.gov.tr dışındaki resmî adresler kendi kurumunun kaynağıyla kaydedilir (kaynak doğrulaması adrese bakar).
SOURCE_BY_HOST = {"www.tcmb.gov.tr": "tcmb_official", "tcmb.gov.tr": "tcmb_official",
                  "ms.hmb.gov.tr": "masak_official", "masak.hmb.gov.tr": "masak_official",
                  "www.bddk.org.tr": "bddk_official", "bddk.org.tr": "bddk_official",
                  "www.bddk.gov.tr": "bddk_official", "bddk.gov.tr": "bddk_official",
                  "www.kvkk.gov.tr": "kvkk_official", "kvkk.gov.tr": "kvkk_official",
                  "www.tmsf.org.tr": "tmsf_official", "tmsf.org.tr": "tmsf_official",
                  "www.seddk.gov.tr": "seddk_official", "seddk.gov.tr": "seddk_official",
                  "www.tbb.org.tr": "tbb_official", "tbb.org.tr": "tbb_official",
                  "www.tkbb.org.tr": "tkbb_official", "tkbb.org.tr": "tkbb_official",
                  "www.resmigazete.gov.tr": "resmi_gazete", "resmigazete.gov.tr": "resmi_gazete"}
RUN_LOG = "import_runs.jsonl"
DOCUMENT_TYPES = {"kanun": "Kanun", "kararname": "Kararname", "tüzük": "Tüzük", "yönetmelik": "Yönetmelik",
                  "tebliğ": "Tebliğ", "genelge": "Genelge", "rehber": "Rehber", "kurul kararı": "Kurul Kararı",
                  "emsal karar": "Kurul Karar Özeti",
                  "talimat": "Talimat", "standart": "Standart", "tip sözleşme": "Tip Sözleşme", "duyuru": "Duyuru"}


# Liste türü → kanonik tür (corpus.model.InstrumentType); `declared_instrument_type` olarak taşınır (en güçlü dayanak).
# Tüzük kanunun uygulanmasını gösteren yürütme düzenlemesidir; hiyerarşide yönetmelik düzeyinde ele alınır. Talimat
# ve tip sözleşme uygulama usulü, standart ve duyuru yol gösterici metindir. Emsal karar (tek olaya ilişkin Kurul
# kararı) yorum gösterir, kural koymaz: yol gösterici rolde (bulgunun dayanağı kanundur).
INSTRUMENT_TYPES = {"kanun": "kanun", "kararname": "cbk", "tüzük": "yonetmelik", "yönetmelik": "yonetmelik",
                    "tebliğ": "teblig", "genelge": "genelge", "rehber": "rehber", "kurul kararı": "kurul_karari",
                    "emsal karar": "rehber",
                    "talimat": "usul_esas", "tip sözleşme": "usul_esas", "standart": "rehber", "duyuru": "diger"}


def instrument_type_for(source: LegislationSource) -> str:
    if source.instrument == "karar":
        return "cb_karari" if source.issuer.startswith("Cumhurbaşkan") else "karar"
    return INSTRUMENT_TYPES[source.instrument]


def document_type_for(source: LegislationSource) -> str:
    if source.instrument == "karar":
        return "Cumhurbaşkanı Kararı" if source.issuer.startswith("Cumhurbaşkan") else "Karar"
    return DOCUMENT_TYPES[source.instrument]


def process_memory_mb() -> Dict[str, float]:
    """Çalışan ve en yüksek çalışma kümesi (MB); Windows dışında ya da hata olursa boş."""
    if sys.platform != "win32":
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.WinDLL("kernel32")
        # Sözde tanıtıcı (-1) 64 bitte int'e sığmaz; dönüş ve argüman tipleri açıkça verilmeli.
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return {}
        return {"working_set_mb": round(counters.WorkingSetSize / 2**20, 1),
                "peak_working_set_mb": round(counters.PeakWorkingSetSize / 2**20, 1)}
    except Exception:
        return {}


def document_text(path: Path, raw: bytes, main_content: bool = False) -> tuple:
    """(metin, sayfa sayısı, OCR kullanıldı mı)."""
    if path.suffix.lower() == ".pdf":
        from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper

        return ResmiGazeteScraper(local_only=True)._extract_pdf_text(raw)
    return non_pdf_text(raw, path.suffix.lower(), main_content=main_content), 0, False


def listed_meta(source: LegislationSource, path: Path, raw: bytes, pages: int, ocr_used: bool) -> Dict[str, Any]:
    url = source.download_url if path.suffix.lower() == ".pdf" else (source.html_url or source.download_url)
    meta = enrich_source_meta({
        "file_hash": sha256_bytes(raw),
        "title": source.title,
        "url": url,
        "timestamp": now_iso(),
        "page_count": pages,
        "ocr_used": ocr_used,
        "ingest_origin": "legislation_list",
        "local_path": str(path),
        "file_size": len(raw),
        "file_extension": path.suffix.lower(),
        "issuer": source.issuer,
        "official_date_hint": source.rg_date,
        "legislation_code": source.key,
        "legislation_group": source.group,
        "legislation_priority": source.priority,
        "listed_document_type": document_type_for(source),
        "listed_law_number": source.number if source.instrument == "kanun" else "",
        "listed_document_number": "" if source.instrument == "kanun" else source.number,
        "listed_document_key": f"mevzuat:{source.key}",
        "declared_instrument_type": instrument_type_for(source),
    }, SOURCE_BY_HOST.get(urlparse(url).hostname or "", SOURCE_ID))
    if source.instrument != "kanun":
        # Kalıcı kimlik listeden (mevzuat.gov.tr kodu ya da resmî adres). Başlık kimliği 80 karakterde kesildiği
        # için adı uzun ve yalnızca sonunda ayrışan belgeler (32 sayılı Karara ilişkin tebliğler) birbirinin
        # sürümü sayılıyordu. Kanunlar resmî numarayla (tr/kanun/N) kaynaktan bağımsız kimliği korur.
        meta["declared_work_id"] = meta["listed_document_key"]
    return meta


def import_downloads(rag, index, sources: List[LegislationSource], records: Dict[str, dict], log_dir: Path,
                     progress: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
                     ingest: Optional[Callable[..., Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """records: indiricinin downloads.json kaydı (anahtar → FetchResult sözlüğü); dosyalar log_dir (indirme
    klasörü) altındadır."""
    if ingest is None:
        from mufettis.rag.maintenance import versioned_ingest_text as ingest
    rows: List[Dict[str, Any]] = []
    log_path = Path(log_dir) / RUN_LOG
    for i, source in enumerate(sources, 1):
        started = time.monotonic()
        record = records.get(source.key) or {}
        row: Dict[str, Any] = {"key": source.key, "title": source.title, "at": now_iso()}
        path = Path(log_dir) / Path(record.get("path") or "-").name
        if record.get("status") not in ("downloaded", "cached") or not path.exists():
            row.update(status="not_downloaded", reason=record.get("reason", "indirilmemiş"))
        else:
            try:
                raw = path.read_bytes()
                if sha256_bytes(raw) != record.get("sha256"):
                    raise ValueError("dosya indirme kaydıyla uyuşmuyor")
                text, pages, ocr_used = document_text(path, raw, main_content=not source.code)
                meta = listed_meta(source, path, raw, pages, ocr_used)
                result = ingest(rag, index, text, meta, raw_bytes=raw, source_path=str(path))
                row.update(status=result.get("status", ""), chunks=int(result.get("count", 0) or 0),
                           chars=len(text), reason=str(result.get("error") or result.get("reason") or ""))
            except Exception as exc:  # bir belgenin hatası çalışmayı durdurmaz
                row.update(status="error", reason=f"{type(exc).__name__}: {exc}")
            gc.collect()
        row["seconds"] = round(time.monotonic() - started, 2)
        row.update(process_memory_mb())
        rows.append(row)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if progress:
            progress(i, len(sources), row)
    return rows


__all__ = ["import_downloads", "listed_meta", "document_type_for", "document_text", "process_memory_mb", "SOURCE_ID"]
