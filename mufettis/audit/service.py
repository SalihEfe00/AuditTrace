# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List, Tuple

import pandas as pd

from mufettis.config import AUDIT_LOG_DIR, REVIEW_FILE
from mufettis.common.utils import (
    atomic_write_json,
    load_json,
    mask_sensitive_payload,
    now_iso,
    safe_json,
)

def append_audit(event: str, payload: Dict[str, Any]) -> None:
    """Sadece gerçekten gerçekleşen olaylarda çağrılır; Streamlit rerunlarında otomatik yazılmaz.

    Tüm metin değerleri yazılmadan önce maskelenir (IBAN, kart, TCKN/VKN, telefon,
    e-posta); çağıranın maskelemeyi unutması kişisel verinin loga düşmesine yol açmaz.
    """
    AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    item = {"timestamp": now_iso(), "event": event, "payload": mask_sensitive_payload(safe_json(payload))}
    path = AUDIT_LOG_DIR / f"audit_{dt.date.today().isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
def load_audit_events(event: str) -> Tuple[List[Dict[str, Any]], int]:
    """Denetim kaydındaki bir olay türünün tüm kayıtları (eskiden yeniye) ve okunamayan satır sayısı.

    Bozuk satırlar atlanır ama sayılır; çağıran bunu kullanıcıya göstermelidir.
    """
    events: List[Dict[str, Any]] = []
    unreadable = 0
    for path in sorted(AUDIT_LOG_DIR.glob("audit_*.jsonl")):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    unreadable += 1
                    continue
                if isinstance(item, dict) and item.get("event") == event:
                    events.append(item)
    events.sort(key=lambda item: str(item.get("timestamp", "")))
    return events, unreadable


def analysis_review_history() -> Tuple[List[Dict[str, Any]], int]:
    """Belge analizi inceleme kararları, en yeni önce. Aynı analiz birden çok kez incelendiyse
    her karar ayrı satırdır (karar geçmişi korunur)."""
    events, unreadable = load_audit_events("document_analysis_review")
    rows = [{"logged_at": item.get("timestamp", ""), **(item.get("payload") or {})} for item in events]
    return list(reversed(rows)), unreadable


def load_reviews() -> Dict[str, Any]:
    data = load_json(REVIEW_FILE, {"version": "1.0", "items": {}})
    if not isinstance(data, dict) or "items" not in data:
        return {"version": "1.0", "items": {}}
    return data
def save_review(finding_id: str, status: str, reviewer: str, note: str = "") -> None:
    registry = load_reviews()
    registry["items"][finding_id] = {
        "status": status,
        "reviewer": reviewer.strip() or "Belirtilmedi",
        "note": note.strip(),
        "updated_at": now_iso(),
    }
    atomic_write_json(REVIEW_FILE, registry)
    append_audit("finding_review", {"finding_id": finding_id, "status": status, "reviewer": reviewer.strip()})
def apply_reviews(findings_df: pd.DataFrame) -> pd.DataFrame:
    out = findings_df.copy()
    registry = load_reviews().get("items", {})
    out["inceleme_durumu"] = "Bekliyor"
    out["inceleyen"] = ""
    out["inceleme_notu"] = ""
    for idx, row in out.iterrows():
        rid = str(row.get("bulgu_id", ""))
        item = registry.get(rid)
        if item:
            out.at[idx, "inceleme_durumu"] = item.get("status", "Bekliyor")
            out.at[idx, "inceleyen"] = item.get("reviewer", "")
            out.at[idx, "inceleme_notu"] = item.get("note", "")
    return out


__all__ = [
    "analysis_review_history",
    "append_audit",
    "load_audit_events",
    "load_reviews",
    "save_review",
    "apply_reviews",
]
