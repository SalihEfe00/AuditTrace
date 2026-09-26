# -*- coding: utf-8 -*-
"""Kanonik kaydın türetilmesi ve okunması.

`derive` saf ve deterministiktir: aynı girdi alanları her zaman aynı kanonik kaydı verir.
Kanonik alanlar ingestion'da (extract_legal_metadata, LocalRAG.ingest_text) kayda yazılır;
alanı olmayan veya eski şemadaki kayıtlar okunurken aynı fonksiyonla türetilir. Böylece
retrieval, corpus'un ne kadarının backfill edildiğinden bağımsız olarak aynı kararı verir.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, FrozenSet, Iterable, List, Optional

from mufettis.corpus.authority import normative_rank, normative_role, structures_for_roles
from mufettis.corpus.identity import (
    content_fingerprint,
    expression_id,
    instrument_identity,
    issuer,
    manifestation_id,
    work_identity,
)
from mufettis.corpus.model import CANONICAL_FIELDS, CANONICAL_SCHEMA, CanonicalRecord, IdentityBasis
from mufettis.corpus.provenance import publisher_kind, registry_stamp, source_record, verification

# Kanonik kaydın bağlı olduğu girdi alanları. Bunlardan biri değişirse kayıt yeniden türetilir.
DERIVATION_INPUTS = (
    "declared_work_id", "eli", "declared_instrument_type", "declared_version", "issuer",
    "title", "title_normalized", "law_number", "document_type", "structure_type",
    "source_id", "source_document_id", "url", "file_hash", "content_fingerprint", "verified_against",
)


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"none", "null", "nan"} else text


def derive(meta: Dict[str, Any], text: Optional[str] = None) -> CanonicalRecord:
    """Belge metadata'sından (ve varsa tam metinden) kanonik kaydı türetir.

    text verilirse içerik özeti bu metinden hesaplanır; verilmezse kayıttaki özet kullanılır.
    """
    m = dict(meta or {})
    if text is not None:
        m["content_fingerprint"] = content_fingerprint(text)
    return _derive_cached(tuple(_clean(m.get(k)) for k in DERIVATION_INPUTS), registry_stamp())


@lru_cache(maxsize=4096)
def _derive_cached(values: tuple, _registry_stamp: int) -> CanonicalRecord:
    m = dict(zip(DERIVATION_INPUTS, values))
    instrument, instrument_basis = instrument_identity(m)
    work, basis = work_identity(m, instrument)
    record = source_record(m.get("source_id", ""))
    kind = publisher_kind(m, record)
    return CanonicalRecord(
        work_id=work,
        expression_id=expression_id(m, work),
        manifestation_id=manifestation_id(m),
        identity_basis=basis,
        identity_confidence=IdentityBasis.CONFIDENCE[basis],
        instrument_type=instrument,
        instrument_basis=instrument_basis,
        issuer=issuer(m, instrument, instrument_basis, record),
        normative_rank=normative_rank(instrument),
        normative_role=normative_role(instrument, m.get("structure_type")),
        publisher_kind=kind,
        verification=verification(m, kind, basis),
        content_fingerprint=m.get("content_fingerprint", ""),
    )


def with_canonical(meta: Dict[str, Any], text: Optional[str] = None) -> Dict[str, Any]:
    """meta + kanonik alanlar (içerik özeti dahil)."""
    return {**meta, **derive(meta, text).to_metadata()}


def refresh_canonical(meta: Dict[str, Any], changed_keys: Iterable[str]) -> Dict[str, Any]:
    """Kanonik alan taşıyan bir kaydın türetme girdisi değiştiyse kanonik alanları yeniler."""
    if _clean(meta.get("canonical_schema")) and set(changed_keys) & set(DERIVATION_INPUTS):
        return with_canonical(meta)
    return meta


def canonical_view(meta: Dict[str, Any]) -> CanonicalRecord:
    """Kayıttaki kanonik alanlar güncel şemadaysa onları, değilse türetilmiş kaydı döndürür."""
    meta = meta or {}
    if _clean(meta.get("canonical_schema")) == CANONICAL_SCHEMA and all(k in meta for k in CANONICAL_FIELDS):
        values = {k: _clean(meta.get(k)) for k in CANONICAL_FIELDS}
        try:
            values["normative_rank"] = int(float(values["normative_rank"] or 0))
        except ValueError:
            return derive(meta)
        return CanonicalRecord(**values)
    return derive(meta)


@dataclass(frozen=True)
class EligibilityPolicy:
    """Kanıt uygunluğu: alaka hesabından ÖNCE uygulanan, evet/hayır kuralları.

    exclude_roles:           bu normatif roldeki parçalar kanıt olamaz (ör. sınıflama).
    exclude_publisher_kinds: bu nitelikteki yayıncıların kopyaları kanıt olamaz.
    """
    exclude_roles: FrozenSet[str] = frozenset()
    exclude_publisher_kinds: FrozenSet[str] = frozenset()

    @classmethod
    def from_filters(cls, filters: Optional[Dict[str, Any]]) -> "EligibilityPolicy":
        filters = filters or {}
        return cls(
            exclude_roles=frozenset(str(r) for r in filters.get("exclude_roles") or ()),
            exclude_publisher_kinds=frozenset(str(k) for k in filters.get("exclude_publisher_kinds") or ()),
        )

    def __bool__(self) -> bool:
        return bool(self.exclude_roles or self.exclude_publisher_kinds)

    def admits(self, meta: Dict[str, Any]) -> bool:
        view = canonical_view(meta)
        return view.normative_role not in self.exclude_roles and view.publisher_kind not in self.exclude_publisher_kinds

    def chroma_clauses(self) -> List[Dict[str, Any]]:
        """Vektör aramasına eklenecek ön filtre. Chroma'da `$nin` alanı olmayan kayıtları da
        geçirir; kanonik alanı henüz yazılmamış eski parçalar için rolün yapıdan türetilen
        karşılığı da filtrelenir. Kalan her şey `admits` ile sonradan elenir."""
        clauses: List[Dict[str, Any]] = []
        if self.exclude_roles:
            clauses.append({"normative_role": {"$nin": sorted(self.exclude_roles)}})
            structures = structures_for_roles(self.exclude_roles)
            if structures:
                clauses.append({"structure_type": {"$nin": structures}})
        if self.exclude_publisher_kinds:
            clauses.append({"publisher_kind": {"$nin": sorted(self.exclude_publisher_kinds)}})
        return clauses


__all__ = [
    "DERIVATION_INPUTS",
    "EligibilityPolicy",
    "canonical_view",
    "derive",
    "refresh_canonical",
    "with_canonical",
]
