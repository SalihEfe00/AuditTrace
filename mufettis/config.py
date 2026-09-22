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
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "mevzuat_db"
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
        "authority_rank": 40,
        "preferred": False,
        "notes": "Yardımcı/ikincil kaynak; resmi hukuki dayanak olarak tek başına kullanılmaz.",
    },
    "resmi_gazete": {
        "name": "Resmi Gazete",
        "base_url": "https://resmigazete.gov.tr",
        "seed_urls": ["https://resmigazete.gov.tr/"],
        "source_type": "official",
        "authority_rank": 100,
        "preferred": True,
        "notes": "Birincil resmi yayın kaynağı; günlük fihrist değişiklikleri için kullanılır.",
        "keywords": ["bankacılık", "banka", "kredi", "mevduat", "finans", "tebliğ", "yönetmelik", "kurul", "sermaye", "likidite"],
    },
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


def resolve_dynamic(name: str, fallback: Any = None) -> Any:
    """Test monkeypatching desteği: test modülleri veya __main__'de override edilmiş değerleri yakalar."""
    # 1. Önce aktif test modüllerini tara
    for mod_name, mod in list(sys.modules.items()):
        if mod is not None and ("test" in mod_name):
            if name in mod.__dict__:
                val = mod.__dict__[name]
                if fallback is not None:
                    if callable(fallback) and val is not fallback:
                        return val
                    elif not callable(fallback) and val != fallback:
                        return val
                else:
                    return val
    # 2. Sonra __main__ ve app
    for mod_name in ("__main__", "app"):
        mod = sys.modules.get(mod_name)
        if mod is not None and name in mod.__dict__:
            val = mod.__dict__[name]
            if fallback is not None:
                if callable(fallback) and val is not fallback:
                    return val
                elif not callable(fallback) and val != fallback:
                    return val
            else:
                return val
    return fallback


__all__ = [k for k in dict(globals()).keys() if not k.startswith('__')]
