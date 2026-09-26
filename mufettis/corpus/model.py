# -*- coding: utf-8 -*-
"""Kanonik mevzuat corpus modeli: sözlük ve kayıt tipi.

Bir belge kaydı birbirinden BAĞIMSIZ dört eksende tanımlanır; hiçbiri diğerinden türetilmez
ve retrieval'da birbirine karıştırılmaz:

1. Kimlik (FRBR / ELI):
   * Work          – soyut düzenleme ("5464 sayılı Kanun"), kaynaktan ve formattan bağımsız.
   * Expression    – düzenlemenin belirli bir metin sürümü (değişiklikler yeni expression'dır).
   * Manifestation – o sürümün somut bir kopyası (PDF/HTML, URL, dosya özeti).
2. Kaynak (provenance): kopyayı kim yayımladı (publisher_kind) ve kopyanın resmî metinle
   özdeşliği nasıl doğrulandı (verification). "Resmî kaynaktan gelmiyor" ile "ikincil/yetkisiz
   yayıncı" aynı şey değildir: kullanıcının yüklediği bir kanun metni doğrulanmamış bir
   kopyadır, ikincil kaynak değil.
3. Normatif konum: belge türü (instrument_type), normlar hiyerarşisindeki yeri (normative_rank)
   ve kural koyup koymadığı (normative_role).
4. Geçerlilik: yürürlük durumu ve tarihleri. Kayıtta saklanmaz; her okumada `status` ve tarih
   alanlarından hesaplanır (bkz. validity.py), çünkü sürüm zinciri durumu sonradan değiştirir.

Bu modül veri kaynağına, klasöre veya dosya adına dair hiçbir kural içermez.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict

# Kayıttaki kanonik alanların şema sürümü. Türetme kuralları değişirse artırılır; eski
# sürümdeki kayıtlar okunurken yeniden türetilir (canonical_view) ve backfill ile güncellenir.
CANONICAL_SCHEMA = "1"


class PublisherKind:
    """Kopyayı yayımlayan tarafın niteliği."""
    PRIMARY_OFFICIAL = "primary_official"    # resmî yayın organı (Resmî Gazete, mevzuat portalı)
    ISSUING_AUTHORITY = "issuing_authority"  # düzenlemeyi çıkaran kurumun kendi yayını
    SECONDARY = "secondary"                  # özel/ticari derleme siteleri
    USER_SUPPLIED = "user_supplied"          # kullanıcının yüklediği dosya; yayıncı belirsiz
    INTERNAL = "internal"                    # kurum içi not/yazışma
    UNKNOWN = "unknown"

    OFFICIAL = frozenset({PRIMARY_OFFICIAL, ISSUING_AUTHORITY})
    ALL = frozenset({PRIMARY_OFFICIAL, ISSUING_AUTHORITY, SECONDARY, USER_SUPPLIED, INTERNAL, UNKNOWN})


class Verification:
    """Kopyanın resmî metinle özdeşliğinin nasıl bilindiği (güçlüden zayıfa)."""
    OFFICIAL_CHANNEL = "official_channel"        # kayıtlı resmî yayıncının izinli adresinden alındı
    FINGERPRINT_MATCHED = "fingerprint_matched"  # içeriği resmî kanaldan alınmış bir kopyayla özdeş
    SELF_DECLARED = "self_declared"              # metin kendi resmî kimliğini beyan ediyor; kanal doğrulanmadı
    UNVERIFIED = "unverified"

    ALL = frozenset({OFFICIAL_CHANNEL, FINGERPRINT_MATCHED, SELF_DECLARED, UNVERIFIED})


class InstrumentType:
    CONSTITUTION = "anayasa"
    LAW = "kanun"
    PRESIDENTIAL_DECREE = "cbk"          # Cumhurbaşkanlığı Kararnamesi / KHK
    PRESIDENTIAL_DECISION = "cb_karari"
    REGULATION = "yonetmelik"
    COMMUNIQUE = "teblig"
    PROCEDURE = "usul_esas"
    BOARD_DECISION = "kurul_karari"
    CIRCULAR = "genelge"
    GUIDELINE = "rehber"                 # rehber / kılavuz
    CHART_OF_ACCOUNTS = "hesap_plani"
    DECISION = "karar"
    OTHER = "diger"

    ALL = frozenset({
        CONSTITUTION, LAW, PRESIDENTIAL_DECREE, PRESIDENTIAL_DECISION, REGULATION, COMMUNIQUE,
        PROCEDURE, BOARD_DECISION, CIRCULAR, GUIDELINE, CHART_OF_ACCOUNTS, DECISION, OTHER,
    })


class NormativeRole:
    """Belge (parça) hukuki kural mı koyar? Kanıt uygunluğu (eligibility) bu eksene bakar."""
    RULE = "rule"                      # bağlayıcı yükümlülük/yasak/yetki
    GUIDANCE = "guidance"              # yorum, iyi uygulama; bağlayıcılığı sınırlı
    CLASSIFICATION = "classification"  # hesap planı, kod listesi: tanım/sınıflama, yükümlülük değil
    INFORMATIONAL = "informational"    # duyuru, aydınlatma metni vb.
    UNKNOWN = "unknown"

    ALL = frozenset({RULE, GUIDANCE, CLASSIFICATION, INFORMATIONAL, UNKNOWN})


class IdentityBasis:
    """Work kimliğinin neye dayandığı; güven düzeyi buna göre belirlenir."""
    DECLARED = "declared"                      # ingestion pipeline'ının verdiği ELI / resmî kimlik
    OFFICIAL_NUMBER = "official_number"        # belgenin kendi resmî numarası (kanun no)
    SOURCE_DOCUMENT_ID = "source_document_id"  # yayıncının kalıcı belge kimliği (kaynağa özgü)
    TITLE = "title"                            # normalize başlık
    CONTENT = "content"                        # hiçbiri yok: kopyanın kendisi

    CONFIDENCE = {
        DECLARED: "high",
        OFFICIAL_NUMBER: "high",
        SOURCE_DOCUMENT_ID: "medium",
        TITLE: "low",
        CONTENT: "low",
    }


class Validity:
    IN_FORCE = "in_force"
    NOT_YET_IN_FORCE = "not_yet_in_force"
    NOT_IN_FORCE = "not_in_force"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CanonicalRecord:
    """Bir manifestation'ın (ve parçalarının) kanonik kimlik/kaynak/normatif alanları."""
    work_id: str
    expression_id: str
    manifestation_id: str
    identity_basis: str
    identity_confidence: str
    instrument_type: str
    instrument_basis: str
    issuer: str
    normative_rank: int
    normative_role: str
    publisher_kind: str
    verification: str
    content_fingerprint: str
    canonical_schema: str = CANONICAL_SCHEMA

    def to_metadata(self) -> Dict[str, str]:
        """Chroma/index metadata'sı için düz, string değerli sözlük."""
        return {k: str(v) for k, v in asdict(self).items()}


CANONICAL_FIELDS = tuple(CanonicalRecord.__dataclass_fields__)


__all__ = [
    "CANONICAL_SCHEMA",
    "CANONICAL_FIELDS",
    "CanonicalRecord",
    "IdentityBasis",
    "InstrumentType",
    "NormativeRole",
    "PublisherKind",
    "Validity",
    "Verification",
]
