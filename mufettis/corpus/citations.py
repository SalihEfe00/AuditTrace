# -*- coding: utf-8 -*-
"""Mevzuat metinlerindeki atıflar (M6.15.1): hangi kanun, kararname, karar, yönetmelik, tebliğ, genelge, rehber anılıyor.

Korpusun eksiksizliği için atıf kapanışı: korpustaki metinlerin andığı her mevzuat aday olur; aday indirilince onun
atıfları da taranır, yeni aday çıkmayana kadar. Çıkarım iki biçimlidir:

* Numaralı: "5411 sayılı Bankacılık Kanunu", "4/12/2003 tarihli ve 5018 sayılı Kanun", "32 sayılı Karar",
  "1 sayılı Cumhurbaşkanlığı Kararnamesi", "2009/15481 sayılı Bakanlar Kurulu Kararı". Numara belgenin kendi
  numarasıdır; "… sayılı Resmî Gazete" numarası atıf değildir.
* Adlı: "Bankaların Kredi İşlemlerine İlişkin Yönetmelik", "… Hakkında Tebliğ (Sıra No: 5)", "… Rehberi". Satır
  sonlarında kırpılmış adlar (ör. "Süreci Hakkında Yönetmelik") burada ayıklanmaz; resmî katalogdaki tam başlıklara
  sonek eşleşmesiyle çözülür (M6.15.4).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

_U = "A-ZÇĞİÖŞÜÂÎÛ"
_L = "a-zçğıöşüâîû"
# Resmî adlarda büyük harfle başlayan sözcükler arasında küçük harfli bağlaçlar / ekler geçer.
_CONNECT = r"(?:ve|ile|ilişkin|hakkında|dair|ait|için|olarak|bazı|bunlar|en|ya\s+da|veya|ile\s+ilgili)"
_TITLE_WORD = rf"(?:[{_U}][{_U}{_L}'’\-]*|{_CONNECT})"

KIND_OF = (
    (re.compile(r"^Kanun\s+Hükmünde\s+Kararname", re.I), "khk"),
    (re.compile(r"^Cumhurbaşkanlığı\s+Kararname", re.I), "cbk"),
    (re.compile(r"^Cumhurbaşkanı\s+Karar", re.I), "cb_karari"),
    (re.compile(r"^Bakanlar\s+Kurulu\s+Karar", re.I), "bkk"),
    (re.compile(r"^Kurul\s+Karar", re.I), "kurul_karari"),
    (re.compile(r"^Kanun", re.I), "kanun"),
    (re.compile(r"^Karar", re.I), "karar"),
    (re.compile(r"^Genelge", re.I), "genelge"),
)
_NUMBERED_KIND = (r"Kanun\s+Hükmünde\s+Kararname\w*|Cumhurbaşkanlığı\s+Kararname\w*|Cumhurbaşkanı\s+Karar\w*|"
                  r"Bakanlar\s+Kurulu\s+Karar\w*|Kurul\s+Karar\w*|Kanun\w*|Karar\w*|Genelge\w*")
NUMBERED = re.compile(
    rf"(?:(\d{{1,2}}/\d{{1,2}}/\d{{4}})\s+tarihli\s+(?:ve\s+)?)?"
    rf"(\d{{4}}/\d{{1,6}}|\d{{1,5}})\s+sayılı\s+"
    rf"((?:{_TITLE_WORD}[\s,]+){{0,16}}?)({_NUMBERED_KIND})\b")
_NAMED_KIND = r"Yönetmeli[kğ]\w*|Tebli[ğg]\w*|Genelgesi|Rehber\w*|Usul\s+ve\s+Esaslar\w*|Esaslar\w*|Talimat\w*"
NAMED = re.compile(
    rf"((?:[{_U}][{_U}{_L}'’\-]*[\s,]+(?:(?:{_CONNECT})\s+)*){{2,20}}?)({_NAMED_KIND})\b"
    r"(?:\s*\((?:Sıra\s+No|Seri\s+No|Tebliğ\s+No)\s*[:.]?\s*([\w./-]+)\))?")


@dataclass(frozen=True)
class Citation:
    kind: str       # kanun | khk | cbk | cb_karari | bkk | kurul_karari | karar | genelge | yonetmelik | teblig | rehber | esaslar
    number: str     # numaralı atıfta belge numarası, adlıda seri numarası (varsa)
    name: str       # atıftaki ad (numaralıda olmayabilir)
    date: str = ""  # "4/12/2003 tarihli" (varsa)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip(" ,")


def _named_kind(word: str) -> str:
    w = word.lower()
    if w.startswith("yönetmeli"):
        return "yonetmelik"
    if w.startswith("tebli"):
        return "teblig"
    if w.startswith("genelge"):
        return "genelge"
    if w.startswith("rehber"):
        return "rehber"
    if w.startswith("talimat"):
        return "talimat"
    return "esaslar"


def extract_citations(text: str) -> List[Citation]:
    """Metindeki atıflar, metin sırasıyla (tekrarlar dahil)."""
    flat = re.sub(r"\s+", " ", text or "")
    out: List[Citation] = []
    for m in NUMBERED.finditer(flat):
        kind = next(k for pattern, k in KIND_OF if pattern.match(m.group(4)))
        out.append(Citation(kind, m.group(2), _clean(m.group(3)), m.group(1) or ""))
    for m in NAMED.finditer(flat):
        name = _clean(m.group(1))
        # Baştaki bağlaç / tek harf ("Ve", "A") ad değildir.
        name = re.sub(rf"^(?:(?:{_CONNECT})\s+)+", "", name, flags=re.I)
        if len(name.split()) < 2:
            continue
        out.append(Citation(_named_kind(m.group(2)), m.group(3) or "", f"{name} {_clean(m.group(2))}"))
    return out


def citation_key(c: Citation) -> Tuple[str, str]:
    """Birleştirme anahtarı: numaralıda (tür, numara), adlıda (tür, küçük harfli ad + seri numarası)."""
    if c.kind in {"kanun", "khk", "cbk", "cb_karari", "bkk", "kurul_karari", "karar"} and c.number:
        return c.kind, c.number
    return c.kind, (turkish_lower(c.name) + (f" #{c.number}" if c.number else ""))


def turkish_lower(text: str) -> str:
    return (text or "").replace("I", "ı").replace("İ", "i").lower()


def tally(documents: Iterable[Tuple[str, str]]) -> Dict[Tuple[str, str], Dict[str, object]]:
    """documents: (belge adı, metin). Anahtar → {"count", "documents" (set), "names" (Counter), "sample"}."""
    out: Dict[Tuple[str, str], Dict[str, object]] = {}
    for title, text in documents:
        for c in extract_citations(text):
            row = out.setdefault(citation_key(c), {"count": 0, "documents": set(), "names": Counter(), "sample": c})
            row["count"] = int(row["count"]) + 1
            row["documents"].add(title)  # type: ignore[union-attr]
            if c.name:
                row["names"][c.name] += 1  # type: ignore[index]
    return out


__all__ = ["Citation", "extract_citations", "citation_key", "tally", "turkish_lower", "NUMBERED", "NAMED"]
