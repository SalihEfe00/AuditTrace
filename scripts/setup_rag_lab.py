#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FAZ 11B Test Corpus Setup — Isolated Mini RAG Corpus.

This script creates an isolated test corpus for FAZ 11B retrieval tests:
1. Zero production Chroma mutation (uses data/rag_lab/chroma).
2. Uses 5 real PDF fixtures (4 from user uploads + 1 accounting plan).
3. Uses canonical extraction, normalization, legal chunking, and embedding.
4. Stores source cache in data/rag_lab/source_cache.
5. Injects metadata environment="rag_lab".
6. Completely isolated from production scanner/repair/commit.
7. Executes smoke tests: document, accounting, exact, semantic, metadata filtering, retrievability.
8. Produces data/rag_lab/retrieval_baseline.json for FAZ 11B quality gate.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mufettis.config import DATA_DIR, EMBEDDING_MODEL, CHUNK_SCHEMA_VERSION
from mufettis.common.utils import sha256_bytes, now_iso
from mufettis.extraction.text_repair import normalize_extracted_text, repair_extracted_spacing
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper
from mufettis.legal.parser import (
    extract_legal_metadata,
    _chunk_is_retrievable,
)
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex

# Isolated directories
RAG_LAB_DIR = DATA_DIR / "rag_lab"
RAG_LAB_FIXTURES = RAG_LAB_DIR / "fixtures"
RAG_LAB_CHROMA = RAG_LAB_DIR / "chroma"
RAG_LAB_SOURCE_CACHE = RAG_LAB_DIR / "source_cache"
RAG_LAB_INDEX_FILE = RAG_LAB_DIR / "ingest_index.json"
RAG_LAB_BASELINE_FILE = RAG_LAB_DIR / "retrieval_baseline.json"


def generate_accounting_plan_pdf() -> bytes:
    """Generates a valid PDF-1.4 binary for BDDK Tekdüzen Hesap Planı."""
    title = "BANKALARCA UYGULANACAK TEKDUZEN HESAP PLANI VE IZAHNAMESI"
    lines = [
        "010 KASA",
        "011 EFEKTIF DEPOSU",
        "012 YOLDAKI PARALAR - T.P.",
        "013 YOLDAKI PARALAR - Y.P.",
        "014 SATIN ALINAN BANKA CEKLERI - T.P.",
        "100 KREDILER",
        "280 DIGER ALACAKLAR",
    ]
    stream_lines = ["BT", "/F1 12 Tf", "50 720 Td", f"({title}) Tj"]
    for l in lines:
        clean_l = l.replace("(", r"\(").replace(")", r"\)")
        stream_lines.append("0 -22 Td")
        stream_lines.append(f"({clean_l}) Tj")
    stream_lines.append("ET")
    stream_content = "\n".join(stream_lines).encode("latin-1", "replace")

    objects = []
    objects.append(b"1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n")
    objects.append(b"2 0 obj\n<</Type/Pages/Count 1/Kids[3 0 R]>>\nendobj\n")
    objects.append(
        b"3 0 obj\n<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>\nendobj\n"
    )
    objects.append(b"4 0 obj\n<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>\nendobj\n")
    obj5 = (
        b"5 0 obj\n<</Length "
        + str(len(stream_content)).encode()
        + b">>\nstream\n"
        + stream_content
        + b"\nendstream\nendobj\n"
    )
    objects.append(obj5)

    header = b"%PDF-1.4\n"
    out = bytearray(header)
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)

    xref_start = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer\n<</Root 1 0 R/Size {len(offsets)}>>\nstartxref\n{xref_start}\n%%EOF\n".encode()
    )
    return bytes(out)


def setup_fixtures() -> List[Dict[str, Any]]:
    """Sets up the 5 PDF fixtures in RAG_LAB_FIXTURES."""
    RAG_LAB_FIXTURES.mkdir(parents=True, exist_ok=True)
    RAG_LAB_SOURCE_CACHE.mkdir(parents=True, exist_ok=True)
    RAG_LAB_CHROMA.mkdir(parents=True, exist_ok=True)

    sources = [
        {
            "id": "doc1_sermaye_yeterliligi",
            "filename": "01_sermaye_yeterliligi.pdf",
            "source_path": Path(
                "/home/keremc/.gemini/antigravity/brain/67bad724-f392-4ed0-8f76-5e71d0163d53/.user_uploaded/media_1789736170273.pdf"
            ),
            "title": "BANKALARIN SERMAYE YETERLİLİĞİNİN ÖLÇÜLMESİNE VE DEĞERLENDİRİLMESİNE İLİŞKİN YÖNETMELİK",
            "doc_type": "Yönetmelik",
            "law_number": "5411",
        },
        {
            "id": "doc2_finansal_manipulasyon",
            "filename": "02_finansal_manipulasyon.pdf",
            "source_path": Path(
                "/home/keremc/.gemini/antigravity/brain/67bad724-f392-4ed0-8f76-5e71d0163d53/.user_uploaded/media_1789741395908.pdf"
            ),
            "title": "FİNANSAL PİYASALARDA MANİPÜLASYON VE YANILTICI İŞLEMLER HAKKINDA YÖNETMELİK",
            "doc_type": "Yönetmelik",
            "law_number": "5411",
        },
        {
            "id": "doc3_kredi_islemleri",
            "filename": "03_kredi_islemleri.pdf",
            "source_path": Path(
                "/home/keremc/.gemini/antigravity/brain/67bad724-f392-4ed0-8f76-5e71d0163d53/.user_uploaded/media_1789741395911.pdf"
            ),
            "title": "BANKALARIN KREDİ İŞLEMLERİNE İLİŞKİN YÖNETMELİK",
            "doc_type": "Yönetmelik",
            "law_number": "5411",
        },
        {
            "id": "doc4_risk_gruplari_kredi_sinirlari",
            "filename": "04_risk_gruplari_kredi_sinirlari.pdf",
            "source_path": Path(
                "/home/keremc/.gemini/antigravity/brain/67bad724-f392-4ed0-8f76-5e71d0163d53/.user_uploaded/media_1789741395915.pdf"
            ),
            "title": "RİSK GRUPLARININ BELİRLENMESİNE VE KREDİ SINIRLARINA İLİŞKİN YÖNETMELİK",
            "doc_type": "Yönetmelik",
            "law_number": "5411",
        },
        {
            "id": "doc5_tekduzen_hesap_plani",
            "filename": "05_tekduzen_hesap_plani.pdf",
            "source_path": None,  # Generated
            "title": "BANKALARCA UYGULANACAK TEKDÜZEN HESAP PLANI VE İZAHNAMESİ",
            "doc_type": "Tebliğ",
            "law_number": "5411",
        },
    ]

    prepared = []
    for s in sources:
        dest_pdf = RAG_LAB_FIXTURES / s["filename"]
        if s["source_path"] is not None and s["source_path"].exists():
            shutil.copy2(s["source_path"], dest_pdf)
        else:
            dest_pdf.write_bytes(generate_accounting_plan_pdf())

        pdf_bytes = dest_pdf.read_bytes()
        s_hash = sha256_bytes(pdf_bytes)
        prepared.append({
            **s,
            "target_path": dest_pdf,
            "file_hash": s_hash,
            "size": len(pdf_bytes),
        })
    return prepared


def ingest_fixtures(prepared_fixtures: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Ingests all fixtures using canonical extraction & LocalRAG in isolation."""
    rag = LocalRAG(
        db_path=RAG_LAB_CHROMA,
        model_name=EMBEDDING_MODEL,
        allow_download=False,
    )
    index = IngestIndex(path=RAG_LAB_INDEX_FILE)
    scraper = ResmiGazeteScraper(local_only=True)

    results = {}
    for doc in prepared_fixtures:
        fpath = doc["target_path"]
        raw = fpath.read_bytes()
        file_hash = doc["file_hash"]

        # Canonical extraction
        raw_text, page_count, ocr_used = scraper._extract_pdf_text(raw)

        # Normalization
        text = normalize_extracted_text(raw_text)
        text = repair_extracted_spacing(text, doc["title"], doc["doc_type"])

        # Save to isolated source cache
        cache_file = RAG_LAB_SOURCE_CACHE / f"{file_hash}.txt"
        cache_file.write_text(text, encoding="utf-8")

        # Extract legal metadata
        meta = {
            "file_hash": file_hash,
            "title": doc["title"],
            "document_type": doc["doc_type"],
            "source_document_id": doc["id"],
            "document_id": doc["id"],
            "source_type": "official",
            "authority_rank": 100,
            "status": "active",
            "environment": "rag_lab",
            "environment_label": "isolated_test_corpus",
            "law_number": doc.get("law_number", "5411"),
            "page_count": page_count,
            "ocr_used": ocr_used,
            "ingested_at": now_iso(),
        }
        meta = extract_legal_metadata(doc["title"], text, meta)
        # Preserve specific lab metadata
        if doc.get("law_number"):
            meta["law_number"] = doc["law_number"]
        meta["status"] = "active"
        meta["document_indexable"] = True
        meta["evidence_indexable"] = True
        meta["environment"] = "rag_lab"
        meta["environment_label"] = "isolated_test_corpus"

        # Ingest into Chroma
        ingest_res = rag.ingest_text(text, meta, force=True)
        chunk_count = ingest_res.get("count", 0)

        meta["chunk_count"] = chunk_count
        meta["chunk_schema_version"] = CHUNK_SCHEMA_VERSION
        index.upsert(file_hash, meta)

        results[doc["id"]] = {
            "title": doc["title"],
            "hash": file_hash,
            "pages": page_count,
            "chunks": chunk_count,
            "structure_type": meta.get("structure_type"),
            "account_code_count": meta.get("account_code_count", 0),
        }

    return {
        "rag": rag,
        "index": index,
        "total_docs": len(prepared_fixtures),
        "total_chunks": rag.count(),
        "details": results,
    }


def run_smoke_tests(rag: LocalRAG) -> Dict[str, Any]:
    """Runs the 6 required retrieval smoke tests on the isolated corpus."""
    test_results = {}

    # 1. Document retrieval
    res_doc = rag.query("FİNANSAL PİYASALARDA MANİPÜLASYON VE YANILTICI İŞLEMLER", k=3)
    test_results["document_retrieval"] = {
        "query": "FİNANSAL PİYASALARDA MANİPÜLASYON VE YANILTICI İŞLEMLER",
        "passed": (
            len(res_doc) > 0
            and any(
                "MANİPÜLASYON" in (r.get("document", "") + " " + r.get("metadata", {}).get("title", "")).upper()
                for r in res_doc
            )
        ),
        "count": len(res_doc),
        "top_match_title": res_doc[0]["metadata"].get("title") if res_doc else None,
    }

    # 2. Accounting retrieval
    res_acc = rag.query("010 KASA", k=3)
    test_results["accounting_retrieval"] = {
        "query": "010 KASA",
        "passed": any("010" in r.get("document", "") or r.get("metadata", {}).get("account_code") == "010" for r in res_acc),
        "count": len(res_acc),
        "found_codes": [r.get("metadata", {}).get("account_code") for r in res_acc if r.get("metadata", {}).get("account_code")],
    }

    # 3. Exact retrieval
    res_exact = rag.query("5411 sayılı Kanun", k=5)
    test_results["exact_retrieval"] = {
        "query": "5411 sayılı Kanun",
        "passed": len(res_exact) > 0 and any(r.get("metadata", {}).get("law_number") == "5411" for r in res_exact),
        "count": len(res_exact),
        "top_law": res_exact[0]["metadata"].get("law_number") if res_exact else None,
    }

    # 4. Semantic retrieval
    res_sem = rag.query("bankalarda kredi açma yetkisi ve kredi komitesi çalışma esasları", k=5)
    test_results["semantic_retrieval"] = {
        "query": "bankalarda kredi açma yetkisi ve kredi komitesi çalışma esasları",
        "passed": len(res_sem) > 0 and any("kredi" in r.get("document", "").lower() for r in res_sem),
        "count": len(res_sem),
        "top_match_excerpt": res_sem[0]["document"][:120] if res_sem else None,
    }

    # 5. Metadata filtering
    got_lab = rag.collection.get(
        where={"environment": "rag_lab"},
        include=["metadatas"],
    )
    got_acc = rag.collection.get(
        where={"structure_type": "accounting_plan"},
        include=["metadatas"],
    )
    test_results["metadata_filtering"] = {
        "passed": len(got_lab.get("ids", [])) == rag.count() and len(got_acc.get("ids", [])) > 0,
        "environment_rag_lab_count": len(got_lab.get("ids", [])),
        "accounting_plan_chunks": len(got_acc.get("ids", [])),
    }

    # 6. Retrievability
    all_chunks = rag.collection.get(include=["documents", "metadatas"])
    docs = all_chunks.get("documents", [])
    metas = all_chunks.get("metadatas", [])
    retrievable_count = sum(1 for d, m in zip(docs, metas) if _chunk_is_retrievable(m, d))
    test_results["retrievability"] = {
        "total_chunks_checked": len(docs),
        "retrievable_count": retrievable_count,
        "ratio": round(retrievable_count / len(docs), 4) if docs else 0,
        "passed": retrievable_count == len(docs),
    }

    return test_results


def build_retrieval_baseline(rag: LocalRAG) -> Dict[str, Any]:
    """Produces the current retrieval baseline for FAZ 11B Quality Gate."""
    benchmark_queries = [
        {"id": "BM-01", "type": "exact_law", "query": "5411 sayılı Kanun"},
        {"id": "BM-02", "type": "document_lookup", "query": "Finansal Piyasalarda Manipülasyon ve Yanıltıcı İşlemler"},
        {"id": "BM-03", "type": "document_lookup", "query": "Bankaların Kredi İşlemlerine İlişkin Yönetmelik"},
        {"id": "BM-04", "type": "document_lookup", "query": "Risk Gruplarının Belirlenmesine ve Kredi Sınırlarına İlişkin"},
        {"id": "BM-05", "type": "document_lookup", "query": "Sermaye Yeterliliğinin Ölçülmesine ve Değerlendirilmesine"},
        {"id": "BM-06", "type": "accounting_lookup", "query": "010 KASA"},
        {"id": "BM-07", "type": "accounting_lookup", "query": "280 DIGER ALACAKLAR"},
        {"id": "BM-08", "type": "semantic", "query": "kredi komitesinin oluşumu ve çalışma esasları"},
        {"id": "BM-09", "type": "semantic", "query": "manipülasyon ve yanıltıcı piyasa işlemleri cezai sorumluluk"},
        {"id": "BM-10", "type": "semantic", "query": "risk gruplarında ekonomik bağımlılık ölçütleri"},
    ]

    baseline = {
        "version": "1.0.0",
        "created_at": now_iso(),
        "environment": "rag_lab",
        "model_name": rag.model_name,
        "total_documents": 5,
        "total_chunks": rag.count(),
        "benchmarks": [],
    }

    for b in benchmark_queries:
        res = rag.query(b["query"], k=3)
        entries = []
        for r in res:
            m = r.get("metadata", {})
            entries.append({
                "document_id": m.get("document_id") or m.get("source_document_id"),
                "title": m.get("title"),
                "chunk_no": m.get("chunk_no"),
                "chunk_type": m.get("chunk_type"),
                "account_code": m.get("account_code"),
                "distance": r.get("distance"),
                "text_excerpt": (r.get("document") or "")[:200],
            })
        baseline["benchmarks"].append({
            "id": b["id"],
            "type": b["type"],
            "query": b["query"],
            "results_count": len(entries),
            "results": entries,
        })

    RAG_LAB_BASELINE_FILE.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    return baseline


def main() -> None:
    print("=== FAZ 11B TEST CORPUS SETUP ===")
    fixtures = setup_fixtures()
    print(f"1. Fixtures prepared: {len(fixtures)} PDFs in {RAG_LAB_FIXTURES}")

    ingest_summary = ingest_fixtures(fixtures)
    rag = ingest_summary["rag"]
    print(f"2. Ingestion completed: {ingest_summary['total_docs']} docs, {ingest_summary['total_chunks']} chunks")
    for k, v in ingest_summary["details"].items():
        print(f"   - {k}: {v['chunks']} chunks, pages={v['pages']}, type={v['structure_type']}")

    smoke_results = run_smoke_tests(rag)
    print("\n3. Retrieval Smoke Tests:")
    for k, v in smoke_results.items():
        status = "PASS" if v.get("passed") else "FAIL"
        print(f"   - {k}: {status}")

    baseline = build_retrieval_baseline(rag)
    print(f"\n4. Retrieval Baseline generated: {len(baseline['benchmarks'])} queries saved to {RAG_LAB_BASELINE_FILE}")


if __name__ == "__main__":
    main()
