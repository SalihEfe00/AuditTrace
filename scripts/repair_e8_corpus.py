#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E8: PDF çıkarma bozulmalarını, bitişik parçaları ve hatalı sürüm zincirlerini onarır.

Arka plan:
  * PDFium, ToUnicode eşlemesi olmayan glifleri U+FFFE olarak döndürüyordu ("Tar\\uFFFEh\\uFFFE").
    GVK/VUK/6183 PDF'leri bu yüzden karantinaya düştü (E7'de eski parçalardan geri kuruldu),
    KVK 5520 ve Damga Vergisi 488 ise hiç aranamıyor. Çıkarma artık bu sayfaları başka
    motordan alıyor; PDF'ler yeniden içe aktarılır.
  * Eski bir parçalayıcı sürümü hesap planı satırlarındaki boşlukları silmiş
    ("İŞTİRAKLERVEBAĞLIORTAKLIKLAR"); kaynak önbellekteki metin sağlam. Bu belgeler güncel
    parçalayıcıyla önbellekten yeniden parçalanır (kapı üzerinden; reddedilirse eski hal korunur).
  * document_key, Resmî Gazete "Sayı"sı veya atıf yapılan kanun numarasından türediği için
    farklı BDDK belgeleri birbirinin "sürümü" sayılmıştı (956→955→…→934). Anahtarlar kaynak
    kimliğiyle (DokumanGetir/<id>) yeniden hesaplanır; aynı belgenin birden çok indirmesinde
    en yenisi aktif kalır, diğerleri "superseded" olur; yanlış superseded_by bağları temizlenir.

Varsayılan: yalnızca plan (hiçbir şey yazmaz). --apply: yedek + uygula + doğrula.
Veri dizini MUFETTIS_DATA_DIR ile değiştirilebilir (ör. production'ın bir kopyası).

Kullanım:
  python scripts/repair_e8_corpus.py            # plan
  python scripts/repair_e8_corpus.py --apply    # yedek + uygula + doğrula
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
from typing import Any, Dict, Iterable, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.config import BASE_DIR, DATA_DIR, DB_PATH, SOURCE_CACHE_DIR

DEPOSIT_DIR = BASE_DIR / "mevzuat_deposu"
INVALID_CHARS = ("￾", "￿", "\x00")
GLUED_RE = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû]{20,}")
# Parçalarda güncel parçalayıcının ürettiğinden bu kadar fazla uzun "kelime" varsa parçalar bozuk sayılır.
GLUED_EXCESS_THRESHOLD = 5
VERIFY_QUERIES = [
    ("5520", "5520 sayılı Kurumlar Vergisi Kanunu kurum kazancı"),
    ("488", "488 sayılı Damga Vergisi Kanunu damga vergisine tabi kağıtlar"),
    ("193", "193 sayılı Gelir Vergisi Kanunu gelirin unsurları"),
    ("5411", "5411 sayılı Bankacılık Kanunu kredi sınırları"),
]
INACTIVE_STATUSES = {"quarantined", "superseded"}


def invalid_count(text: str) -> int:
    return sum(str(text or "").count(c) for c in INVALID_CHARS)


def glued_count(texts: Iterable[str]) -> int:
    return sum(len(GLUED_RE.findall(str(t or ""))) for t in texts)


def plan_version_chains(items: Dict[str, Dict[str, Any]], new_keys: Dict[str, str]) -> Dict[str, Any]:
    """Saf karar fonksiyonu: aynı (source_id, document_key) altındaki kayıtlardan en yenisi
    aktif kalır; diğerleri ona "superseded" olur. Farklı anahtara işaret eden superseded_by
    bağları yanlıştır ve temizlenir. Karantinadaki kayıtlar zincire katılmaz."""
    groups: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for h, rec in items.items():
        if str(rec.get("status", "")) == "quarantined":
            continue
        groups[(str(rec.get("source_id", "")), new_keys.get(h, str(rec.get("document_key", ""))))].append(h)

    supersede: List[Dict[str, str]] = []
    keep: Dict[str, str] = {}
    for (_, key), hashes in groups.items():
        ordered = sorted(
            hashes,
            key=lambda h: (str(items[h].get("timestamp", "")), int(items[h].get("version_number", 1) or 1)),
        )
        newest = ordered[-1]
        keep[newest] = key
        for h in ordered[:-1]:
            if items[h].get("status") != "superseded" or items[h].get("superseded_by") != newest:
                supersede.append({"hash": h, "superseded_by": newest, "key": key,
                                  "title": str(items[h].get("title", ""))[:60]})

    clear_links = []
    for h, key in keep.items():
        target = str(items[h].get("superseded_by") or "")
        if target or items[h].get("status") == "superseded":
            clear_links.append({"hash": h, "old_superseded_by": target, "key": key,
                                "title": str(items[h].get("title", ""))[:60]})
    return {"supersede": supersede, "clear_links": clear_links, "groups": len(groups)}


def _chunk_texts_by_hash(rag) -> Dict[str, List[str]]:
    from mufettis.rag.paging import iter_pages

    out: Dict[str, List[str]] = defaultdict(list)
    for _ids, docs, metas in iter_pages(rag.collection):  # sayfalı: tek bir tam get kopyası tutulmaz
        for doc, meta in zip(docs, metas):
            out[str(meta.get("file_hash", ""))].append(doc or "")
    return out


def _cache_text(file_hash: str) -> str:
    path = SOURCE_CACHE_DIR / f"{file_hash}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def current_chunk_texts(text: str, record: Dict[str, Any]) -> List[str]:
    """Bugünkü parçalayıcının aynı metinden üreteceği parça metinleri (LocalRAG.ingest_text ile aynı adımlar)."""
    from mufettis.extraction.text_repair import repair_extracted_spacing
    from mufettis.legal.parser import split_legal_chunks

    return [
        repair_extracted_spacing(ch["text"], record.get("title", ""), record.get("document_type", ""))
        for ch in split_legal_chunks(text)
    ]


def _recomputed_identity(file_hash: str, record: Dict[str, Any]) -> Dict[str, str]:
    from mufettis.legal.parser import extract_legal_metadata

    fresh = extract_legal_metadata(record.get("title", ""), _cache_text(file_hash), dict(record))
    return {k: str(fresh.get(k) or "") for k in ("document_key", "law_number", "source_document_id")}


def plan(rag, index) -> Dict[str, Any]:
    from mufettis.common.utils import sha256_bytes

    items = index.data.get("indexed_items", {})
    chunk_texts = _chunk_texts_by_hash(rag)

    pdf_targets = []
    for path in sorted(DEPOSIT_DIR.glob("*.pdf")):
        file_hash = sha256_bytes(path.read_bytes())
        rec = items.get(file_hash) or {}
        chunks = chunk_texts.get(file_hash, [])
        reasons = []
        if rec.get("status") == "quarantined":
            reasons.append("quarantined")
        if rec.get("ingest_origin") == "legacy_chunk_recovery":
            reasons.append("legacy_reconstruction")
        if any(invalid_count(t) for t in chunks):
            reasons.append("invalid_unicode_in_chunks")
        if reasons:
            pdf_targets.append({"file": path.name, "hash": file_hash, "reasons": reasons, "chunks": len(chunks)})

    pdf_hashes = {t["hash"] for t in pdf_targets}
    rechunk = []
    for h, rec in items.items():
        if h in pdf_hashes or rec.get("status") == "quarantined":
            continue
        cache = _cache_text(h)
        chunks = chunk_texts.get(h, [])
        if not cache.strip() or not chunks:
            continue
        excess = glued_count(chunks) - glued_count(current_chunk_texts(cache, rec))
        if excess > GLUED_EXCESS_THRESHOLD:
            rechunk.append({"hash": h, "title": str(rec.get("title", ""))[:60], "chunks": len(chunks),
                            "glued_excess": excess})

    identity_updates = []
    new_keys: Dict[str, str] = {}
    for h, rec in items.items():
        fresh = _recomputed_identity(h, rec) if _cache_text(h) else {
            "document_key": str(rec.get("document_key", "")),
            "law_number": str(rec.get("law_number", "")),
            "source_document_id": str(rec.get("source_document_id", "")),
        }
        new_keys[h] = fresh["document_key"]
        changed = {k: [str(rec.get(k) or ""), v] for k, v in fresh.items() if str(rec.get(k) or "") != v}
        if changed:
            identity_updates.append({"hash": h, "title": str(rec.get("title", ""))[:60], **changed})

    return {
        "pdf_reimport": pdf_targets,
        "rechunk": rechunk,
        "identity_updates": identity_updates,
        "version_chains": plan_version_chains(items, new_keys),
        "invalid_unicode_chunks": sum(1 for ts in chunk_texts.values() for t in ts if invalid_count(t)),
    }


def backup(stamp: str) -> Path:
    target = DATA_DIR / "backups" / f"e8_corpus_repair_{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    from mufettis.rag.index import IngestIndex

    IngestIndex().backup(target / "ingest_index.sqlite3")  # tutarlı SQLite yedeği
    shutil.copytree(SOURCE_CACHE_DIR, target / "source_cache")
    shutil.copytree(DB_PATH, target / "mevzuat_db")  # Chroma açılmadan önce
    from mufettis.rag.store import backup_corpus_store

    backup_corpus_store(target)  # SQLite parça deposu (etkinse değiştirilecek olan odur)
    return target


def apply(rag, index, the_plan: Dict[str, Any]) -> Dict[str, Any]:
    from mufettis.importers.bulk_importer import BulkRegulationImporter
    from mufettis.legal.parser import extract_legal_metadata
    from mufettis.rag.maintenance import versioned_ingest_text
    from repair.ingestion_gate import AutoHealingIngestionGate

    report: Dict[str, Any] = {}

    # 1. PDF'ler düzeltilmiş çıkarma ile yeniden içe aktarılır (kapı reddederse önceki hal kalır).
    importer = BulkRegulationImporter(folder=DEPOSIT_DIR, rag=rag, index=index)
    pdf_results = []
    for target in the_plan["pdf_reimport"]:
        path = DEPOSIT_DIR / target["file"]
        raw = path.read_bytes()
        text, pages, ocr_used = importer._text_from_file(path, raw)
        meta = importer._build_meta(path, target["hash"], text, pages, ocr_used, len(raw))
        result = versioned_ingest_text(rag, index, text, meta, force=True, raw_bytes=raw, source_path=str(path))
        status = result.get("status")
        if status not in ("quarantined", "blocked"):
            final_meta = dict(result.get("meta", meta))
            final_meta["chunk_count"] = int(result.get("count", 0) or 0)
            index.upsert(target["hash"], final_meta)
        pdf_results.append({"file": target["file"], "status": status, "chunks": result.get("count", 0),
                            "invalid_in_text": invalid_count(text), "error": result.get("error", "")})
    report["pdf_reimport"] = pdf_results

    # 2. Bitişik parçalar önbellekteki sağlam metinden yeniden parçalanır.
    gate = AutoHealingIngestionGate()
    rechunk_results = []
    for target in the_plan["rechunk"]:
        record = dict(index.get(target["hash"]) or {})
        cached = _cache_text(target["hash"])
        meta = extract_legal_metadata(record.get("title", ""), cached, record)
        result = gate.ingest(rag, index, cached, meta, force=True)
        rechunk_results.append({"title": target["title"], "status": result.get("status"),
                                "chunks_before": target["chunks"], "chunks_after": result.get("count", 0),
                                "error": result.get("error", "")})
    report["rechunk"] = rechunk_results

    # 3. Kimlik alanları (yalnızca meta veri; metin/embedding değişmez)
    for upd in the_plan["identity_updates"]:
        h = upd["hash"]
        record = index.get(h)
        if not record:
            continue
        fresh = {k: v[1] for k, v in upd.items() if isinstance(v, list)}
        rag.update_hash_metadata(h, fresh)
        index.upsert(h, {**record, **fresh})
    report["identity_updates"] = len(the_plan["identity_updates"])

    # 4. Sürüm zincirleri: adımlar 1-2 kayıtları "active" yazdığından karar yeniden hesaplanır.
    items = index.data.get("indexed_items", {})
    chains = plan_version_chains(items, {h: str(r.get("document_key", "")) for h, r in items.items()})
    for item in chains["clear_links"]:
        # upsert mevcut kayıtla birleştirir: alanı kayıttan çıkarmak onu silmez, boş değer yazılır.
        index.upsert(item["hash"], {"status": "active", "superseded_by": "", "valid_to": ""})
        rag.update_hash_metadata(item["hash"], {"status": "active", "superseded_by": "", "valid_to": ""})
    for item in chains["supersede"]:
        record = index.get(item["hash"]) or {}
        stamp = str(index.get(item["superseded_by"]).get("timestamp", "")) or dt.datetime.now().isoformat()
        index.upsert(item["hash"], {**record, "status": "superseded", "superseded_by": item["superseded_by"], "valid_to": stamp})
        rag.mark_hash_superseded(item["hash"], item["superseded_by"], effective_to=stamp)
    report["version_chains"] = {"cleared_links": len(chains["clear_links"]), "superseded": len(chains["supersede"]),
                                "details": chains["supersede"]}
    return report


def verify(rag, index) -> Dict[str, Any]:
    items = index.data.get("indexed_items", {})
    chunk_texts = _chunk_texts_by_hash(rag)
    bad_chunks = sum(1 for ts in chunk_texts.values() for t in ts if invalid_count(t))
    glued_docs = []
    for h, rec in items.items():
        if rec.get("status") in INACTIVE_STATUSES:
            continue
        cache = _cache_text(h)
        if cache and glued_count(chunk_texts.get(h, [])) - glued_count(current_chunk_texts(cache, rec)) > GLUED_EXCESS_THRESHOLD:
            glued_docs.append(str(rec.get("title", ""))[:40])
    keys = defaultdict(list)
    for h, rec in items.items():
        if rec.get("status") not in INACTIVE_STATUSES:
            keys[(str(rec.get("source_id", "")), str(rec.get("document_key", "")))].append(str(rec.get("title", ""))[:30])
    duplicate_active = {f"{k[0]}:{k[1]}": v for k, v in keys.items() if len(v) > 1}
    checks = []
    for law, q in VERIFY_QUERIES:
        results = rag.query(q, k=3)
        laws = [str((r.get("metadata") or {}).get("law_number", "")) for r in results]
        checks.append({"query": q, "expected_law": law, "found": law in laws, "top_laws": laws})
    quarantined = [str(r.get("title", "")) for r in items.values() if r.get("status") == "quarantined"]
    return {
        "invalid_unicode_chunks": bad_chunks,
        "glued_docs": glued_docs,
        "duplicate_active_keys": duplicate_active,
        "quarantined": quarantined,
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
    v = report["verify"]
    ok = (v["invalid_unicode_chunks"] == 0 and not v["glued_docs"] and not v["duplicate_active_keys"]
          and not v["quarantined"] and all(c["found"] for c in v["queries"]))
    print(f"\nUYGULANDI. Doğrulama: {'BAŞARILI' if ok else 'KONTROL GEREKİYOR'}. Yedek: {backup_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
