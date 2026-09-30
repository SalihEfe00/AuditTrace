# -*- coding: utf-8 -*-
"""Belgenin kendisinin atıf yaptığı mevzuat ve bu mevzuatın yerel veritabanında olup olmadığı.

Denetçi için "bulgu yok" sonucunun en sık nedeni, belgenin dayandığı düzenlemenin veritabanında
bulunmamasıdır (ör. şikâyet "Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği"ne dayanıyor ama
yönetmelik yüklenmemiş). Bu modül atıfları kurala dayalı olarak çıkarır ve veritabanı kataloğuyla
eşleştirir; dil modeli kullanılmaz, aynı girdi her zaman aynı sonucu verir.

Durumlar:
* ``in_corpus``: düzenleme veritabanında var (kanun numarası veya ad kökleriyle eşleşti).
* ``not_in_corpus``: veritabanında yok; iddia bu düzenlemeye dayanıyorsa doğrulanamaz.
* ``internal``: kurum içi düzenleme ("bankamız Kredi Tahsis Yönetmeliği"); mevzuat kapsamı dışında.
* ``not_checked``: katalog yok (örnek kanıtla çalışan analiz).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from mufettis.analysis.turkish_text import stem, tokenize, turkish_lower

IN_CORPUS = "in_corpus"
NOT_IN_CORPUS = "not_in_corpus"
INTERNAL = "internal"
NOT_CHECKED = "not_checked"

_UPPER = "A-ZÇĞİÖŞÜÂÎÛ"
_LOWER = "a-zçğıöşüâîû"
_WORD = rf"[{_UPPER}][{_LOWER}{_UPPER}’'\-]*"
_CONNECTOR = r"(?:ve|ile|veya|ilişkin|hakkında|dair|için|olan|yapılacak)"
# Tür sözcüğü adın sonundadır (Türkçede baş isim sondadır); iyelik/hâl ekleri kabul edilir.
_INSTRUMENT = (
    r"(?:Kanun(?:u|unun|una|unda|a)?|Yönetmeli(?:k|ği|ğin|ğine|ğinde|ğe)|Tebli(?:ğ|ği|ğin|ğine|ğinde|ğe)|"
    r"Genelge(?:si|sinin|sine|sinde)?|Karar(?:ı|ının|ına|ında)?|Rehber(?:i|inin|ine|inde)?|"
    r"Usul\s+ve\s+Esaslar(?:ı|ın|a)?|Yönerge(?:si|sinin|sine|sinde)?|Talimat(?:ı|ının|ına|ında)?)"
)
_NAMED_RE = re.compile(
    rf"(?:(?<=\s)|^)((?:{_WORD}|\d{{3,6}}\s+sayılı)(?:\s+(?:{_WORD}|{_CONNECTOR})){{0,12}}?\s+{_INSTRUMENT})\b"
)
_LAW_NUMBER_RE = re.compile(r"(?<!\d)(\d{3,6})\s*sayılı\b", re.I)
_ARTICLE_RE = re.compile(r"^\W{0,3}(?:(?:[A-Za-zÇĞİÖŞÜçğıöşü]+\s+){0,3})?(?:madde|md\.?)\s*(\d{1,3}(?:/\d{1,2})?)",
                         re.I)
# "54 üncü maddesi", "12. maddesi", "13'üncü maddesinin"
_ARTICLE_BEFORE_RE = re.compile(
    r"(\d{1,3})\s*(?:\.|['’]?\s*(?:inci|ıncı|üncü|uncu|nci|ncı|ncü|ncu))?\s*maddes(?:i|inin|ine|inde)\b", re.I)
# Cümle başında büyük harfle yazılmış sıradan sözcükler adın parçası değildir ("Ayrıca Kişisel ...").
_LEADING_NON_NAME = frozenset({
    "ayrıca", "ancak", "fakat", "bu", "şu", "söz", "konusu", "ilgili", "anılan", "bankanız", "bankamız",
    "bankanın", "şube", "şubeniz", "yine", "ayrıntılı", "sayın", "madde", "mevcut", "yürürlükteki", "yeni",
})
# Atıftaki tür sözcüğü ve katalogdaki karşılığı: kanun atfı yalnızca kanunla, yönetmelik atfı
# yalnızca adında "yönetmelik" geçen belgeyle eşleşir (tek köklü adlar yanlış belgeye gitmesin).
_KIND_RE = re.compile(r"(kanun|yönetmeli|tebli|genelge|karar|rehber|usul\s+ve\s+esas|yönerge|talimat)\w*\s*$")
# "bankamız Kredi Tahsis Yönetmeliği", "kurum içi yönerge": mevzuat değil, kurumun kendi kuralı.
_INTERNAL_MARKER_RE = re.compile(
    r"(?:bankamız|bankamızın|şirketimiz|şirketimizin|kurumumuz|kurumumuzun|kurum\s+içi|banka\s+içi|iç)\s*$", re.I)
_GENERIC_NAME_STEMS = frozenset(stem(w) for w in (
    "kanun", "kanunu", "yönetmelik", "yönetmeliği", "tebliğ", "tebliği", "genelge", "genelgesi", "karar",
    "kararı", "rehber", "rehberi", "usul", "esaslar", "yönerge", "talimat", "hakkında", "ilişkin", "dair",
    "sayılı", "bazı", "değişiklik", "yapılmasına",
))
# Katalogdaki adın atıftaki ad köklerinin en az bu kadarını içermesi gerekir.
MIN_NAME_COVERAGE = 0.75
CONTEXT_CHARS = 40


@dataclass(frozen=True)
class CatalogEntry:
    """Veritabanındaki bir belgenin eşleştirme için gereken kısmı. `names`: eşleştirmede kullanılan
    adlar (beyan edilen ad + dosya başlığı); boşsa `title` kullanılır."""
    title: str
    law_number: str = ""
    is_law: bool = False
    names: str = ""


@dataclass(frozen=True)
class CitedReference:
    label: str
    status: str
    law_number: str = ""
    article: str = ""
    matched_title: str = ""


def _name_stems(text: str) -> frozenset:
    return frozenset(stem(t) for t in tokenize(turkish_lower(text)) if not t.isdigit()) - _GENERIC_NAME_STEMS


def _article_after(text: str, end: int) -> str:
    m = _ARTICLE_RE.match(text[end:end + CONTEXT_CHARS])
    return m.group(1) if m else ""


def extract_citations(text: str) -> List[Dict[str, str]]:
    """Belgedeki atıflar (sırası korunur, aynı atıf bir kez): label, law_number, article, internal."""
    text = str(text or "")
    out: List[Dict[str, str]] = []
    seen = set()
    for m in _NAMED_RE.finditer(text):
        label = re.sub(r"\s+", " ", m.group(1)).strip()
        words = label.split(" ")
        dropped: List[str] = []
        while len(words) > 1 and turkish_lower(words[0]) in _LEADING_NON_NAME:
            dropped.append(words.pop(0))
        label = " ".join(words)
        law = _LAW_NUMBER_RE.search(label)
        law_number = law.group(1) if law else ""
        if law_number:
            label = label[law.start():]
        elif not _name_stems(label):
            continue  # "Yönetmelik", "Kanun" gibi yalnızca tür sözcüğü: atıf değil
        # Cümle başındaki "Bankamız" eşleşmenin içindedir ve addan atılmıştır; bağlama geri eklenir.
        before = " ".join([text[max(0, m.start() - CONTEXT_CHARS):m.start()], *dropped])
        internal = bool(_INTERNAL_MARKER_RE.search(before)) and not law_number
        article = _article_after(text, m.end())
        if not article:
            prev = _ARTICLE_BEFORE_RE.search(text[m.end():m.end() + CONTEXT_CHARS])
            article = prev.group(1) if prev else ""
        key = (law_number or turkish_lower(label), article)
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "law_number": law_number, "article": article, "internal": "1" if internal else ""})
    # Adsız kanun atfı ("5411 sayılı Kanun" yukarıda yakalanır; "Kanun No: 5411" gibi biçimler burada).
    from mufettis.extraction.text_repair import extract_legal_identifiers
    for number in extract_legal_identifiers(text):
        if not any(c["law_number"] == number for c in out):
            out.append({"label": f"{number} sayılı Kanun", "law_number": number, "article": "", "internal": ""})
    return out


def resolve(citation: Dict[str, str], catalog: Optional[Iterable[CatalogEntry]]) -> CitedReference:
    base = dict(label=citation["label"], law_number=citation["law_number"], article=citation["article"])
    if citation.get("internal"):
        return CitedReference(status=INTERNAL, **base)
    if catalog is None:
        return CitedReference(status=NOT_CHECKED, **base)
    entries = list(catalog)
    if citation["law_number"]:
        for entry in entries:
            if entry.is_law and entry.law_number == citation["law_number"]:
                return CitedReference(status=IN_CORPUS, matched_title=entry.title, **base)
        return CitedReference(status=NOT_IN_CORPUS, **base)
    wanted = _name_stems(citation["label"])
    kind = _KIND_RE.search(turkish_lower(citation["label"]))
    kind_word = kind.group(1) if kind else ""
    best, best_score = None, (0.0, 0.0)
    for entry in entries:
        names = entry.names or entry.title
        if kind_word == "kanun" and not entry.is_law:
            continue
        if kind_word and kind_word != "kanun" and not re.search(kind_word, turkish_lower(names)):
            continue
        have = _name_stems(names)
        if not wanted or not have:
            continue
        common = len(wanted & have)
        # Önce atıftaki adın ne kadarının karşılandığı, eşitlikte fazladan kökü az olan ad.
        score = (common / len(wanted), common / len(wanted | have))
        if score > best_score:
            best, best_score = entry, score
    if best is not None and best_score[0] >= MIN_NAME_COVERAGE:
        return CitedReference(status=IN_CORPUS, matched_title=best.title, **base)
    return CitedReference(status=NOT_IN_CORPUS, **base)


def cited_references(text: str, catalog: Optional[Iterable[CatalogEntry]]) -> List[CitedReference]:
    entries = list(catalog) if catalog is not None else None
    return [resolve(c, entries) for c in extract_citations(text)]


def catalog_from_index(items: Dict[str, Dict[str, Any]],
                       display_title: Callable[[Dict[str, Any]], str]) -> List[CatalogEntry]:
    """Yürürlükteki/aktif index kayıtlarından katalog. Ad olarak belgenin beyan ettiği ad ve dosya
    başlığı birlikte kullanılır (biri eksik olabilir)."""
    from mufettis.corpus import INACTIVE_STATUSES, InstrumentType, canonical_view

    out: List[CatalogEntry] = []
    for rec in items.values():
        rec = rec or {}
        if str(rec.get("status", "")) in INACTIVE_STATUSES:
            continue
        names = " ".join(dict.fromkeys(filter(None, (
            str(rec.get("declared_title") or ""), str(rec.get("title") or ""), display_title(rec)))))
        out.append(CatalogEntry(
            title=display_title(rec) or names,
            law_number=str(rec.get("law_number") or ""),
            is_law=canonical_view(rec).instrument_type == InstrumentType.LAW,
            names=names,
        ))
    return out


STATUS_LABELS = {
    IN_CORPUS: "veritabanında var",
    NOT_IN_CORPUS: "veritabanında yok",
    INTERNAL: "kurum içi düzenleme",
    NOT_CHECKED: "kontrol edilmedi",
}


def describe(ref: Any) -> str:
    """Tek satırlık açıklama (arayüz ve rapor ortak): "5464 sayılı ... Md. 26 — veritabanında var (...)"."""
    text = str(getattr(ref, "label", "") or "")
    article = str(getattr(ref, "article", "") or "")
    if article:
        text += f" Md. {article}"
    status = str(getattr(ref, "status", "") or "")
    text += f" — {STATUS_LABELS.get(status, status)}"
    matched = str(getattr(ref, "matched_title", "") or "")
    if status == IN_CORPUS and matched and turkish_lower(matched) != turkish_lower(str(getattr(ref, "label", ""))):
        text += f" (eşleşen belge: {matched})"
    return text


def missing_note(refs: Iterable[Any], has_findings: bool) -> str:
    """Belgenin dayandığı mevzuat veritabanında yoksa denetçiye açıklama; yoksa boş metin."""
    missing = [str(getattr(r, "label", "")) for r in refs if getattr(r, "status", "") == NOT_IN_CORPUS]
    if not missing:
        return ""
    names = ", ".join(missing)
    if has_findings:
        return (f"Belgenin atıf yaptığı şu düzenlemeler veritabanında yok: {names}. Bu düzenlemelere dayanan "
                "iddialar değerlendirilemedi; ilgili metinler Admin sekmesinden yüklenebilir.")
    return (f"Belgenin atıf yaptığı şu düzenlemeler veritabanında yok: {names}. Bulgu üretilmemesinin olası "
            "nedeni budur; ilgili metinler Admin sekmesinden yüklenip analiz tekrarlanabilir.")


__all__ = [
    "CatalogEntry", "CitedReference", "INTERNAL", "IN_CORPUS", "NOT_CHECKED", "NOT_IN_CORPUS", "STATUS_LABELS",
    "catalog_from_index", "cited_references", "describe", "extract_citations", "missing_note", "resolve",
]
