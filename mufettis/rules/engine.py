# -*- coding: utf-8 -*-
"""Kural motoru (M9.2): belge alanlarından her kural için sonuç + kanıt. Belirlenimci.

Alan değerleri üç durumludur (M9.3): `dolu` (değer + konum), `boş` (etiket bulundu, değer yok), `bulunamadı`
(etiket bile yok; OCR kaçırmış olabilir). Müfettişin elle girdiği değer `kaynak="müfettiş beyanı"` taşır.

Sonuç sınıfları ve kesin kurallar (M9.0):
- "karşılandı" yalnızca olumlu kanıtla; eksik alan asla "karşılandı" olmaz.
- Alan **boş** ve kural `absence_is_violation` ise "karşılanmadı"; alan **bulunamadı** ise "belirsiz".
- İstisna koşulu sağlanıyorsa "istisna", bilinmiyorsa (koşul alanı dolu değil) "karşılanmadı" yerine "belirsiz".
- Kural yalnızca belli durumda uygulanıyorsa (`applies_if`) ve o durum bilinmiyorsa "belirsiz"; durum
  dışındaysa "uygulanmaz".
- `human` kuralları "manuel kontrol" ve ne yapılacağını söyleyen kontrol maddesi üretir.
- Bulgu eksik öğeyi adlandırır (kuraldaki `finding`), yalnızca madde numarası vermez.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from mufettis.rules.catalog import Rule

FILLED, EMPTY, NOT_FOUND = "dolu", "boş", "bulunamadı"
MET, NOT_MET, UNKNOWN, MANUAL, EXEMPT, NOT_APPLICABLE = (
    "karşılandı", "karşılanmadı", "belirsiz", "manuel kontrol", "istisna", "uygulanmaz")
INSPECTOR = "müfettiş beyanı"


@dataclass(frozen=True)
class FieldValue:
    state: str                 # dolu / boş / bulunamadı
    value: object = None
    location: str = ""         # belgede konum (sayfa / satır) ya da alıntı
    source: str = "belge"      # belge / müfettiş beyanı

    @staticmethod
    def filled(value, location="", source="belge"):
        return FieldValue(FILLED, value, location, source)


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    status: str
    finding: str               # sonuç karşılanmadı / belirsiz / manuel ise gösterilecek metin
    basis: str                 # "6098 md. 584/1"
    evidence: tuple = ()       # (alan, durum, değer, konum, kaynak)
    reason: str = ""


def _ref(b) -> str:
    return f"{b.law} md. {b.article}" + (f"/{b.paragraph}" if b.paragraph else "")


def _get(fields: Dict[str, FieldValue], name: str) -> FieldValue:
    return fields.get(name) or FieldValue(NOT_FOUND)


def _ev(name: str, fv: FieldValue) -> tuple:
    return (name, fv.state, fv.value, fv.location, fv.source)


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _passes(rule: Rule, fields: Dict[str, FieldValue], text: str) -> Optional[bool]:
    """Kontrolün kendisi: True / False, ya da karar verilemiyorsa None."""
    check = rule.check
    kind = check["type"]
    if kind == "field_present":
        fv = _get(fields, check["field"])
        return True if fv.state == FILLED else None
    if kind == "field_value":
        fv = _get(fields, check["field"])
        if fv.state != FILLED:
            return None
        if _norm(fv.value) in {_norm(v) for v in check.get("undecided", [])}:
            return None  # bu değer bu kural için karar vermeye yetmez (ör. türü yazmayan e-imza, 5070 için)
        return _norm(fv.value) in {_norm(v) for v in check.get("expected", [])}
    if kind == "fields_differ":
        # İki alan aynı kişiyi mi gösteriyor (ör. kefil ile asıl borçlu): biri ötekinin içinde geçiyorsa aynı sayılır
        # ("Arslan Kuaförlüğü - Selim Arslan" ile "Selim Arslan")
        a, b = _get(fields, check["field"]), _get(fields, check["other"])
        if a.state != FILLED or b.state != FILLED:
            return None
        x, y = _norm(a.value), _norm(b.value)
        return not (x and y and (x in y or y in x))
    if kind == "date_order":
        a, b = _get(fields, check["before"]), _get(fields, check["after"])
        if a.state != FILLED or b.state != FILLED:
            return None
        if not isinstance(a.value, dt.date) or not isinstance(b.value, dt.date):
            return None
        return a.value < b.value or (check.get("allow_equal", True) and a.value == b.value)
    if kind == "threshold":
        fv = _get(fields, check["field"])
        if fv.state != FILLED:
            return None
        try:
            value = float(fv.value)
        except (TypeError, ValueError):
            return None
        if "max" in check and value > float(check["max"]):
            return False
        if "min" in check and value < float(check["min"]):
            return False
        return True
    if kind == "text_pattern":
        return bool(re.search(check["pattern"], text or "", re.I)) if text else None
    return None


def _condition_holds(cond: Dict, fv: FieldValue) -> Optional[bool]:
    """applies_if koşulu: değer listesinde mi ('values') ya da sayısal eşiğin üstünde mi ('above');
    sayı okunamazsa None (bilinmiyor)."""
    if "above" in cond:
        try:
            return float(fv.value) > float(cond["above"])
        except (TypeError, ValueError):
            return None
    return _norm(fv.value) in {_norm(v) for v in cond["values"]}


def _missing_fields(rule: Rule, fields: Dict[str, FieldValue]) -> List[str]:
    names = [rule.check.get(k) for k in ("field", "other", "before", "after") if rule.check.get(k)]
    return [n for n in names if _get(fields, n).state != FILLED]


def evaluate_rule(rule: Rule, fields: Dict[str, FieldValue], text: str = "",
                  event_date: Optional[dt.date] = None) -> RuleResult:
    basis = _ref(rule.basis)
    used = [k for k in ("field", "other", "before", "after") if rule.check.get(k)]
    evidence = tuple(_ev(rule.check[k], _get(fields, rule.check[k])) for k in used)

    def result(status, reason, finding=rule.finding, ev=evidence):
        return RuleResult(rule.id, status, finding if status != MET else "", basis, ev, reason)

    if not rule.applies_on(event_date):
        return result(NOT_APPLICABLE, "kural olay tarihindeki metne göre yazılmamış", finding="")
    cond = rule.check.get("applies_if")
    if cond:
        fv = _get(fields, cond["field"])
        evidence = evidence + (_ev(cond["field"], fv),)
        if cond.get("present"):
            holds = {FILLED: True, EMPTY: False}.get(fv.state)  # boşsa varlık kuralı zaten bulgu verir
        else:
            holds = _condition_holds(cond, fv) if fv.state == FILLED else None
        if holds is None:
            return result(UNKNOWN, f"kuralın uygulanıp uygulanmadığı bilinmiyor: {cond['field']} {fv.state}",
                          finding=f"{rule.finding} (önce {cond['field']} belirlenmeli)", ev=evidence)
        if not holds:
            return result(NOT_APPLICABLE, f"{cond['field']}={fv.value}", finding="", ev=evidence)
    if rule.check["type"] == "human":
        return result(MANUAL, "insan kontrolü", finding=rule.check["instruction"], ev=evidence)
    outcome = _passes(rule, fields, text)
    if outcome is True:
        return result(MET, "olumlu kanıt", ev=evidence)
    # Karşılanmadı demeden önce istisna: koşulu sağlanıyorsa istisna, bilinmiyorsa belirsiz.
    unknown_exception = None
    for ex in rule.exceptions:
        # Bütün koşullar sağlanırsa istisna; biri sağlanmıyorsa istisna yok; yoksa bilinmiyor.
        states = []
        for name, values in ex.conditions:
            fv = _get(fields, name)
            evidence = evidence + (_ev(name, fv),)
            states.append(None if fv.state != FILLED else _norm(fv.value) in {_norm(v) for v in values})
        if all(states):
            status = NOT_APPLICABLE if getattr(ex, "result", "istisna") == "uygulanmaz" else EXEMPT
            return result(status, f"istisna {ex.id} ({_ref(ex.basis)})", finding="", ev=evidence)
        if False not in states:
            unknown_exception = ex
    if outcome is False:
        status, reason = NOT_MET, "kontrol sağlanmadı"
    else:
        missing = _missing_fields(rule, fields)
        empty = [n for n in missing if _get(fields, n).state == EMPTY]
        # Belgedeki boşluk güvenilmezse (absence_is_violation false: ör. imza metne yansımaz) yalnızca müfettişin
        # "yok" beyanı ihlal sayılır.
        stated = all(_get(fields, n).source == INSPECTOR for n in empty)
        if empty and (rule.absence_is_violation or stated) and len(empty) == len(missing):
            status, reason = NOT_MET, "boş alan: " + ", ".join(empty)
        else:
            return result(UNKNOWN, "alan bulunamadı: " + ", ".join(missing) if missing else "karar verilemedi",
                          ev=evidence)
    if unknown_exception is not None:
        return result(UNKNOWN, f"{reason}; ama istisna {unknown_exception.id} koşulu bilinmiyor "
                               f"({', '.join(n for n, _ in unknown_exception.conditions)})",
                      finding=f"{rule.finding}; istisna kontrol edilmeli: {unknown_exception.note or unknown_exception.id}",
                      ev=evidence)
    return result(status, reason, ev=evidence)


def evaluate(rules: Iterable[Rule], document_type: str, fields: Dict[str, FieldValue], text: str = "",
             event_date: Optional[dt.date] = None) -> List[RuleResult]:
    """Belge türüne ait bütün kurallar; katalog sırasıyla."""
    return [evaluate_rule(r, fields, text, event_date) for r in rules if r.document_type == document_type]


__all__ = ["FieldValue", "RuleResult", "evaluate", "evaluate_rule", "FILLED", "EMPTY", "NOT_FOUND", "MET",
           "NOT_MET", "UNKNOWN", "MANUAL", "EXEMPT", "NOT_APPLICABLE", "INSPECTOR"]
