# -*- coding: utf-8 -*-
"""Belge kimliği: belge türü ve Work / Expression / Manifestation kimlikleri.

Kimlik yalnızca belgenin kendi beyanlarından (başlık, resmî numara) veya ingestion
pipeline'ının açıkça verdiği alanlardan (declared_*) türetilir; kaynak sitesi, klasör veya
dosya adı kimliğe güven katmaz.

Pipeline'ın verebileceği girdi alanları (hepsi isteğe bağlı):
  declared_work_id          ELI veya eşdeğer kalıcı resmî kimlik
  declared_instrument_type  InstrumentType değeri
  declared_version          sürüm işareti (ör. değişiklik/yürürlük tarihi); aynı sürümün farklı
                            formattaki kopyaları aynı değeri taşır
  issuer                    düzenlemeyi çıkaran kurum
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional, Tuple

from mufettis.corpus.model import IdentityBasis, InstrumentType

_TR_UPPER = str.maketrans({"İ": "i", "I": "ı"})


def _lower(text: Any) -> str:
    return str(text or "").translate(_TR_UPPER).lower().replace("̇", "")


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"none", "null", "nan"} else text


# Türkçe düzenleme başlıklarında belge türü baş isimdir ve başlığın SONUNDA yer alır
# ("Bankacılık Kanununun 42. Maddesine İlişkin Yönetmelik" bir yönetmeliktir). Bu yüzden en
# sonda biten eşleşme kazanır; başlığın başka yerinde geçen tür adları atıftır.
_TITLE_INSTRUMENT_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (InstrumentType.CONSTITUTION, r"\banayasa(?:sı)?\b"),
    (InstrumentType.PRESIDENTIAL_DECREE, r"cumhurbaşkanlığı\s+kararnamesi\w*|kanun\s+hükmünde\s+kararname\w*|\bkhk\b"),
    (InstrumentType.PRESIDENTIAL_DECISION, r"cumhurbaşkan(?:ı|lığı)\s+kararı\w*|bakanlar\s+kurulu\s+kararı\w*"),
    (InstrumentType.LAW, r"\bkanun(?:u|nu)?\b"),
    (InstrumentType.REGULATION, r"\byönetmeli[kğ]\w*"),
    (InstrumentType.COMMUNIQUE, r"\btebli[ğg]\w*"),
    (InstrumentType.PROCEDURE, r"\busul\s+ve\s+esas\w*"),
    (InstrumentType.BOARD_DECISION, r"\bkurulu?\s+kararı\w*"),  # "... Denetleme Kurulu Kararı"
    (InstrumentType.CIRCULAR, r"\bgenelge\w*"),
    (InstrumentType.GUIDELINE, r"\brehber\w*|\bkılavuz\w*"),
    (InstrumentType.CHART_OF_ACCOUNTS, r"\bhesap\s+planı\w*"),
    (InstrumentType.DECISION, r"\bkarar(?:ı)?\b"),
)

# Parser'ın belge türü etiketi (document_type) -> kanonik tür. Başlıktan tür çıkmazsa kullanılır.
_DOCUMENT_TYPE_MAP = {
    "anayasa": InstrumentType.CONSTITUTION,
    "kanun": InstrumentType.LAW,
    "cumhurbaşkanlığı kararnamesi": InstrumentType.PRESIDENTIAL_DECREE,
    "cumhurbaşkanı kararı": InstrumentType.PRESIDENTIAL_DECISION,
    "yönetmelik": InstrumentType.REGULATION,
    "tebliğ": InstrumentType.COMMUNIQUE,
    "usul ve esas": InstrumentType.PROCEDURE,
    "kurul kararı": InstrumentType.BOARD_DECISION,
    "genelge": InstrumentType.CIRCULAR,
    "rehber": InstrumentType.GUIDELINE,
    "kılavuz": InstrumentType.GUIDELINE,
    "hesap planı": InstrumentType.CHART_OF_ACCOUNTS,
    "karar": InstrumentType.DECISION,
}


def instrument_from_title(title: Any) -> Optional[str]:
    text = _lower(title)
    best: Optional[Tuple[int, str]] = None
    for instrument, pattern in _TITLE_INSTRUMENT_PATTERNS:
        for m in re.finditer(pattern, text):
            if best is None or m.end() > best[0]:
                best = (m.end(), instrument)
    return best[1] if best else None


# Tür dayanakları. Güçlü olanlar belgenin kendi başlığı/resmî numarası veya pipeline beyanıdır;
# parser'ın gövde metninden yaptığı tür tahmini ve parça yapısı zayıf dayanaktır.
STRONG_INSTRUMENT_BASES = frozenset({"declared", "declared_title", "title", "official_number"})


def instrument_identity(meta: Dict[str, Any]) -> Tuple[str, str]:
    """(instrument_type, instrument_basis)."""
    declared = _clean(meta.get("declared_instrument_type")).lower()
    if declared in InstrumentType.ALL:
        return declared, "declared"
    # Belgenin kendi başlığı dosya adından güvenilirdir (dosya adı "mevzuat_1310" veya yanıltıcı olabilir).
    from_declared = instrument_from_title(meta.get("declared_title"))
    if from_declared:
        return from_declared, "declared_title"
    from_title = instrument_from_title(meta.get("title"))
    if from_title:
        return from_title, "title"
    # Belge "Kanun Numarası: N" beyan ediyorsa (parser law_number'ı yalnızca belgenin kendi
    # başlık bölgesinden alır) tür kanundur.
    if law_number(meta):
        return InstrumentType.LAW, "official_number"
    mapped = _DOCUMENT_TYPE_MAP.get(_lower(meta.get("document_type")).strip())
    if mapped:
        return mapped, "document_type"
    # Parça yapısı (ör. hesap planı satırları) belge türü değildir; yalnızca normatif rolü
    # belirler (authority.normative_role). Tür belge düzeyinde kalmalı ki index kaydı ile
    # parçaları aynı kimliği taşısın.
    return InstrumentType.OTHER, "none"


def law_number(meta: Dict[str, Any]) -> str:
    value = _clean(meta.get("law_number"))
    return value if value.isdigit() else ""


_ISSUER_BY_INSTRUMENT = {
    InstrumentType.CONSTITUTION: "TBMM",
    InstrumentType.LAW: "TBMM",
    InstrumentType.PRESIDENTIAL_DECREE: "Cumhurbaşkanlığı",
    InstrumentType.PRESIDENTIAL_DECISION: "Cumhurbaşkanlığı",
}


def issuer(meta: Dict[str, Any], instrument: str, instrument_basis: str, profile: Dict[str, Any]) -> str:
    """Düzenlemeyi çıkaran kurum: beyan > türün anayasal çıkarıcısı (tür güçlü dayanaklıysa) >
    kaynak kaydının 'issuer' alanı (yalnızca kendi düzenlemelerini yayımlayan kurumlar için)."""
    declared = _clean(meta.get("issuer"))
    if declared:
        return declared
    if instrument in _ISSUER_BY_INSTRUMENT:
        return _ISSUER_BY_INSTRUMENT[instrument] if instrument_basis in STRONG_INSTRUMENT_BASES else ""
    return _clean(profile.get("issuer"))


def content_fingerprint(text: Any) -> str:
    """Formattan ve boşluk/satır düzeninden bağımsız içerik özeti.

    Yalnızca harf ve rakam dizisi kullanılır: PDF ile HTML kopyası veya boşluk onarımı öncesi ve
    sonrası aynı metin aynı özeti verir. Metin boşsa "" döner.
    """
    letters = re.sub(r"[^0-9a-zçğıöşüâîû]+", "", _lower(text))
    return hashlib.sha256(letters.encode("utf-8")).hexdigest() if letters else ""


def _slug(value: str, limit: int = 80) -> str:
    return re.sub(r"[^0-9a-zçğıöşü]+", "-", _lower(value)).strip("-")[:limit]


def work_identity(meta: Dict[str, Any], instrument: str) -> Tuple[str, str]:
    """(work_id, identity_basis). Resmî numara kaynaktan bağımsızdır; kaynak belge kimliği
    yalnızca o kaynak içinde tekildir ve öyle adlandırılır."""
    declared = _clean(meta.get("declared_work_id") or meta.get("eli"))
    if declared:
        return declared, IdentityBasis.DECLARED
    number = law_number(meta)
    if number and instrument == InstrumentType.LAW:
        return f"tr/{InstrumentType.LAW}/{number}", IdentityBasis.OFFICIAL_NUMBER
    source_document_id = _clean(meta.get("source_document_id"))
    source_id = _clean(meta.get("source_id"))
    if source_document_id and source_id:
        return f"src/{source_id}/{source_document_id}", IdentityBasis.SOURCE_DOCUMENT_ID
    # Başlık kimliğine tür eklenmez: tür tahmini sonradan iyileşirse kimlik değişmemeli.
    title = _slug(_clean(meta.get("title_normalized") or meta.get("title")))
    if title:
        return f"title/{title}", IdentityBasis.TITLE
    manifestation = manifestation_id(meta)
    return f"content/{manifestation[:24]}", IdentityBasis.CONTENT


def manifestation_id(meta: Dict[str, Any]) -> str:
    return _clean(meta.get("file_hash")) or _clean(meta.get("content_fingerprint"))


def expression_id(meta: Dict[str, Any], work_id: str) -> str:
    """Sürüm kimliği. Pipeline sürüm işareti vermediyse içerik özeti kullanılır: yalnızca metni
    birebir aynı kopyalar aynı expression sayılır (tarih tahminiyle farklı sürümler birleşmez)."""
    version = _clean(meta.get("declared_version"))
    if version:
        return f"{work_id}@{version}"
    fingerprint = _clean(meta.get("content_fingerprint"))
    if fingerprint:
        return f"{work_id}@{fingerprint[:16]}"
    return f"{work_id}@m:{manifestation_id(meta)[:16]}"


__all__ = [
    "content_fingerprint",
    "expression_id",
    "STRONG_INSTRUMENT_BASES",
    "instrument_from_title",
    "instrument_identity",
    "issuer",
    "law_number",
    "manifestation_id",
    "work_identity",
]
