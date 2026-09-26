# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import io

try:
    import requests
except Exception:
    requests = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    from docx import Document
except Exception:
    Document = None

from mufettis.audit.service import append_audit
from mufettis.common.utils import canonical_source_id, now_iso, sha256_bytes, source_profile
from mufettis.config import CHROMA_COLLECTION, CHUNK_SCHEMA_VERSION, SOURCE_CACHE_DIR
from mufettis.extraction.pdf import (
    _extract_pdf_with_pdfplumber,
    _pdf_generic_quality_metrics,
    _pdf_text_quality,
    _reconstruct_pdf_spatial_text,
)
from mufettis.extraction.text_repair import normalize_extracted_text, repair_extracted_spacing
from mufettis.legal.parser import (
    _chunk_is_retrievable,
    _chunk_matches_account_ref,
    _chunk_matches_document_ref,
    classify_legal_structure,
    extract_legal_metadata,
    legal_diff,
)
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper, enrich_source_meta
from mufettis.rag.index import IngestIndex
from mufettis.rag.engine import LocalRAG

def save_source_cache(file_hash: str, text: str) -> Path:
    """Belge hash'i için güncel extraction metnini cache'e yazar.

    Aynı PDF hash'i altında extraction davranışı düzeltildiğinde eski bozuk
    metnin kalmasını önlemek için mevcut cache artık sessizce korunmaz;
    güncel metin güvenli geçici dosya üzerinden atomik olarak değiştirilir.
    """
    cache_dir = SOURCE_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{file_hash}.txt"
    payload = str(text or "")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    return path
def load_source_cache(file_hash: str) -> str:
    cache_dir = SOURCE_CACHE_DIR
    path = cache_dir / f"{file_hash}.txt"
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        return ""
def _rag_chunk_quality_summary(rag: Any, file_hash: str) -> Dict[str, Any]:
    """Tek bir production file_hash için Chroma metin kalite özetini çıkarır."""
    if rag is None or getattr(rag, "collection", None) is None:
        return {
            "chroma_available": False,
            "chunk_count": 0,
            "glued_token_count": 0,
            "very_long_token_count": 0,
            "replacement_char_count": 0,
            "mojibake_sequence_count": 0,
            "min_quality_score": None,
            "max_quality_score": None,
        }
    try:
        got = rag.collection.get(
            where={"file_hash": file_hash},
            include=["documents"],
        )
    except Exception as exc:
        return {
            "chroma_available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "chunk_count": 0,
            "glued_token_count": 0,
            "very_long_token_count": 0,
            "replacement_char_count": 0,
            "mojibake_sequence_count": 0,
            "min_quality_score": None,
            "max_quality_score": None,
        }

    docs = got.get("documents", []) or []
    qualities = [_pdf_generic_quality_metrics(str(doc or "")) for doc in docs]
    scores = [float(q.get("score", 0.0) or 0.0) for q in qualities]
    return {
        "chroma_available": True,
        "chunk_count": len(docs),
        "glued_token_count": sum(int(q.get("glued_token_count", 0) or 0) for q in qualities),
        "very_long_token_count": sum(int(q.get("very_long_token_count", 0) or 0) for q in qualities),
        "replacement_char_count": sum(int(q.get("replacement_char_count", 0) or 0) for q in qualities),
        "mojibake_sequence_count": sum(int(q.get("mojibake_sequence_count", 0) or 0) for q in qualities),
        "min_quality_score": min(scores) if scores else None,
        "max_quality_score": max(scores) if scores else None,
    }
def scan_production_rag_quality(
    index: IngestIndex,
    rag: Any = None,
    source_ids: Optional[List[str]] = None,
    quality_threshold: float = 0.88,
) -> Dict[str, Any]:
    """Production source_cache + Chroma kalite taraması; hiçbir kaydı değiştirmez."""
    wanted = {canonical_source_id(x) for x in source_ids} if source_ids else None
    rows: List[Dict[str, Any]] = []
    suspicious: List[Dict[str, Any]] = []

    for file_hash, meta in sorted(
        index.data.get("indexed_items", {}).items(),
        key=lambda item: (
            canonical_source_id(str(item[1].get("source_id", ""))),
            str(item[1].get("title", "")),
            str(item[0]),
        ),
    ):
        meta = dict(meta or {})
        sid = canonical_source_id(str(meta.get("source_id", "")))
        if wanted is not None and sid not in wanted:
            continue

        cache_text = load_source_cache(file_hash)
        cache_quality = _pdf_generic_quality_metrics(cache_text) if cache_text else {
            "score": 0.0, "chars": 0, "glued_token_count": 0,
            "very_long_token_count": 0, "replacement_char_count": 0,
            "mojibake_sequence_count": 0,
        }
        chroma_quality = _rag_chunk_quality_summary(rag, file_hash)

        stale_reasons: List[str] = []
        if not cache_text.strip():
            stale_reasons.append("missing_cache")
        if int(cache_quality.get("glued_token_count", 0) or 0) > 0:
            stale_reasons.append("cache_glued_text")
        if int(cache_quality.get("replacement_char_count", 0) or 0) > 0:
            stale_reasons.append("cache_replacement_char")
        if int(cache_quality.get("mojibake_sequence_count", 0) or 0) > 0:
            stale_reasons.append("cache_mojibake")
        if float(cache_quality.get("score", 0.0) or 0.0) < float(quality_threshold) and cache_text.strip():
            stale_reasons.append("cache_low_quality")
        if chroma_quality.get("chroma_available") and int(chroma_quality.get("glued_token_count", 0) or 0) > 0:
            stale_reasons.append("chroma_glued_text")
        if chroma_quality.get("chroma_available") and int(chroma_quality.get("replacement_char_count", 0) or 0) > 0:
            stale_reasons.append("chroma_replacement_char")
        if chroma_quality.get("chroma_available") and int(chroma_quality.get("mojibake_sequence_count", 0) or 0) > 0:
            stale_reasons.append("chroma_mojibake")
        if chroma_quality.get("chroma_available") and chroma_quality.get("min_quality_score") is not None and float(chroma_quality["min_quality_score"]) < float(quality_threshold):
            stale_reasons.append("chroma_low_quality")

        row = {
            "file_hash": str(file_hash),
            "title": str(meta.get("title", "") or ""),
            "source_id": sid,
            "source": str(meta.get("source", "") or ""),
            "status": str(meta.get("status", "") or ""),
            "cache_chars": int(cache_quality.get("chars", 0) or 0),
            "cache_quality_score": float(cache_quality.get("score", 0.0) or 0.0),
            "cache_glued_token_count": int(cache_quality.get("glued_token_count", 0) or 0),
            "cache_replacement_char_count": int(cache_quality.get("replacement_char_count", 0) or 0),
            "cache_mojibake_sequence_count": int(cache_quality.get("mojibake_sequence_count", 0) or 0),
            "chroma": chroma_quality,
            "reasons": list(dict.fromkeys(stale_reasons)),
            "repair_candidate": bool(stale_reasons),
        }
        rows.append(row)
        if stale_reasons:
            suspicious.append(row)

    return {
        "ok": True,
        "quality_threshold": float(quality_threshold),
        "total_documents": len(rows),
        "repair_candidates": len(suspicious),
        "healthy_documents": len(rows) - len(suspicious),
        "documents": rows,
        "candidates": suspicious,
        "dry_run": True,
    }
def repair_production_document_from_pdf(
    rag: Any,
    index: IngestIndex,
    pdf_path: Path,
    dry_run: bool = True,
    quality_threshold: float = 0.88,
    extractor: Optional[Any] = None,
) -> Dict[str, Any]:
    """Aynı hash'e ait production belgesini PDF'den yeniden çıkarıp cache/chunk/index yeniler.

    dry_run=True varsayılandır: extraction + kalite + plan kontrol edilir, hiçbir kalıcı veri
    değiştirilmez. Gerçek onarımda PDF hash'i mevcut index kaydıyla birebir eşleşmelidir.
    """
    path = Path(pdf_path)
    if not path.is_file() or path.suffix.lower() != ".pdf":
        return {"ok": False, "status": "invalid_pdf", "path": str(path)}

    pdf_bytes = path.read_bytes()
    file_hash = sha256_bytes(pdf_bytes)
    meta = dict(index.get(file_hash) or {})
    if not meta:
        return {
            "ok": False,
            "status": "missing_index",
            "file_hash": file_hash,
            "path": str(path),
        }

    if extractor is None:
        scraper = ResmiGazeteScraper(index=index, local_only=True)
        extracted_text, page_count, ocr_used = scraper._extract_pdf_text(pdf_bytes)
    else:
        extracted_text, page_count, ocr_used = extractor(pdf_bytes)

    normalized = normalize_extracted_text(extracted_text)
    normalized = repair_extracted_spacing(
        normalized, meta.get("title", path.name), meta.get("document_type", "")
    )
    quality = _pdf_generic_quality_metrics(normalized)
    result = {
        "ok": bool(normalized.strip()),
        "status": "dry_run" if dry_run else "planned",
        "file_hash": file_hash,
        "title": meta.get("title", path.name),
        "path": str(path),
        "page_count": int(page_count or 0),
        "ocr_used": bool(ocr_used),
        "chars": int(quality.get("chars", 0) or 0),
        "quality_score": float(quality.get("score", 0.0) or 0.0),
        "glued_token_count": int(quality.get("glued_token_count", 0) or 0),
        "replacement_char_count": int(quality.get("replacement_char_count", 0) or 0),
        "mojibake_sequence_count": int(quality.get("mojibake_sequence_count", 0) or 0),
        "dry_run": bool(dry_run),
        "would_reindex": False,
    }

    if not normalized.strip():
        result["status"] = "empty_extraction"
        result["ok"] = False
        return result

    if result["quality_score"] < float(quality_threshold):
        result["status"] = "quality_reject"
        result["ok"] = False
        return result

    result["would_reindex"] = True
    if dry_run:
        return result

    save_source_cache(file_hash, normalized)
    row = rechunk_cached_document(rag, index, file_hash)
    result["status"] = row.get("status", "reindexed")
    result["reindexed_chunks"] = int(row.get("chunks", 0) or 0)
    result["would_reindex"] = result["status"] == "reindexed"
    if result["status"] != "reindexed":
        result["ok"] = False
    return result
def _diagnostic_focus_text(value: Any, target: str = "", max_chars: int = 1800) -> str:
    """Teşhis görünümü için metnin hedef çevresindeki bölümünü döndürür.

    Hiçbir veri düzeltmez; yalnızca inceleme çıktısını kısaltır.
    """
    text = str(value or "")
    if not text:
        return "[BOŞ]"
    target = str(target or "").strip()
    if target:
        # Önce hedef tam değerini, sonra boşluksuz karşılığını ara.
        needles = [target, re.sub(r"\s+", "", target)]
        lower = text.lower()
        compact = re.sub(r"\s+", "", text).lower()
        for needle in needles:
            if not needle:
                continue
            pos = lower.find(needle.lower())
            if pos >= 0:
                start = max(0, pos - 350)
                end = min(len(text), pos + max_chars - 350)
                return text[start:end].strip()
            cneedle = needle.lower()
            cpos = compact.find(cneedle)
            if cpos >= 0:
                # compact->original yaklaşık eşleme
                count = 0
                orig_pos = 0
                for i, ch in enumerate(text):
                    if not ch.isspace():
                        if count >= cpos:
                            orig_pos = i
                            break
                        count += 1
                start = max(0, orig_pos - 350)
                end = min(len(text), orig_pos + max_chars - 350)
                return text[start:end].strip()
    if len(text) <= max_chars:
        return text.strip()
    return text[:max_chars].rstrip() + "\n...[kısaltıldı]..."
def _diagnostic_quality(value: Any) -> Dict[str, Any]:
    text = str(value or "")
    score, info = _pdf_text_quality(text)
    compact = re.sub(r"\s+", "", text)
    return {
        "chars": int(len(compact)),
        "spaces": int(info.get("spaces", 0)),
        "long_glued": int(info.get("long_glued", 0)),
        "long_upper": int(info.get("long_upper", 0)),
        "quality_score": round(float(score), 4),
    }
def _diagnostic_pdf_candidates(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """Gerçek PDF için hiçbir adayı seçmeden bütün extraction sonuçlarını döndür."""
    candidates: List[Tuple[str, str]] = []

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        plain_pages: List[str] = []
        layout_pages: List[str] = []
        for page in reader.pages:
            try:
                plain_pages.append(page.extract_text() or "")
            except Exception:
                plain_pages.append("")
            try:
                layout_pages.append(page.extract_text(extraction_mode="layout") or "")
            except Exception:
                layout_pages.append("")
        plain = "\n".join(plain_pages).strip()
        layout = "\n".join(layout_pages).strip()
        if plain:
            candidates.append(("pypdf_plain", plain))
        if layout and layout != plain:
            candidates.append(("pypdf_layout", layout))
    except Exception as exc:
        candidates.append(("pypdf_error", f"{type(exc).__name__}: {exc}"))

    pdfium_text = _reconstruct_pdf_spatial_text(pdf_bytes)
    if pdfium_text:
        candidates.append(("pypdfium2", pdfium_text))

    plumber = _extract_pdf_with_pdfplumber(pdf_bytes)
    if plumber:
        candidates.append(("pdfplumber_words", plumber))

    result: List[Dict[str, Any]] = []
    for name, raw in candidates:
        repaired = raw if name.endswith("_error") else repair_extracted_spacing(raw)
        result.append({
            "name": name,
            "raw": raw,
            "repaired": repaired,
            "quality_raw": _diagnostic_quality(raw),
            "quality_repaired": _diagnostic_quality(repaired),
        })
    return result
def _sha256_text(value: str, compact: bool = False) -> str:
    """Teşhis için metnin ham veya whitespace-normalize SHA-256 özetini döndür."""
    text = str(value or "")
    if compact:
        text = re.sub(r"\s+", "", text)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
def _diagnostic_cache_excerpt(text: str, account_ref: str = "", max_chars: int = 1800) -> str:
    """Cache'in yalnızca hedef hesap çevresini göster; cache'i değiştirmez."""
    return _diagnostic_focus_text(text, account_ref, max_chars=max_chars)
def _collect_exact_document_account_matches(
    rag: LocalRAG,
    document_ref: str,
    account_ref: str = "",
) -> List[Dict[str, Any]]:
    """Belge+hesap için güncel Chroma kayıtlarını provenance amacıyla toplar."""
    got = rag.collection.get(
        where={"chunk_schema_version": CHUNK_SCHEMA_VERSION},
        include=["documents", "metadatas"],
    )
    ids = got.get("ids", []) or []
    docs = got.get("documents", []) or []
    metas = got.get("metadatas", []) or []
    rows: List[Dict[str, Any]] = []
    for rid, doc, meta in zip(ids, docs, metas):
        meta = dict(meta or {})
        text = str(doc or "")
        if not _chunk_is_retrievable(meta, text):
            continue
        if not _chunk_matches_document_ref(meta, text, document_ref):
            continue
        if account_ref and not _chunk_matches_account_ref(meta, text, account_ref):
            continue
        rows.append({
            "id": str(rid),
            "title": str(meta.get("title", "") or ""),
            "source": str(meta.get("source", "") or ""),
            "url": str(meta.get("url", "") or ""),
            "file_hash": str(meta.get("file_hash", "") or ""),
            "document_key": str(meta.get("document_key", "") or ""),
            "source_document_id": str(meta.get("source_document_id", "") or ""),
            "account_code": str(meta.get("account_code", "") or ""),
            "chunk_type": str(meta.get("chunk_type", "") or ""),
            "chunk_no": str(meta.get("chunk_no", "") or ""),
            "document": text,
            "document_sha256": _sha256_text(text),
            "document_compact_sha256": _sha256_text(text, compact=True),
            "excerpt": _diagnostic_focus_text(text, account_ref or document_ref, 1200),
        })
    rows.sort(key=lambda x: (x.get("chunk_no", ""), x.get("id", "")))
    return rows
def run_rag_provenance_compare(
    rag: LocalRAG,
    document_a: str,
    account_a: str,
    document_b: str,
    account_b: str,
) -> Dict[str, Any]:
    """İki belgeyi cache -> chunk -> Chroma boyunca karşılaştırır; hiçbir şeyi değiştirmez."""
    a = str(document_a or "").strip()
    b = str(document_b or "").strip()
    aa = str(account_a or "").strip()
    ab = str(account_b or "").strip()
    for label, value in (("Belge A", a), ("Belge B", b)):
        if not value:
            return {"ok": False, "error": f"{label} kimliği zorunlu."}
    for label, value in (("Hesap A", aa), ("Hesap B", ab)):
        if value and not re.fullmatch(r"\d{3,6}", value):
            return {"ok": False, "error": f"{label} 3-6 haneli rakam olmalı."}

    try:
        rows_a = _collect_exact_document_account_matches(rag, a, aa)
        rows_b = _collect_exact_document_account_matches(rag, b, ab)
    except Exception as exc:
        return {"ok": False, "error": f"Chroma provenance okunamadı: {type(exc).__name__}: {exc}"}

    def build_side(rows: List[Dict[str, Any]], doc_ref: str, acct_ref: str) -> Dict[str, Any]:
        unique_hashes: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            fh = row.get("file_hash", "")
            if not fh or fh in unique_hashes:
                continue
            cache = load_source_cache(fh)
            unique_hashes[fh] = {
                "file_hash": fh,
                "cache_exists": bool(cache),
                "cache_chars": len(re.sub(r"\s+", "", cache)),
                "cache_sha256": _sha256_text(cache),
                "cache_compact_sha256": _sha256_text(cache, compact=True),
                "cache_excerpt": _diagnostic_cache_excerpt(cache, acct_ref or doc_ref, 1800),
                "url": row.get("url", ""),
                "title": row.get("title", ""),
            }
        cache_rows = list(unique_hashes.values())
        return {
            "document_ref": doc_ref,
            "account_ref": acct_ref,
            "exact_match_count": len(rows),
            "exact_chunks": [
                {
                    k: row[k] for k in (
                        "id", "title", "source", "url", "file_hash", "document_key",
                        "source_document_id", "account_code", "chunk_type", "chunk_no",
                        "document_sha256", "document_compact_sha256", "excerpt",
                    )
                }
                for row in rows
            ],
            "source_caches": cache_rows,
        }

    side_a = build_side(rows_a, a, aa)
    side_b = build_side(rows_b, b, ab)

    cache_a = side_a["source_caches"]
    cache_b = side_b["source_caches"]
    chunk_a = {x["document_compact_sha256"] for x in side_a["exact_chunks"]}
    chunk_b = {x["document_compact_sha256"] for x in side_b["exact_chunks"]}
    cache_hash_a = {x["cache_compact_sha256"] for x in cache_a if x["cache_exists"]}
    cache_hash_b = {x["cache_compact_sha256"] for x in cache_b if x["cache_exists"]}

    same_cache_content = bool(cache_hash_a and cache_hash_b and cache_hash_a.intersection(cache_hash_b))
    same_chunk_content = bool(chunk_a and chunk_b and chunk_a.intersection(chunk_b))

    if not side_a["exact_match_count"] or not side_b["exact_match_count"]:
        diagnosis = "Hedef belgelerden en az biri için exact Chroma kaydı eksik; provenance karşılaştırması tamamlanamadı."
    elif same_cache_content and side_a["document_ref"] != side_b["document_ref"]:
        diagnosis = "KIRILMA ADAYI: SOURCE CACHE / KAYNAK PROVENANCE — iki farklı belge aynı cache metnini taşıyor."
    elif same_chunk_content and not same_cache_content:
        diagnosis = "KIRILMA ADAYI: CHUNK / CHROMA INDEX — cache içerikleri farklı iken farklı belgelerde aynı chunk metni var."
    else:
        diagnosis = "Cache ve exact chunk içerikleri farklı; çapraz belge karışması semantic retrieval/reranking katmanında ayrıca incelenmeli."

    return {
        "ok": True,
        "diagnosis": diagnosis,
        "chroma_count": rag.count(),
        "same_cache_content": same_cache_content,
        "same_chunk_content": same_chunk_content,
        "document_a": side_a,
        "document_b": side_b,
    }
def run_rag_diagnostic(
    rag: LocalRAG,
    document_ref: str,
    account_ref: str = "",
) -> Dict[str, Any]:
    """RAG zincirini gözlemle; hiçbir kaydı değiştirme, silme veya indexleme yapma."""
    doc_ref = str(document_ref or "").strip()
    acct_ref = str(account_ref or "").strip()
    if not doc_ref:
        return {"ok": False, "error": "Belge kimliği zorunlu."}
    if acct_ref and not re.fullmatch(r"\d{3,6}", acct_ref):
        return {"ok": False, "error": "Hesap kodu 3-6 haneli rakam olmalı."}

    try:
        got = rag.collection.get(
            where={"chunk_schema_version": CHUNK_SCHEMA_VERSION},
            include=["documents", "metadatas"],
        )
    except Exception as exc:
        return {"ok": False, "error": f"Chroma okunamadı: {type(exc).__name__}: {exc}"}

    docs = got.get("documents", []) or []
    metas = got.get("metadatas", []) or []
    matches: List[Tuple[str, Dict[str, Any], str]] = []
    ids = got.get("ids", []) or []
    for rid, doc, meta in zip(ids, docs, metas):
        meta = meta or {}
        if not _chunk_is_retrievable(meta, doc):
            continue
        if not _chunk_matches_document_ref(meta, doc, doc_ref):
            continue
        if acct_ref and not _chunk_matches_account_ref(meta, doc, acct_ref):
            continue
        matches.append((str(rid), dict(meta), str(doc or "")))

    if not matches:
        return {
            "ok": False,
            "error": "Hedef Chroma chunk bulunamadı.",
            "chroma_count": rag.count(),
            "matches": [],
        }

    # Aynı belge/kod için bütün eşleşmeleri göster; tek kaydı keyfi seçme.
    matches.sort(key=lambda x: (
        str(x[1].get("account_code", "")),
        int(str(x[1].get("chunk_no", "0") or "0")) if str(x[1].get("chunk_no", "0") or "0").isdigit() else 0,
        x[0],
    ))
    primary_id, primary_meta, primary_doc = matches[0]

    source_hash = str(primary_meta.get("file_hash", "") or "")
    cache_text = load_source_cache(source_hash) if source_hash else ""

    source_url = str(primary_meta.get("url", "") or "")
    downloaded_pdf = b""
    fetch_info: Dict[str, Any] = {"status": "not_attempted", "url": source_url}
    if source_url:
        try:
            from mufettis.common.http import build_http_session  # requests opsiyonel; hata aşağıda yakalanır

            with build_http_session() as session:
                response = session.get(
                    source_url,
                    timeout=20,
                    verify=True,
                    headers={"User-Agent": "MufettisAI-RAG-Diagnostic/0.13.48"},
                )
            fetch_info.update({
                "status": f"http_{response.status_code}",
                "content_type": response.headers.get("Content-Type", ""),
                "bytes": len(response.content or b""),
            })
            if response.status_code == 200 and (
                source_url.lower().endswith(".pdf")
                or "application/pdf" in response.headers.get("Content-Type", "").lower()
                or bytes(response.content[:4]) == b"%PDF"
            ):
                downloaded_pdf = response.content
        except Exception as exc:
            fetch_info.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})

    extraction = _diagnostic_pdf_candidates(downloaded_pdf) if downloaded_pdf else []
    all_chunk_docs = [m[2] for m in matches]

    # Normal semantic query ayrıca gösterilir; fakat teşhisin "exact" katmanı yerine geçmez.
    try:
        semantic = rag.query(acct_ref or doc_ref, k=5) if (acct_ref or doc_ref) else []
    except Exception as exc:
        semantic = [{"error": f"{type(exc).__name__}: {exc}"}]

    stages: List[Dict[str, Any]] = []
    if extraction:
        for candidate in extraction:
            stages.append({
                "stage": candidate["name"] + " / raw",
                "text": _diagnostic_focus_text(candidate["raw"], acct_ref or doc_ref),
                "quality": candidate["quality_raw"],
            })
            stages.append({
                "stage": candidate["name"] + " / repaired",
                "text": _diagnostic_focus_text(candidate["repaired"], acct_ref or doc_ref),
                "quality": candidate["quality_repaired"],
            })
    stages.append({
        "stage": "source_cache",
        "text": _diagnostic_focus_text(cache_text, acct_ref or doc_ref),
        "quality": _diagnostic_quality(cache_text),
        "file_hash": source_hash,
    })
    for rid, meta, doc in matches:
        stages.append({
            "stage": "chroma_chunk",
            "id": rid,
            "text": _diagnostic_focus_text(doc, acct_ref or doc_ref),
            "quality": _diagnostic_quality(doc),
            "metadata": {
                "title": meta.get("title", ""),
                "document_key": meta.get("document_key", ""),
                "source_document_id": meta.get("source_document_id", ""),
                "account_code": meta.get("account_code", ""),
                "chunk_type": meta.get("chunk_type", ""),
                "chunk_no": meta.get("chunk_no", ""),
            },
        })

    return {
        "ok": True,
        "target": {"document_ref": doc_ref, "account_ref": acct_ref},
        "chroma_count": rag.count(),
        "match_count": len(matches),
        "primary": {
            "id": primary_id,
            "metadata": primary_meta,
            "document": primary_doc,
        },
        "source": {
            "file_hash": source_hash,
            "cache_exists": bool(cache_text),
            "cache_chars": len(re.sub(r"\s+", "", cache_text)),
            "url": source_url,
            "fetch": fetch_info,
        },
        "extraction": extraction,
        "stages": stages,
        "semantic_results": [
            {
                "id": str(x.get("metadata", {}).get("file_hash", ""))[:24] + "#" + str(x.get("metadata", {}).get("chunk_no", "")),
                "title": x.get("metadata", {}).get("title", ""),
                "source_document_id": x.get("metadata", {}).get("source_document_id", ""),
                "account_code": x.get("metadata", {}).get("account_code", ""),
                "chunk_type": x.get("metadata", {}).get("chunk_type", ""),
                "document_excerpt": _diagnostic_focus_text(x.get("document", ""), acct_ref or doc_ref, 600),
            }
            for x in semantic if isinstance(x, dict) and "metadata" in x
        ],
        "exact_matches": [
            {
                "id": rid,
                "document_ref_match": _chunk_matches_document_ref(meta, doc, doc_ref),
                "account_ref_match": (not acct_ref) or _chunk_matches_account_ref(meta, doc, acct_ref),
                "title": meta.get("title", ""),
                "source_document_id": meta.get("source_document_id", ""),
                "account_code": meta.get("account_code", ""),
                "chunk_type": meta.get("chunk_type", ""),
                "chunk_no": meta.get("chunk_no", ""),
                "excerpt": _diagnostic_focus_text(doc, acct_ref or doc_ref, 900),
            }
            for rid, meta, doc in matches
        ],
    }
def find_previous_version(index: IngestIndex, meta: Dict[str, Any], exclude_hash: Optional[str] = None) -> Optional[Dict[str, Any]]:
    source_id = meta.get("source_id", "")
    document_key = meta.get("document_key", meta.get("title_normalized", ""))
    candidates = []
    for h, item in index.data.get("indexed_items", {}).items():
        if exclude_hash and h == exclude_hash:
            continue
        if source_id and item.get("source_id") and item.get("source_id") != source_id:
            continue
        old_key = item.get("document_key", item.get("title_normalized", ""))
        if old_key and old_key == document_key:
            candidates.append((int(item.get("version_number", 1) or 1), item.get("timestamp", ""), item))
    if not candidates:
        return None
    candidates.sort(reverse=True, key=lambda x: (x[0], x[1]))
    return candidates[0][2]
def reset_chroma_collection(rag: LocalRAG) -> None:
    """Mevcut mevzuat koleksiyonunu tamamen silip temiz koleksiyon oluştur."""
    try:
        rag.client.delete_collection(name=CHROMA_COLLECTION)
    except Exception:
        pass
    rag.collection = rag.client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        embedding_function=None,
    )
def rebuild_rag_from_source_cache(
    rag: LocalRAG,
    index: IngestIndex,
    source_ids: Optional[List[str]] = None,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """Legacy chunk'ları tamamen kaldırıp cache'teki gerçek belgelerden yeniden kur."""
    wanted = {canonical_source_id(x) for x in source_ids} if source_ids else None
    items = []

    for file_hash, meta in index.data.get("indexed_items", {}).items():
        sid = canonical_source_id(str(meta.get("source_id", "")))
        if wanted is not None and sid not in wanted:
            continue
        cached = load_source_cache(file_hash)
        if cached.strip():
            items.append((file_hash, meta, cached))

    items.sort(key=lambda x: (
        canonical_source_id(str(x[1].get("source_id", ""))),
        str(x[1].get("title", "")),
    ))

    result = {
        "started_at": now_iso(),
        "total_documents": len(items),
        "processed": 0,
        "documents_ok": 0,
        "chunks": 0,
        "errors": 0,
        "not_indexed": 0,
        "finished_at": "",
    }

    for done, (file_hash, meta, cached) in enumerate(items, 1):
        try:
            ingest_meta = extract_legal_metadata(
                meta.get("title", ""),
                cached,
                dict(meta),
            )
            ingest_meta["chunk_schema_version"] = CHUNK_SCHEMA_VERSION
            ingest_meta["chunk_metadata_valid"] = False

            # v0.13.34: parse edilemeyen hukuki hiyerarşi belgeyi RAG'dan
            # düşürmez. Gerçek indekslenebilirlik aşağıdaki chunk sonucuyla
            # belirlenir.

            row = rag.ingest_text(cached, ingest_meta, force=True)
            count = int(row.get("count", 0) or 0)

            updated = dict(meta)
            updated.update({
                "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                "chunk_metadata_valid": True,
                "chunk_count": count,
                "rebuild_at": now_iso(),
                "structure_type": ingest_meta.get("structure_type", updated.get("structure_type", "document")),
                "parse_status": ingest_meta.get("parse_status", updated.get("parse_status", "")),
                "legal_structure_resolved": bool(ingest_meta.get("legal_structure_resolved", updated.get("legal_structure_resolved", False))),
            })
            index.upsert(file_hash, updated)

            result["processed"] += 1
            result["documents_ok"] += 1
            result["chunks"] += count

            if progress_callback:
                progress_callback(
                    done, len(items), meta.get("title") or file_hash,
                    "yeniden indekslendi", {"chunks": count}
                )
        except Exception as exc:
            result["processed"] += 1
            result["errors"] += 1
            append_audit(
                "rag_full_rebuild_document_error",
                {"file_hash": file_hash, "title": meta.get("title", ""), "error": str(exc)},
            )
            if progress_callback:
                progress_callback(
                    done, len(items), meta.get("title") or file_hash,
                    f"hata: {exc}", {"chunks": 0}
                )

    result["finished_at"] = now_iso()
    append_audit("rag_full_rebuild_completed", result)
    return result
def rechunk_cached_document(rag: LocalRAG, index: IngestIndex, file_hash: str) -> Dict[str, Any]:
    meta = dict(index.get(file_hash) or {})
    cached = load_source_cache(file_hash)
    if not meta:
        return {"status": "missing_index", "file_hash": file_hash, "chunks": 0}
    if not cached.strip():
        return {"status": "missing_cache", "file_hash": file_hash, "chunks": 0}

    refreshed_meta = extract_legal_metadata(
        meta.get("title", ""),
        cached,
        dict(meta),
    )
    refreshed_meta["chunk_schema_version"] = CHUNK_SCHEMA_VERSION
    result = rag.ingest_text(
        cached,
        refreshed_meta,
        force=True,
    )
    updated = dict(refreshed_meta)
    updated.update({
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "chunk_count": int(result.get("count", 0) or 0),
        "rechunked_at": now_iso(),
        "structure_type": refreshed_meta.get("structure_type", "document"),
        "parse_status": refreshed_meta.get("parse_status", ""),
        "legal_structure_resolved": bool(refreshed_meta.get("legal_structure_resolved", False)),
    })
    index.upsert(file_hash, updated)

    return {
        "status": "reindexed",
        "file_hash": file_hash,
        "title": meta.get("title", ""),
        "chunks": int(result.get("count", 0) or 0),
    }
def reindex_cached_documents(
    rag: LocalRAG,
    index: IngestIndex,
    source_ids: Optional[List[str]] = None,
    progress_callback: Optional[Any] = None,
    force: bool = False,
) -> Dict[str, Any]:
    wanted = {canonical_source_id(x) for x in source_ids} if source_ids else None
    items = []
    for file_hash, meta in index.data.get("indexed_items", {}).items():
        sid = canonical_source_id(str(meta.get("source_id", "")))
        if wanted is not None and sid not in wanted:
            continue
        if load_source_cache(file_hash).strip():
            items.append((file_hash, meta))

    items.sort(key=lambda x: (
        canonical_source_id(str(x[1].get("source_id", ""))),
        str(x[1].get("title", "")),
    ))

    out = {
        "started_at": now_iso(),
        "total": len(items),
        "processed": 0,
        "reindexed": 0,
        "errors": 0,
        "finished_at": "",
    }

    for done, (file_hash, meta) in enumerate(items, 1):
        try:
            if (
                not force
                and meta.get("chunk_schema_version") == CHUNK_SCHEMA_VERSION
                and meta.get("chunk_metadata_valid") is True
            ):
                row = {"status": "already_current", "chunks": int(meta.get("chunk_count", 0) or 0)}
            else:
                row = rechunk_cached_document(rag, index, file_hash)

            out["processed"] += 1
            if row.get("status") == "reindexed":
                out["reindexed"] += 1

            if progress_callback:
                progress_callback(done, len(items), meta.get("title") or file_hash, row.get("status", "işlendi"), row)

        except Exception as exc:
            out["errors"] += 1
            append_audit(
                "rechunk_cached_document_error",
                {"file_hash": file_hash, "title": meta.get("title", ""), "error": str(exc)},
            )

    out["finished_at"] = now_iso()
    append_audit("rechunk_cached_documents_completed", out)
    return out
def versioned_ingest_text(
    rag: LocalRAG,
    index: IngestIndex,
    text: str,
    meta: Dict[str, Any],
    force: bool = False,
    auto_heal: bool = True,
    raw_bytes: Optional[bytes] = None,
    source_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Belgeyi metadata ile zenginleştirir; Auto-Healing Ingestion Gate ile denetler ve indeksler."""
    file_hash = meta["file_hash"]

    try:
        meta = extract_legal_metadata(
            meta.get("title", ""),
            text,
            dict(meta),
        )
    except Exception as exc:
        meta = dict(meta)
        meta["parse_status"] = "failed"
        meta["evidence_indexable"] = False
        append_audit(
            "legal_metadata_extraction_error",
            {"file_hash": file_hash, "title": meta.get("title", ""), "error": str(exc)},
        )

    # Cross-document identity and duplicate guard
    existing = index.get(file_hash)
    if existing and not force:
        existing_doc_id = str(existing.get("document_id") or "").strip()
        incoming_doc_id = str(meta.get("document_id") or "").strip()
        if existing_doc_id and incoming_doc_id and existing_doc_id != incoming_doc_id:
            return {
                "status": "blocked",
                "count": 0,
                "error": (
                    f"Cross-document identity conflict: hash {file_hash} is already "
                    f"assigned to document_id '{existing_doc_id}', cannot overwrite with '{incoming_doc_id}'."
                ),
                "meta": meta,
            }
        merged = {**existing, **meta, "status": existing.get("status", "active")}
        return {"status": "skipped", "count": existing.get("chunk_count", 0), "meta": merged}

    previous = find_previous_version(index, meta, exclude_hash=file_hash)
    previous_hash = previous.get("file_hash", "") if previous else ""
    old_text = load_source_cache(previous_hash) if previous_hash else ""
    diff_info = (
        legal_diff(old_text, text)
        if old_text
        else {
            "changed": bool(previous),
            "added_lines": 0,
            "removed_lines": 0,
            "diff_excerpt": "",
        }
    )

    next_version = int(previous.get("version_number", 0) or 0) + 1 if previous else 1
    meta = dict(meta)
    meta.update({
        "version_number": next_version,
        "status": meta.get("status") or "active",
        "previous_hash": previous_hash,
        "version_relation": "supersedes_previous_version" if previous else "initial_version",
        "diff_changed": bool(diff_info.get("changed")),
        "diff_added_lines": int(diff_info.get("added_lines", 0)),
        "diff_removed_lines": int(diff_info.get("removed_lines", 0)),
    })

    effective_text = text
    was_healed = False

    effective_source_path = source_path or meta.get("source_path") or meta.get("local_path")
    effective_raw_bytes = raw_bytes if raw_bytes is not None else meta.pop("raw_bytes", None)

    if auto_heal:
        from repair.ingestion_gate import AutoHealingIngestionGate

        gate = AutoHealingIngestionGate()
        gate_res = gate.ingest(
            rag=rag,
            index=index,
            text=text,
            meta=meta,
            force=force,
            raw_bytes=effective_raw_bytes,
            source_path=effective_source_path,
        )

        if gate_res.get("status") in ("quarantined", "blocked", "skipped"):
            return gate_res

        effective_text = gate_res.get("text", text)
        count = int(gate_res.get("count", 0) or 0)
        meta = gate_res.get("meta", meta)
        was_healed = bool(gate_res.get("healed", False))
    else:
        result = rag.ingest_text(text, meta, force=force)
        count = int(result.get("count", 0) or 0)
        meta["chunk_count"] = count
        meta["chunk_schema_version"] = CHUNK_SCHEMA_VERSION
        meta["chunk_metadata_valid"] = bool(count)
        meta["ingest_status"] = "indexed" if count else "indexed_zero_chunks"
        index.upsert(file_hash, meta)

    if previous and previous_hash and previous_hash != file_hash:
        try:
            rag.mark_hash_superseded(previous_hash, file_hash, effective_to=now_iso())
        except Exception as exc:
            append_audit(
                "version_status_update_warning",
                {"old_hash": previous_hash, "new_hash": file_hash, "error": str(exc)},
            )
        old_meta = dict(previous)
        old_meta.update({
            "status": "superseded",
            "superseded_by": file_hash,
            "valid_to": now_iso(),
        })
        index.upsert(previous_hash, old_meta)

    save_source_cache(file_hash, effective_text)
    append_audit(
        "regulation_version_registered",
        {
            "file_hash": file_hash,
            "previous_hash": previous_hash,
            "version_number": next_version,
            "diff_changed": bool(diff_info.get("changed")),
            "document_type": meta.get("document_type", ""),
            "law_number": meta.get("law_number", ""),
            "parse_status": meta.get("parse_status", ""),
            "healed": was_healed,
        },
    )
    return {
        "status": "repaired_and_indexed" if was_healed else "indexed",
        "count": count,
        "meta": meta,
        "diff": diff_info,
        "healed": was_healed,
    }
def ingest_local_regulation_file(path: Path, rag: LocalRAG, index: Optional[IngestIndex] = None, force: bool = False) -> Dict[str, Any]:
    index = index or IngestIndex()
    data = path.read_bytes()
    file_hash = sha256_bytes(data)
    text = ""
    pages = 0
    ocr_used = False
    if path.suffix.lower() == ".pdf":
        scraper = ResmiGazeteScraper(index=index, local_only=True)
        text, pages, ocr_used = scraper._extract_pdf_text(data)
    elif path.suffix.lower() == ".docx":
        if Document is None:
            raise RuntimeError("DOCX desteği için python-docx kurulu değil")
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        text = "\n".join(parts).strip()
    elif path.suffix.lower() in {".html", ".htm"}:
        soup = BeautifulSoup(data.decode("utf-8", errors="replace"), "html.parser")
        for el in soup(["script", "style", "header", "footer", "nav"]):
            el.extract()
        text = re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip()
    else:
        text = data.decode("utf-8-sig", errors="replace").strip()
    if not text.strip():
        raise ValueError("Belgeden metin çıkarılamadı")
    meta = extract_legal_metadata(path.stem, text, enrich_source_meta({
        "file_hash": file_hash,
        "title": path.stem,
        "url": "",
        "source": "Yerel mevzuat paketi",
        "timestamp": now_iso(),
        "page_count": pages,
        "ocr_used": ocr_used,
        "ingest_origin": "local_file",
        "local_path": str(path),
    }, "local_file"))
    pdf_diagnostic = dict(scraper._last_pdf_diagnostic) if path.suffix.lower() == ".pdf" else {}
    result = versioned_ingest_text(
        rag,
        index,
        text,
        meta,
        force=force,
        raw_bytes=data,
        source_path=str(path),
    )
    meta = result.get("meta", meta)
    meta["chunk_count"] = result.get("count", meta.get("chunk_count", 0))
    index.upsert(file_hash, meta)
    append_audit("regulation_ingested", {"title": meta["title"], "file_hash": file_hash, "count": result.get("count", 0), "version": meta.get("version_number", 1)})
    output = meta | {k: v for k, v in result.items() if k != "meta"}
    if pdf_diagnostic:
        output["pdf_diagnostic"] = pdf_diagnostic
    return output
def resolve_inventory_evidence_meta(
    chroma_meta: Dict[str, Any],
    ingest_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Envanter için belge yapısını güncel source_cache üzerinden yeniden keşfeder.

    Temel kural: source_cache mevcutsa eski Chroma evidence_indexable/reason alanı
    otorite değildir. Güncel kaynak metni yeniden sınıflandırılır ve yalnızca salt-okuma
    sonucu envantere yansıtılır. Böylece eski FALSE metadata'sı gerçek belge yapısını
    sonsuza kadar kilitlemez.
    """
    m = dict(chroma_meta or {})
    fallback = dict(ingest_meta or {})

    def first_value(key: str) -> str:
        value = str(m.get(key, "") or "").strip()
        if value:
            return value
        return str(fallback.get(key, "") or "").strip()

    file_hash = first_value("file_hash")
    title = first_value("title")
    document_type = first_value("document_type")

    # En güvenilir yol: mevcut kaynak metnini tekrar keşfet. Bu işlem Chroma'yı,
    # source_cache'i veya ingest index'i değiştirmez.
    if file_hash:
        cached = load_source_cache(file_hash)
        if cached:
            try:
                structure = classify_legal_structure(title, cached, document_type)
                # Kaynak cache güncel gerçektir: envanterde yalnızca
                # indexlenebilirlik değil, keşfedilen yapının TAMAMI gösterilir.
                # Eski Chroma metadata'sındaki document/failed değeri bunu ezemez.
                return {
                    "document_indexable": bool(structure.get("document_indexable", False)),
                    "indexability_reason": str(structure.get("indexability_reason", "") or ""),
                    "evidence_indexable": bool(structure.get("evidence_indexable", False)),
                    "evidence_indexable_reason": str(structure.get("evidence_indexable_reason", "") or ""),
                    "legal_structure_resolved": bool(structure.get("legal_structure_resolved", False)),
                    "structure_type": str(structure.get("structure_type", "") or ""),
                    "parse_status": str(structure.get("parse_status", "") or ""),
                    "article_count": int(structure.get("article_count", 0) or 0),
                    "section_count": int(structure.get("section_count", 0) or 0),
                    "account_code_count": int(structure.get("account_code_count", 0) or 0),
                    "text_char_count": int(structure.get("text_char_count", 0) or 0),
                }
            except Exception:
                pass

    # Source cache yoksa, mevcut aktif şema kaydı zaten belgenin RAG'a
    # gerçekten indekslenmiş olduğuna dair doğrudan kanıttır. Eski FALSE
    # metadata'sı bu gerçeği geçersiz kılamaz.
    if str(m.get("chunk_schema_version", "")) == str(CHUNK_SCHEMA_VERSION):
        return {
            "evidence_indexable": True,
            "evidence_indexable_reason": (
                "Belge mevcut RAG chunk kaydından indekslenebilir olarak doğrulandı; "
                "ayrıntılı yapı gerekçesi eski metadata'da bulunmuyor ve source_cache "
                "bulunmadığı için yapı keşfi yeniden çalıştırılamadı."
            ),
            "document_indexable": True,
            "indexability_reason": (
                "Belge mevcut RAG chunk kaydından indekslenebilir olarak doğrulandı."
            ),
            "legal_structure_resolved": bool(
                str(m.get("structure_type", "")) in {"article", "section", "accounting_plan"}
            ),
            "structure_type": str(m.get("structure_type", "") or ""),
        }

    # Source cache yoksa metadata fallback kullanılır. Eski explicit reason korunur.
    reason = first_value("evidence_indexable_reason")
    raw_evidence = m.get("evidence_indexable", None)
    if raw_evidence is None or str(raw_evidence).strip() == "":
        raw_evidence = fallback.get("evidence_indexable", None)

    if raw_evidence is None or str(raw_evidence).strip() == "":
        evidence_indexable = True
    else:
        evidence_indexable = (
            raw_evidence is True
            or str(raw_evidence).strip().lower() in {"true", "1", "yes", "evet"}
        )

    if reason:
        return {
            "evidence_indexable": bool(evidence_indexable),
            "evidence_indexable_reason": reason,
        }

    structure_type = first_value("structure_type")
    parse_status = first_value("parse_status")
    article_count = first_value("article_count")
    section_count = first_value("section_count")
    account_code_count = first_value("account_code_count")
    text_char_count = first_value("text_char_count")

    if evidence_indexable:
        if account_code_count and account_code_count not in {"0", "0.0"}:
            reason = f"Hesap planı yapısı metadata'da tespit edildi ({account_code_count} hesap kodu belirteci)."
        elif article_count and article_count not in {"0", "0.0"}:
            reason = f"Hukuki madde yapısı tespit edildi ({article_count} MADDE belirteci)."
        elif section_count and section_count not in {"0", "0.0"}:
            reason = f"Numaralı bölüm yapısı tespit edildi ({section_count} bölüm)."
        else:
            reason = "Belge güncel RAG chunk'ı olarak indekslenmiş; ayrıntılı yapı gerekçesi eski metadata'da bulunmuyor."
    else:
        if text_char_count and text_char_count.isdigit() and int(text_char_count) < 250:
            reason = f"Belge metni çok kısa ({text_char_count} karakter); yalnızca başlık veya yetersiz içerik."
        elif parse_status:
            reason = f"Belge yapı keşfi başarısız ({parse_status}); doğrulanabilir chunk yapısı bulunamadı."
        elif structure_type:
            reason = f"Belge yapısı '{structure_type}' olarak işaretlenmiş; doğrulanabilir chunk yapısı bulunamadı."
        else:
            reason = "Belgenin evidence_indexable durumu false olarak kayıtlı; source_cache bulunamadığı için yeniden keşif yapılamadı."

    return {
        "evidence_indexable": bool(evidence_indexable),
        "evidence_indexable_reason": reason,
    }
def build_rag_inventory(rag: Optional[LocalRAG]) -> Dict[str, Any]:
    """RAG içinde gerçekten indekslenmiş güncel belgelerin okunabilir envanterini çıkarır.

    Yalnızca aktif chunk şemasındaki kayıtlar okunur. Envanter salt-okuma bir görünümdür;
    Chroma, source cache veya ingest index üzerinde değişiklik yapmaz.
    """
    if rag is None:
        return {
            "available": False,
            "error": "RAG hazır değil.",
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "total_chunks": 0,
            "documents": [],
            "sources": [],
        }

    try:
        result = rag.collection.get(
            where={"chunk_schema_version": CHUNK_SCHEMA_VERSION},
            include=["metadatas"],
        )
    except Exception as exc:
        return {
            "available": False,
            "error": f"RAG envanteri okunamadı: {exc}",
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
            "total_chunks": 0,
            "documents": [],
            "sources": [],
        }

    metadatas = result.get("metadatas", []) or []
    docs: Dict[str, Dict[str, Any]] = {}
    # Eski Chroma kayıtlarında yeni evidence alanları bulunmayabilir.
    # Ingest index yalnızca tamamlayıcı okuma kaynağıdır.
    try:
        inventory_index = IngestIndex()
    except Exception:
        inventory_index = None

    # Aynı belge yüzlerce chunk içerebilir. Evidence gerekçesini her chunk için
    # source_cache'ten tekrar okumak gereksiz ve özellikle büyük RAG'larda yavaştır.
    # Her belge için bir kez hesaplanır ve sonraki chunk'larda yeniden kullanılır.
    evidence_cache: Dict[str, Dict[str, Any]] = {}

    for meta in metadatas:
        m = dict(meta or {})
        file_hash = str(m.get("file_hash", "") or "")
        document_key = str(m.get("document_key", "") or "")
        title = str(m.get("title", "") or "").strip()
        source_id = canonical_source_id(str(m.get("source_id", "") or ""))
        source_name = str(m.get("source", "") or "").strip()
        if not source_name and source_id:
            source_name = str(source_profile(source_id).get("name", source_id))
        if not source_name:
            source_name = source_id or "Bilinmeyen"

        # Normal ingestte file_hash benzersiz belge anahtarıdır. Eski/eksik metadata
        # için deterministik bir fallback kullanılır; aynı belge farklı chunklarda
        # tek satır olarak görünür.
        doc_id = file_hash or document_key or title or f"unknown-{len(docs)+1}"
        if doc_id not in docs:
            fallback_meta = inventory_index.get(file_hash) if inventory_index and file_hash else None
            evidence_meta = resolve_inventory_evidence_meta(m, fallback_meta)
            evidence_cache[doc_id] = evidence_meta
            docs[doc_id] = {
                "file_hash": file_hash,
                "document_key": document_key,
                "title": title or document_key or "Başlıksız belge",
                "source_id": source_id,
                "source": source_name,
                "document_type": str(m.get("document_type", "") or ""),
                "law_number": str(m.get("law_number", "") or ""),
                "document_number": str(m.get("document_number", "") or ""),
                "status": str(m.get("status", "") or ""),
                "effective_date": str(m.get("effective_date", "") or ""),
                "official_date_hint": str(m.get("official_date_hint", "") or ""),
                "url": str(m.get("url", "") or ""),
                "authority_rank": int(m.get("authority_rank", 0) or 0),
                "document_indexable": bool(evidence_meta.get("document_indexable", evidence_meta.get("evidence_indexable"))),
                "evidence_indexable": bool(evidence_meta.get("evidence_indexable")),
                "legal_structure_resolved": bool(evidence_meta.get("legal_structure_resolved", False)),
                "structure_type": str(evidence_meta.get("structure_type", "") or ""),
                "parse_status": str(evidence_meta.get("parse_status", "") or ""),
                "evidence_indexable_reason": str(evidence_meta.get("evidence_indexable_reason", "") or ""),
                "chunk_count": 0,
            }
        docs[doc_id]["chunk_count"] += 1

        # Metadata bazı chunklarda eksikse dolu değeri koru.
        for field in (
            "title", "document_key", "source_id", "source", "document_type",
            "law_number", "document_number", "status", "effective_date",
            "official_date_hint", "url", "evidence_indexable_reason",
            "document_indexable", "legal_structure_resolved", "structure_type", "parse_status",
        ):
            value = str(m.get(field, "") or "").strip()
            if value and not str(docs[doc_id].get(field, "") or "").strip():
                docs[doc_id][field] = value

    documents = sorted(
        docs.values(),
        key=lambda x: (str(x.get("source", "")), str(x.get("title", ""))),
    )

    source_map: Dict[str, Dict[str, Any]] = {}
    for doc in documents:
        source = str(doc.get("source", "") or "Bilinmeyen")
        if source not in source_map:
            source_map[source] = {
                "source": source,
                "source_id": str(doc.get("source_id", "")),
                "documents": 0,
                "chunks": 0,
                "active_documents": 0,
            }
        source_map[source]["documents"] += 1
        source_map[source]["chunks"] += int(doc.get("chunk_count", 0) or 0)
        if str(doc.get("status", "")).lower() == "active":
            source_map[source]["active_documents"] += 1

    sources = sorted(
        source_map.values(),
        key=lambda x: (-int(x.get("documents", 0)), str(x.get("source", ""))),
    )
    return {
        "available": True,
        "error": "",
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "total_chunks": len(metadatas),
        "total_documents": len(documents),
        "active_documents": sum(
            1 for d in documents if str(d.get("status", "")).lower() == "active"
        ),
        "searchable_documents": sum(
            1 for d in documents if bool(d.get("evidence_indexable"))
        ),
        "documents": documents,
        "sources": sources,
    }


__all__ = [
    "save_source_cache",
    "load_source_cache",
    "scan_production_rag_quality",
    "repair_production_document_from_pdf",
    "run_rag_provenance_compare",
    "run_rag_diagnostic",
    "find_previous_version",
    "reset_chroma_collection",
    "rebuild_rag_from_source_cache",
    "rechunk_cached_document",
    "reindex_cached_documents",
    "versioned_ingest_text",
    "ingest_local_regulation_file",
    "resolve_inventory_evidence_meta",
    "build_rag_inventory",
]
