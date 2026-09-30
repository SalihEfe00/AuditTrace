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

# Belge türü sınıflandırıcısının işaretleri (M5.6; analysis/branch_classifier.py). Genel banka şubesi dilinden
# derlenmiştir, tek bir örneğe özgü ifade içermez; küçük harf, kelime başında aranır. "title": belgenin ilk
# satırlarındaki başlık ifadeleri (güçlü işaret), "body": form alanları ve tipik ifadeler (zayıf işaret).
SIGNALS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "kredi_teklif_onay": {
        "title": ("kredi teklif", "teklif ve onay", "kredi tahsis", "kredi onay formu", "kredi talep ve değerlendirme",
                  "limit teklif", "kredi komitesi kararı", "tahsis talebi"),
        "body": ("talep edilen limit", "teklif edilen limit", "limit", "vade", "faiz oranı", "kredi komitesi",
                 "bölge kredi komitesi", "genel müdürlük kredi", "yetki seviyesi", "istihbarat", "şube görüşü",
                 "teminat", "mali analiz", "risk durumu", "kredi türü", "kullanım amacı"),
    },
    "teminat": {
        "title": ("kefalet sözleşmesi", "kefaletname", "ipotek", "rehin sözleşmesi", "rehin belgesi",
                  "teminat mektubu", "araç rehni", "ticari işletme rehni", "alacağın temliki", "temlikname"),
        "body": ("kefil", "müteselsil kefil", "eş rızası", "azami sorumluluk", "kefalet tutarı", "kefalet tarihi",
                 "el yazısı", "rehin veren", "ipotek veren", "derece", "tapu", "ada", "parsel", "dain-i mürtehin",
                 "asıl borçlu", "medeni hali"),
    },
    "kredi_kullandirim": {
        "title": ("kullandırım", "kredi kullandırım", "kullandırım talimatı", "kullandırım dosyası",
                  "kredi açılış"),
        "body": ("kullandırım tarihi", "kullandırılan tutar", "kullandırım hesabı", "teminat tesis", "tescil tarihi",
                 "sözleşme tarihi", "dosya masrafı", "ödeme planı", "taksit", "alacaklandırılan hesap"),
    },
    "mali_tablo": {
        "title": ("mali tablo", "bilanço", "gelir tablosu", "mizan", "mali durum", "finansal tablo"),
        "body": ("net satışlar", "net satış", "net kâr", "net kar", "özkaynak", "aktif toplamı", "dönen varlıklar",
                 "duran varlıklar", "kısa vadeli yabancı kaynak", "uzun vadeli yabancı kaynak", "brüt kâr", "favök",
                 "banka kredileri", "ciro"),
    },
    "kkb_istihbarat": {
        "title": ("kkb", "findeks", "istihbarat raporu", "risk raporu", "kredi raporu", "memzuç", "piyasa istihbaratı"),
        "body": ("kredi notu", "sorgu tarihi", "gecikmedeki risk", "toplam nakdi risk", "limit artışı",
                 "karşılıksız çek", "protestolu senet", "takipteki", "risk merkezi", "istihbarat"),
    },
    "hesap_acilis_kyc": {
        "title": ("hesap açılış", "müşteri bilgi formu", "müşteri tanıma", "imza sirküleri", "vekaletname",
                  "gerçek kişi müşteri", "tüzel kişi müşteri", "hesap açma"),
        "body": ("kimlik tespiti", "kimlik doğrulama", "kps", "adres teyit", "yerleşim yeri", "kendi adına",
                 "siyasi nüfuz", "gerçek faydalanıcı", "meslek", "gelir bilgisi", "hesap açılış amacı",
                 "beklenen işlem hacmi", "ticaret sicil", "kimlik belgesi", "uyruk", "müşteri no"),
    },
    "kasa_sayim": {
        "title": ("sayım tutanağı", "kasa sayım", "atm dolum", "kıymetli evrak sayım", "kasa tutanağı",
                  "gün sonu sayım"),
        "body": ("sayılan toplam", "sistem bakiyesi", "kasa mevcudu", "banknot", "madeni para", "fark",
                 "kasa anahtarı", "çift anahtar", "sayımı yapan", "nezaret", "kupür", "boş çek karnesi"),
    },
    "islem_dekontu": {
        "title": ("dekont", "havale talimatı", "eft talimatı", "döviz alım", "döviz satım", "nakit yatırma",
                  "nakit çekme", "virman", "para transferi"),
        "body": ("dekont no", "işlem tarihi", "gişe", "yatıran", "yatırılan tutar", "alıcı hesap", "gönderen",
                 "işlemi yapan", "kendisi adına", "başkası adına", "kur", "açıklama", "işlem saati"),
    },
    "islem_listesi": {
        "title": ("hesap hareketleri", "hesap ekstresi", "işlem listesi", "hesap dökümü", "hareket dökümü"),
        "body": ("bakiye", "karşı taraf", "kanal", "yön", "gelen", "giden", "atm", "fast", "eft", "havale"),
    },
    "musteri_sikayeti": {
        "title": ("şikayet", "şikâyet", "itiraz", "başvurunuz", "dilekçe"),
        "body": ("talep ediyorum", "iadesini", "mağdur", "bilgim dışında", "şikayetçiyim", "başvurunuz incelenmiştir",
                 "müşteri ilişkileri", "tüketici hakem", "iade talebi"),
    },
    "ucret_tahsilat": {
        "title": ("ücret", "komisyon", "masraf dökümü", "tahsilat dökümü", "ücret tarifesi"),
        "body": ("tarife", "bsmv", "yıllık üyelik", "hesap işletim", "tahsis ücreti", "ekspertiz ücreti",
                 "dosya masrafı", "komisyon", "sözleşme öncesi bilgi formu", "ücret"),
    },
    "resmi_yazi": {
        "title": ("icra dairesi", "haciz ihbarnamesi", "haciz yazısı", "mahkemesi", "masak", "bddk",
                  "vergi dairesi", "cumhuriyet başsavcılığı", "bilgi talebi", "müzekkere", "t.c."),
        "body": ("esas", "dosya no", "iik", "haciz", "ihbarname", "tebliğ", "müdürlüğü'ne", "müdürlüğüne",
                 "arz ederiz", "bloke", "takip tutarı", "borçlu", "alacaklı", "icra müdürü", "uyap"),
    },
    "ic_yazisma": {
        "title": ("iç yazışma", "talimat", "genelge", "yetki devri", "duyuru", "bilgi notu", "iç yazı"),
        "body": ("kimden", "kime", "konu", "bilgi", "yetki devri", "devredilmiştir", "genel müdürlük",
                 "bölge müdürlüğü", "iyi çalışmalar", "bilgilerinize", "ekte", "izin", "talimat"),
    },
    "denetim_raporu": {
        "title": ("teftiş", "iç kontrol", "denetim raporu", "bulgu takip", "teftiş raporu", "iç denetim"),
        "body": ("bulgu", "bulgu no", "risk derecesi", "öneri", "aksiyon", "aksiyon tarihi", "kapanış",
                 "kapatıldı", "sorumlu", "şube cevabı", "teftiş dönemi", "müfettiş"),
    },
    "personel": {
        "title": ("görevlendirme", "personel", "izin formu", "rotasyon", "disiplin", "atama"),
        "body": ("sicil no", "görev yeri", "unvan", "izin başlangıç", "izin bitiş", "disiplin kurulu",
                 "savunma", "görevlendirilmiştir", "rotasyon", "insan kaynakları"),
    },
}

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
    "GROUPS", "UNKNOWN", "DocumentTypeLabel", "LABELS", "SIGNALS", "BY_CODE", "BY_TASK", "CODES", "ENGINE_TYPE_MAP",
    "from_engine", "from_task", "from_free_text",
]
