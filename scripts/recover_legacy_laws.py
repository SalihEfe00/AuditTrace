#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E7: Eski şema (chunk_schema_version yok) parçalarında kalan mevzuatı kurtarır.

Arka plan: LocalRAG.query yalnızca güncel CHUNK_SCHEMA_VERSION parçalarını döndürür.
Eski formatta kalan belgeler (ör. 5411 Bankacılık Kanunu, 5464 Kart Kanunu, VUK, GVK)
bu yüzden aramada hiç çıkmıyor ve indekste de kayıtlı değil. Ayrıca mevzuat_deposu/
klasöründeki bazı PDF'ler hiç içe aktarılmamış.

Adımlar (--apply ile):
  0. Yedek: DATA_DIR/backups/e7_legacy_recovery_<zaman>/ (ingest_index, source_cache, etkin parça deposu)
  1. mevzuat_deposu/ PDF'leri BulkRegulationImporter ile içe aktarılır (güncel şema).
  2. PDF'i olmayan eski belgeler, eski parçaların sayfa/alt parça sırasıyla birleştirilmesiyle
     yeniden oluşturulup içe aktarılır (ingest_origin=legacy_chunk_recovery).
  3. İndekste daha yeni sürümü bulunan eski BDDK tek parçaları silinir.
  4. Doğrulama: kalan eski parça sayısı ve örnek aramalar.

Varsayılan: yalnızca plan (hiçbir şey yazmaz; Chroma yalnızca okunur).
Veri dizini MUFETTIS_DATA_DIR ile değiştirilebilir (ör. production'ın bir kopyası).

Kullanım:
  python scripts/recover_legacy_laws.py            # plan
  python scripts/recover_legacy_laws.py --apply    # yedek + uygula + doğrula
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.config import BASE_DIR, CHUNK_SCHEMA_VERSION, DATA_DIR, SOURCE_CACHE_DIR
from mufettis.common.backups import new_backup_dir, prune_backups

DEPOSIT_DIR = BASE_DIR / "mevzuat_deposu"
VERIFY_QUERIES = [
    ("5411", "5411 sayılı Bankacılık Kanunu kredi sınırları"),
    ("5464", "5464 sayılı Banka Kartları ve Kredi Kartları Kanunu kart hamili"),
    ("193", "193 sayılı Gelir Vergisi Kanunu gelirin unsurları"),
    ("213", "213 sayılı Vergi Usul Kanunu defter tutma"),
]


def _legacy_groups(rag) -> Dict[str, List[Tuple[str, Dict[str, Any], str]]]:
    """Şema sürümü olmayan parçaları file_hash'e göre gruplar."""
    from mufettis.rag.paging import iter_pages

    groups: Dict[str, List[Tuple[str, Dict[str, Any], str]]] = {}
    pairs = ((cid, meta, doc) for ids, docs, metas in iter_pages(rag.collection)
             for cid, doc, meta in zip(ids, docs, metas))
    for cid, meta, doc in pairs:  # sayfalı: yalnızca eski şemadaki parçalar bellekte tutulur
        if str(meta.get("chunk_schema_version", "") or "") == str(CHUNK_SCHEMA_VERSION):
            continue
        groups.setdefault(str(meta.get("file_hash", "")), []).append((cid, meta, doc or ""))
    return groups


def _chunk_order(chunk_id: str) -> Tuple[int, ...]:
    tail = chunk_id.split("-", 1)[1] if "-" in chunk_id else chunk_id
    return tuple(int(x) for x in re.findall(r"\d+", tail))


def reconstruct_text(rows: List[Tuple[str, Dict[str, Any], str]]) -> str:
    """Eski parçaları sayfa/alt parça sırasıyla birleştirir (parçalar örtüşmez)."""
    pages: Dict[int, List[Tuple[Tuple[int, ...], str]]] = {}
    for cid, _meta, doc in rows:
        order = _chunk_order(cid)
        pages.setdefault(order[0] if order else 0, []).append((order, doc))
    return "\n".join("".join(doc for _, doc in sorted(parts)) for _, parts in sorted(pages.items()))


def plan(rag, index) -> Dict[str, Any]:
    from mufettis.common.utils import sha256_bytes

    groups = _legacy_groups(rag)
    deposit = {sha256_bytes(p.read_bytes()): p for p in sorted(DEPOSIT_DIR.glob("*.pdf"))} if DEPOSIT_DIR.is_dir() else {}
    indexed = set(index.data.get("indexed_items", {}))
    out = {"deposit_pdfs": [], "reconstruct": [], "delete_superseded": [], "legacy_chunks": sum(len(v) for v in groups.values())}
    for h, path in deposit.items():
        out["deposit_pdfs"].append({"file": path.name, "hash": h[:14], "indexed": h in indexed, "legacy_chunks": len(groups.get(h, []))})
    for h, rows in groups.items():
        if h in deposit or h in indexed:
            continue
        source = str(rows[0][1].get("source", ""))
        first_line = rows[0][2].strip().splitlines()[0] if rows[0][2].strip() else ""
        if source.lower().endswith(".pdf"):
            out["reconstruct"].append({"hash": h, "source": source, "title": first_line[:80], "chunks": len(rows)})
        else:
            out["delete_superseded"].append({"hash": h, "source": source, "title": str(rows[0][1].get("title", "")), "chunks": len(rows)})
    return out


def backup(stamp: str) -> Path:
    target = new_backup_dir("e7_legacy_recovery", stamp)
    from mufettis.rag.index import IngestIndex

    IngestIndex().backup(target / "ingest_index.sqlite3")  # tutarlı SQLite yedeği
    shutil.copytree(SOURCE_CACHE_DIR, target / "source_cache")
    from mufettis.rag.store import backup_vector_store

    backup_vector_store(target)  # yalnızca etkin parça deposu (SQLite veya Chroma)
    return target


def apply(rag, index, the_plan: Dict[str, Any]) -> Dict[str, Any]:
    from mufettis.common.utils import enrich_source_meta, now_iso
    from mufettis.importers.bulk_importer import BulkRegulationImporter
    from mufettis.legal.parser import extract_legal_metadata
    from mufettis.rag.maintenance import versioned_ingest_text

    report: Dict[str, Any] = {}
    # 1. Klasördeki PDF'ler (eski parçaları olanlar güncel şemayla değiştirilir)
    bulk = BulkRegulationImporter(folder=DEPOSIT_DIR, rag=rag, index=index).import_folder()
    report["bulk_import"] = {k: bulk.get(k) for k in ("total", "ingested", "quarantined", "skipped_index", "skipped_chroma", "errors", "chunks")}
    report["bulk_files"] = [{k: f.get(k) for k in ("file", "status", "chunks", "error")} for f in bulk.get("files", [])]

    def ingest_reconstructed(file_hash: str, source: str, rows) -> Dict[str, Any]:
        text = reconstruct_text(rows)
        first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
        title = first_line[:120] or source
        meta = extract_legal_metadata(title, text, enrich_source_meta({
            "file_hash": file_hash,
            "title": title,
            "url": "",
            "source": "Yerel mevzuat paketi (eski şemadan kurtarıldı)",
            "timestamp": now_iso(),
            "ingest_origin": "legacy_chunk_recovery",
            "legacy_source_file": source,
        }, "local_file"))
        # force: PDF'i karantinaya düşen belgenin indeks kaydı zaten var; eski parçalar
        # yeni şemayla değiştirilir (kapı reddederse önceki hali geri yükler).
        result = versioned_ingest_text(rag, index, text, meta, force=True)
        status = result.get("status")
        chunks = int(result.get("count", 0) or 0)
        if status not in ("quarantined", "blocked"):  # karantinada kaydı kapı yazar
            final_meta = result.get("meta", meta)
            final_meta["chunk_count"] = chunks
            index.upsert(file_hash, final_meta)
        return {"title": title, "source": source, "status": status, "chunks": chunks, "error": result.get("error", "")}

    # 2. PDF'i olmayan eski belgeler + PDF'i kapıdan geçemeyen ama eski parçaları temiz olanlar
    groups = _legacy_groups(rag)
    targets = [(item["hash"], item["source"]) for item in the_plan["reconstruct"]]
    targets += [
        (h, str(rows[0][1].get("source", "")))
        for h, rows in groups.items()
        if h not in {t[0] for t in targets} and (index.get(h) or {}).get("status") != "active"
        and str(rows[0][1].get("source", "")).lower().endswith(".pdf")
    ]
    report["recovered"] = [
        ingest_reconstructed(h, source, groups[h]) for h, source in targets if groups.get(h)
    ]

    # 3. Yerini daha yeni sürüm almış eski BDDK tek parçaları
    for item in the_plan["delete_superseded"]:
        rag.delete_hash(item["hash"])
    report["deleted_superseded"] = len(the_plan["delete_superseded"])

    # 4. Mevcut belgelerde durum / kanun numarası yenileme (yalnızca meta veri)
    report["metadata_refresh"] = refresh_metadata(rag, index)
    return report


def refresh_metadata(rag, index) -> Dict[str, Any]:
    """Düzeltilmiş kurallarla status ve law_number'ı yeniden hesaplar; metin/embedding değişmez.

    Önceden BDDK menüsündeki "Mülga Düzenlemeler" ve madde notları "(Mülga: …)" yüzünden
    yürürlükteki belgeler "repealed_or_changed" sayılıp aramadan dışlanıyordu.
    """
    from mufettis.legal.parser import extract_legal_metadata

    changed = []
    for file_hash, record in list(index.data.get("indexed_items", {}).items()):
        if record.get("status") in ("quarantined",):
            continue
        cache = SOURCE_CACHE_DIR / f"{file_hash}.txt"
        if not cache.exists():
            continue
        fresh = extract_legal_metadata(record.get("title", ""), cache.read_text(encoding="utf-8"), dict(record))
        updates = {k: fresh.get(k) for k in ("status", "law_number") if str(fresh.get(k) or "") != str(record.get(k) or "")}
        if not updates:
            continue
        if "law_number" in updates and str(record.get("document_key") or "") in ("", str(record.get("law_number") or "")):
            updates["document_key"] = fresh.get("document_key")
        rag.update_hash_metadata(file_hash, {k: str(v or "") for k, v in updates.items()})
        index.upsert(file_hash, {**record, **updates})
        changed.append({"title": str(record.get("title", ""))[:40], **{k: [record.get(k), v] for k, v in updates.items()}})
    return {"changed": len(changed), "details": changed}


def verify(rag, index=None) -> Dict[str, Any]:
    groups = _legacy_groups(rag)
    still_legacy = []
    for h, rows in groups.items():
        record = (index.get(h) if index is not None else None) or {}
        still_legacy.append({
            "hash": h[:14],
            "source": str(rows[0][1].get("source", "")),
            "chunks": len(rows),
            "index_status": record.get("status", ""),
            "reason": (record.get("last_rejected_ingest") or {}).get("reason") or record.get("quarantine_reason", ""),
        })
    checks = []
    for law, q in VERIFY_QUERIES:
        results = rag.query(q, k=3)
        laws = [str((r.get("metadata") or {}).get("law_number", "")) for r in results]
        checks.append({"query": q, "expected_law": law, "found": law in laws, "top_laws": laws})
    return {
        "legacy_chunks_remaining": sum(len(v) for v in groups.values()),
        # Karantinaya alınan belgelerin eski parçaları korunur (kapı önceki sürümü geri yükler).
        "still_legacy": still_legacy,
        "queries": checks,
    }


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

    report = apply(rag, index, the_plan)
    report["verify"] = verify(rag, index)
    report["backup"] = str(backup_dir)
    (backup_dir / "manifest.json").write_text(json.dumps({"plan": the_plan, "report": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = report["verify"]["legacy_chunks_remaining"] == 0 and all(c["found"] for c in report["verify"]["queries"])
    if ok:
        for old in prune_backups():  # saklama politikası: mufettis/common/backups.py
            print(f"Eski yedek silindi (manifest korundu): {old.name}")
    print(f"\nUYGULANDI. Doğrulama: {'BAŞARILI' if ok else 'KONTROL GEREKİYOR'}. Yedek: {backup_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
