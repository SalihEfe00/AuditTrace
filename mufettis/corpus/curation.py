# -*- coding: utf-8 -*-
"""Mevzuat olmayan kayıtların ayıklanması (M6.5).

Kural: ikincil bir siteden gelen ve hiç madde yapısı taşımayan kayıt mevzuat metni değildir (portal, giriş,
hesaplama sayfası) ve ayıklanır. Kullanıcının yerel dosyası aynı durumdaysa yalnızca incelemeye önerilir
(`review`): bankanın maddesiz iç düzenlemeleri (yetki tablosu, ücret tarifesi; M8) de yerel dosyadır, karar
kullanıcınındır. Resmî kaynaklı (maddesiz BDDK rehberi, genelgesi) ve kurum içi kayıtlar hiç önerilmez.

Ayıklama silme değildir: kayıt ve parçalar `excluded` durumuna geçer (aramaya ve istatistiklere girmez),
önceki durum saklanır ve `restore` ile geri alınır. Her işlem denetim kaydına yazılır.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from mufettis.common.utils import now_iso
from mufettis.corpus.model import PublisherKind
from mufettis.corpus.validity import INACTIVE_STATUSES

EXCLUDED = "excluded"


def non_legislation_reason(record: Dict[str, Any]) -> str:
    """Kayıt mevzuat değilse gerekçe, değilse boş dizge."""
    if str(record.get("status", "")) in INACTIVE_STATUSES:
        return ""
    kind = str(record.get("publisher_kind") or "").lower()
    if kind in PublisherKind.OFFICIAL or kind == PublisherKind.INTERNAL:
        return ""
    try:
        articles = int(record.get("article_count") or 0)
    except (TypeError, ValueError):
        articles = 0
    if articles > 0 or str(record.get("structure_type") or "") == "article":
        return ""
    origin = "ikincil kaynak" if kind == PublisherKind.SECONDARY else "yerel dosya"
    return f"{origin}, madde yapısı yok: mevzuat metni değil"


def find_non_legislation(records: Iterable[Tuple[str, Dict[str, Any]]]) -> List[Dict[str, str]]:
    """action: `exclude` (ikincil kaynak; uygulanabilir) ya da `review` (yerel dosya; kullanıcı karar verir)."""
    out = []
    for h, r in records:
        reason = non_legislation_reason(r or {})
        if reason:
            kind = str(r.get("publisher_kind") or "").lower()
            out.append({"file_hash": h, "title": str(r.get("title") or ""), "source_id": str(r.get("source_id") or ""),
                        "reason": reason, "action": "exclude" if kind == PublisherKind.SECONDARY else "review"})
    return out


def exclude(rag, index, file_hash: str, reason: str) -> int:
    """Kaydı ve parçalarını ayıklar; güncellenen parça sayısı."""
    from mufettis.audit.service import append_audit

    record = index.get(file_hash) or {}
    previous = str(record.get("status") or "active")
    index.upsert(file_hash, {"status": EXCLUDED, "status_before_exclusion": previous,
                             "excluded_reason": reason, "excluded_at": now_iso()})
    count = rag.update_hash_metadata(file_hash, {"status": EXCLUDED}) if rag is not None else 0
    append_audit("corpus_record_excluded", {"file_hash": file_hash, "title": record.get("title", ""),
                                            "reason": reason, "previous_status": previous, "chunks": count})
    return count


def restore(rag, index, file_hash: str) -> int:
    """Ayıklamayı geri alır: önceki durum kayda ve parçalara döner."""
    from mufettis.audit.service import append_audit

    record = index.get(file_hash) or {}
    if record.get("status") != EXCLUDED:
        return 0
    previous = str(record.get("status_before_exclusion") or "active")
    index.upsert(file_hash, {"status": previous, "status_before_exclusion": "", "excluded_reason": "",
                             "excluded_at": ""})
    count = rag.update_hash_metadata(file_hash, {"status": previous}) if rag is not None else 0
    append_audit("corpus_record_restored", {"file_hash": file_hash, "title": record.get("title", ""),
                                            "status": previous, "chunks": count})
    return count


__all__ = ["non_legislation_reason", "find_non_legislation", "exclude", "restore", "EXCLUDED"]
