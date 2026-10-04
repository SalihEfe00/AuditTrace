# -*- coding: utf-8 -*-
"""Kural sonucundan denetim bulgusu (M11): tespit, ölçüt (dayanak + birebir alıntı), kanıt, sonuç sınıfı, öneri.

Bulgu yalnızca motorun ürettiği sonucu düzenler; yeni yorum eklemez. "Karşılandı", "istisna" ve "uygulanmaz"
sonuçları bulgu değildir (kontrol listesinde kalır). Önem sırası: karşılanmadı → manuel kontrol → belirsiz.
Öneri kural katalogunda yazılıysa (`recommendation`) o, değilse sonucun ve norm türünün genel önerisidir.
Kişisel veri (TC kimlik no, IBAN …) bulgu metninde maskelenir. Yaptırım eşlemesi v2'dedir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from mufettis.common.utils import mask_sensitive
from mufettis.rules.catalog import INTERNAL_BASES, Rule
from mufettis.rules.engine import EMPTY, FILLED, MANUAL, NOT_MET, UNKNOWN, RuleResult

# Dayanak kodlarının adı (korpustaki resmi başlıklar)
LAW_NAMES = {
    "4721": "Türk Medeni Kanunu", "5070": "Elektronik İmza Kanunu", "6098": "Türk Borçlar Kanunu",
    "6102": "Türk Ticaret Kanunu", "6305": "Afet Sigortaları Kanunu", "6502": "Tüketicinin Korunması Hakkında Kanun",
    "6750": "Ticari İşlemlerde Taşınır Rehni Kanunu",
    "7.5.23228": "Bankaların Değerleme Hizmeti Almaları … Hakkında Yönetmelik",
    "21.5.200713012": "Suç Gelirlerinin Aklanmasının ve Terörün Finansmanının Önlenmesine Dair Tedbirler Hakkında "
                      "Yönetmelik",
}
SEVERITY = {NOT_MET: 0, MANUAL: 1, UNKNOWN: 2}
# Kuralın konusu olan teminat belgesi dosyada hiç yoksa (ör. rehin verilmemiş) sonuç bulgu değildir: dosyada o tür
# teminat yoktur. Dosyada bulunması beklenen belgenin (DASK, imza sirküleri, onay …) yokluğu ise belirsiz bulgudur.
COLLATERAL_KINDS = {"ipotek", "rehin", "kefalet_sozlesmesi"}
RESULT_CLASS_NOTE = {
    "otomatik": "belgeden otomatik karar",
    "kısmen": "belgeden kısmen; aslı üzerinden teyit önerilir",
    "belirsiz": "belgeden karar çoğunlukla verilemez",
    "manuel": "insan kontrolü",
}
_RECOMMENDATION = {
    (NOT_MET, "yükümlülük"): "Eksiklik giderilmeli ya da belge yeniden düzenlenmeli; giderilemiyorsa teminatın hukuki "
                             "durumu hukuk birimiyle değerlendirilmeli.",
    (NOT_MET, "yasak"): "İşlemin yasağa aykırılığı ilgili birimden sorulmalı; hukuki sonucu hukuk birimiyle "
                        "değerlendirilmeli.",
    (NOT_MET, "hukuki_sonuç"): "Kanunun bağladığı hukuki sonuç (ör. kefaletin geçersizliği ya da türünün değişmesi) "
                               "teminatın değerlendirilmesine yansıtılmalı; hukuk birimine danışılmalı.",
    (NOT_MET, "iç_kontrol"): "Banka içi şartın / yetkinin neden karşılanmadığı ilgili birimden sorulmalı; gerekirse "
                             "onay mercii bilgilendirilmeli.",
    (MANUAL, ""): "Kontrol maddesi belge aslı ve ilgili kayıtlar üzerinden yapılmalı.",
    (UNKNOWN, ""): "Belgeden karar verilemedi: belge aslı ya da ilgili kayıt üzerinden kontrol edilmeli ve sistemin "
                   "sorduğu bilgi cevaplanmalı.",
}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    title: str
    status: str                 # karşılanmadı / manuel kontrol / belirsiz
    detection: str              # tespit
    criterion: str              # ölçüt: dayanak ve adı
    quote: str                  # dayanağın birebir alıntısı (iç kontrolde kaynağın tanımı)
    evidence: Tuple[str, ...]   # kanıt satırları
    result_class: str           # kuralın sonuç sınıfı (otomatik / kısmen / …) ve açıklaması
    recommendation: str         # öneri
    documents: Tuple[str, ...] = ()


def criterion_text(rule: Rule, basis_ref: str) -> str:
    law = rule.basis.law
    if law in INTERNAL_BASES:
        return basis_ref
    name = LAW_NAMES.get(law)
    return f"{name} ({basis_ref})" if name else basis_ref


def evidence_lines(evidence: Iterable[tuple]) -> Tuple[str, ...]:
    lines = []
    for name, state, value, where, source in evidence:
        if state == FILLED:
            shown = value.strftime("%d.%m.%Y") if hasattr(value, "strftime") else value
            text = f"{name}: {shown}"
        elif state == EMPTY:
            text = f"{name}: boş"
        else:
            text = f"{name}: belgede bulunamadı"
        extra = "; ".join(x for x in (source if source != "belge" else "", where) if x)
        lines.append(mask_sensitive(f"{text} ({extra})" if extra else text))
    return tuple(lines)


def finding_for(rule: Rule, result: RuleResult, documents: Sequence[str] = ()) -> Optional[Finding]:
    if result.status not in SEVERITY:
        return None
    detection = result.finding or rule.finding
    if result.status == UNKNOWN:
        detection = f"Belgeden karar verilemedi — kontrol edilen: {rule.title}. Olası bulgu: {detection}"
    recommendation = (rule.raw or {}).get("recommendation") or _RECOMMENDATION.get(
        (result.status, rule.norm_type)) or _RECOMMENDATION.get((result.status, "")) or \
        _RECOMMENDATION[(NOT_MET, "yükümlülük")]
    quote = rule.basis.quote
    if rule.basis.law in INTERNAL_BASES:
        # İç kontrolün metni onay belgesindeki şartın kendisidir (kanun alıntısı yok)
        cond = (rule.check.get("applies_if") or {}).get("field")
        where = next((e[3] for e in result.evidence if e[0] == cond and e[1] == FILLED), "")
        quote = where.split(": ", 1)[1] if ": " in where else INTERNAL_BASES[rule.basis.law]
    return Finding(
        rule_id=rule.id, title=rule.title, status=result.status, detection=mask_sensitive(detection),
        criterion=criterion_text(rule, result.basis), quote=quote, evidence=evidence_lines(result.evidence),
        result_class=f"{rule.result_class} ({RESULT_CLASS_NOTE.get(rule.result_class, '')})".replace(" ()", ""),
        recommendation=recommendation, documents=tuple(documents))


def absent_collateral(item) -> Tuple[str, ...]:
    """Kredi dosyası sonucunda dosyada hiç olmayan teminat türleri (bulgu değil, ayrı not)."""
    return tuple(k for k in getattr(item, "missing", ()) if k in COLLATERAL_KINDS)


def build_findings(rules: Iterable[Rule], results: Iterable) -> List[Finding]:
    """Sonuçlardan bulgular, önem sırasıyla. `results`: RuleResult ya da kredi dosyası FileResult (belgeleriyle)."""
    by_id: Dict[str, Rule] = {r.id: r for r in rules}
    out = []
    for item in results:
        if absent_collateral(item):
            continue
        result = getattr(item, "result", item)
        rule = by_id.get(result.rule_id)
        if rule is None:
            continue
        finding = finding_for(rule, result, getattr(item, "documents", ()))
        if finding is not None:
            out.append(finding)
    return sorted(out, key=lambda f: SEVERITY[f.status])


def summary(results: Iterable) -> Dict[str, int]:
    """Kontrol listesi özeti: sonuç → sayı."""
    counts: Dict[str, int] = {}
    for item in results:
        status = getattr(item, "result", item).status
        counts[status] = counts.get(status, 0) + 1
    return counts


__all__ = ["Finding", "LAW_NAMES", "COLLATERAL_KINDS", "absent_collateral", "build_findings", "finding_for", "criterion_text", "evidence_lines", "summary"]
