# -*- coding: utf-8 -*-
"""Normatif konum ve otorite önceliği.

Otorite bir ALAKA sinyali değildir. Retrieval'da yalnızca alaka puanı birbirine yakın adaylar
arasında sıra belirleyen, üst sınırı küçük bir öncelik (prior) olarak kullanılır. Hiyerarşide
üstte olan veya resmî kanaldan doğrulanmış bir belge, konuyla daha az ilgiliyse öne geçemez.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from mufettis.corpus.model import InstrumentType, NormativeRole, PublisherKind, Verification

# Normlar hiyerarşisi (Anayasa > kanun > CBK > ... ). Aynı düzeydeki türler eşit puan alır.
NORMATIVE_RANK: Dict[str, int] = {
    InstrumentType.CONSTITUTION: 100,
    InstrumentType.LAW: 90,
    InstrumentType.PRESIDENTIAL_DECREE: 85,
    InstrumentType.PRESIDENTIAL_DECISION: 80,
    InstrumentType.REGULATION: 70,
    InstrumentType.COMMUNIQUE: 60,
    InstrumentType.PROCEDURE: 55,
    InstrumentType.BOARD_DECISION: 50,
    InstrumentType.CIRCULAR: 50,
    InstrumentType.DECISION: 45,
    InstrumentType.GUIDELINE: 40,
    InstrumentType.CHART_OF_ACCOUNTS: 30,
    InstrumentType.OTHER: 20,
}

_ROLE_BY_INSTRUMENT = {
    InstrumentType.GUIDELINE: NormativeRole.GUIDANCE,
    InstrumentType.CHART_OF_ACCOUNTS: NormativeRole.CLASSIFICATION,
    InstrumentType.OTHER: NormativeRole.UNKNOWN,
}

# Parça yapısının rolü belge türünden önce gelir: bir tebliğin eki olan hesap planının
# satırları yükümlülük değil sınıflamadır. Kanonik alanı olmayan eski parçalarda Chroma
# filtresi de bu eşlemeden türetilir (structures_for_roles).
_ROLE_BY_STRUCTURE = {
    "accounting_plan": NormativeRole.CLASSIFICATION,
}

# Kopya güveni: resmî metinle özdeşliği ne kadar kesin?
VERIFICATION_TRUST = {
    Verification.OFFICIAL_CHANNEL: 1.0,
    Verification.FINGERPRINT_MATCHED: 1.0,
    Verification.SELF_DECLARED: 0.6,
    Verification.UNVERIFIED: 0.3,
}
# İkincil yayıncının kopyası, kendi beyanı ne olursa olsun bu düzeyi aşamaz.
PUBLISHER_TRUST_CEILING = {
    PublisherKind.SECONDARY: 0.5,
}

# Otorite önceliğinin üst sınırı. Alaka puanında anlamsal benzerlik 0.40, sözcüksel örtüşme
# 0.22 ağırlıklıdır; öncelik bunların küçük bir kesridir ve yalnızca başa baş adayları ayırır.
AUTHORITY_PRIOR_MAX = 0.06


def normative_rank(instrument: str) -> int:
    return NORMATIVE_RANK.get(instrument, NORMATIVE_RANK[InstrumentType.OTHER])


def normative_role(instrument: str, structure_type: Any = "") -> str:
    by_structure = _ROLE_BY_STRUCTURE.get(str(structure_type or ""))
    if by_structure:
        return by_structure
    return _ROLE_BY_INSTRUMENT.get(instrument, NormativeRole.RULE)


def structures_for_roles(roles: Iterable[str]) -> List[str]:
    wanted = set(roles)
    return sorted(s for s, role in _ROLE_BY_STRUCTURE.items() if role in wanted)


def copy_trust(verification: str, publisher_kind: str) -> float:
    trust = VERIFICATION_TRUST.get(verification, VERIFICATION_TRUST[Verification.UNVERIFIED])
    return min(trust, PUBLISHER_TRUST_CEILING.get(publisher_kind, 1.0))


def authority_prior(rank: int, verification: str, publisher_kind: str) -> float:
    """0..AUTHORITY_PRIOR_MAX: yarısı normatif hiyerarşi, yarısı kopya güveni."""
    hierarchy = max(0, min(100, int(rank))) / 100.0
    return AUTHORITY_PRIOR_MAX * (0.5 * hierarchy + 0.5 * copy_trust(verification, publisher_kind))


__all__ = [
    "AUTHORITY_PRIOR_MAX",
    "NORMATIVE_RANK",
    "VERIFICATION_TRUST",
    "authority_prior",
    "copy_trust",
    "normative_rank",
    "normative_role",
    "structures_for_roles",
]
