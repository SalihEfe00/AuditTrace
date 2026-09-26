# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Optional

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
    "append_audit",
    "load_reviews",
    "save_review",
    "apply_reviews",
]
