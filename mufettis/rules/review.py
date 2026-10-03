# -*- coding: utf-8 -*-
"""Belirsiz alanları müfettişe sorma ve cevabı "müfettiş beyanı" olarak işleme (M9.3 kullanıcı kararı).

Akış: belge alanları çıkarılır → kurallar değerlendirilir → sonucu "belirsiz" olan kuralların karar için gereken
ama belgede dolu olmayan alanları `pending_questions` ile sorulur → cevaplar `apply_answers` ile `müfettiş beyanı`
kaynaklı alan değeri olur → kurallar yeniden değerlendirilir. Arayüzden bağımsız; Streamlit bölümü
mufettis/ui/rules_view.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from mufettis.rules.catalog import Rule
from mufettis.rules.engine import EMPTY, FILLED, INSPECTOR, UNKNOWN, FieldValue, RuleResult

UNKNOWN_ANSWER = "bilinmiyor"


@dataclass(frozen=True)
class Question:
    field: str
    text: str
    options: Tuple[Tuple[str, str], ...]  # (gösterilen, alan değeri); "" değeri = boş (öğe yok)


QUESTIONS: Dict[str, Question] = {q.field: q for q in [
    Question("el_yazisi_azami_tutar", "Azami kefalet tutarı kefilin kendi el yazısıyla mı yazılmış?",
             (("evet", "var"), ("hayır (matbu / başkası yazmış)", "yok"))),
    Question("el_yazisi_tarih", "Kefalet tarihi kefilin kendi el yazısıyla mı yazılmış?",
             (("evet", "var"), ("hayır", "yok"))),
    Question("el_yazisi_kefalet_turu", "'Müteselsil kefil' ibaresi kefilin kendi el yazısıyla mı yazılmış?",
             (("evet", "var"), ("hayır", "yok"))),
    Question("kefil_medeni_hal", "Kefilin medeni hali nedir?",
             (("evli", "evli"), ("bekâr", "bekar"), ("boşanmış", "boşanmış"), ("dul", "dul"))),
    Question("kefil_ticari_sifat", "Kefil, kefalet verdiği işle ilgili olarak hangi sıfatta?",
             (("tacir / ticari işletme sahibi", "tacir_isletme_sahibi"),
              ("şirket ortağı / yöneticisi (şirket kredisi)", "sirket_ortagi_yoneticisi"),
              ("esnaf / sanatkâr (mesleki)", "esnaf_sanatkar"), ("5570 sayılı Kanun kredisi", "5570_kredisi"),
              ("kooperatif kredisi", "kooperatif_kredisi"), ("hiçbiri", "yok"))),
    Question("es_rizasi", "Eşin yazılı rızası (ad soyad, imza) belgede var mı?",
             (("var", "var"), ("yok / boş", ""))),
    Question("azami_tutar", "Azami kefalet tutarı sözleşmede yazılı mı?", (("yazılı", "var"), ("yok / boş", ""))),
    Question("sozlesme_tarihi", "Kefalet tarihi sözleşmede yazılı mı?", (("yazılı", "var"), ("yok / boş", ""))),
    Question("imza_turu", "Kefil sözleşmeyi nasıl imzalamış?", (("ıslak imza", "ıslak"), ("elektronik imza", "e-imza"))),
    Question("kredi_turu", "Kefalet hangi tür krediye verilmiş?", (("tüketici / bireysel", "tüketici"), ("ticari", "ticari"))),
    Question("kefalet_turu", "Kefalet türü nedir?", (("müteselsil", "müteselsil"), ("adi", "adi"))),
    # Kefalet dışı teminat belgeleri (M9.6)
    Question("ipotek_tutari", "İpotek tutarı (üst sınır) belgede yazılı mı?", (("yazılı", "var"), ("yok / boş", ""))),
    Question("yevmiye_no", "İpoteğin tapu tescili (yevmiye numarası) belgelenmiş mi?",
             (("evet", "var"), ("hayır / boş", ""))),
    Question("rehin_tutari", "Rehnin azami miktarı sözleşmede yazılı mı?", (("yazılı", "var"), ("yok / boş", ""))),
    Question("sicil_no", "Rehin Rehinli Taşınır Siciline tescil edilmiş mi (sicil numarası)?",
             (("evet", "var"), ("hayır / boş", ""))),
    Question("imza_onayi", "Rehin sözleşmesindeki imzalar nasıl onaylanmış?",
             (("noter onayı", "noter"), ("sicil yetkilisi huzurunda", "sicil_yetkilisi"),
              ("güvenli elektronik imza", "guvenli_e_imza"), ("onaysız", "yok"))),
    Question("lisans_no", "Değerleme uzmanının lisans numarası raporda var mı?", (("var", "var"), ("yok / boş", ""))),
    Question("rapor_tarihi", "Değerleme raporunun tarihi yazılı mı?", (("yazılı", "var"), ("yok / boş", ""))),
    Question("deger", "Raporda nihai değer takdiri var mı?", (("var", "var"), ("yok / boş", ""))),
]}


def _rule_fields(rule: Rule) -> List[str]:
    check = rule.check
    names = [check.get(k) for k in ("field", "before", "after") if check.get(k)]
    if isinstance(check.get("applies_if"), dict):
        names.insert(0, check["applies_if"]["field"])
    names += [e.condition_field for e in rule.exceptions]
    return names


def pending_questions(rules: Iterable[Rule], results: Iterable[RuleResult],
                      fields: Dict[str, FieldValue]) -> List[Question]:
    """Belirsiz kalan kuralların karar için sorulabilir ve belgede dolu olmayan alanları (tekrarsız, kural sırasıyla)."""
    by_id = {r.id: r for r in rules}
    out, seen = [], set()
    for res in results:
        if res.status != UNKNOWN or res.rule_id not in by_id:
            continue
        for name in _rule_fields(by_id[res.rule_id]):
            fv = fields.get(name)
            # Müfettişin önceki cevabı soruyu kaldırmaz (değiştirebilsin); belgede dolu olan alan sorulmaz.
            if name in seen or name not in QUESTIONS or (fv is not None and fv.state == FILLED and fv.source != INSPECTOR):
                continue
            seen.add(name)
            out.append(QUESTIONS[name])
    return out


def apply_answers(fields: Dict[str, FieldValue], answers: Dict[str, Optional[str]],
                  note: str = "elle giriş") -> Dict[str, FieldValue]:
    """Cevapları alanlara işler. Değer "" → öğe yok (boş, müfettiş beyanı); None / "bilinmiyor" → değişmez."""
    out = dict(fields)
    for name, value in answers.items():
        if value is None or value == UNKNOWN_ANSWER:
            continue
        out[name] = FieldValue(EMPTY, None, note, INSPECTOR) if value == "" else FieldValue.filled(value, note, INSPECTOR)
    return out


__all__ = ["Question", "QUESTIONS", "pending_questions", "apply_answers", "UNKNOWN_ANSWER"]
