# -*- coding: utf-8 -*-
"""Şube belge türlerinin kanonik etiket kümesi (docs/product/domain_scope.md Bölüm 2, M4.1).

Belge türü benchmark'ı (mufettis/analysis/document_benchmark.py) beklenen ve bulunan türü bu kodlarla
karşılaştırır; M5'in sınıflandırıcısı da aynı kümeyi üretir. Motorun bugünkü üç türü (models.DocumentType)
ENGINE_TYPE_MAP ile bu kodlara eşlenir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from mufettis.analysis.turkish_text import turkish_lower

# Bölüm 2'deki gruplar
GROUPS: Dict[str, str] = {
    "kredi": "Kredi dosyası",
    "hesap_musteri": "Hesap / müşteri",
    "nakit_kiymetli": "Nakit ve kıymetli",
    "islem": "İşlem kayıtları",
    "tuketici": "Tüketici",
    "kurum_ici": "Kurum içi",
    "resmi": "Resmî yazışma",
    "personel": "Personel",
}

UNKNOWN = "bilinmiyor"


@dataclass(frozen=True)
class DocumentTypeLabel:
    code: str
    name: str
    group: str
    brief_task: str = ""          # benchmarks/synthetic/BRIEF.md görev kodu
    keywords: Tuple[str, ...] = ()  # serbest tür adını (D01 başlıkları) koda çevirmek için; küçük harf


LABELS: Tuple[DocumentTypeLabel, ...] = (
    DocumentTypeLabel("kredi_teklif_onay", "Kredi teklif ve onay belgesi", "kredi", "T01",
                      ("kredi teklif", "teklif ve onay", "kredi onay", "komite karar", "kredi tahsis")),
    DocumentTypeLabel("teminat", "Teminat belgesi (kefalet, ipotek, rehin)", "kredi", "T02",
                      ("teminat", "kefalet", "ipotek", "rehin")),
    DocumentTypeLabel("kredi_kullandirim", "Kredi kullandırım dosyası", "kredi", "T03",
                      ("kullandırım", "kullandirim")),
    DocumentTypeLabel("mali_tablo", "Mali tablo / mizan özeti", "kredi", "",
                      ("mali tablo", "bilanço", "mizan", "gelir tablosu")),
    DocumentTypeLabel("kkb_istihbarat", "KKB / istihbarat raporu", "kredi", "",
                      ("kkb", "findeks", "istihbarat", "risk raporu")),
    DocumentTypeLabel("hesap_acilis_kyc", "Hesap açılış ve müşteri tanıma", "hesap_musteri", "T04",
                      ("hesap açılış", "müşteri tanıma", "müşteri bilgi formu", "imza sirküleri", "vekaletname")),
    DocumentTypeLabel("kasa_sayim", "Kasa / ATM / kıymetli evrak sayım tutanağı", "nakit_kiymetli", "T05",
                      ("sayım", "kasa", "atm dolum", "kıymetli evrak")),
    DocumentTypeLabel("islem_dekontu", "Nakit / havale / EFT / döviz işlem dekontu", "islem", "T06",
                      ("dekont", "havale", "eft", "döviz alım", "döviz satım", "talimat formu")),
    DocumentTypeLabel("islem_listesi", "Hesap hareketleri listesi", "islem", "T12",
                      ("hesap hareket", "işlem listesi", "ekstre")),
    DocumentTypeLabel("musteri_sikayeti", "Müşteri şikâyeti ve banka cevabı", "tuketici", "T07",
                      ("şikayet", "şikâyet", "itiraz")),
    DocumentTypeLabel("ucret_tahsilat", "Ücret / komisyon tahsilatı", "tuketici", "T08",
                      ("ücret", "komisyon", "masraf")),
    DocumentTypeLabel("resmi_yazi", "Resmî kurum yazısı ve cevabı", "resmi", "T09",
                      ("haciz", "icra", "mahkeme", "masak", "bddk", "vergi dairesi", "bilgi talebi")),
    DocumentTypeLabel("ic_yazisma", "İç yazışma / talimat / yetki devri", "kurum_ici", "T10",
                      ("iç yazışma", "talimat", "genelge", "yetki devri", "yetki tablosu")),
    DocumentTypeLabel("denetim_raporu", "Teftiş / iç kontrol raporu bulgusu", "kurum_ici", "T11",
                      ("teftiş", "iç kontrol", "denetim rapor", "bulgu")),
    DocumentTypeLabel("personel", "Personel belgesi (görevlendirme, izin, rotasyon, disiplin)", "personel", "",
                      ("görevlendirme", "rotasyon", "disiplin", "izin")),
)

BY_CODE: Dict[str, DocumentTypeLabel] = {label.code: label for label in LABELS}
BY_TASK: Dict[str, DocumentTypeLabel] = {label.brief_task: label for label in LABELS if label.brief_task}
CODES = frozenset(BY_CODE) | {UNKNOWN}

# Motorun bugünkü türleri (models.DocumentType değerleri) → kanonik kod. M5 motoru doğrudan kod üretince
# bu eşleme kanonik kodları olduğu gibi geçirir.
ENGINE_TYPE_MAP: Dict[str, str] = {
    "complaint": "musteri_sikayeti",
    "internal_correspondence": "ic_yazisma",
    "unknown": UNKNOWN,
}


def from_engine(document_type: str) -> str:
    """Motorun ürettiği tür → kanonik kod (tanınmayan değer bilinmiyor)."""
    value = str(document_type or "").strip()
    if value in CODES:
        return value
    return ENGINE_TYPE_MAP.get(value, UNKNOWN)


def from_task(task_code: str) -> Optional[str]:
    """BRIEF görev kodu (T01, t1, T01_003) → kanonik kod; D01 ve bilinmeyen görev için None."""
    match = re.match(r"^[Tt]0*(\d{1,2})", str(task_code or "").strip())
    if not match:
        return None
    label = BY_TASK.get(f"T{int(match.group(1)):02d}")
    return label.code if label else None


def _starts_word(keyword: str, text: str) -> bool:
    """Anahtar kelime bir kelimenin başında mı ("eft" "defter" içinde sayılmaz; "teminatı" teminat sayılır)."""
    return re.search(r"(?<![0-9a-zçğıöşüâîû])" + re.escape(keyword), text) is not None


def from_free_text(name: str) -> Optional[str]:
    """Serbest tür adı ("Kefalet Sözleşmesi", "KKB Raporu") → kanonik kod. Anahtar kelimesi en uzun eşleşen
    etiket kazanır ("kredi kullandırım talimatı" kullandırım, "kredi teklif" teklif olur); eşleşme yoksa None."""
    text = turkish_lower(str(name or ""))
    best: Optional[Tuple[int, str]] = None
    for label in LABELS:
        for keyword in label.keywords:
            if _starts_word(keyword, text) and (best is None or len(keyword) > best[0]):
                best = (len(keyword), label.code)
    return best[1] if best else None


__all__ = [
    "GROUPS", "UNKNOWN", "DocumentTypeLabel", "LABELS", "BY_CODE", "BY_TASK", "CODES", "ENGINE_TYPE_MAP",
    "from_engine", "from_task", "from_free_text",
]
