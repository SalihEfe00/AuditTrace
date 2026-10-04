# -*- coding: utf-8 -*-
"""Kural kataloğu biçimi ve yükleyicisi (M9.1).

Katalog bir JSON veri dosyasıdır (`mufettis/rules/catalog/*.json`); kod kural içermez. Her kural:

- `id`, `document_type` (DOCUMENT_FIELDS anahtarı), `title`
- `basis`: dayanak; `law` (kanun numarası ya da mevzuat kodu), `article`, `paragraph` (isteğe bağlı) ve `quote`
  (madde metninden birebir alıntı; boşluk / satır sonu farkı yok sayılır)
- `check`: kontrol türü (CHECK_TYPES) ve alanları; `human` dışındaki türler belge alanı adlandırır
- `result_class`: otomatik / kısmen / belirsiz / manuel
- `norm_type` (yükümlülük / yasak / izin / hukuki_sonuç: kanunun kendiliğinden bağladığı sonuç, ör. kefaletin
  adi kefalet sayılması), `obligor` (yükümlü taraf)
- `absence_is_violation`: öğe **boş** (etiket var, değer yok) ise ihlal mi (M9.2), yoksa yalnızca belirsizlik mi
- `exceptions`: istisna kuralları; her biri koşul alanı + dayanak, isteğe bağlı `also` (birlikte sağlanması
  gereken ek koşullar). İstisna koşulu sağlanıyorsa ya da bilinmiyorsa kural "karşılanmadı" demez (M9.0)
- `check.applies_if`: kural yalnızca koşul alanı `values` değerlerindeyse, `above` eşiğinin üstündeyse ya da
  `present: true` ile doluysa uygulanır (boşsa uygulanmaz, bulunamadıysa belirsiz)
- `validity`: kuralın yazıldığı madde metninin geçerlilik aralığı (`from`, `to` ya da null); olay tarihi bu
  aralıkta değilse kural uygulanmaz, eski metne göre yazılmış kural aranır (M7)
- `finding`: eksik öğeyi adlandıran bulgu metni ("eş rızası: ad soyad, imza, tarih boş")
- `rationale`: kuralın neden böyle yazıldığı

Yükleyici bozuk kuralı reddeder: bilinmeyen alan / tür / sınıf, dayanaksız kural, alıntısı madde metninde
geçmeyen kural (madde metni verildiyse). Reddedilen kural sessizce atlanmaz; `CatalogError` yükseltilir.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from mufettis.analysis.turkish_text import turkish_lower

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"

CHECK_TYPES = {
    "field_present",   # alan dolu mu
    "field_value",     # alan değeri beklenen değerlerden mi
    "date_order",      # bir tarih alanı diğerinden önce / aynı anda mı
    "fields_differ",   # iki alan farklı kişiyi mi gösteriyor (kefil ≠ asıl borçlu)
    "threshold",       # sayısal alan eşiğin altında / üstünde mi
    "text_pattern",    # belgede kalıp geçiyor mu
    "human",           # yalnızca müfettişin yapabileceği kontrol; kontrol maddesi üretir
}
RESULT_CLASSES = {"otomatik", "kısmen", "belirsiz", "manuel"}
NORM_TYPES = {"yükümlülük", "yasak", "izin", "hukuki_sonuç", "iç_kontrol"}
# Kanun değil, bankanın kendi kredi onayındaki şart (M10.2, kullanıcı kararı 2026-10-04): alıntı korpusta aranmaz,
# kural yalnızca onay belgesinde şart okunursa uygulanır ve norm türü iç_kontrol olmalıdır
INTERNAL_BASIS = "kredi_onayi"
OBLIGORS = {"banka", "kefil", "eş", "borçlu", "alacaklı", "rehin_veren", "taraflar"}

# Belge türü → kuralların adlandırabileceği alanlar (M9.3 alanları; tür eklendikçe genişler).
DOCUMENT_FIELDS: Dict[str, set] = {
    "kefalet_sozlesmesi": {
        "kefil", "borclu", "alacakli", "azami_tutar", "sozlesme_tarihi", "kefalet_turu",
        "es_rizasi", "es_rizasi_tarihi", "el_yazisi_azami_tutar", "el_yazisi_tarih", "el_yazisi_kefalet_turu",
        "imza_turu", "kefil_medeni_hal", "kefil_ticari_sifat", "kredi_turu",
        "kefalet_tarihi", "ayrilik_durumu", "kefalet_isletmeyle_ilgili", "kredi_sozlesme_tarihi",
    },
    # Çıkarıcılar: mufettis/rules/fields_teminat.py (test iki listenin tutarlılığını korur)
    "ipotek": {"malik", "tasinmaz", "ipotek_tutari", "derece", "tescil_tarihi", "yevmiye_no", "alacakli"},
    "rehin": {"rehin_veren", "rehin_konusu", "rehin_tutari", "sicil_tescil_tarihi", "sicil_no", "imza_onayi",
              "sozlesme_tarihi"},
    "ekspertiz": {"rapor_tarihi", "inceleme_tarihi", "deger", "degerleme_kurulusu", "eksper", "lisans_no"},
    "dask": {"police_no", "baslangic_tarihi", "bitis_tarihi", "sigortali", "dain_murtehin", "islem_tarihi"},
    "senet_gks": {"borclu", "tutar", "duzenleme_tarihi", "vade_tarihi", "imza"},
    "bono": {"duzenleyen", "lehtar", "tutar", "vade_tarihi", "odeme_yeri", "duzenleme_tarihi", "duzenleme_yeri", "imza"},
    # Kredi dosyasının teminat dışı belgeleri (M10.1)
    "kredi_onay": {"borclu", "onaylanan_tutar", "onay_tarihi", "onay_mercii", "teminat_sarti"},
    "kullandirim": {"borclu", "kullandirilan_tutar", "kullandirim_tarihi", "imzalayan"},
    "imza_sirkuleri": {"unvan", "yetkili", "yetki_sekli", "yetki_suresi", "duzenleme_tarihi"},
}
# Kredi dosyası (M10.1): belgeler arası kurallar alanı "<belge türü>.<alan>" diye adlandırır
FILE_TYPE = "kredi_dosyasi"
DOCUMENT_FIELDS[FILE_TYPE] = {f"{kind}.{name}" for kind, names in DOCUMENT_FIELDS.items() for name in names}


class CatalogError(ValueError):
    """Katalogda bozuk kural; mesaj kural kimliğini ve sorunları sayar."""


@dataclass(frozen=True)
class Basis:
    law: str
    article: str
    quote: str
    paragraph: str = ""


@dataclass(frozen=True)
class Exception_:
    id: str
    condition_field: str
    condition_values: tuple
    basis: Basis
    note: str = ""
    also: tuple = ()  # ((alan, değerler), ...): istisna yalnızca bunlar da sağlanırsa uygulanır
    # "istisna": kural geçerli ama kanun bu durumu muaf tutar; "uygulanmaz": kanun durumu başka bir hükme bağlar ve
    # kural konusuz kalır (ör. tüketici kredisinde kefalet zaten adi sayılır, müteselsillik el yazısı sorulmaz)
    result: str = "istisna"

    @property
    def conditions(self) -> tuple:
        return ((self.condition_field, self.condition_values),) + self.also


@dataclass(frozen=True)
class Rule:
    id: str
    document_type: str
    title: str
    basis: Basis
    check: Dict
    result_class: str
    norm_type: str
    obligor: str
    absence_is_violation: bool
    finding: str
    rationale: str
    exceptions: tuple = ()
    valid_from: Optional[dt.date] = None
    valid_to: Optional[dt.date] = None
    raw: Dict = field(default_factory=dict, compare=False, repr=False)

    def applies_on(self, event_date: Optional[dt.date]) -> bool:
        """Olay tarihi kuralın yazıldığı metnin geçerlilik aralığında mı (tarih yoksa güncel metin)."""
        if event_date is None:
            return self.valid_to is None
        return (self.valid_from is None or self.valid_from <= event_date) and \
               (self.valid_to is None or event_date < self.valid_to)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", turkish_lower(text or "")).strip()


def quote_in_text(quote: str, article_text: str) -> bool:
    """Alıntı madde metninde birebir geçiyor mu (boşluk, satır sonu ve büyük / küçük harf farkı yok sayılır)."""
    q = _norm(quote)
    return bool(q) and q in _norm(article_text)


def _date(value) -> Optional[dt.date]:
    return dt.date.fromisoformat(value) if value else None


def _basis(raw: Dict, where: str, problems: List[str]) -> Optional[Basis]:
    if not isinstance(raw, dict):
        problems.append(f"{where}: dayanak yok")
        return None
    missing = [k for k in ("law", "article", "quote") if not str(raw.get(k) or "").strip()]
    if missing:
        problems.append(f"{where}: dayanakta eksik: {', '.join(missing)}")
        return None
    return Basis(law=str(raw["law"]), article=str(raw["article"]), quote=raw["quote"],
                 paragraph=str(raw.get("paragraph") or ""))


def _check_fields(check: Dict) -> List[str]:
    out = [check.get("field")] if check.get("field") else []
    out += [f for f in (check.get("other"), check.get("before"), check.get("after")) if f]
    if isinstance(check.get("applies_if"), dict):  # kural yalnızca bu alan şu değerlerdeyse / eşiğin üstündeyse uygulanır
        out.append(check["applies_if"].get("field"))
    return out


KNOWN_KEYS = {"id", "document_type", "title", "basis", "check", "result_class", "norm_type", "obligor",
              "absence_is_violation", "exceptions", "validity", "finding", "rationale"}


def parse_rule(raw: Dict, article_text: Optional[Callable[[str, str], Optional[str]]] = None) -> Rule:
    """Tek kuralı doğrular ve döndürür; sorun varsa CatalogError. article_text(law, article) verildiyse
    alıntılar madde metninde aranır (metin bulunamazsa da ret: dayanağı korpusta olmayan kural yazılmaz)."""
    rid = str(raw.get("id") or "?")
    problems: List[str] = []
    unknown = set(raw) - KNOWN_KEYS
    if unknown:
        problems.append(f"bilinmeyen anahtar: {', '.join(sorted(unknown))}")
    doc_type = raw.get("document_type")
    fields = DOCUMENT_FIELDS.get(doc_type)
    if fields is None:
        problems.append(f"bilinmeyen belge türü: {doc_type}")
        fields = set()
    basis = _basis(raw.get("basis"), "kural", problems)
    check = raw.get("check") or {}
    if check.get("type") not in CHECK_TYPES:
        problems.append(f"bilinmeyen kontrol türü: {check.get('type')}")
    elif check["type"] == "human":
        if not str(check.get("instruction") or "").strip():
            problems.append("insan kontrolünde talimat yok")
    elif check["type"] != "text_pattern" and not _check_fields(check):
        problems.append("kontrol bir belge alanı adlandırmıyor")
    cond = check.get("applies_if")
    if cond is not None and (not isinstance(cond, dict)
                             or sum(k in cond for k in ("values", "above", "present")) != 1
                             or cond.get("present", True) is not True):
        problems.append("applies_if: alan ve 'values' (değer listesi), 'above' (sayısal eşik) ya da "
                        "'present: true' (dolu) koşullarından biri olmalı")
    for f in _check_fields(check):
        if f not in fields:
            problems.append(f"bilinmeyen alan: {f}")
    for key, allowed in (("result_class", RESULT_CLASSES), ("norm_type", NORM_TYPES), ("obligor", OBLIGORS)):
        if raw.get(key) not in allowed:
            problems.append(f"geçersiz {key}: {raw.get(key)}")
    if not isinstance(raw.get("absence_is_violation"), bool):
        problems.append("absence_is_violation true / false olmalı")
    for key in ("title", "finding", "rationale"):
        if not str(raw.get(key) or "").strip():
            problems.append(f"{key} boş")
    exceptions = []
    for i, ex in enumerate(raw.get("exceptions") or []):
        where = f"istisna {ex.get('id') or i}"
        ex_basis = _basis(ex.get("basis"), where, problems)
        cond = ex.get("condition") or {}
        also = ex.get("also") or []
        if ex.get("result", "istisna") not in ("istisna", "uygulanmaz"):
            problems.append(f"{where}: sonuç 'istisna' ya da 'uygulanmaz' olmalı")
        for c in [cond] + list(also):
            if c.get("field") not in fields:
                problems.append(f"{where}: bilinmeyen koşul alanı: {c.get('field')}")
            if not c.get("values"):
                problems.append(f"{where}: koşul değeri yok")
        if ex_basis:
            exceptions.append(Exception_(id=str(ex.get("id") or i), condition_field=str(cond.get("field")),
                                         condition_values=tuple(cond.get("values") or ()), basis=ex_basis,
                                         note=str(ex.get("note") or ""), result=str(ex.get("result") or "istisna"),
                                         also=tuple((str(c.get("field")), tuple(c.get("values") or ()))
                                                    for c in also)))
    validity = raw.get("validity") or {}
    try:
        valid_from, valid_to = _date(validity.get("from")), _date(validity.get("to"))
    except ValueError:
        problems.append(f"geçersiz geçerlilik tarihi: {validity}")
        valid_from = valid_to = None
    internal = basis is not None and basis.law == INTERNAL_BASIS
    if internal != (raw.get("norm_type") == "iç_kontrol"):
        problems.append("iç kontrol kuralı (dayanak kredi_onayi) ile norm türü iç_kontrol birlikte kullanılır")
    applies = check.get("applies_if")
    if internal and not (isinstance(applies, dict) and str(applies.get("field") or "").startswith("kredi_onay.")):
        problems.append("iç kontrol kuralı yalnızca kredi onayındaki şart okunursa uygulanmalı (applies_if kredi_onay.*)")
    if article_text is not None:
        for where, b in [("kural", basis)] + [(f"istisna {e.id}", e.basis) for e in exceptions]:
            if b is None or b.law == INTERNAL_BASIS:
                continue
            text = article_text(b.law, b.article)
            if not text:
                problems.append(f"{where}: {b.law} md. {b.article} korpusta bulunamadı")
            elif not quote_in_text(b.quote, text):
                problems.append(f"{where}: alıntı {b.law} md. {b.article} metninde geçmiyor")
    if problems:
        raise CatalogError(f"{rid}: " + "; ".join(problems))
    return Rule(id=rid, document_type=doc_type, title=raw["title"], basis=basis, check=dict(check),
                result_class=raw["result_class"], norm_type=raw["norm_type"], obligor=raw["obligor"],
                absence_is_violation=raw["absence_is_violation"], finding=raw["finding"],
                rationale=raw["rationale"], exceptions=tuple(exceptions), valid_from=valid_from,
                valid_to=valid_to, raw=raw)


def load_catalog(paths: Optional[Iterable[Path]] = None,
                 article_text: Optional[Callable[[str, str], Optional[str]]] = None) -> List[Rule]:
    """Katalog dosyalarındaki bütün kuralları yükler; bozuk ya da tekrarlanan kimlikli kural varsa
    CatalogError (bütün sorunlar tek mesajda)."""
    paths = sorted(paths) if paths is not None else sorted(CATALOG_DIR.glob("*.json"))
    rules, errors, seen = [], [], set()
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for raw in data.get("rules", []):
            try:
                rule = parse_rule(raw, article_text)
            except CatalogError as exc:
                errors.append(f"{Path(path).name}: {exc}")
                continue
            if rule.id in seen:
                errors.append(f"{Path(path).name}: {rule.id}: kimlik tekrarı")
                continue
            seen.add(rule.id)
            rules.append(rule)
    if errors:
        raise CatalogError("\n".join(errors))
    return rules


__all__ = ["Rule", "Basis", "CatalogError", "load_catalog", "parse_rule", "quote_in_text", "CATALOG_DIR",
           "CHECK_TYPES", "RESULT_CLASSES", "NORM_TYPES", "OBLIGORS", "DOCUMENT_FIELDS"]
