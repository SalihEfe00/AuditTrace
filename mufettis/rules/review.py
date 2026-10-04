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
from mufettis.rules.fields import as_amount, as_date
from mufettis.rules.engine import EMPTY, FILLED, INSPECTOR, UNKNOWN, FieldValue, RuleResult

UNKNOWN_ANSWER = "bilinmiyor"


@dataclass(frozen=True)
class Question:
    field: str
    text: str
    options: Tuple[Tuple[str, str], ...]  # (gösterilen, alan değeri); "" değeri = boş (öğe yok)
    kind: str = "choice"                  # "choice": seçenekler; "amount" / "date": müfettiş tutarı (TL) / tarihi yazar


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
              ("esnaf ve sanatkârlar siciline kayıtlı esnaf / sanatkâr (mesleki)", "esnaf_sanatkar"), ("5570 sayılı Kanun kredisi", "5570_kredisi"),
              ("kooperatif kredisi", "kooperatif_kredisi"), ("hiçbiri", "yok"))),
    Question("es_rizasi", "Eşin yazılı rızası (ad soyad, imza) belgede var mı?",
             (("var", "var"), ("yok / boş", ""))),
    Question("azami_tutar", "Azami kefalet tutarı sözleşmede yazılı mı?", (("yazılı", "var"), ("yok / boş", ""))),
    Question("kefalet_tarihi", "Sözleşmedeki kefalet tarihi nedir? (yazılı değilse 'yok')", (("yok / boş", ""),), kind="date"),
    Question("es_rizasi_tarihi", "Eşin rızası hangi tarihte verilmiş?", (), kind="date"),
    Question("imza_turu", "Kefil sözleşmeyi nasıl imzalamış?",
             (("ıslak imza", "ıslak"), ("güvenli elektronik imza", "guvenli_e_imza"),
              ("başka elektronik imza (güvenli değil)", "diger_elektronik"), ("kefil imzası yok", "imzasiz"))),
    Question("ayrilik_durumu", "Kefil ile eşi hakkında mahkemece verilmiş ayrılık kararı ya da yasal ayrı yaşama hakkı var mı?",
             (("yok", "yok"), ("mahkemece ayrılık kararı", "mahkeme_ayrilik_karari"),
              ("yasal ayrı yaşama hakkı", "yasal_ayri_yasama"))),
    Question("kefalet_isletmeyle_ilgili", "Kefalet, kefilin kendi işletmesi / şirketi ya da esnaf olarak mesleki faaliyetiyle "
             "ilgili olarak mı verilmiş?", (("evet", "evet"), ("hayır", "hayir"))),
    Question("kredi_turu", "Kefalet hangi tür krediye verilmiş?", (("tüketici / bireysel", "tüketici"), ("ticari", "ticari"))),
    Question("kefalet_turu", "Kefalet türü nedir?", (("müteselsil", "müteselsil"), ("adi", "adi"))),
    # Kefalet dışı teminat belgeleri (M9.6)
    Question("ipotek_tutari", "İpotekle güvence altına alınan tutar (belli alacak ya da üst sınır) belgede yazılı mı?",
             (("yazılı", "var"), ("yok / boş", ""))),
    Question("yevmiye_no", "İpotek tapu kütüğüne tescil edilmiş mi? (tapu kaydına / tescil belgesine bakarak)",
             (("evet, tescil var", "var"), ("hayır, tescil yok", ""))),
    Question("rehin_tutari", "Rehnin azami miktarı sözleşmede yazılı mı?", (("yazılı", "var"), ("yok / boş", ""))),
    Question("sicil_no", "Rehin Rehinli Taşınır Siciline tescil edilmiş mi? (sicil kaydına bakarak)",
             (("evet, kayıt var", "var"), ("hayır, kayıt yok", ""))),
    Question("imza_onayi", "Rehin sözleşmesindeki imzalar nasıl onaylanmış?",
             (("noter onayı", "noter"), ("sicil yetkilisi huzurunda", "sicil_yetkilisi"),
              ("güvenli elektronik imza", "guvenli_e_imza"), ("onaysız", "yok"))),
    Question("lisans_no", "Değerleme uzmanının lisans numarası raporda var mı?", (("var", "var"), ("yok / boş", ""))),
    Question("rapor_tarihi", "Değerleme raporunun tarihi nedir? (yazılı değilse 'yok')", (("yok / boş", ""),), kind="date"),
    Question("baslangic_tarihi", "DASK poliçesinin başlangıç tarihi nedir?", (), kind="date"),
    Question("bitis_tarihi", "DASK poliçesinin bitiş tarihi nedir?", (), kind="date"),
    Question("tescil_tarihi", "İpoteğin tapuya tescil (yevmiye) tarihi nedir?", (), kind="date"),
    Question("islem_tarihi", "Tapu işleminin (ipotek tescilinin) tarihi nedir? (ipotek belgesindeki yevmiye tarihi)", (),
             kind="date"),
    Question("deger", "Rapordaki nihai değer kaç TL? (raporda değer yoksa 'yok' seçin)", (("yok / boş", ""),),
             kind="amount"),
    Question("tutar", "Senette bedel yazılı mı?", (("yazılı", "var"), ("yok / boş", ""))),
    Question("lehtar", "Bonoda lehtarın (alacaklının) adı yazılı mı?", (("yazılı", "var"), ("yok / boş", ""))),
    Question("duzenleme_tarihi", "Senedin düzenlenme tarihi nedir? (yazılı değilse 'yok')", (("yok / boş", ""),),
             kind="date"),
    Question("imza", "Senette düzenleyenin imzası var mı?", (("var", "var"), ("yok", ""))),
    Question("duzenleme_yeri", "Bonoda düzenlenme yeri yazmıyor: düzenleyenin adının yanında bir yer (il / ilçe) yazılı mı?",
             (("evet, yazılı", "var"), ("hayır, hiçbir yer yok", ""))),
]}


def question(name: str) -> Optional[Question]:
    """Alanın sorusu. Kredi dosyası alanı ("dask.baslangic_tarihi", M10.1) belge türündeki aynı alanın sorusunu
    kullanır; soru hangi belgeyi sorduğunu söyler."""
    if name in QUESTIONS:
        return QUESTIONS[name]
    kind, _, base = name.partition(".")
    q = QUESTIONS.get(base) if base else None
    if q is None:
        return None
    return Question(name, f"[{kind}] {q.text}", q.options, q.kind)


def _rule_fields(rule: Rule) -> List[str]:
    check = rule.check
    names = [check.get(k) for k in ("field", "other", "before", "after") if check.get(k)]
    if isinstance(check.get("applies_if"), dict):
        names.insert(0, check["applies_if"]["field"])
    names += [n for e in rule.exceptions for n, _ in e.conditions]
    return names


def pending_questions(rules: Iterable[Rule], results: Iterable[RuleResult],
                      fields: Dict[str, FieldValue]) -> List[Question]:
    """Belirsiz kalan kuralların karar için sorulabilir ve belgede dolu olmayan alanları (tekrarsız, kural sırasıyla)."""
    by_id = {r.id: r for r in rules}
    out, seen = [], set()
    for res in results:
        if res.rule_id not in by_id:
            continue
        # Müfettişin cevapladığı kural karara bağlansa da soru kalır: cevap değiştirilebilsin.
        answered = any(getattr(fields.get(n), "source", None) == INSPECTOR for n in _rule_fields(by_id[res.rule_id]))
        if res.status != UNKNOWN and not answered:
            continue
        for name in _rule_fields(by_id[res.rule_id]):
            fv = fields.get(name)
            # Müfettişin önceki cevabı soruyu kaldırmaz (değiştirebilsin); belgede dolu olan alan sorulmaz.
            q = question(name)
            if name in seen or q is None or (fv is not None and fv.state == FILLED and fv.source != INSPECTOR):
                continue
            seen.add(name)
            out.append(q)
    return out


def apply_answers(fields: Dict[str, FieldValue], answers: Dict[str, Optional[str]],
                  note: str = "elle giriş") -> Dict[str, FieldValue]:
    """Cevapları alanlara işler. Değer "" → öğe yok (boş, müfettiş beyanı); None / "bilinmiyor" → değişmez."""
    out = dict(fields)
    for name, value in answers.items():
        if value is None or value == UNKNOWN_ANSWER:
            continue
        if value == "":
            out[name] = FieldValue(EMPTY, None, note, INSPECTOR)
            continue
        q = question(name)
        if q is not None and q.kind == "amount":
            # Müfettiş birimi yazmayabilir ("12.500.000"); okunamayan tutar ham metin kalır → eşik kuralı belirsiz
            value = as_amount(value) or as_amount(value + " TL") or value
        elif q is not None and q.kind == "date":
            value = as_date(value) or value  # okunamayan tarih ham metin kalır → tarih sırası kuralı belirsiz
        out[name] = FieldValue.filled(value, note, INSPECTOR)
    return out


__all__ = ["Question", "QUESTIONS", "question", "pending_questions", "apply_answers", "UNKNOWN_ANSWER"]
