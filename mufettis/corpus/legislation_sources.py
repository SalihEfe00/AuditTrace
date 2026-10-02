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
# Yönetmelik / tebliğ / kararların PDF'i yok; güncel (değişiklikler işlenmiş) resmî metin bu sayfada HTML olarak.
MEVZUAT_HTML_URL = ("https://www.mevzuat.gov.tr/anasayfa/MevzuatFihristDetayIframe"
                    "?MevzuatTur={tur}&MevzuatNo={no}&MevzuatTertip={tertip}")
OFFICIAL_HOSTS = frozenset({
    "www.mevzuat.gov.tr", "mevzuat.gov.tr",
    "www.bddk.org.tr", "bddk.org.tr", "www.bddk.gov.tr", "bddk.gov.tr",
    "www.tcmb.gov.tr", "tcmb.gov.tr",
    "masak.hmb.gov.tr", "ms.hmb.gov.tr",  # MASAK sayfaları ve Bakanlığın dosya sunucusu
    "www.resmigazete.gov.tr", "resmigazete.gov.tr",
    # M6.15: düzenlemeyi kendisi çıkaran diğer kurumlar (kanunla verilen düzenleme yetkisi)
    "www.kvkk.gov.tr", "kvkk.gov.tr",
    "www.tmsf.org.tr", "tmsf.org.tr",
    "www.seddk.gov.tr", "seddk.gov.tr",
    "www.tbb.org.tr", "tbb.org.tr",      # 5411 md. 79-80: kamu kurumu niteliğinde meslek kuruluşu
    "tkbb.org.tr", "www.tkbb.org.tr",    # 5411 md. 79-80: Katılım Bankaları Birliği
})
# M6.15: kurumların kendi düzenleme türleri eklendi (talimat, standart, Kurul kararı, duyuru, tip sözleşme);
# tüzük ve kararname (KHK / CBK) mevzuat.gov.tr türleridir.
# "emsal karar": bir kurulun tek olaya ilişkin kararı (KVKK Kurul karar özeti); genel kural koymaz, yorumu gösterir.
# "yargı kararı" (K2b): RG'de yayımlanan Yargıtay / Danıştay kararı (içtihadı birleştirme, daire kararı); kanunun
# yorumunu gösterir, bulgunun dayanağı yine kanundur.
INSTRUMENTS = ("kanun", "kararname", "tüzük", "yönetmelik", "tebliğ", "karar", "kurul kararı", "emsal karar", "yargı kararı", "genelge",
               "talimat", "rehber", "standart", "tip sözleşme", "duyuru")
GROUPS = ("masak", "borclar_medeni", "bddk", "tuketici", "odeme", "cek_ticaret_icra", "kambiyo", "kvkk_sir", "vergi",
          "sigorta_emeklilik", "sermaye_piyasasi", "katilim", "mevduat_sigortasi", "meslek_birligi", "sosyal_guvenlik",
          "genel")
# 1 = öncelikli set, 2 = tam set (M6), 3 = kapsamlı envanterin geniş katmanı (M6.15: içerikte banka hükmü geçenler)
PRIORITIES = (1, 2, 3)
_CODE_RE = re.compile(r"^\d{1,2}\.\d\.\d+$")
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


@dataclass(frozen=True)
class LegislationSource:
    code: str            # mevzuat.gov.tr kodu; yoksa boş ve url dolu
    title: str           # resmî ad (kaynaktaki yazımıyla)
    instrument: str      # INSTRUMENTS
    number: str          # kanun no, tebliğ sıra no, karar sayısı; yönetmeliklerde çoğunlukla boş
    issuer: str
    priority: int        # PRIORITIES
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

    @property
    def html_url(self) -> str:
        """mevzuat.gov.tr metin sayfası (PDF yoksa); kodsuz kayıtta boş."""
        if not self.code:
            return ""
        tur, tertip, no = self.code.split(".")
        return MEVZUAT_HTML_URL.format(tur=tur, no=no, tertip=tertip)

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
        if self.priority not in PRIORITIES:
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
           "OFFICIAL_HOSTS", "INSTRUMENTS", "GROUPS", "PRIORITIES"]
