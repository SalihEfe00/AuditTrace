# -*- coding: utf-8 -*-
"""Kefalet dışı teminat belgelerinin alanları (M9.3, blok 2): ipotek, taşınır rehni, ekspertiz raporu, DASK
poliçesi, senet / genel kredi sözleşmesi. Ortak üç durumlu etiket çıkarıcıyla (mufettis.rules.fields).
"""
from __future__ import annotations

from typing import Dict, List

from mufettis.rules.engine import FieldValue
from mufettis.rules.fields import as_amount, as_date, extract_labeled_fields, one_of

DOCUMENT_LABELS: Dict[str, Dict[str, List[str]]] = {
    "ipotek": {
        "malik": [r"(?:taşınmaz )?malik(?:i)?", r"ipotek veren"],
        "tasinmaz": [r"taşınmaz(?:ın)?(?: bilgileri| niteliği)?", r"ada ?/ ?parsel", r"ada parsel"],
        "ipotek_tutari": [r"ipotek (?:tutarı|bedeli|miktarı)"],
        "derece": [r"(?:ipotek )?derece(?:si)?(?: ?/ ?sıra)?"],
        "tescil_tarihi": [r"(?:tescil|yevmiye) tarihi"],
        "yevmiye_no": [r"yevmiye (?:no|numarası)"],
        "alacakli": [r"alacaklı(?: banka)?", r"ipotek alacaklısı"],
    },
    "rehin": {
        "rehin_veren": [r"rehin veren"],
        "rehin_konusu": [r"rehin(?:in)? konusu(?: taşınır)?", r"rehinli (?:taşınır|varlık)"],
        "rehin_tutari": [r"rehin (?:tutarı|bedeli|miktarı)", r"azam[iî] (?:rehin )?tutar[ıi]?"],
        "sicil_tescil_tarihi": [r"(?:sicil )?tescil tarihi", r"rehinli taşınır sicili kayıt tarihi"],
        "sicil_no": [r"(?:sicil|kayıt) (?:no|numarası)"],
    },
    "ekspertiz": {
        "rapor_tarihi": [r"(?:rapor|değerleme|ekspertiz) tarihi"],
        "deger": [r"(?:piyasa |tespit edilen )?değer(?:i)?", r"değerleme sonucu"],
        "degerleme_kurulusu": [r"değerleme (?:kuruluşu|şirketi)", r"ekspertiz (?:firması|şirketi)"],
        "eksper": [r"(?:sorumlu )?(?:değerleme uzmanı|eksper)"],
        "lisans_no": [r"lisans (?:no|numarası)"],
    },
    "dask": {
        "police_no": [r"poliçe (?:no|numarası)"],
        "baslangic_tarihi": [r"(?:poliçe )?başlangıç tarihi"],
        "bitis_tarihi": [r"(?:poliçe )?bitiş tarihi", r"vade (?:sonu|bitiş)"],
        "sigortali": [r"sigortalı(?: adı soyadı)?"],
        "dain_murtehin": [r"da[iı]n[- ]?i? ?mürtehin", r"rehin alacaklısı"],
    },
    "senet_gks": {
        "borclu": [r"borçlu(?: adı soyadı| unvanı)?", r"kredi kullanan"],
        "tutar": [r"(?:kredi |senet )?(?:tutarı|bedeli|limiti)"],
        "duzenleme_tarihi": [r"(?:düzenleme|sözleşme|tanzim) tarihi"],
        "vade_tarihi": [r"vade(?: tarihi)?"],
        "imza": [r"(?:borçlu )?imza(?:sı)?"],
    },
}

_DATES = {"tescil_tarihi", "sicil_tescil_tarihi", "rapor_tarihi", "baslangic_tarihi", "bitis_tarihi",
          "duzenleme_tarihi", "vade_tarihi"}
_AMOUNTS = {"ipotek_tutari", "rehin_tutari", "deger", "tutar"}
_CONVERT = {f: as_date for f in _DATES}
_CONVERT.update({f: as_amount for f in _AMOUNTS})
_CONVERT["derece"] = one_of([(r"\b1\b|birinci", "1"), (r"\b2\b|ikinci", "2"), (r"\b3\b|üçüncü", "3")])


def extract_teminat_fields(document_type: str, text: str) -> Dict[str, FieldValue]:
    """Belge türünün alanları; bilinmeyen tür → ValueError."""
    labels = DOCUMENT_LABELS.get(document_type)
    if labels is None:
        raise ValueError(f"bilinmeyen teminat belgesi türü: {document_type}")
    return extract_labeled_fields(text, labels, {k: v for k, v in _CONVERT.items() if k in labels})


__all__ = ["extract_teminat_fields", "DOCUMENT_LABELS"]
