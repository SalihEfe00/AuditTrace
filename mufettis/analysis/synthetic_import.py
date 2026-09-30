# -*- coding: utf-8 -*-
"""BRIEF biçimindeki sentetik örnekleri belge vakasına çevirir (M4.3; benchmarks/synthetic/BRIEF.md).

Bir örnek dosyası: "=== BELGE ===" (dosya paketinde "=== BELGE n: <tür> ===") altında belge, ardından
"=== CEVAP ANAHTARI ===" altında JSON. Modeller biçimi tam tutturmayabilir (Markdown başlığı, kod çiti,
sondaki virgül); bunlar tolere edilir, anlamı belirsiz olan dosya sebebiyle reddedilir.

Tür görev kodundan gelir (T01 → kredi_teklif_onay); modelin yazdığı "belge_turu" yalnızca not olarak kalır.
Bölüm belirlenimcidir: sealed/ altındakiler "sealed", diğerleri dosya yolunun özetine göre dev / holdout.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mufettis.analysis import document_taxonomy as tax
from mufettis.analysis.document_benchmark import (
    Defect, DocumentCase, ExpectedFields, SubDocument, find_dates, locate_quote,
)
from mufettis.analysis.turkish_text import turkish_lower

HOLDOUT_BUCKETS = 3   # dosyaların ~1/3'ü holdout
PACKAGE_TASK = "D01"

_MARKER_RE = re.compile(r"^[#*\s>_`]*===\s*(?P<name>[^=\n]+?)\s*===[*\s_`]*$", re.MULTILINE)
_FILE_RE = re.compile(r"^(?P<task>[TD]\d{2})_(?P<n>\d+)$", re.IGNORECASE)


@dataclass
class ImportOutcome:
    case: Optional[DocumentCase] = None
    rejected: str = ""                        # boş değilse reddedilme sebebi
    warnings: List[str] = field(default_factory=list)


def split_for(relative_path: str) -> str:
    """Dosya yolunun özetine göre dev / holdout (aynı dosya her çalıştırmada aynı bölüme düşer)."""
    digest = hashlib.sha256(relative_path.replace("\\", "/").encode("utf-8")).digest()
    return "holdout" if digest[0] % HOLDOUT_BUCKETS == 0 else "dev"


def _strip_fences(text: str) -> str:
    """Bölümü saran kod çitini ve boş satırları kaldırır (CSV örnekleri ```csv içinde gelebilir)."""
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_answer_key(text: str) -> Dict[str, Any]:
    body = _strip_fences(text)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        raise ValueError("cevap anahtarında JSON yok")
    raw = body[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    lenient = re.sub(r",(\s*[}\]])", r"\1", raw)          # sondaki virgül
    try:
        return json.loads(lenient)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cevap anahtarı JSON değil ({exc.msg}, satır {exc.lineno})") from None


def _is_key(name: str) -> bool:
    upper = name.upper()
    return "CEVAP" in upper and "ANAHTAR" in upper


def _is_section(name: str) -> bool:
    return _is_key(name) or re.match(r"BELGE(?![A-ZÇĞİÖŞÜ])", name.strip().upper()) is not None


def split_sections(content: str) -> Tuple[List[Tuple[str, str]], str]:
    """(belge başlığı, belge metni) listesi ve cevap anahtarı metni."""
    # Yalnızca BELGE ve CEVAP ANAHTARI işaretleri bölüm sınırıdır; belgenin kendi içindeki "=== ... ===" satırları
    # (ör. bir dekont başlığı) metinde kalır.
    markers = [m for m in _MARKER_RE.finditer(content) if _is_section(m.group("name"))]
    documents: List[Tuple[str, str]] = []
    key_text = ""
    for i, m in enumerate(markers):
        body = content[m.end():markers[i + 1].start() if i + 1 < len(markers) else len(content)]
        name = m.group("name").strip()
        if _is_key(name):
            key_text = body
        else:
            documents.append((name, _strip_fences(body)))
    return documents, key_text


def _severity(value: Any) -> str:
    text = turkish_lower(str(value or "")).strip()
    return {"yuksek": "yüksek", "yüksek": "yüksek", "orta": "orta", "dusuk": "düşük", "düşük": "düşük"}.get(text, "")


def _defects(key: Dict[str, Any], text: str, warnings: List[str]) -> List[Defect]:
    entries: List[Tuple[Any, str]] = [(d, "belge") for d in key.get("kusurlar") or []]
    for name, value in key.items():
        if re.search(r"belgeler\s*_?\s*aras", turkish_lower(name)) and isinstance(value, list):
            entries += [(d, "dosya") for d in value]
    for doc in key.get("belgeler") or []:
        if isinstance(doc, dict):
            entries += [(d, "belge") for d in doc.get("kusurlar") or []]
    defects = []
    for item, scope in entries:
        if isinstance(item, str):
            item = {"aciklama": item}
        if not isinstance(item, dict) or not str(item.get("aciklama") or "").strip():
            warnings.append("açıklamasız kusur atlandı")
            continue
        quote = str(item.get("belgedeki_yer") or "").strip()
        if quote and not locate_quote(quote, text):
            warnings.append(f"kusur alıntısı belgede bulunamadı, boşaltıldı: {item['aciklama'][:60]!r}")
            quote = ""
        defects.append(Defect(description=str(item["aciklama"]).strip(), quote=quote,
                              regulation=str(item.get("ilgili_mevzuat") or "").strip(),
                              severity=_severity(item.get("onem")), scope=scope))
    return defects


def _fields(key: Dict[str, Any]) -> ExpectedFields:
    alanlar = key.get("alanlar") if isinstance(key.get("alanlar"), dict) else {}
    per_doc = [d.get("alanlar") for d in key.get("belgeler") or [] if isinstance(d, dict) and isinstance(d.get("alanlar"), dict)]
    if per_doc:
        merged: Dict[str, List[Any]] = {}
        for source in [alanlar] + per_doc:
            for name, value in source.items():
                merged.setdefault(name, []).append(value)
        alanlar = merged
    return ExpectedFields.from_answer_key(alanlar)


def _first_date(value: Any) -> str:
    dates = find_dates(str(value or ""))
    return dates[0] if dates else ""


def convert_file(path: Path, root: Path, split: Optional[str] = None) -> ImportOutcome:
    """root: incoming/ ya da sealed/ klasörü; dosya root/<model>/<görev>_<n>.md olmalı."""
    rel = path.relative_to(root)
    warnings: List[str] = []
    if len(rel.parts) != 2:
        return ImportOutcome(rejected="yol <model>/<görev>_<n>.md değil")
    model, stem = rel.parts[0], path.stem
    name = _FILE_RE.match(stem)
    if not name:
        return ImportOutcome(rejected="dosya adı <görev>_<n> değil (ör. T01_001)")
    task = name.group("task").upper()
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return ImportOutcome(rejected="UTF-8 değil")
    documents, key_text = split_sections(content)
    if not documents:
        return ImportOutcome(rejected="'=== BELGE ===' bölümü yok")
    if not key_text.strip():
        return ImportOutcome(rejected="'=== CEVAP ANAHTARI ===' bölümü yok")
    try:
        key = parse_answer_key(key_text)
    except ValueError as exc:
        return ImportOutcome(rejected=str(exc))
    if not isinstance(key, dict):
        return ImportOutcome(rejected="cevap anahtarı nesne değil")

    subdocs: List[SubDocument] = []
    if task == PACKAGE_TASK:
        for title, body in documents:
            label = re.sub(r"^BELGE\s*\d*\s*[:.\-–]?\s*", "", title, flags=re.IGNORECASE).strip()
            code = tax.from_free_text(label)
            if code is None:
                return ImportOutcome(rejected=f"dosya paketindeki belge türü tanınmadı: '{label}'")
            subdocs.append(SubDocument(document_type=code, title=label, text=body))
        if len(subdocs) < 2:
            return ImportOutcome(rejected="dosya paketinde birden az belge")
        text = "\n\n".join(f"=== {d.title} ===\n{d.text}" for d in subdocs)
        document_type = ""
    else:
        document_type = tax.from_task(task) or ""
        if not document_type:
            return ImportOutcome(rejected=f"bilinmeyen görev kodu {task}")
        if len(documents) > 1:
            warnings.append(f"{len(documents)} belge bölümü var; birleştirildi")
        text = "\n\n".join(body for _, body in documents)
        declared = tax.from_free_text(str(key.get("belge_turu") or ""))
        if declared and declared != document_type:
            warnings.append(f"modelin belge_turu '{key.get('belge_turu')}' görevle ({task}) uyuşmuyor; görev esas")

    defects = _defects(key, text, warnings)
    declared_ok = key.get("kusursuz")
    if isinstance(declared_ok, str):
        declared_ok = turkish_lower(declared_ok).strip() in ("true", "evet")
    compliant = not defects if declared_ok is None else bool(declared_ok)
    if compliant and defects:
        return ImportOutcome(rejected="'kusursuz' true ama kusur listelenmiş")
    if not compliant and not defects:
        return ImportOutcome(rejected="'kusursuz' false ama kusur yok")

    case_id = re.sub(r"[^a-z0-9._-]+", "-", f"{model}-{stem}".lower())
    case = DocumentCase(
        case_id=case_id, split=split or split_for(rel.as_posix()), document_type=document_type, text=text,
        compliant=compliant, defects=defects, fields=_fields(key), documents=subdocs,
        issue_date=_first_date(key.get("duzenlenme_tarihi")), event_date=_first_date(key.get("olay_tarihi")),
        source={"model": model, "task": task, "file": rel.as_posix(), "declared_type": str(key.get("belge_turu") or "")},
    )
    problems = case.problems()
    if problems:
        return ImportOutcome(rejected="; ".join(problems), warnings=warnings)
    return ImportOutcome(case=case, warnings=warnings)


def convert_tree(root: Path, sealed: bool = False) -> Tuple[List[DocumentCase], Dict[str, str], Dict[str, List[str]]]:
    """root altındaki tüm .md dosyaları (sıralı). Dönüş: vakalar, {dosya: red sebebi}, {dosya: uyarılar}."""
    cases, rejected, warnings = [], {}, {}
    for path in sorted(Path(root).rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        outcome = convert_file(path, Path(root), split="sealed" if sealed else None)
        if outcome.warnings:
            warnings[rel] = outcome.warnings
        if outcome.case is None:
            rejected[rel] = outcome.rejected
        else:
            cases.append(outcome.case)
    return cases, rejected, warnings


__all__ = ["HOLDOUT_BUCKETS", "ImportOutcome", "split_for", "parse_answer_key", "split_sections",
           "convert_file", "convert_tree"]
