# -*- coding: utf-8 -*-
"""
Test oturumunun kullandığı veri dizini için yapısal bütünlük kontrolleri.

Production verisinin değişmediği conftest.py tarafından oturum düzeyinde
(önce/sonra karşılaştırması) doğrulanır. Buradaki kontroller sabit hash veya
kayıt sayısı beklemez; meşru veri güncellemelerinde kırılmayan değişmezleri
denetler.
"""
from __future__ import annotations

import json
import sqlite3
from typing import List

from mufettis.config import BASE_DIR, DATA_DIR, DB_PATH, INGEST_INDEX_FILE
from repair.audit import REPAIR_AUDIT_LOG_FILE, verify_audit_integrity


def data_integrity_problems() -> List[str]:
    """Bulunan sorunların listesini döndürür; boş liste = sağlam."""
    problems: List[str] = []

    if DATA_DIR.resolve() == (BASE_DIR / "data").resolve():
        problems.append(f"Testler production veri dizininde çalışıyor: {DATA_DIR}")

    if INGEST_INDEX_FILE.exists():
        try:
            items = json.loads(INGEST_INDEX_FILE.read_text(encoding="utf-8")).get("indexed_items")
            if not isinstance(items, dict):
                problems.append("ingest_index.json: 'indexed_items' sözlük değil")
        except ValueError as exc:
            problems.append(f"ingest_index.json okunamadı: {exc}")

    chroma_sqlite = DB_PATH / "chroma.sqlite3"
    if chroma_sqlite.exists():
        conn = sqlite3.connect(f"file:{chroma_sqlite.as_posix()}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if result != "ok":
            problems.append(f"Chroma SQLite bütünlük kontrolü başarısız: {result}")

    if REPAIR_AUDIT_LOG_FILE.exists():
        chain_valid, chain_errors = verify_audit_integrity(REPAIR_AUDIT_LOG_FILE)
        if not chain_valid:
            problems.append(f"repair_audit hash zinciri geçersiz: {chain_errors[:3]}")

    return problems
