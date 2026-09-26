# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import io
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    from docx import Document
except Exception:
    Document = None

from mufettis.audit.service import append_audit
from mufettis.common.utils import atomic_write_json, load_json, now_iso, safe_json, sha256_bytes
from mufettis.config import (
    BULK_ERROR_FILE,
    BULK_MAX_FILE_MB,
    BULK_STATE_FILE,
    BULK_SUPPORTED_EXTENSIONS,
)
from mufettis.legal.parser import extract_legal_metadata, find_previous_same_title
from mufettis.rag.maintenance import versioned_ingest_text
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper, enrich_source_meta
from mufettis.rag.index import IngestIndex
from mufettis.rag.engine import LocalRAG

class BulkRegulationImporter:
    """
    Bir klasör ağacındaki mevzuat dosyalarını tek tek, kaldığı yerden devam
    edebilecek şekilde ChromaDB'ye aktarır.

    Tasarım:
    - Path.rglob ile alt klasörler dahil tarama
    - İçerik hash'i ile duplicate / daha önce işlenmiş dosya kontrolü
    - Her dosya tek başına işlenir; tüm klasör RAM'e alınmaz
    - Başarılı ingest sonrası indeks kaydı yapılır
    - Hatalı dosya tüm çalışmayı durdurmaz
    - İsteğe bağlı progress callback ile Streamlit UI desteklenir
    - DOCX/PDF/TXT/MD/HTML/HTM destekler
    """

    def __init__(
        self,
        folder: Path,
        rag: Optional[LocalRAG],
        index: Optional[IngestIndex] = None,
        max_file_mb: int = BULK_MAX_FILE_MB,
    ):
        self.folder = Path(folder).expanduser().resolve()
        self.rag = rag
        self.index = index or IngestIndex()
        self.max_file_bytes = int(max_file_mb * 1024 * 1024)
        self.state = load_json(BULK_STATE_FILE, {"version": "1.0", "runs": []})
        if not isinstance(self.state, dict) or not isinstance(self.state.get("runs"), list):
            self.state = {"version": "1.0", "runs": []}

    def scan(self) -> List[Path]:
        if not self.folder.exists():
            raise FileNotFoundError(f"Klasör bulunamadı: {self.folder}")
        if not self.folder.is_dir():
            raise NotADirectoryError(f"Klasör değil: {self.folder}")
        files = [
            p for p in self.folder.rglob("*")
            if p.is_file() and p.suffix.lower() in BULK_SUPPORTED_EXTENSIONS
        ]
        return sorted(files, key=lambda x: str(x).lower())

    def _text_from_file(self, path: Path, raw: bytes) -> Tuple[str, int, bool]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            scraper = ResmiGazeteScraper(index=self.index, local_only=True)
            return scraper._extract_pdf_text(raw)
        if suffix == ".docx":
            if Document is None:
                raise RuntimeError("DOCX desteği için python-docx kurulu değil")
            doc = Document(io.BytesIO(raw))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            # Tabloları da metne dahil et; mevzuat bazen tablo içine alınabilir.
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        paragraphs.append(" | ".join(cells))
            return "\n".join(paragraphs).strip(), 0, False
        if suffix in {".html", ".htm"}:
            soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
            for el in soup(["script", "style", "header", "footer", "nav"]):
                el.extract()
            return re.sub(r"\n\s*\n", "\n", soup.get_text(separator="\n")).strip(), 0, False
        return raw.decode("utf-8-sig", errors="replace").strip(), 0, False

    def _build_meta(self, path: Path, file_hash: str, text: str, pages: int, ocr_used: bool, size: int) -> Dict[str, Any]:
        meta = extract_legal_metadata(path.stem, text, enrich_source_meta({
            "file_hash": file_hash,
            "title": path.stem,
            "url": "",
            "source": "Yerel mevzuat paketi",
            "timestamp": now_iso(),
            "page_count": pages,
            "ocr_used": ocr_used,
            "ingest_origin": "bulk_local_folder",
            "local_path": str(path),
            "relative_path": str(path.relative_to(self.folder)),
            "file_size": size,
            "file_extension": path.suffix.lower(),
        }, "local_file"))
        previous = find_previous_same_title(self.index, meta["title"], exclude_hash=file_hash)
        if previous:
            meta["previous_hash"] = previous.get("file_hash", "")
            meta["version_relation"] = "same_title_previous_version"
        return meta

    def summarize(self, files: List[Path]) -> Dict[str, Any]:
        total = len(files)
        indexed = 0
        new = 0
        too_large = 0
        try:
            for path in files:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > self.max_file_bytes:
                    too_large += 1
                    continue
                try:
                    file_hash = sha256_bytes(path.read_bytes())
                except Exception:
                    continue
                if self.index.get(file_hash):
                    indexed += 1
                else:
                    new += 1
        except Exception:
            pass
        return {"total": total, "already_indexed": indexed, "new": new, "too_large": too_large}

    def _record_error(self, path: Path, stage: str, error: Exception) -> Dict[str, Any]:
        item = {
            "timestamp": now_iso(),
            "file": str(path),
            "stage": stage,
            "error": str(error),
        }
        BULK_ERROR_FILE.parent.mkdir(parents=True, exist_ok=True)
        with BULK_ERROR_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(safe_json(item), ensure_ascii=False) + "\n")
        return item

    def _save_run_state(self, run_state: Dict[str, Any]) -> None:
        self.state["runs"] = (self.state.get("runs") or [])[-19:] + [safe_json(run_state)]
        atomic_write_json(BULK_STATE_FILE, self.state)

    def import_folder(
        self,
        force: bool = False,
        max_files: Optional[int] = None,
        progress_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if self.rag is None:
            raise RuntimeError("Toplu ingest için LocalRAG hazır olmalı")
        files = self.scan()
        if max_files is not None:
            files = files[: max(0, int(max_files))]

        started = now_iso()
        stats = {
            "run_id": uuid.uuid4().hex,
            "started_at": started,
            "folder": str(self.folder),
            "total": len(files),
            "processed": 0,
            "ingested": 0,
            "skipped_index": 0,
            "skipped_chroma": 0,
            "too_large": 0,
            "empty": 0,
            "errors": 0,
            "chunks": 0,
            "ocr_files": 0,
            "files": [],
        }

        for i, path in enumerate(files, 1):
            item = {"file": str(path), "status": "pending"}
            try:
                size = path.stat().st_size
                if size > self.max_file_bytes:
                    stats["too_large"] += 1
                    item.update({"status": "too_large", "size": size})
                    stats["files"].append(item)
                    continue

                raw = path.read_bytes()
                file_hash = sha256_bytes(raw)
                existing = self.index.get(file_hash)
                if existing and not force:
                    stats["skipped_index"] += 1
                    item.update({"status": "skipped_index", "hash": file_hash})
                    stats["files"].append(item)
                    stats["processed"] += 1
                    if progress_callback:
                        progress_callback(i, len(files), path.name, item["status"], stats)
                    continue

                text, pages, ocr_used = self._text_from_file(path, raw)
                if not text.strip():
                    stats["empty"] += 1
                    item.update({"status": "empty", "hash": file_hash})
                    stats["files"].append(item)
                    stats["processed"] += 1
                    if progress_callback:
                        progress_callback(i, len(files), path.name, item["status"], stats)
                    continue

                meta = self._build_meta(path, file_hash, text, pages, ocr_used, size)
                result = versioned_ingest_text(
                    self.rag,
                    self.index,
                    text,
                    meta,
                    force=force,
                    raw_bytes=raw,
                    source_path=str(path),
                )
                result_status = result.get("status", "")
                result_meta = result.get("meta", meta)
                chunk_count = int(result.get("count", 0) or 0)
                if result_status == "skipped":
                    stats["skipped_chroma"] += 1
                    item["status"] = "skipped_chroma"
                else:
                    stats["ingested"] += 1
                    stats["chunks"] += chunk_count
                    item["status"] = "ingested"
                meta = result_meta
                if ocr_used:
                    stats["ocr_files"] += 1
                # Sadece başarılı/atlanan durumdan sonra indeksle; böylece yarım ingest
                # nedeniyle belge kalıcı olarak "işlenmiş" sayılmaz.
                meta["chunk_count"] = chunk_count
                meta["last_bulk_run_id"] = stats["run_id"]
                self.index.upsert(file_hash, meta)
                item.update({
                    "hash": file_hash,
                    "title": meta.get("title"),
                    "chunks": chunk_count,
                    "ocr_used": ocr_used,
                })
                stats["files"].append(item)
                stats["processed"] += 1

            except Exception as exc:
                stats["errors"] += 1
                item.update({"status": "error", "error": str(exc)})
                self._record_error(path, "bulk_ingest", exc)
                stats["files"].append(item)
                stats["processed"] += 1

            if progress_callback:
                progress_callback(i, len(files), path.name, item["status"], stats)

        stats["finished_at"] = now_iso()
        stats["duration_seconds"] = max(0.0, (dt.datetime.fromisoformat(stats["finished_at"]) - dt.datetime.fromisoformat(stats["started_at"])).total_seconds())
        self._save_run_state(stats)
        append_audit("bulk_regulation_ingest_completed", {
            k: v for k, v in stats.items() if k not in {"files"}
        })
        return stats
def scan_regulation_folder(folder: Path, max_file_mb: int = BULK_MAX_FILE_MB) -> Dict[str, Any]:
    importer = BulkRegulationImporter(folder=folder, rag=None, max_file_mb=max_file_mb)
    files = importer.scan()
    return importer.summarize(files)


__all__ = [
    "BulkRegulationImporter",
    "scan_regulation_folder",
]
