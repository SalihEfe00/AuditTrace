# -*- coding: utf-8 -*-
"""Kurulumsuz demo: örnek kredi dosyası (uydurma bilgili 4 belge) arayüzsüz kontrol edilir, bulgular terminale yazılır.

Mevzuat kütüphanesi (data/), Tesseract ve dil modeli gerekmez; yalnızca requirements.txt. Arayüzdeki
"Örnek dosyayla dene" düğmesiyle aynı belgeler ve aynı kurallar:

    python app.py --demo
    python -m mufettis.demo [klasör]      # başka bir klasördeki belgeler (PDF, DOCX, DOC, TXT)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from mufettis.extraction.files import SUPPORTED_EXTENSIONS, extract_file_text
from mufettis.rules.catalog import load_catalog
from mufettis.rules.credit_file import CreditFile, FileResult, evaluate_file, file_event_date
from mufettis.rules.display import status_label
from mufettis.rules.engine import EXEMPT, MANUAL, MET, NOT_MET, UNKNOWN, evaluate
from mufettis.rules.event_date import document_event_date
from mufettis.rules.findings import Finding, build_findings

SAMPLE_DIR = Path(__file__).resolve().parent / "ui" / "samples" / "kredi_dosyasi"


def run_demo(folder: Optional[Path] = None, out: Callable[[str], None] = print) -> List[Finding]:
    """Klasördeki belgeleri tek kredi dosyası olarak kontrol eder; bulguları yazar ve döndürür."""
    folder = Path(folder or SAMPLE_DIR)
    paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)
    if not paths:
        raise SystemExit(f"{folder} içinde belge yok ({', '.join(SUPPORTED_EXTENSIONS)})")
    credit_file = CreditFile.from_texts((p.name, extract_file_text(p.name, p.read_bytes()).text) for p in paths)
    event_date, source = file_event_date(credit_file)
    rules = load_catalog()
    # Arayüzdeki gibi: belgeler arası kurallar + her belgenin kendi kuralları (kendi olay tarihiyle)
    results = list(evaluate_file(rules, credit_file, event_date=event_date))
    for doc in credit_file.documents:
        if doc.kind:
            day = document_event_date(doc.kind, doc.fields)[0]
            results += [FileResult((doc.name,), r)
                        for r in evaluate(rules, doc.kind, doc.fields, text=doc.text, event_date=day)]
    groups = _group(results)
    findings = build_findings(rules, groups["finding"])

    out(f"AuditTrace demo — {folder}")
    for doc in credit_file.documents:
        out(f"  {doc.name}: {doc.kind or 'tanınmadı'}")
    out(f"Olay tarihi: {event_date:%d.%m.%Y} ({source})")
    shown = sum(len(v) for v in groups.values())
    out(f"{shown} kontrolden {len(findings)} bulgu | {len(groups['undecided'])} karar verilemedi | "
        f"{len(groups['passed'])} uygun | {len(groups['missing'])} belge yok")
    out("")
    for i, f in enumerate(findings, 1):
        out(f"{i}. [{status_label(f.status)}] {f.detection}")
        out(f"   Ölçüt: {f.criterion}" + (f" — “{f.quote}”" if f.quote else ""))
        for line in f.evidence:
            out(f"   Kanıt: {line}")
        out(f"   Öneri: {f.recommendation}")
        out("")
    for item in groups["undecided"]:
        out(f"[karar verilemedi] {item.result.finding}")
    if groups["missing"]:
        kinds = sorted({k for item in groups["missing"] for k in item.missing})
        out(f"Dosyada olmayan belgeler yüzünden yapılamayan {len(groups['missing'])} kontrol: {', '.join(kinds)}")
    out("")
    out("Arayüz için: streamlit run app.py → \"Örnek dosyayla dene\"")
    return findings


def _group(results) -> Dict[str, list]:
    """Sonuçların ekrandaki grupları (mufettis.ui.rules_view._group ile aynı; arayüz modülü Streamlit ister)."""
    groups: Dict[str, list] = {"finding": [], "undecided": [], "passed": [], "missing": []}
    for item in results:
        res = item.result
        if item.missing and res.status in (UNKNOWN, NOT_MET):
            groups["missing"].append(item)
        elif res.status in (NOT_MET, MANUAL):
            groups["finding"].append(item)
        elif res.status == UNKNOWN:
            groups["undecided"].append(item)
        elif res.status in (MET, EXEMPT):
            groups["passed"].append(item)
    return groups


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run_demo(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
