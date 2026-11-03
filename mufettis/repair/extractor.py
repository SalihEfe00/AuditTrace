# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 10: Source Re-Extraction / Verified Candidate Extraction
Extractor Module.

Performs source validation, cryptographic SHA256 checking, canonical re-extraction,
normalization, structure discovery, and legal chunking.
STRICTLY IN-MEMORY / TEMPORARY: absolutely no writes to Chroma or source cache.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup

from mufettis.common.utils import sha256_bytes
from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.extraction.pdf import _pdf_generic_quality_metrics
from mufettis.extraction.text_repair import normalize_extracted_text, repair_extracted_spacing
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper
from mufettis.legal.parser import extract_legal_metadata, split_legal_chunks
from mufettis.repair.models import (
    ClassificationResult,
    DocumentScanResult,
    ExtractionCandidate,
    ExtractionPlan,
    SourceValidationResult,
    SourceValidationStatus,
)

CANONICAL_FALLBACK_CHAIN = [
    "pypdfium2_fast",
    "spatial_reconstruction",
    "pdfplumber_fallback",
    "pypdf_fallback",
    "ocr_tesseract",
]


def validate_source(
    source_path: Path | str,
    expected_hash: Optional[str] = None,
    document_id: str = "",
    source_document_id: str = "",
) -> SourceValidationResult:
    """Kaynak PDF dosyasını ve hash bütünlüğünü doğrular.

    Kaynak belirsiz veya hash uyumsuz ise extraction başlatılmamalıdır.
    """
    path = Path(source_path) if source_path else None
    res = SourceValidationResult(
        document_id=document_id,
        source_document_id=source_document_id,
        source_path=str(path) if path else "",
        expected_sha256=str(expected_hash or "").strip().lower(),
    )

    if not path or not path.is_file():
        res.status = SourceValidationStatus.MISSING.value
        res.issues.append(f"Kaynak PDF dosyası mevcut değil: {path}")
        return res

    res.exists = True

    if path.suffix.lower() != ".pdf":
        res.status = SourceValidationStatus.UNREADABLE.value
        res.issues.append(f"Kaynak dosya PDF uzantısına sahip değil: {path.name}")
        return res

    try:
        data = path.read_bytes()
    except Exception as exc:
        res.status = SourceValidationStatus.UNREADABLE.value
        res.issues.append(f"Kaynak dosya okunamadı: {exc}")
        return res

    res.readable = True

    if len(data) == 0:
        res.status = SourceValidationStatus.UNREADABLE.value
        res.issues.append("Kaynak PDF dosyası 0 bayt (boş).")
        return res

    if not data.startswith(b"%PDF"):
        res.status = SourceValidationStatus.UNREADABLE.value
        res.issues.append("Kaynak dosya geçerli bir PDF imzasına (magic bytes %PDF) sahip değil.")
        return res

    actual_hash = sha256_bytes(data).lower()
    res.actual_sha256 = actual_hash

    if not res.expected_sha256:
        res.status = SourceValidationStatus.HASH_MISSING.value
        res.issues.append("Beklenen source SHA256 hash bilgisi bulunamadı.")
        return res

    if res.actual_sha256 != res.expected_sha256:
        res.status = SourceValidationStatus.HASH_MISMATCH.value
        res.hash_match = False
        res.issues.append(
            f"SHA256 uyumsuzluğu: aktüel={res.actual_sha256}, beklenen={res.expected_sha256}"
        )
        return res

    res.hash_match = True
    res.identity_verified = True
    res.status = SourceValidationStatus.VERIFIED.value
    return res


def create_repair_plan(
    scan_result: DocumentScanResult,
    classification: Optional[ClassificationResult] = None,
) -> ExtractionPlan:
    """Belge için kanonik onarım ve yeniden çıkarım planı üretir.

    Üretim ortamına hiçbir yazma işlemi yapmaz; yalnızca plan sözleşmesini tanımlar.
    """
    primary = "pypdfium2_fast"
    fallbacks = [
        "spatial_reconstruction",
        "pdfplumber_fallback",
        "pypdf_fallback",
        "ocr_tesseract",
    ]

    reason_parts = []
    if classification:
        reason_parts.append(f"Sınıflandırma: {classification.status}")
        if classification.review_issues:
            reason_parts.append(f"İnceleme nedenleri: {', '.join(classification.review_issues[:3])}")
    elif scan_result.issue_codes:
        reason_parts.append(f"Tespit edilen sorunlar: {', '.join(scan_result.issue_codes[:3])}")
    else:
        reason_parts.append("Standart kanonik çıkarma planı.")

    return ExtractionPlan(
        document_id=scan_result.document_id,
        source_document_id=scan_result.source_document_id,
        source_path=scan_result.source_path or "",
        source_hash=scan_result.source_hash,
        plan_version="1.0.0",
        primary_method=primary,
        fallback_methods=fallbacks,
        extraction_chain=CANONICAL_FALLBACK_CHAIN,
        ocr_allowed=True,
        normalization_enabled=True,
        structure_enabled=True,
        chunking_enabled=True,
        expected_schema_version=str(CHUNK_SCHEMA_VERSION),
        expected_quality_metrics={
            "min_score": 0.88,
            "max_glued_tokens": 0,
            "max_mojibake": 0,
            "max_replacement_chars": 0,
        },
        reason="; ".join(reason_parts),
    )


def extract_web_candidate(
    url: str,
    expected_hash: Optional[str] = None,
    document_id: str = "",
    source_document_id: str = "",
    title: str = "",
    document_type: str = "",
    meta: Optional[Dict[str, Any]] = None,
    harvester: Optional[Any] = None,
    source_id: str = "mevzuat_org",
) -> ExtractionCandidate:
    """
    Extracts candidate text from a secondary web source URL via WebSourceHarvester.
    Decodes raw HTTP bytes using proper charset (avoiding flawed res.text ISO-8859-1 decoding),
    verifies SHA256 cryptographic provenance against expected_hash,
    normalizes text, discovers structure, and performs legal chunking in-memory.

    Strictly read-only: absolutely zero writes to Chroma, source cache, or ingest index.
    """
    url = str(url or "").strip()
    doc_id = document_id or (meta.get("document_id") if meta else "") or (meta.get("source_id") if meta else "") or (meta.get("document_key") if meta else "") or source_id
    src_doc_id = source_document_id or doc_id
    exp_hash = str(expected_hash or "").strip().lower()

    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=url,
            source_sha256=exp_hash,
            extraction_method="web_harvester_utf8_soup",
            status="INVALID_URL",
            metadata={"error": f"Geçersiz veya eksik web URL: {url}"},
            in_memory_only=True,
        )

    # 1. Fetch via WebSourceHarvester
    try:
        if harvester is None:
            from mufettis.harvesting.web_harvester import WebSourceHarvester
            harvester = WebSourceHarvester(source_id or "mevzuat_org", local_only=False)
        res = harvester._get(url)
    except Exception as exc:
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=url,
            source_sha256=exp_hash,
            extraction_method="web_harvester_utf8_soup",
            status="FETCH_FAILED",
            metadata={"error": f"Web kaynağı çekilemedi: {exc}"},
            in_memory_only=True,
        )

    if res is None or res.status_code != 200:
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=url,
            source_sha256=exp_hash,
            extraction_method="web_harvester_utf8_soup",
            status="FETCH_FAILED",
            metadata={"error": f"HTTP isteği başarısız: {res.status_code if res else 'None'}"},
            in_memory_only=True,
        )

    raw_bytes = res.content
    if not raw_bytes or len(raw_bytes) == 0:
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=url,
            source_sha256=exp_hash,
            extraction_method="web_harvester_utf8_soup",
            status="EMPTY_EXTRACTION",
            metadata={"error": "Web kaynağından 0 bayt döndü."},
            in_memory_only=True,
        )

    # 2. Cryptographic SHA-256 Provenance Check
    actual_hash = sha256_bytes(raw_bytes).lower()
    if exp_hash and actual_hash != exp_hash:
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=url,
            source_sha256=actual_hash,
            extraction_method="web_harvester_utf8_soup",
            status="HASH_MISMATCH",
            metadata={
                "error": f"SHA256 uyumsuzluğu: aktüel={actual_hash}, beklenen={exp_hash}",
                "expected_sha256": exp_hash,
                "actual_sha256": actual_hash,
            },
            in_memory_only=True,
        )

    # 3. Encoding & HTML Parsing (Do NOT use flawed res.text)
    try:
        soup = BeautifulSoup(raw_bytes, "html.parser")
        for el in soup(["script", "style", "header", "footer", "nav"]):
            el.extract()
        raw_text = re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip()
    except Exception as exc:
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=url,
            source_sha256=actual_hash,
            extraction_method="web_harvester_utf8_soup",
            status="EXTRACTION_FAILED",
            metadata={"error": f"HTML parse hatası: {exc}"},
            in_memory_only=True,
        )

    if not raw_text.strip():
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=url,
            source_sha256=actual_hash,
            extraction_method="web_harvester_utf8_soup",
            status="EMPTY_EXTRACTION",
            metadata={"error": "HTML temizleme sonrası metin boş."},
            in_memory_only=True,
        )

    # 4. Normalization
    doc_title = title or (meta.get("title") if meta else "") or doc_id
    doc_type = document_type or (meta.get("document_type") if meta else "") or "Kanun"
    try:
        normalized = normalize_extracted_text(raw_text)
        normalized = repair_extracted_spacing(normalized, doc_title, doc_type)
    except Exception:
        normalized = raw_text

    # 5. Structure Discovery
    try:
        structure = extract_legal_metadata(
            title=doc_title,
            text=normalized,
            base_meta={**(meta or {}), "source_id": doc_id, "document_type": doc_type},
        )
    except Exception as exc:
        structure = {"error": str(exc)}

    # 6. Legal Chunking
    try:
        raw_chunks = split_legal_chunks(
            text=normalized,
            title=doc_title,
            document_type=doc_type,
        )
        legal_chunks = []
        for chk in raw_chunks:
            c = dict(chk)
            c["chunk_schema_version"] = str(CHUNK_SCHEMA_VERSION)
            c["source_hash"] = actual_hash
            c["source_document_id"] = src_doc_id
            c["document_id"] = doc_id
            c["title"] = doc_title
            c["status"] = "active"
            legal_chunks.append(c)
    except Exception as exc:
        legal_chunks = []
        structure["chunking_error"] = str(exc)

    # 7. Quality Metrics
    quality = _pdf_generic_quality_metrics(normalized)

    return ExtractionCandidate(
        document_id=doc_id,
        source_document_id=src_doc_id,
        source_path=url,
        source_sha256=actual_hash,
        extraction_method="web_harvester_utf8_soup",
        fallback_used=False,
        page_count=1,
        raw_text=raw_bytes.decode("utf-8", errors="replace"),
        normalized_text=normalized,
        structure=structure,
        legal_chunks=legal_chunks,
        quality_metrics=quality,
        metadata={
            **(meta or {}),
            "url": url,
            "chunk_count": len(legal_chunks),
            "schema_version": str(CHUNK_SCHEMA_VERSION),
            "content_size": len(raw_bytes),
            "extraction_method": "web_harvester_utf8_soup",
        },
        status="SUCCESS",
        in_memory_only=True,
    )


def extract_cache_candidate(
    cache_text: str,
    file_hash: str,
    document_id: str = "",
    source_document_id: str = "",
    title: str = "",
    document_type: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> ExtractionCandidate:
    """Extracts an in-memory candidate from verified source cache text.
    Used when local PDF is not on disk and web fetch is not available,
    allowing re-chunking of clean text to repair corrupted Chroma chunks.
    """
    meta_dict = dict(meta or {})
    doc_id = document_id or str(meta_dict.get("document_id") or meta_dict.get("source_id") or file_hash)
    src_doc_id = source_document_id or doc_id
    doc_title = title or str(meta_dict.get("title") or doc_id)

    try:
        normalized = repair_extracted_spacing(cache_text, doc_title, document_type)
    except Exception:
        normalized = cache_text

    try:
        structure = extract_legal_metadata(
            title=doc_title,
            text=normalized,
            base_meta={"source_id": doc_id, "document_type": document_type},
        )
    except Exception as exc:
        structure = {"error": str(exc)}

    try:
        raw_chunks = split_legal_chunks(
            text=normalized,
            title=doc_title,
            document_type=document_type,
        )
        legal_chunks = []
        for chk in raw_chunks:
            c = dict(chk)
            c["chunk_schema_version"] = str(CHUNK_SCHEMA_VERSION)
            c["source_hash"] = file_hash
            c["source_document_id"] = src_doc_id
            c["document_id"] = doc_id
            c["title"] = doc_title
            c["status"] = "active"
            c["document_indexable"] = True
            legal_chunks.append(c)
    except Exception as exc:
        legal_chunks = []
        structure["chunking_error"] = str(exc)

    quality = _pdf_generic_quality_metrics(normalized)

    return ExtractionCandidate(
        document_id=doc_id,
        source_document_id=src_doc_id,
        source_path="",
        source_sha256=file_hash,
        extraction_method="source_cache_verified",
        fallback_used=True,
        spatial_reconstruction_used=False,
        pdfplumber_used=False,
        pypdf_used=False,
        ocr_used=False,
        page_count=1,
        raw_text=cache_text,
        normalized_text=normalized,
        structure=structure,
        legal_chunks=legal_chunks,
        quality_metrics=quality,
        metadata={
            "source": "source_cache",
            "file_hash": file_hash,
            "chunk_count": len(legal_chunks),
            "schema_version": str(CHUNK_SCHEMA_VERSION),
        },
        status="SUCCESS",
        in_memory_only=True,
    )


def extract_candidate(
    pdf_path: Optional[Path | str] = None,
    expected_hash: Optional[str] = None,
    document_id: str = "",
    source_document_id: str = "",
    title: str = "",
    document_type: str = "",
    custom_extractor: Optional[Any] = None,
    source_path: Optional[Path | str] = None,
    meta: Optional[Dict[str, Any]] = None,
    expected_sha256: Optional[str] = None,
    raw_bytes: Optional[bytes] = None,
    **kwargs,
) -> ExtractionCandidate:
    """PDF dosyasından veya secondary web kaynağından doğrulanmış kaynak kontrolüyle bellek içi aday çıkarımı yapar.

    UYARI: Bu fonksiyon kesinlikle Chroma veya source cache üzerine yazmaz.
    Elde edilen aday ve chunk'lar salt karşılaştırma için bellekte tutulur.
    """
    effective_path = source_path if source_path is not None else pdf_path
    effective_hash = expected_hash or expected_sha256
    meta_dict = dict(meta or {})
    effective_raw_bytes = raw_bytes if raw_bytes is not None else kwargs.get("raw_bytes")
    if not document_id:
        document_id = str(meta_dict.get("document_id") or meta_dict.get("source_id") or meta_dict.get("document_key") or "")
    if not title:
        title = str(meta_dict.get("title") or "")
    if not document_type:
        document_type = str(meta_dict.get("document_type") or "")

    # Direct in-memory extraction if raw_bytes is provided and no local file exists
    if effective_raw_bytes is not None and (not effective_path or not Path(str(effective_path)).is_file()):
        doc_id = document_id or "in_memory_source"
        src_doc_id = source_document_id or doc_id
        actual_hash = sha256_bytes(effective_raw_bytes).lower()
        if effective_hash and actual_hash != str(effective_hash).strip().lower():
            return ExtractionCandidate(
                document_id=doc_id,
                source_document_id=src_doc_id,
                source_path="",
                source_sha256=actual_hash,
                status="HASH_MISMATCH",
                metadata={"expected_hash": effective_hash, "actual_hash": actual_hash},
                in_memory_only=True,
            )

        is_pdf_data = effective_raw_bytes.startswith(b"%PDF") or bool(meta_dict.get("is_pdf"))
        raw_text = ""
        page_count = 0
        ocr_used = False
        try:
            if is_pdf_data:
                if custom_extractor is not None:
                    raw_text, page_count, ocr_used = custom_extractor(effective_raw_bytes)
                else:
                    scraper = ResmiGazeteScraper(local_only=True)
                    raw_text, page_count, ocr_used = scraper._extract_pdf_text(effective_raw_bytes)
            else:
                soup = BeautifulSoup(effective_raw_bytes.decode("utf-8", errors="replace"), "html.parser")
                for el in soup(["script", "style", "header", "footer", "nav"]):
                    el.extract()
                raw_text = re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip()

            if not raw_text or not raw_text.strip():
                return ExtractionCandidate(
                    document_id=doc_id,
                    source_document_id=src_doc_id,
                    source_path="",
                    source_sha256=actual_hash,
                    page_count=int(page_count or 0),
                    raw_text="",
                    status="EMPTY_EXTRACTION",
                    in_memory_only=True,
                )

            normalized = normalize_extracted_text(raw_text)
            normalized = repair_extracted_spacing(normalized, title, document_type)
            # Önceden split_legal_chunks(normalized, meta_dict) ile max_chars yerine sözlük
            # geçiriliyor ve var olmayan `chunks` alanı kullanılıyordu; iki hata da her
            # çağrıda EXTRACTION_FAILED üretiyordu (HTML kaynaklı aday hiç çıkarılamıyordu).
            # Parçalar PDF yolundakiyle aynı biçimde zenginleştirilir.
            source_hash = effective_hash or actual_hash
            legal_chunks = []
            for chk in split_legal_chunks(normalized, title=title, document_type=document_type):
                c = dict(chk)
                c["chunk_schema_version"] = str(CHUNK_SCHEMA_VERSION)
                c["source_hash"] = source_hash
                c["source_document_id"] = src_doc_id
                c["document_id"] = doc_id
                c["title"] = title
                c["status"] = "active"
                legal_chunks.append(c)
            return ExtractionCandidate(
                document_id=doc_id,
                source_document_id=src_doc_id,
                source_path="",
                source_sha256=source_hash,
                page_count=int(page_count or 0),
                raw_text=normalized,
                legal_chunks=legal_chunks,
                status="SUCCESS",
                metadata={"in_memory_raw_bytes": True, "ocr_used": ocr_used},
                in_memory_only=True,
            )
        except Exception as exc:
            return ExtractionCandidate(
                document_id=doc_id,
                source_document_id=src_doc_id,
                source_path="",
                source_sha256=actual_hash,
                status="EXTRACTION_FAILED",
                metadata={"error": str(exc)},
                in_memory_only=True,
            )

    # Secondary web source veya URL kontrolü
    is_web = False
    url = ""
    if effective_path and str(effective_path).startswith(("http://", "https://")):
        is_web = True
        url = str(effective_path)
    elif meta_dict.get("url") and (
        meta_dict.get("is_pdf") is False
        or meta_dict.get("source_type") == "secondary"
        or meta_dict.get("ingest_origin") == "secondary_web_source"
        or not effective_path
        or not Path(str(effective_path)).exists()
    ):
        is_web = True
        url = str(meta_dict["url"])

    if is_web:
        cand = extract_web_candidate(
            url=url,
            expected_hash=effective_hash,
            document_id=document_id,
            source_document_id=source_document_id,
            title=title,
            document_type=document_type,
            meta=meta_dict,
            source_id=str(meta_dict.get("source_id") or "mevzuat_org"),
        )
        if cand.status == "SUCCESS":
            return cand
        # Fallback to source_cache if web fetch fails (e.g. SSL/offline) but verified cache exists
        if effective_hash:
            from mufettis.rag.maintenance import load_source_cache
            cache_text = load_source_cache(effective_hash)
            if cache_text and cache_text.strip():
                return extract_cache_candidate(
                    cache_text=cache_text,
                    file_hash=effective_hash,
                    document_id=document_id,
                    source_document_id=source_document_id,
                    title=title,
                    document_type=document_type,
                    meta=meta_dict,
                )
        return cand

    path = Path(effective_path) if effective_path else Path("")
    doc_id = document_id or (path.stem if path else "")
    src_doc_id = source_document_id or doc_id

    # If local file does not exist on disk, check if verified source_cache exists
    if (not effective_path or not path.exists()) and effective_hash:
        from mufettis.rag.maintenance import load_source_cache
        cache_text = load_source_cache(effective_hash)
        if cache_text and cache_text.strip():
            return extract_cache_candidate(
                cache_text=cache_text,
                file_hash=effective_hash,
                document_id=doc_id,
                source_document_id=src_doc_id,
                title=title,
                document_type=document_type,
                meta=meta_dict,
            )

    # 1. ADIM: SOURCE & HASH VALIDATION
    validation = validate_source(
        source_path=path,
        expected_hash=effective_hash,
        document_id=doc_id,
        source_document_id=src_doc_id,
    )

    if validation.status != SourceValidationStatus.VERIFIED.value:
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=str(path),
            source_sha256=validation.actual_sha256 or str(expected_hash or ""),
            status=validation.status,
            metadata={"validation": validation.to_dict()},
            in_memory_only=True,
        )

    # 2. ADIM: CANONICAL RE-EXTRACTION
    try:
        data = path.read_bytes()
    except Exception as exc:
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=str(path),
            source_sha256=validation.actual_sha256,
            status="EXTRACTION_FAILED",
            metadata={"error": f"Okuma hatası: {exc}"},
            in_memory_only=True,
        )

    method_info = {
        "primary_method": "pypdfium2",
        "fallback_used": False,
        "spatial_reconstruction_used": False,
        "pdfplumber_used": False,
        "pypdf_used": False,
        "ocr_used": False,
        "ocr_details": {},
    }

    try:
        if custom_extractor is not None:
            raw_text, page_count, ocr_used = custom_extractor(data)
            method_info["primary_method"] = "custom"
            method_info["ocr_used"] = bool(ocr_used)
        else:
            scraper = ResmiGazeteScraper(local_only=True)
            raw_text, page_count, ocr_used = scraper._extract_pdf_text(data)
            diag = getattr(scraper, "_last_pdf_diagnostic", {}) or {}

            engine = str(diag.get("selected_engine", "pypdfium2"))
            method_info["primary_method"] = engine
            method_info["ocr_used"] = bool(ocr_used)
            method_info["spatial_reconstruction_used"] = "spatial" in engine
            method_info["pdfplumber_used"] = "pdfplumber" in engine
            method_info["pypdf_used"] = "pypdf" in engine
            method_info["fallback_used"] = (
                method_info["spatial_reconstruction_used"]
                or method_info["pdfplumber_used"]
                or method_info["pypdf_used"]
                or bool(ocr_used)
            )
            method_info["ocr_details"] = {
                "ocr_used": bool(ocr_used),
                "pipeline": diag.get("pipeline", ""),
                "fast_pass_status": diag.get("fast_pass", {}).get("status", ""),
            }

        if not raw_text or not raw_text.strip():
            return ExtractionCandidate(
                document_id=doc_id,
                source_document_id=src_doc_id,
                source_path=str(path),
                source_sha256=validation.actual_sha256,
                page_count=int(page_count or 0),
                raw_text="",
                status="EMPTY_EXTRACTION",
                metadata={"validation": validation.to_dict(), "method_info": method_info},
                in_memory_only=True,
            )

    except Exception as exc:
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=str(path),
            source_sha256=validation.actual_sha256,
            status="EXTRACTION_FAILED",
            metadata={"error": f"Kanonik çıkarma hatası: {exc}"},
            in_memory_only=True,
        )

    # 3. ADIM: CANONICAL NORMALIZATION
    doc_title = title or path.stem
    try:
        normalized = normalize_extracted_text(raw_text)
        normalized = repair_extracted_spacing(normalized, doc_title, document_type)
    except Exception as exc:
        return ExtractionCandidate(
            document_id=doc_id,
            source_document_id=src_doc_id,
            source_path=str(path),
            source_sha256=validation.actual_sha256,
            raw_text=raw_text,
            status="NORMALIZATION_FAILED",
            metadata={"error": f"Normalizasyon hatası: {exc}"},
            in_memory_only=True,
        )

    # 4. ADIM: STRUCTURE DISCOVERY
    try:
        structure = extract_legal_metadata(
            title=doc_title,
            text=normalized,
            base_meta={"source_id": doc_id, "document_type": document_type},
        )
    except Exception as exc:
        structure = {"error": str(exc)}

    # 5. ADIM: LEGAL CHUNKING (Candidate Chunks - Memory only)
    try:
        raw_chunks = split_legal_chunks(
            text=normalized,
            title=doc_title,
            document_type=document_type,
        )
        legal_chunks = []
        for chk in raw_chunks:
            c = dict(chk)
            c["chunk_schema_version"] = str(CHUNK_SCHEMA_VERSION)
            c["source_hash"] = validation.actual_sha256
            c["source_document_id"] = src_doc_id
            c["document_id"] = doc_id
            c["title"] = doc_title
            c["status"] = "active"
            legal_chunks.append(c)
    except Exception as exc:
        legal_chunks = []
        structure["chunking_error"] = str(exc)

    # 6. ADIM: QUALITY METRICS
    quality = _pdf_generic_quality_metrics(normalized)

    # 7. ADIM: IN-MEMORY EXTRACTION CANDIDATE
    return ExtractionCandidate(
        document_id=doc_id,
        source_document_id=src_doc_id,
        source_path=str(path),
        source_sha256=validation.actual_sha256,
        extraction_method=method_info["primary_method"],
        fallback_used=method_info["fallback_used"],
        spatial_reconstruction_used=method_info["spatial_reconstruction_used"],
        pdfplumber_used=method_info["pdfplumber_used"],
        pypdf_used=method_info["pypdf_used"],
        ocr_used=method_info["ocr_used"],
        ocr_details=method_info["ocr_details"],
        page_count=int(page_count or 0),
        raw_text=raw_text,
        normalized_text=normalized,
        structure=structure,
        legal_chunks=legal_chunks,
        quality_metrics=quality,
        metadata={
            "validation": validation.to_dict(),
            "method_info": method_info,
            "chunk_count": len(legal_chunks),
            "schema_version": str(CHUNK_SCHEMA_VERSION),
        },
        status="SUCCESS",
        in_memory_only=True,
    )
