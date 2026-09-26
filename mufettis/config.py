# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

PDFIUM_LOCK = threading.RLock()

APP_VERSION = "0.13.81"
BASE_DIR = Path(__file__).resolve().parent.parent
# MUFETTIS_DATA_DIR verilirse tüm veri yolları oraya yönlenir (ör. testlerde izole kopya).
DATA_DIR = Path(os.environ["MUFETTIS_DATA_DIR"]).resolve() if os.environ.get("MUFETTIS_DATA_DIR") else BASE_DIR / "data"
DB_PATH = DATA_DIR / "mevzuat_db"
# Parça deposu: "chroma" (varsayılan) veya "sqlite" (SQLite + disk üzerinde kesin vektör taraması,
# mufettis/rag/store.py). Geçiş, iki depo aynı sorgu kümesinde karşılaştırıldıktan sonra yapılır.
VECTOR_STORE = os.environ.get("MUFETTIS_VECTOR_STORE", "chroma").strip().lower()
CORPUS_STORE_PATH = DATA_DIR / "corpus_store"
LOG_DIR = DATA_DIR / "llm_logs"
AUDIT_LOG_DIR = DATA_DIR / "audit_logs"
EXPORT_DIR = DATA_DIR / "exports"
INGEST_INDEX_FILE = DATA_DIR / "ingest_index.json"
REVIEW_FILE = DATA_DIR / "review_registry.json"
SCRAPE_STATE_FILE = DATA_DIR / "scrape_state.json"
OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_LLM = "llama3.2:3b"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CHROMA_COLLECTION = "mevzuat"
MAX_RAG_RESULTS = 100
EMBED_BATCH_SIZE_CPU = 32
EMBED_BATCH_SIZE_GPU = 64
CHROMA_UPSERT_BATCH_SIZE = 128
WEB_FAST_WORKERS = 4
WEB_FAST_REQUEST_DELAY = 0.15
WEB_HTTP_CACHE_DIR = DATA_DIR / "web_http_cache"
MAX_FINDINGS_FOR_MATCH = 20
MAX_UPLOAD_MB = 100
SCRAPER_MIN_DELAY = 1.0
BULK_MAX_FILE_MB = 200
BULK_SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".html", ".htm", ".docx"}
BULK_STATE_FILE = DATA_DIR / "bulk_ingest_state.json"
BOOTSTRAP_STATE_FILE = DATA_DIR / "bootstrap_state.json"
BOOTSTRAP_MAX_BDDK_PAGES = 500
BOOTSTRAP_BDDK_DEPTH = 3
BOOTSTRAP_RESMI_DAYS = 7
BOOTSTRAP_RESMI_MAX_DOCS_PER_DAY = 120
BOOTSTRAP_SECONDARY_DEFAULT = False
BULK_ERROR_FILE = DATA_DIR / "bulk_ingest_errors.jsonl"
SOURCE_REGISTRY_FILE = DATA_DIR / "source_registry.json"
SOURCE_CACHE_DIR = DATA_DIR / "source_cache"
QUARANTINE_CACHE_DIR = DATA_DIR / "quarantine_cache"
QUARANTINE_MAX_PAYLOAD_MB = 50
WEB_SOURCE_MAX_PAGES = 250
WEB_SOURCE_MAX_DEPTH = 2
SOURCE_REQUEST_TIMEOUT = 20
SCHEDULER_STATE_FILE = DATA_DIR / "scheduler_state.json"
SCHEDULER_LOG_FILE = DATA_DIR / "scheduler_runs.jsonl"
SCHEDULER_TIMEZONE = "Europe/Istanbul"
SCHEDULER_HOUR = 0
SCHEDULER_MINUTE = 5
BROWSER_TIMEOUT_MS = 10000
BROWSER_DOCUMENT_TIMEOUT_MS = 10000
BROWSER_MAX_PAGES = 120
CHUNK_SCHEMA_VERSION = "10.0.0"
SOURCE_DEFINITIONS = {
    "bddk_official": {
        "name": "BDDK",
        "base_url": "https://www.bddk.gov.tr",
        "seed_urls": ["https://www.bddk.gov.tr/Mevzuat"],
        "source_type": "official",
        "publisher_kind": "issuing_authority",
        "issuer": "BDDK",
        "authority_rank": 100,
        "preferred": True,
        "notes": "BDDK resmi mevzuat sayfaları; mümkün olduğunda dinamik DOM/Playwright ile taranır.",
        "keywords": ["kanun", "yönetmelik", "tebliğ", "genelge", "kurul kararı", "bankacılık", "kredi", "mevduat", "sermaye", "likidite", "risk"],
    },
    "mevzuat_org": {
        "name": "mevzuat.org",
        "base_url": "https://www.mevzuat.org",
        "seed_urls": ["https://www.mevzuat.org/"],
        "source_type": "secondary",
        "publisher_kind": "secondary",
        "authority_rank": 40,
        "preferred": False,
        "notes": "Yardımcı/ikincil kaynak; resmi hukuki dayanak olarak tek başına kullanılmaz.",
    },
    "resmi_gazete": {
        "name": "Resmi Gazete",
        "base_url": "https://resmigazete.gov.tr",
        "seed_urls": ["https://resmigazete.gov.tr/"],
        "source_type": "official",
        "publisher_kind": "primary_official",
        "authority_rank": 100,
        "preferred": True,
        "notes": "Birincil resmi yayın kaynağı; günlük fihrist değişiklikleri için kullanılır.",
        "keywords": ["bankacılık", "banka", "kredi", "mevduat", "finans", "tebliğ", "yönetmelik", "kurul", "sermaye", "likidite"],
    },
}
# Yayıncı değil içe aktarma kanalı olan kaynak kimlikleri. Bu kanallardan gelen belgelerin
# yayıncısı bilinmez; güven belgenin kendisinden ve doğrulamadan gelir (bkz. mufettis.corpus).
INGESTION_CHANNELS = {
    "local_file": {"name": "Yerel dosya", "publisher_kind": "user_supplied"},
}
SOURCE_ALIASES = {"bdkk_official": "bddk_official"}
SOURCE_HOST_ALIASES = {
    # BDDK'nin resmi alan adları; doküman bağlantıları iki host arasında
    # yönlenebildiği için yalnızca bu iki resmi host kontrollü olarak eşdeğer
    # kabul edilir. Diğer alan adları hiçbir şekilde otomatik kabul edilmez.
    "bddk_official": {"www.bddk.gov.tr", "bddk.gov.tr", "www.bddk.org.tr", "bddk.org.tr"},
}

# Gerekli temel dizinlerin oluşturulması
SOURCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
WEB_HTTP_CACHE_DIR.mkdir(parents=True, exist_ok=True)

for _d in [DATA_DIR, DB_PATH, LOG_DIR, AUDIT_LOG_DIR, EXPORT_DIR]:
    _d.mkdir(parents=True, exist_ok=True)


__all__ = [
    "PDFIUM_LOCK",
    "APP_VERSION",
    "BASE_DIR",
    "DATA_DIR",
    "DB_PATH",
    "VECTOR_STORE",
    "CORPUS_STORE_PATH",
    "LOG_DIR",
    "AUDIT_LOG_DIR",
    "EXPORT_DIR",
    "INGEST_INDEX_FILE",
    "REVIEW_FILE",
    "SCRAPE_STATE_FILE",
    "OLLAMA_URL",
    "DEFAULT_LLM",
    "EMBEDDING_MODEL",
    "CHROMA_COLLECTION",
    "MAX_RAG_RESULTS",
    "EMBED_BATCH_SIZE_CPU",
    "EMBED_BATCH_SIZE_GPU",
    "CHROMA_UPSERT_BATCH_SIZE",
    "WEB_FAST_WORKERS",
    "WEB_FAST_REQUEST_DELAY",
    "WEB_HTTP_CACHE_DIR",
    "MAX_FINDINGS_FOR_MATCH",
    "MAX_UPLOAD_MB",
    "SCRAPER_MIN_DELAY",
    "BULK_MAX_FILE_MB",
    "BULK_SUPPORTED_EXTENSIONS",
    "BULK_STATE_FILE",
    "BOOTSTRAP_STATE_FILE",
    "BOOTSTRAP_MAX_BDDK_PAGES",
    "BOOTSTRAP_BDDK_DEPTH",
    "BOOTSTRAP_RESMI_DAYS",
    "BOOTSTRAP_RESMI_MAX_DOCS_PER_DAY",
    "BOOTSTRAP_SECONDARY_DEFAULT",
    "BULK_ERROR_FILE",
    "SOURCE_REGISTRY_FILE",
    "SOURCE_CACHE_DIR",
    "QUARANTINE_CACHE_DIR",
    "QUARANTINE_MAX_PAYLOAD_MB",
    "WEB_SOURCE_MAX_PAGES",
    "WEB_SOURCE_MAX_DEPTH",
    "SOURCE_REQUEST_TIMEOUT",
    "SCHEDULER_STATE_FILE",
    "SCHEDULER_LOG_FILE",
    "SCHEDULER_TIMEZONE",
    "SCHEDULER_HOUR",
    "SCHEDULER_MINUTE",
    "BROWSER_TIMEOUT_MS",
    "BROWSER_DOCUMENT_TIMEOUT_MS",
    "BROWSER_MAX_PAGES",
    "CHUNK_SCHEMA_VERSION",
    "SOURCE_DEFINITIONS",
    "INGESTION_CHANNELS",
    "SOURCE_ALIASES",
    "SOURCE_HOST_ALIASES",
]
