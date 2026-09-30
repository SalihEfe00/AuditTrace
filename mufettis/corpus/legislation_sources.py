# -*- coding: utf-8 -*-
"""Mevzuat listesi (M6.2): korpusa girecek mevzuatın veri dosyası (legislation_sources.json) ve doğrulaması.

Her kayıt resmî bir kaynağa bağlıdır: mevzuat.gov.tr kodu (MevzuatTur.MevzuatTertip.MevzuatNo) ya da izinli
resmî alan adlarından birinde bir adres. Belge kimliği (tür, numara, ad, kurum) bu listeden gelir; yükleme
sırasında başlıktan tahmin edilen kimliğin önüne geçer (M6.4).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

SOURCES_FILE = Path(__file__).with_name("legislation_sources.json")
MEVZUAT_TEXT_URL = "https://www.mevzuat.gov.tr/MevzuatMetin/{code}.pdf"
OFFICIAL_HOSTS = frozenset({
    "www.mevzuat.gov.tr", "mevzuat.gov.tr",
    "www.bddk.org.tr", "bddk.org.tr", "www.bddk.gov.tr", "bddk.gov.tr",
    "www.tcmb.gov.tr", "tcmb.gov.tr",
    "masak.hmb.gov.tr",
    "www.resmigazete.gov.tr", "resmigazete.gov.tr",
})
INSTRUMENTS = ("kanun", "yönetmelik", "tebliğ", "karar", "genelge")
GROUPS = ("masak", "borclar_medeni", "bddk", "tuketici", "odeme", "cek_ticaret_icra", "kambiyo", "kvkk_sir", "vergi")
_CODE_RE = re.compile(r"^\d{1,2}\.\d\.\d+$")
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


@dataclass(frozen=True)
class LegislationSource:
    code: str            # mevzuat.gov.tr kodu; yoksa boş ve url dolu
    title: str           # resmî ad (kaynaktaki yazımıyla)
    instrument: str      # INSTRUMENTS
    number: str          # kanun no, tebliğ sıra no, karar sayısı; yönetmeliklerde çoğunlukla boş
    issuer: str
    priority: int        # 1 = öncelikli set, 2 = tam set
    group: str           # GROUPS
    reason: str
    in_corpus: bool = False
    rg_date: str = ""    # Resmî Gazete tarihi (GG.AA.YYYY)
    url: str = ""        # mevzuat.gov.tr dışındaki resmî kaynak

    @property
    def key(self) -> str:
        return self.code or self.url

    @property
    def download_url(self) -> str:
        return self.url or MEVZUAT_TEXT_URL.format(code=self.code)

    def problems(self) -> List[str]:
        out: List[str] = []
        if not self.code and not self.url:
            out.append("kod ya da adres yok")
        if self.code and not _CODE_RE.match(self.code):
            out.append(f"geçersiz mevzuat.gov.tr kodu: {self.code}")
        if urlparse(self.download_url).hostname not in OFFICIAL_HOSTS:
            out.append(f"resmî olmayan alan adı: {self.download_url}")
        if not urlparse(self.download_url).scheme == "https":
            out.append("https olmayan adres")
        if not self.title.strip():
            out.append("ad boş")
        if self.instrument not in INSTRUMENTS:
            out.append(f"bilinmeyen tür: {self.instrument}")
        if self.instrument == "kanun" and not self.number:
            out.append("kanun numarası yok")
        if self.priority not in (1, 2):
            out.append(f"geçersiz öncelik: {self.priority}")
        if self.group not in GROUPS:
            out.append(f"bilinmeyen grup: {self.group}")
        if not self.reason.strip():
            out.append("gerekçe boş")
        if self.rg_date and not _DATE_RE.match(self.rg_date):
            out.append(f"geçersiz Resmî Gazete tarihi: {self.rg_date}")
        return out


def load_sources(path: Optional[Path] = None) -> List[LegislationSource]:
    data = json.loads(Path(path or SOURCES_FILE).read_text(encoding="utf-8"))
    return [LegislationSource(**entry) for entry in data["entries"]]


def validate_sources(sources: List[LegislationSource]) -> List[Tuple[str, str]]:
    """(kayıt anahtarı, sorun) listesi; boşsa liste geçerli."""
    out = [(s.key, p) for s in sources for p in s.problems()]
    seen = set()
    for s in sources:
        if s.key in seen:
            out.append((s.key, "tekrarlanan kayıt"))
        seen.add(s.key)
    return out


def select_sources(sources: List[LegislationSource], max_priority: int = 2,
                   include_in_corpus: bool = False) -> List[LegislationSource]:
    return [s for s in sources if s.priority <= max_priority and (include_in_corpus or not s.in_corpus)]


__all__ = ["LegislationSource", "load_sources", "validate_sources", "select_sources", "SOURCES_FILE",
           "OFFICIAL_HOSTS", "INSTRUMENTS", "GROUPS"]
