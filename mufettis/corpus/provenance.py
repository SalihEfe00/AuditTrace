# -*- coding: utf-8 -*-
"""Kaynak (provenance): yayıncı niteliği ve kopya doğrulaması.

Güven, kaynak kaydındaki (source registry) beyandan ve doğrulanabilir kanıttan gelir:
  * publisher_kind: kaynak kaydının `publisher_kind` alanı. Kaydı olmayan kaynaklar UNKNOWN'dır;
    yerel içe aktarma kanalları (config.INGESTION_CHANNELS) yayıncı değil, kanal olarak tanımlanır.
  * verification: belgenin nereden ve nasıl alındığına göre; dosya adı/klasör hiçbir zaman
    doğrulama sayılmaz.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from mufettis.config import INGESTION_CHANNELS, SOURCE_DEFINITIONS, SOURCE_REGISTRY_FILE
from mufettis.corpus.model import IdentityBasis, PublisherKind, Verification

_KIND_BY_LEGACY_SOURCE_TYPE = {
    "official": PublisherKind.ISSUING_AUTHORITY,
    "secondary": PublisherKind.SECONDARY,
}

_profile_cache: Dict[Any, Dict[str, Any]] = {}


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"none", "null", "nan"} else text


def registry_stamp() -> int:
    """Kaynak kayıt dosyasının sürüm damgası; önbellekler kayıt değişince geçersiz olur."""
    try:
        return SOURCE_REGISTRY_FILE.stat().st_mtime_ns
    except OSError:
        return 0


def source_record(source_id: str) -> Dict[str, Any]:
    """Kaynak kaydı: kod içi tanım + (varsa) kalıcı kayıt dosyasındaki güncel değerler.

    Kayıt dosyası eski bir sürümde yazıldıysa yeni alanlar (publisher_kind, issuer) kod içi
    tanımdan tamamlanır. İçe aktarma kanalları (local_file) yayıncı kaydı değildir.
    """
    from mufettis.common.utils import canonical_source_id, source_profile

    sid = canonical_source_id(source_id)
    key = (sid, registry_stamp())
    if key not in _profile_cache:
        if len(_profile_cache) > 256:
            _profile_cache.clear()
        record = {**INGESTION_CHANNELS.get(sid, {}), **SOURCE_DEFINITIONS.get(sid, {})}
        record.update(source_profile(sid) if sid else {})
        _profile_cache[key] = record
    return dict(_profile_cache[key])


def publisher_kind(meta: Dict[str, Any], record: Dict[str, Any]) -> str:
    declared = _clean(record.get("publisher_kind")).lower()
    if declared in PublisherKind.ALL:
        return declared
    return _KIND_BY_LEGACY_SOURCE_TYPE.get(_clean(record.get("source_type")).lower(), PublisherKind.UNKNOWN)


def _official_channel(meta: Dict[str, Any], kind: str) -> bool:
    from mufettis.common.utils import source_is_allowed

    url = _clean(meta.get("url"))
    source_id = _clean(meta.get("source_id"))
    return bool(kind in PublisherKind.OFFICIAL and url and source_id and source_is_allowed(url, source_id))


def verification(meta: Dict[str, Any], kind: str, identity_basis: str) -> str:
    """Kopyanın resmî metinle özdeşliğinin kanıtı, güçlüden zayıfa."""
    if _clean(meta.get("verified_against")):
        return Verification.FINGERPRINT_MATCHED
    if _official_channel(meta, kind):
        return Verification.OFFICIAL_CHANNEL
    if identity_basis in (IdentityBasis.DECLARED, IdentityBasis.OFFICIAL_NUMBER):
        return Verification.SELF_DECLARED
    return Verification.UNVERIFIED


def reconcile_fingerprints(records: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    """Resmî kanaldan alınmış bir kopyayla içerik özeti birebir aynı olan diğer kopyaları bulur.

    records: manifestation_id -> kanonik alanları içeren kayıt. Dönüş: `verified_against`
    alanı yazılması gereken kopyalar. Saf fonksiyondur; yazmayı çağıran yapar.
    """
    by_fingerprint: Dict[str, List[str]] = defaultdict(list)
    for mid, rec in records.items():
        fingerprint = _clean(rec.get("content_fingerprint"))
        if fingerprint:
            by_fingerprint[fingerprint].append(mid)
    updates: List[Dict[str, str]] = []
    for fingerprint, members in by_fingerprint.items():
        anchors = sorted(m for m in members if records[m].get("verification") == Verification.OFFICIAL_CHANNEL)
        if not anchors:
            continue
        for mid in sorted(members):
            rec = records[mid]
            if mid in anchors or _clean(rec.get("verified_against")):
                continue
            updates.append({"manifestation_id": mid, "verified_against": anchors[0]})
    return updates


__all__ = [
    "publisher_kind",
    "reconcile_fingerprints",
    "registry_stamp",
    "source_record",
    "verification",
]
