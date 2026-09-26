# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from mufettis.config import INGEST_INDEX_FILE
from mufettis.common.utils import (
    atomic_write_json,
    load_json,
    normalize_title,
    safe_json,
)

class IngestIndex:
    @staticmethod
    def default_path() -> Path:
        return INGEST_INDEX_FILE

    def __init__(self, path: Path = INGEST_INDEX_FILE):
        self.path = path
        self.data = load_json(path, {"version": "2.0", "indexed_items": {}})
        if "indexed_items" not in self.data:
            self.data = {"version": "2.0", "indexed_items": {}}

    def save(self) -> None:
        atomic_write_json(self.path, self.data)

    def get(self, file_hash: str) -> Optional[Dict[str, Any]]:
        return self.data["indexed_items"].get(file_hash)

    def upsert(self, file_hash: str, meta: Dict[str, Any]) -> None:
        existing = self.data["indexed_items"].get(file_hash, {})
        merged = {**existing, **safe_json(meta)}
        self.data["indexed_items"][file_hash] = merged
        self.save()

    def find_by_title_and_source(self, title: str, source_id: str) -> List[Dict[str, Any]]:
        wanted = normalize_title(title) if "normalize_title" in globals() else re.sub(r"\s+", " ", title).strip().lower()
        out = []
        for item in self.data["indexed_items"].values():
            if item.get("source_id") == source_id and item.get("title_normalized") == wanted:
                out.append(item)
        return out

    def remove(self, file_hash: str) -> None:
        self.data["indexed_items"].pop(file_hash, None)
        self.save()


__all__ = [
    "IngestIndex",
]
