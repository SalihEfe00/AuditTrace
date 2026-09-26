#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parçalayıcı değiştikten sonra, kayıtlı parçaları güncel parçalayıcının üreteceklerinden farklı
olan belgeleri kaynak önbellekteki metinden yeniden parçalar.

Karar belgeye veya kaynağa özgü değildir: kayıtlı parça metinleri, LocalRAG.ingest_text'in aynı
önbellek metninden bugün üreteceği parça metinleriyle karşılaştırılır. Yeniden parçalama
Auto-Healing Ingestion Gate üzerinden yapılır; kapı reddederse önceki parçalar geri yüklenir.

Varsayılan: yalnızca plan. --apply: yedek + uygula + doğrula.
Veri dizini MUFETTIS_DATA_DIR ile değiştirilebilir (ör. production'ın bir kopyası).

Kullanım:
  python scripts/rechunk_outdated_documents.py            # plan
  python scripts/rechunk_outdated_documents.py --apply    # yedek + uygula + doğrula
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.config import DATA_DIR, DB_PATH, INGEST_INDEX_FILE, SOURCE_CACHE_DIR

INACTIVE_STATUSES = {"quarantined", "superseded"}


def _cache_text(file_hash: str) -> str:
    path = SOURCE_CACHE_DIR / f"{file_hash}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _norm(text: str) -> str:
    # Yalnızca boşluk farkı (boşluk onarımındaki iyileştirmeler) yeniden gömmeyi gerektirmez;
    # parça sınırı veya içerik farkı gerektirir.
    return re.sub(r"\s+", "", str(text or ""))


def expected_chunks(text: str, record: Dict[str, Any]) -> List[str]:
    """LocalRAG.ingest_text'in bu metinden üreteceği parça metinleri (maskeleme hariç aynı adımlar)."""
    from mufettis.common.utils import mask_sensitive
    from mufettis.extraction.text_repair import normalize_extracted_text, repair_extracted_spacing
    from mufettis.legal.parser import split_legal_chunks

    title, doc_type = record.get("title", ""), record.get("document_type", "")
    text = repair_extracted_spacing(normalize_extracted_text(text), title, doc_type)
    return [_norm(mask_sensitive(repair_extracted_spacing(ch["text"], title, doc_type)))
            for ch in split_legal_chunks(text, title=str(title or ""))]


def stored_chunks(rag) -> Dict[str, List[str]]:
    got = rag.collection.get(include=["documents", "metadatas"])
    rows: Dict[str, List[tuple]] = defaultdict(list)
    for doc, meta in zip(got.get("documents") or [], got.get("metadatas") or []):
        meta = meta or {}
        rows[str(meta.get("file_hash", ""))].append((int(meta.get("chunk_no", 0) or 0), _norm(doc)))
    return {h: [t for _, t in sorted(r)] for h, r in rows.items()}


def plan(rag, index) -> Dict[str, Any]:
    items = index.data.get("indexed_items", {})
    stored = stored_chunks(rag)
    targets = []
    for h, rec in items.items():
        if rec.get("status") in INACTIVE_STATUSES:
            continue
        cache = _cache_text(h)
        if not cache.strip() or h not in stored:
            continue
        expected = expected_chunks(cache, rec)
        if expected != stored[h]:
            targets.append({"hash": h, "title": str(rec.get("title", ""))[:50],
                            "chunks_before": len(stored[h]), "chunks_after": len(expected)})
    return {"documents": len(items), "outdated": targets}


def backup(stamp: str) -> Path:
    target = DATA_DIR / "backups" / f"rechunk_{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(INGEST_INDEX_FILE, target / "ingest_index.json")
    shutil.copytree(DB_PATH, target / "mevzuat_db")  # Chroma açılmadan önce
    return target


def apply(rag, index, the_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    from mufettis.legal.parser import extract_legal_metadata
    from repair.ingestion_gate import AutoHealingIngestionGate

    gate = AutoHealingIngestionGate()
    results = []
    for target in the_plan["outdated"]:
        record = dict(index.get(target["hash"]) or {})
        cached = _cache_text(target["hash"])
        meta = extract_legal_metadata(record.get("title", ""), cached, record)
        result = gate.ingest(rag, index, cached, meta, force=True)
        results.append({"title": target["title"], "status": result.get("status"),
                        "chunks_before": target["chunks_before"], "chunks_after": result.get("count", 0),
                        "error": result.get("error", "")})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="Yedek al, uygula ve doğrula")
    args = parser.parse_args()

    from mufettis.rag.engine import LocalRAG
    from mufettis.rag.index import IngestIndex

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup(stamp) if args.apply else None
    rag = LocalRAG(allow_download=False)
    index = IngestIndex()

    the_plan = plan(rag, index)
    print(json.dumps({"data_dir": str(DATA_DIR), "plan": the_plan}, ensure_ascii=False, indent=2))
    if not args.apply:
        print("\nPLAN (hiçbir şey yazılmadı). Uygulamak için --apply")
        return 0

    results = apply(rag, index, the_plan)
    remaining = plan(rag, index)["outdated"]
    rejected = [r for r in results if r["status"] in ("quarantined", "blocked")]
    report = {"results": results, "remaining_outdated": remaining, "backup": str(backup_dir)}
    (backup_dir / "manifest.json").write_text(json.dumps({"plan": the_plan, "report": report},
                                                         ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = not rejected and not remaining
    print(f"\nUYGULANDI. Doğrulama: {'BAŞARILI' if ok else 'KONTROL GEREKİYOR'}. Yedek: {backup_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
