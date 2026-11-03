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

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.rules.catalog import FIELD_KEYS, INTERNAL_BASES, LAW_SHORT_NAMES, Rule

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
    if b.law in INTERNAL_BASES:  # kanun değil: bankanın kendi kredi onay şartı (M10.2) ya da yetki tablosu (M10.4)
        return f"{INTERNAL_BASES[b.law]}: {b.article}"
    law = LAW_SHORT_NAMES.get(b.law, b.law)
    return f"{law} md. {b.article}" + (f"/{b.paragraph}" if b.paragraph else "")


def _get(fields: Dict[str, FieldValue], name: str) -> FieldValue:
    return fields.get(name) or FieldValue(NOT_FOUND)


def _ev(name: str, fv: FieldValue) -> tuple:
    return (name, fv.state, fv.value, fv.location, fv.source)


def _add(evidence: tuple, name: str, fv: FieldValue) -> tuple:
    """Kanıta alan ekler; alan kontrolde ya da başka koşulda zaten varsa tekrarlanmaz."""
    return evidence if any(e[0] == name for e in evidence) else evidence + (_ev(name, fv),)


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


_ASCII = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def _people(value) -> List[frozenset]:
    """Ad listesi ("Ali Veli, Ayşe Kaya ve Can Er") → her kişi için ad sözcükleri kümesi. Türkçe harfler sadeleşir
    (OCR noktaları düşürebilir); tek sözcüklü parça (ad soyad değil) kişi sayılmaz."""
    out = []
    text = re.sub(r"\([^)]*\)", " ", str(value or ""))  # "Ali Veli (İmza)"
    for part in re.split(r"[,;/&\n]|\bve\b|\bile\b", turkish_lower(text).translate(_ASCII)):
        words = frozenset(re.findall(r"[a-z]{2,}", part))
        if len(words) >= 2:
            out.append(words)
    return out


def _name_key(value) -> str:
    """Karşılaştırma için ad: küçük harf, Türkçe harfler sadeleşmiş, noktalama atılmış sözcükler."""
    text = turkish_lower(str(value or "")).replace("̇", "").translate(_ASCII)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _contains_words(part: str, whole: str) -> bool:
    """`part` sözcük dizisi `whole` içinde sözcük sınırlarında geçiyor mu ("can er" ⊄ "can eren")."""
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(part) + r"(?![a-z0-9])", whole))


def _same_person(a: frozenset, b: frozenset) -> bool:
    return a <= b or b <= a  # ikinci ad yazılmamış olabilir ("Ali Veli" ↔ "Ali Rıza Veli")


# Ticaret şirketi / tüzel kişi ekleri (Türkçe ve ASCII yazım); "Kooperatif Başkanı" gibi kişi unvanı ek sayılmaz
_ENTITY = re.compile(r"(?:\ba\.\s?[şs]\.?|\ba\s?ş\b|\banonim\b|\bltd\b\.?|\blimited\b|\b[şs]ti\b\.?|"
                     r"\b[şs]irketi\b|\bkooperatifi\b|\bvakf[ıi]\b|\bderne[ğg]i\b|\bholding\b|\binc\b\.?|"
                     r"\bllc\b|\bgmbh\b)")


def _entity_text(name) -> str:
    return turkish_lower(str(name or "")).replace("\u0307", "")


def _legal_entity(name: str) -> bool:
    """Unvan bir ticaret şirketi / tüzel kişi biçiminde mi (A.Ş., Ltd. Şti., ANONİM ŞİRKETİ, kooperatifi …)."""
    return bool(_ENTITY.search(_entity_text(name)))


def _without_entity(name: str) -> str:
    """Unvandan şirket ekleri atılmış ad ("Arslan Gıda A.Ş." → "arslan gıda")."""
    return re.sub(r"\s+", " ", _ENTITY.sub(" ", _entity_text(name))).strip(" .,-")


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
        # Ticaret şirketi / tüzel kişi ile gerçek kişi, unvan kişinin adını içerse de ayrı kişidir (M9.7c);
        # şahıs işletmesi ("Arslan Kuaförlüğü - Selim Arslan") tüzel kişi değildir, sahibiyle aynı kişidir.
        # Biri eksiz yazılmış aynı unvansa ("Arslan Gıda" / "Arslan Gıda A.Ş.") aynı kişidir.
        if _legal_entity(a.value) != _legal_entity(b.value):
            return _without_entity(a.value) != _without_entity(b.value)
        # Ad, ötekinin içinde bütün sözcükler olarak geçiyorsa aynı kişidir; harf düzeyinde değil: "Can Er" ile
        # "Can Eren", "Ayşe Kaya" ile "Ayşe Kayalı" ayrı kişilerdir. Büyük / küçük harf ve Türkçe harfler sadeleşir
        # ("SELİM ARSLAN" = "Selim Arslan").
        x, y = _name_key(a.value), _name_key(b.value)
        if x and y and (_contains_words(x, y) or _contains_words(y, x)):
            return False
        # İkinci ad ya da kısaltma farkı ("Ayşe Kaya" / "Ayşe Nur Kaya", "M. Ali Veli" / "Mehmet Ali Veli"): aynı kişi
        # olabilir; "farklı" denirse bulgu kaçar. Karar verilmez, kimlik bilgisiyle kontrol edilir.
        if _possibly_same_person(a.value, b.value):
            return None
        return True
    if kind == "date_order":
        a, b = _get(fields, check["before"]), _get(fields, check["after"])
        if a.state != FILLED or b.state != FILLED:
            return None
        if not isinstance(a.value, dt.date) or not isinstance(b.value, dt.date):
            return None
        if a.value != b.value:
            return a.value < b.value
        # Aynı gün: saat belgelerde yok. "Önce" şartında sıra bilinmez (belirsiz, M9.7c); "en geç aynı gün"
        # şartında (equal_ok_if) aynı gün karşılar
        ok_if = check.get("equal_ok_if")
        if ok_if and _get(fields, ok_if["field"]).state == FILLED and \
                _norm(_get(fields, ok_if["field"]).value) in {_norm(v) for v in ok_if["values"]}:
            return True
        if check.get("equal_unknown"):
            return None
        return bool(check.get("allow_equal", True))
    if kind == "amount_not_above":
        # "<tutar> <para birimi>": field ≤ other; para birimi farklıysa (dövize endeksli kredi olabilir) karar yok
        a, b = _get(fields, check["field"]), _get(fields, check["other"])
        if a.state != FILLED or b.state != FILLED:
            return None
        try:
            (x, cx), (y, cy) = (str(v.value).split() for v in (a, b))
            if cx != cy:
                return None
            return float(x) <= float(y)
        except ValueError:
            return None
    if kind in ("names_within", "joint_signature"):
        a, b = _get(fields, check["field"]), _get(fields, check["other"])
        if a.state != FILLED or b.state != FILLED:
            return None
        signers, allowed = _people(a.value), _people(b.value)
        if not signers or not allowed:
            return None  # ad soyad okunamadı
        if kind == "names_within":
            return all(any(_same_person(s, p) for p in allowed) for s in signers)
        # müşterek yetki: en az iki ayrı yetkilinin imzası; imzalayan yetkili değilse sayılmaz
        signed = {i for s in signers for i, p in enumerate(allowed) if _same_person(s, p)}
        if len(signed) >= 2:
            return True
        joint = _get(fields, check["joint"])
        if joint.state != FILLED:
            return None
        return {"münferit": True, "müşterek": False}.get(_norm(joint.value))
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


def _possibly_same_person(a, b) -> bool:
    """İki gerçek kişi adı ikinci ad / kısaltma farkıyla aynı kişiyi gösterebilir mi (imza yetkisi kontrolündeki
    eşleştirmeyle aynı: `_people` + `_same_person`)."""
    if _legal_entity(a) or _legal_entity(b):
        return False
    return any(_same_person(p, q) for p in _people(a) for q in _people(b))


def _similar_names_unknown(rule: Rule, fields: Dict[str, FieldValue]) -> bool:
    check = rule.check
    if check.get("type") != "fields_differ":
        return False
    a, b = _get(fields, check["field"]), _get(fields, check["other"])
    return a.state == FILLED and b.state == FILLED and _possibly_same_person(a.value, b.value)


def _same_day_unknown(rule: Rule, fields: Dict[str, FieldValue]) -> bool:
    check = rule.check
    if check.get("type") != "date_order" or not check.get("equal_unknown"):
        return False
    a, b = _get(fields, check["before"]), _get(fields, check["after"])
    return a.state == FILLED and b.state == FILLED and isinstance(a.value, dt.date) and a.value == b.value


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
    names = [rule.check.get(k) for k in FIELD_KEYS if rule.check.get(k)]
    return [n for n in names if _get(fields, n).state != FILLED]


def evaluate_rule(rule: Rule, fields: Dict[str, FieldValue], text: str = "",
                  event_date: Optional[dt.date] = None) -> RuleResult:
    basis = _ref(rule.basis)
    used = [k for k in FIELD_KEYS if rule.check.get(k)]
    evidence = tuple(_ev(rule.check[k], _get(fields, rule.check[k])) for k in used)

    def result(status, reason, finding=rule.finding, ev=evidence):
        return RuleResult(rule.id, status, finding if status != MET else "", basis, ev, reason)

    if not rule.applies_on(event_date):
        return result(NOT_APPLICABLE, "kural olay tarihindeki metne göre yazılmamış", finding="")
    cond = rule.check.get("applies_if")
    if cond:
        fv = _get(fields, cond["field"])
        evidence = _add(evidence, cond["field"], fv)
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
    if outcome is None and _same_day_unknown(rule, fields):
        before, after = rule.check["before"], rule.check["after"]
        return result(UNKNOWN, "aynı gün: saat bilgisi yok, sıra belirlenemedi",
                      finding=f"{before} ile {after} aynı gün; saat bilgisi olmadığından şartın sağlanıp sağlanmadığı "
                              f"belirlenemedi ({rule.title})", ev=evidence)
    if outcome is None and _similar_names_unknown(rule, fields):
        first, other = rule.check["field"], rule.check["other"]
        return result(UNKNOWN, "adlar benzer: ikinci ad ya da kısaltma farkı, aynı kişi olabilir",
                      finding=f"{first} ile {other} aynı kişi olabilir (adlar yalnızca ikinci ad ya da kısaltmayla "
                              f"ayrılıyor); kimlik numarasıyla kontrol edilmeli ({rule.title})", ev=evidence)
    if outcome is True:
        return result(MET, "olumlu kanıt", ev=evidence)
    # Karşılanmadı demeden önce istisna: koşulu sağlanıyorsa istisna, bilinmiyorsa belirsiz.
    unknown_exception = None
    for ex in rule.exceptions:
        # Bütün koşullar sağlanırsa istisna; biri sağlanmıyorsa istisna yok; yoksa bilinmiyor.
        states = []
        for name, values in ex.conditions:
            fv = _get(fields, name)
            evidence = _add(evidence, name, fv)
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
