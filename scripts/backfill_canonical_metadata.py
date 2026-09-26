#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kanonik corpus alanlarını (mufettis.corpus) mevcut index kayıtlarına ve Chroma parçalarına yazar.

Yalnızca meta veri değişir; metin ve embedding'ler aynı kalır. Adımlar:
  1. Her index kaydı için içerik özeti kaynak önbellekteki metinden hesaplanır ve kanonik
     alanlar türetilir (kimlik, yayıncı niteliği, doğrulama, belge türü, normatif rol).
  2. İçerik özeti resmî kanaldan alınmış bir kopyayla birebir aynı olan kopyalara
     `verified_against` yazılır (doğrulama: fingerprint_matched).
  3. Her Chroma parçası kendi meta verisinden (parça yapısı dahil) yeniden türetilir.

Kurallar veri kaynağından bağımsızdır; klasör/dosya adı kullanılmaz. Script tekrar
çalıştırılabilir (idempotent): değişiklik yoksa hiçbir şey yazmaz.

Varsayılan: yalnızca plan. --apply: yedek + uygula + doğrula.
Veri dizini MUFETTIS_DATA_DIR ile değiştirilebilir (ör. production'ın bir kopyası).

Kullanım:
  python scripts/backfill_canonical_metadata.py            # plan
  python scripts/backfill_canonical_metadata.py --apply    # yedek + uygula + doğrula
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.config import CHROMA_UPSERT_BATCH_SIZE, DATA_DIR, DB_PATH, INGEST_INDEX_FILE, SOURCE_CACHE_DIR
from mufettis.corpus import CANONICAL_SCHEMA, with_canonical
from mufettis.corpus.canonical import DERIVATION_INPUTS
from mufettis.corpus.identity import content_fingerprint
from mufettis.corpus.model import CANONICAL_FIELDS
from mufettis.corpus.provenance import reconcile_fingerprints
from mufettis.legal.parser import extract_declared_title


def _cache_text(file_hash: str) -> str:
    path = SOURCE_CACHE_DIR / f"{file_hash}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# Kanonik türetmeye girmeyen ama belge düzeyinde parçalara da taşınan alanlar.
DOCUMENT_FIELDS = ("verified_against", "title", "title_normalized", "declared_title")


def _canonical_part(meta: Dict[str, Any]) -> Dict[str, str]:
    return {k: str(meta.get(k, "")) for k in (*CANONICAL_FIELDS, *DOCUMENT_FIELDS)}


# Eski yönetim ekranı yüklenen dosyayı "upload_<uuid>_<ad>" geçici adıyla içe aktarıyordu ve başlık
# bu addan türüyordu. Rastgele önek başlık tabanlı Work kimliğine de giriyordu.
LEGACY_UPLOAD_PREFIX = re.compile(r"^upload_[0-9a-f]{32}_")


def clean_legacy_title(rec: Dict[str, Any]) -> Dict[str, Any]:
    title = str(rec.get("title") or "")
    if not LEGACY_UPLOAD_PREFIX.match(title):
        return rec
    clean = LEGACY_UPLOAD_PREFIX.sub("", title)
    return {**rec, "title": clean, "title_normalized": re.sub(r"\s+", " ", clean).strip().lower()}


def plan_index(items: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Saf karar: her index kaydının kanonik alanlarla güncellenmiş hali."""
    fresh: Dict[str, Dict[str, Any]] = {}
    for h, rec in items.items():
        rec = clean_legacy_title(rec)
        text = _cache_text(h)
        if text.strip():
            rec = {**rec, "declared_title": extract_declared_title(text)}
        fresh[h] = with_canonical(rec, text) if text.strip() else with_canonical(rec)
    # Özdeş içerik doğrulaması tüm corpus üzerinde yapılır.
    by_manifestation = {rec["manifestation_id"]: h for h, rec in fresh.items()}
    for upd in reconcile_fingerprints({rec["manifestation_id"]: rec for rec in fresh.values()}):
        h = by_manifestation[upd["manifestation_id"]]
        fresh[h] = with_canonical({**fresh[h], "verified_against": upd["verified_against"]})
    return fresh


def plan(rag, index) -> Dict[str, Any]:
    items = index.data.get("indexed_items", {})
    fresh = plan_index(items)
    index_updates = [h for h, rec in items.items() if _canonical_part(rec) != _canonical_part(fresh[h])]

    got = rag.collection.get(include=["metadatas"])
    chunk_updates = 0
    for meta in got.get("metadatas") or []:
        meta = meta or {}
        target = _chunk_target(meta, fresh.get(str(meta.get("file_hash", ""))))
        if _canonical_part(meta) != _canonical_part(target):
            chunk_updates += 1

    active = [rec for rec in fresh.values() if rec.get("status") not in ("superseded", "quarantined")]
    return {
        "index_records": len(items),
        "index_updates": len(index_updates),
        "chunks": len(got.get("ids") or []),
        "chunk_updates": chunk_updates,
        "active_distribution": {
            field: dict(Counter(str(rec.get(field)) for rec in active).most_common())
            for field in ("publisher_kind", "verification", "instrument_type", "normative_role", "identity_basis")
        },
        "fingerprint_matched": sorted(
            f"{rec.get('title', '')[:40]} -> {rec.get('verified_against', '')[:12]}"
            for rec in fresh.values() if rec.get("verified_against")
        ),
        "shared_works": {
            work: titles for work, titles in _group_titles(active, "work_id").items() if len(titles) > 1
        },
    }


def _group_titles(records: List[Dict[str, Any]], field: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for rec in records:
        out.setdefault(str(rec.get(field)), []).append(f"{rec.get('source_id')}:{str(rec.get('title', ''))[:30]}")
    return out


def _chunk_target(meta: Dict[str, Any], record: Dict[str, Any] | None) -> Dict[str, Any]:
    """Parçanın hedef meta verisi. Belge düzeyi kimlik girdileri index kaydından alınır (eski
    parçalarda bu alanlar kayıttan sapmış olabilir, ör. source_document_id); parça düzeyi tek
    girdi olan yapı tipi parçanın kendisinden gelir (normatif rol parçanın yapısına bağlıdır)."""
    inputs = dict(meta)
    if record:
        for key in DERIVATION_INPUTS:
            if key != "structure_type":
                inputs[key] = record.get(key) or ""
        inputs["declared_title"] = record.get("declared_title") or ""
    return {k: ("" if v is None else str(v)) for k, v in with_canonical(inputs).items()}


def backup(stamp: str) -> Path:
    target = DATA_DIR / "backups" / f"canonical_backfill_{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(INGEST_INDEX_FILE, target / "ingest_index.json")
    shutil.copytree(DB_PATH, target / "mevzuat_db")  # Chroma açılmadan önce
    return target


def apply(rag, index) -> Dict[str, Any]:
    items = index.data.get("indexed_items", {})
    fresh = plan_index(items)
    index_written = 0
    for h, rec in items.items():
        if _canonical_part(rec) != _canonical_part(fresh[h]):
            index.upsert(h, fresh[h])
            index_written += 1

    got = rag.collection.get(include=["metadatas"])
    ids, metas = [], []
    for cid, meta in zip(got.get("ids") or [], got.get("metadatas") or []):
        meta = meta or {}
        target = _chunk_target(meta, fresh.get(str(meta.get("file_hash", ""))))
        if _canonical_part(meta) != _canonical_part(target):
            ids.append(cid)
            metas.append(target)
    for start in range(0, len(ids), CHROMA_UPSERT_BATCH_SIZE):
        rag.collection.update(ids=ids[start:start + CHROMA_UPSERT_BATCH_SIZE],
                              metadatas=metas[start:start + CHROMA_UPSERT_BATCH_SIZE])
    return {"index_written": index_written, "chunks_written": len(ids)}


def verify(rag, index) -> Dict[str, Any]:
    items = index.data.get("indexed_items", {})
    got = rag.collection.get(include=["metadatas"])
    metas = [m or {} for m in got.get("metadatas") or []]
    missing_chunks = sum(1 for m in metas if m.get("canonical_schema") != CANONICAL_SCHEMA)
    missing_index = [h[:12] for h, r in items.items() if r.get("canonical_schema") != CANONICAL_SCHEMA]
    # Parçalar ile index kaydı aynı Work/Expression'ı göstermeli.
    mismatched = sorted({
        str(m.get("file_hash", ""))[:12] for m in metas
        if str(m.get("file_hash", "")) in items and (
            m.get("work_id") != items[m["file_hash"]].get("work_id")
            or m.get("expression_id") != items[m["file_hash"]].get("expression_id"))
    })
    fingerprints_ok = all(
        items[h].get("content_fingerprint") == content_fingerprint(_cache_text(h))
        for h in items if _cache_text(h).strip()
    )
    return {"chunks_without_canonical": missing_chunks, "index_without_canonical": missing_index,
            "chunk_index_identity_mismatch": mismatched, "fingerprints_match_cache": fingerprints_ok,
            "remaining_updates": plan(rag, index)["chunk_updates"]}


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

    report = apply(rag, index)
    report["verify"] = verify(rag, index)
    report["backup"] = str(backup_dir)
    (backup_dir / "manifest.json").write_text(json.dumps({"plan": the_plan, "report": report},
                                                         ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    v = report["verify"]
    ok = (v["chunks_without_canonical"] == 0 and not v["index_without_canonical"]
          and not v["chunk_index_identity_mismatch"] and v["fingerprints_match_cache"]
          and v["remaining_updates"] == 0)
    print(f"\nUYGULANDI. Doğrulama: {'BAŞARILI' if ok else 'KONTROL GEREKİYOR'}. Yedek: {backup_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
