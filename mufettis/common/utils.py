# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from mufettis.config import (
    BASE_DIR,
    DATA_DIR,
    SOURCE_ALIASES,
    SOURCE_DEFINITIONS,
    SOURCE_HOST_ALIASES,
    SOURCE_REGISTRY_FILE,
    WEB_HTTP_CACHE_DIR,
)

def canonical_source_id(source_id: str) -> str:
    return SOURCE_ALIASES.get(str(source_id), str(source_id))
def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
def normalize_title(title: str) -> str:
    return re.sub(r"\W+", " ", str(title).lower(), flags=re.UNICODE).strip()
def load_source_registry() -> Dict[str, Any]:
    default = {"version": "1.0", "sources": SOURCE_DEFINITIONS, "updated_at": now_iso()}
    data = load_json(SOURCE_REGISTRY_FILE, default)
    if not isinstance(data, dict) or "sources" not in data:
        return default
    return data
def source_profile(source_id: str) -> Dict[str, Any]:
    source_id = canonical_source_id(source_id)
    registry = load_source_registry()
    return dict(registry.get("sources", {}).get(source_id, SOURCE_DEFINITIONS.get(source_id, {})))
def enrich_source_meta(meta: Dict[str, Any], source_id: str) -> Dict[str, Any]:
    profile = source_profile(source_id)
    out = dict(meta)
    out.update({
        "source_id": source_id,
        "source": profile.get("name", out.get("source", "")),
        "source_type": profile.get("source_type", "secondary"),
        "authority_rank": int(profile.get("authority_rank", 0) or 0),
        "source_preferred": bool(profile.get("preferred", False)),
        "source_notes": profile.get("notes", ""),
    })
    return out
def source_is_allowed(url: str, source_id: str) -> bool:
    source_id = canonical_source_id(source_id)
    profile = source_profile(source_id)
    base = profile.get("base_url", "")
    if not base:
        return False
    try:
        target = urlparse(url)
        origin = urlparse(base)
        if target.scheme not in {"http", "https"}:
            return False
        aliases = SOURCE_HOST_ALIASES.get(source_id)
        if aliases:
            return target.netloc.lower() in aliases
        return target.netloc.lower() == origin.netloc.lower()
    except Exception:
        return False
def web_cache_key(url: str) -> str:
    return hashlib.sha256(str(url).encode("utf-8")).hexdigest()
def web_cache_meta_path(url: str) -> Path:
    return WEB_HTTP_CACHE_DIR / f"{web_cache_key(url)}.json"
def load_web_cache_meta(url: str) -> Dict[str, Any]:
    data=load_json(web_cache_meta_path(url), {})
    return data if isinstance(data, dict) else {}
def save_web_cache_meta(url: str, meta: Dict[str, Any]) -> None:
    atomic_write_json(web_cache_meta_path(url), meta)
def safe_json(value: Any) -> Any:
    """JSON-serializable güvenli dönüştürme."""
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.ndarray,)):
        return [safe_json(x) for x in value.tolist()]
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe_json(x) for x in value]
    return str(value)
def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(safe_json(payload), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
# Kişisel/finansal veri kalıpları. Sıra önemlidir: uzun ve özgül kalıplar (IBAN, kart)
# genel rakam dizilerinden (TCKN/VKN) önce uygulanır. Denetim ve LLM bağlamında fazla
# maskeleme kabul edilebilir; eksik maskeleme edilemez.
_SENSITIVE_PATTERNS = [
    # E-posta
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[E-POSTA]"),
    # IBAN: bitişik ya da 4'lü gruplar halinde; eksik yazılmış (kısaltılmış) IBAN'lar da maskelenir.
    # TR IBAN'ında ülke kodundan sonrası yalnızca rakamdır (harf kabul etmek sonraki kelimeyi yutar).
    (re.compile(r"\bTR\d{2}(?:[ ]?\d{4}){2,5}(?:[ ]?\d{1,4})?\b", re.I), "[IBAN]"),
    # Kart numarası: 13-19 hane, boşluk/tire ile 4'lü gruplar dahil (Luhn aranmaz: hatalı
    # yazılmış kart numarası da kişisel veridir).
    (re.compile(r"\b\d{4}(?:[ -]?\d{4}){2}[ -]?\d{1,7}\b"), "[KART]"),
    (re.compile(r"\b\d{4}[ -]?\d{6}[ -]?\d{5}\b"), "[KART]"),  # American Express 4-6-5
    # Cep telefonu: +90 / 0 önekli ya da öneksiz 5xx
    (re.compile(r"(?:\+90[ ]?|\b0[ ]?|\b)5\d{2}[ .-]?\d{3}[ .-]?\d{2}[ .-]?\d{2}\b"), "[TELEFON]"),
    # TCKN (11 hane) ve VKN (10 hane)
    (re.compile(r"\b\d{11}\b"), "[TCKN/VKN]"),
    (re.compile(r"\b\d{10}\b"), "[TCKN/VKN]"),
]


def mask_sensitive(text: str) -> str:
    """LLM/audit loglarında kişisel/finansal değerleri maskele.

    Kapsam: e-posta, IBAN (boşluklu/kısmi dahil), kart numarası, cep telefonu,
    TCKN (11 hane) ve VKN (10 hane).
    """
    if not text:
        return text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def mask_sensitive_payload(value: Any) -> Any:
    """Sözlük/liste içindeki tüm metin değerlerini mask_sensitive ile maskeler (anahtarlar korunur)."""
    if isinstance(value, str):
        return mask_sensitive(value)
    if isinstance(value, dict):
        return {k: mask_sensitive_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_sensitive_payload(v) for v in value]
    return value
def atomic_csv_write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8-sig", suffix=".tmp", dir=path.parent) as tmp:
        temp_path = Path(tmp.name)
        df.to_csv(temp_path, index=False, encoding="utf-8-sig")
    os.replace(temp_path, path)
def check_disk_health(target: Path = DATA_DIR) -> Dict[str, Any]:
    target.mkdir(parents=True, exist_ok=True)
    try:
        total, used, free = shutil.disk_usage(target)
        test_path = target / ".healthcheck.tmp"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink(missing_ok=True)
        return {"ok": True, "free_gb": round(free / 1024**3, 2), "total_gb": round(total / 1024**3, 2)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


__all__ = [
    "canonical_source_id",
    "now_iso",
    "normalize_title",
    "load_source_registry",
    "source_profile",
    "enrich_source_meta",
    "source_is_allowed",
    "web_cache_key",
    "web_cache_meta_path",
    "load_web_cache_meta",
    "save_web_cache_meta",
    "safe_json",
    "atomic_write_json",
    "load_json",
    "sha256_bytes",
    "mask_sensitive",
    "mask_sensitive_payload",
    "atomic_csv_write",
    "check_disk_health",
]
