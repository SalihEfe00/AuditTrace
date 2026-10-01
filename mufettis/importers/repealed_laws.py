# -*- coding: utf-8 -*-
"""Yürürlükten kalkmış (mülga) kanunlar önceki metin olarak (M7.3c).

Korpusun atıf verdiği ama yürürlükte olmayan kanunlar (6762 sayılı eski TTK: 2012 öncesi çek / senet hükümleri;
2499 sayılı eski SPK; 4389 sayılı Bankalar Kanunu) olayı o döneme ait belgede dayanak olabilir. Metinler
"historical" durumuyla, kanun numarasıyla ve geçerlilik aralığıyla yüklenir; M7.4 olay tarihli aramada güncel
kanun olmadan da aday olurlar (`versions_in_force` kanun numarasına göre seçer).

Aralık: başlangıç metindeki son değişiklik notunun tarihi (yoksa ilk yayım) — yürürlükten kalkmadan önceki son
hâl o tarihten beri yürürlükteydi; daha eski olaylarda bu metin seçilmez (güncel metin + uyarı, M7.5). Bitiş
yürürlükten kalkış tarihi. Kalkış tarihi kaynakların başlığında tutarlı yazılmadığından tabloda, dayanağıyla.
"""
from __future__ import annotations

import gc
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from mufettis.common.utils import now_iso, sha256_bytes
from mufettis.corpus.legislation_sources import LegislationSource
from mufettis.importers.historical_laws import HISTORICAL_STATUS
from mufettis.importers.legislation_importer import RUN_LOG, document_text, listed_meta, process_memory_mb
from mufettis.legal.amendments import amendment_notes


@dataclass(frozen=True)
class RepealedLaw:
    number: str
    title: str
    url: str             # resmî kaynak (mevzuat.gov.tr "Mülga" metni ya da yayımlayan kurumun kopyası)
    published_on: str    # ilk Resmî Gazete tarihi (YYYY-MM-DD)
    repealed_on: str     # yürürlükten kalkış (YYYY-MM-DD)
    repealed_by: str     # kaldıran hüküm
    group: str

    def source(self) -> LegislationSource:
        return LegislationSource(code="", url=self.url, title=self.title, instrument="kanun", number=self.number,
                                 issuer="TBMM", priority=2, group=self.group,
                                 reason=f"M7.3c: mülga ({self.repealed_by}); olay tarihi eski belgeler için")


REPEALED_LAWS = (
    RepealedLaw("6762", "TÜRK TİCARET KANUNU", "https://www.mevzuat.gov.tr/MevzuatMetin/5.3.6762.pdf",
                "1956-07-09", "2012-07-01", "6102 sayılı Türk Ticaret Kanunu md. 1534 (yürürlük 1/7/2012)",
                "cek_ticaret_icra"),
    RepealedLaw("2499", "SERMAYE PİYASASI KANUNU", "https://www.mevzuat.gov.tr/MevzuatMetin/5.5.2499.pdf",
                "1981-07-30", "2012-12-30", "6362 sayılı Sermaye Piyasası Kanunu md. 139 (RG 30/12/2012)",
                "sermaye_piyasasi"),
)


def repeal_window(law: RepealedLaw, text: str) -> Dict[str, str]:
    """Son değişiklik notu (kalkıştan önceki) ya da ilk yayım → kalkış."""
    notes = [n.date for n in amendment_notes(text) if n.date and n.date < law.repealed_on]
    return {"valid_from": max(notes + [law.published_on]), "valid_to": law.repealed_on}


def repealed_meta(law: RepealedLaw, path: Path, raw: bytes, pages: int, ocr_used: bool, text: str) -> Dict[str, Any]:
    meta = listed_meta(law.source(), path, raw, pages, ocr_used)
    key = f"mevzuat:mulga:{law.number}"
    meta.update({
        "title": f"{law.title} ({law.number}; mülga — yürürlükten kalkmadan önceki son hâli)",
        "listed_document_key": key,
        "declared_work_id": key,  # güncel bir kanunla eşleşmez, sürüm zinciri kurmaz
        "repealed_by": law.repealed_by,
        "status": HISTORICAL_STATUS,
        **repeal_window(law, text),
    })
    return meta


def import_repealed(rag, index, fetcher, log_dir: Path, laws: Sequence[RepealedLaw] = REPEALED_LAWS,
                    progress: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
                    ingest: Optional[Callable[..., Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    if ingest is None:
        from mufettis.rag.maintenance import versioned_ingest_text as ingest
    rows: List[Dict[str, Any]] = []
    for i, law in enumerate(laws, 1):
        started = time.monotonic()
        row: Dict[str, Any] = {"key": f"mulga:{law.number}", "title": law.title, "at": now_iso()}
        try:
            result = fetcher.fetch(law.source())
            path = Path(fetcher.out_dir) / Path(result.path or "-").name
            if result.status not in ("downloaded", "cached") or not path.exists():
                row.update(status="not_downloaded", reason=result.reason or result.status)
            else:
                raw = path.read_bytes()
                if sha256_bytes(raw) != result.sha256:
                    raise ValueError("dosya indirme kaydıyla uyuşmuyor")
                text, pages, ocr_used = document_text(path, raw)
                meta = repealed_meta(law, path, raw, pages, ocr_used, text)
                out = ingest(rag, index, text, meta, raw_bytes=raw, source_path=str(path), link_versions=False)
                row.update(status=out.get("status", ""), chunks=int(out.get("count", 0) or 0), chars=len(text),
                           valid_from=meta["valid_from"], valid_to=meta["valid_to"],
                           reason=str(out.get("error") or out.get("reason") or ""))
        except Exception as exc:  # bir kanunun hatası çalışmayı durdurmaz
            row.update(status="error", reason=f"{type(exc).__name__}: {exc}")
        gc.collect()
        row["seconds"] = round(time.monotonic() - started, 2)
        row.update(process_memory_mb())
        rows.append(row)
        with (Path(log_dir) / RUN_LOG).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**row, "kind": "repealed"}, ensure_ascii=False) + "\n")
        if progress:
            progress(i, len(laws), row)
    return rows


__all__ = ["RepealedLaw", "REPEALED_LAWS", "repeal_window", "repealed_meta", "import_repealed"]
