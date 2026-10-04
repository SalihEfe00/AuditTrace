# -*- coding: utf-8 -*-
"""Kredi dosyası (M10.1): birden çok belge tek dosya olarak; belgeler arası kurallar M9 motoruyla.

Her belge türüne göre tanınır (mufettis.rules.doctype) ve alanları çıkarılır. Dosya kuralı (katalogda
`document_type: kredi_dosyasi`) alanı "<belge türü>.<alan>" diye adlandırır ("kullandirim.kullandirim_tarihi").
Motor değişmez: dosyanın alanları bu adlarla tek sözlükte toplanır.

- Eksik belge: o türün alanları sözlükte yoktur, motor "bulunamadı" görür, kural "belirsiz" olur; bulguya hangi
  belgenin dosyada olmadığı yazılır.
- Aynı türden birden çok belge (iki kefalet, iki ipotek): kural her biri için ayrı değerlendirilir ve sonuç hangi
  belgeye ait olduğunu söyler; ancak kuralın öbür belgeleri dosyada doğası gereği tekse (onay, kullandırım, imza
  sirküleri). İki teminat belgesi arasındaki kuralda (DASK ↔ ipotek) biri çoklu ise hangisinin hangisiyle ilgili
  olduğu belgeden bilinemez: kural "belirsiz" olur, eşleştirme elle yapılır.
- Tanınmayan belge dosyaya yerleşmez; listede "tanınmadı" olarak görünür (sessizce atlanmaz).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from mufettis.rules.authority import authority_fields
from mufettis.rules.catalog import AUTHORITY_KIND, FIELD_KEYS as _FIELD_KEYS, FILE_TYPE, Rule
from mufettis.rules.doctype import detect_collateral_type
from mufettis.rules.engine import UNKNOWN, FieldValue, RuleResult, evaluate_rule
from mufettis.rules.event_date import document_event_date
from mufettis.rules.fields_kefalet import extract_kefalet_fields
from mufettis.rules.fields_teminat import DOCUMENT_LABELS, extract_teminat_fields

# Bir kredi dosyasında doğası gereği tek olan belgeler: çoklu teminat belgesinin her biri bunlarla ayrı eşleşir
FILE_UNIQUE = {"kredi_onay", "kullandirim", "imza_sirkuleri"}


@dataclass
class FileDocument:
    name: str                       # kullanıcının verdiği ad (dosya adı)
    text: str
    kind: Optional[str]             # belge türü; tanınmadıysa None
    fields: Dict[str, FieldValue] = field(default_factory=dict)
    reason: str = ""


@dataclass
class FileResult:
    documents: Tuple[str, ...]      # sonucun ait olduğu belgeler (çoklu türde hangisi)
    result: RuleResult
    missing: Tuple[str, ...] = ()   # kuralın gerektirdiği ama dosyada olmayan belge türleri


def extract_fields(kind: str, text: str) -> Dict[str, FieldValue]:
    return extract_kefalet_fields(text) if kind == "kefalet_sozlesmesi" else extract_teminat_fields(kind, text)


def load_document(name: str, text: str, kind: Optional[str] = None) -> FileDocument:
    """Belgeyi tanır (tür verilmediyse) ve alanlarını çıkarır."""
    reason = "kullanıcı seçti"
    if kind is None:
        found = detect_collateral_type(text)
        kind, reason = found.document_type, found.reason
    if kind is not None and kind != "kefalet_sozlesmesi" and kind not in DOCUMENT_LABELS:
        kind, reason = None, f"bilinmeyen tür: {kind}"
    return FileDocument(name, text, kind, extract_fields(kind, text) if kind else {}, reason)


@dataclass
class CreditFile:
    documents: List[FileDocument] = field(default_factory=list)

    @classmethod
    def from_texts(cls, items: Iterable[Tuple[str, str]]) -> "CreditFile":
        return cls([load_document(name, text) for name, text in items])

    def by_kind(self) -> Dict[str, List[FileDocument]]:
        out: Dict[str, List[FileDocument]] = {}
        for doc in self.documents:
            if doc.kind:
                out.setdefault(doc.kind, []).append(doc)
        return out

    def unrecognised(self) -> List[FileDocument]:
        return [d for d in self.documents if not d.kind]


def _kinds(rule: Rule) -> List[str]:
    """Kuralın adlandırdığı belge türleri (alan adlarının önekleri, sırayla, tekrarsız)."""
    names = [rule.check.get(k) for k in _FIELD_KEYS if rule.check.get(k)]
    cond = rule.check.get("applies_if")
    if cond:
        names.append(cond["field"])
    names += [n for ex in rule.exceptions for n, _ in ex.conditions]
    return list(dict.fromkeys(n.split(".", 1)[0] for n in names))


def _merge(docs: Sequence[FileDocument]) -> Dict[str, FieldValue]:
    out: Dict[str, FieldValue] = {}
    for doc in docs:
        for name, fv in doc.fields.items():
            out[f"{doc.kind}.{name}"] = FieldValue(fv.state, fv.value, f"{doc.name}, {fv.location}".rstrip(", "),
                                                   fv.source)
    return out


def file_fields(credit_file: CreditFile) -> Dict[str, FieldValue]:
    """Dosyadaki bütün belgelerin alanları "<tür>.<alan>" adıyla (aynı türden birden çok belgede sonuncusu)."""
    return _merge([d for d in credit_file.documents if d.kind])


def file_event_date(credit_file: CreditFile, today: Optional[dt.date] = None) -> Tuple[dt.date, str]:
    groups = credit_file.by_kind()
    single = [docs[0] for docs in groups.values() if len(docs) == 1]
    return document_event_date(FILE_TYPE, _merge(single), today)


def evaluate_file(rules: Iterable[Rule], credit_file: CreditFile, event_date: Optional[dt.date] = None,
                  answers: Optional[Dict[str, FieldValue]] = None,
                  authority_table: Optional[Sequence[Tuple[str, str]]] = None) -> List[FileResult]:
    """Dosya kurallarının sonuçları. `answers`: müfettiş beyanları ("<tür>.<alan>" → FieldValue).
    `authority_table`: kullanıcının yetki tablosu (merci, "<tutar> <birim>"; M10.4) — belge değil, dosyaya sanal alan."""
    groups = credit_file.by_kind()
    approvals = groups.get("kredi_onay", [])
    virtual = authority_fields(approvals[0].fields, authority_table) if len(approvals) == 1 else {}
    if event_date is None:
        event_date, _ = file_event_date(credit_file)
    out: List[FileResult] = []
    for rule in rules:
        if rule.document_type != FILE_TYPE:
            continue
        kinds = _kinds(rule)
        multi = [k for k in kinds if len(groups.get(k, [])) > 1]
        fixed = [groups[k][0] for k in kinds if len(groups.get(k, [])) == 1]
        # Çoklu belge ancak dosyada tek olan belgelerle eşleşir; iki teminat belgesi (DASK ↔ ipotek) aynı taşınmaza
        # mı ait, belgeden bilinemez
        if multi and any(k not in FILE_UNIQUE for k in kinds if k not in multi):
            multi = multi + [k for k in kinds if k not in multi and k not in FILE_UNIQUE]
        if len(multi) > 1:
            res = evaluate_rule(rule, {}, event_date=event_date)
            out.append(FileResult(tuple(d.name for k in multi for d in groups[k]), RuleResult(
                rule.id, UNKNOWN, f"{rule.finding} (birden çok {' ve '.join(multi)} belgesi: eşleştirme elle yapılmalı)",
                res.basis, (), "çoklu belge türleri eşleştirilemedi")))
            continue
        variants = [[d] for d in groups[multi[0]]] if multi else [[]]
        missing = [k for k in kinds if k not in groups and k != AUTHORITY_KIND]
        notes = [f"dosyada yok: {', '.join(missing)}"] if missing else []
        if AUTHORITY_KIND in kinds and not virtual:
            notes.append("onay mercii yetki tablosunda yok" if authority_table else "yetki tablosu girilmedi")
        for extra in variants:
            docs = fixed + extra
            fields = {**_merge(docs), **virtual, **(answers or {})}
            res = evaluate_rule(rule, fields, event_date=event_date)
            if notes and res.status == UNKNOWN:
                res = RuleResult(res.rule_id, res.status, f"{res.finding} ({'; '.join(notes)})",
                                 res.basis, res.evidence, res.reason)
            out.append(FileResult(tuple(d.name for d in docs), res, tuple(missing)))
    return out


__all__ = ["CreditFile", "FileDocument", "FileResult", "load_document", "evaluate_file", "file_event_date", "file_fields",
           "extract_fields"]
