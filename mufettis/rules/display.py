# -*- coding: utf-8 -*-
"""Sonuçların ekranda ve DOCX çalışma kâğıdında gösterilen sade adları (M15.7).

Yalnız gösterim içindir: motorun sonuç değerleri ("belirsiz", "uygulanmaz"), kanıt kaynağı ("müfettiş beyanı"),
kural kimlikleri ve ölçüm anahtarları değişmez.
"""
from __future__ import annotations

from mufettis.rules.engine import INSPECTOR, NOT_APPLICABLE, UNKNOWN

STATUS_LABELS = {UNKNOWN: "karar verilemedi", NOT_APPLICABLE: "bu dosyaya uygulanmaz"}
INSPECTOR_LABEL = "sizin cevabınız"

# Ekrandaki terimlerin (?) açıklamaları
TERM_HELP = {
    "Bulgu": "Belgede kuralın açıkça karşılanmadığı ya da elle kontrol edilmesi gereken durum.",
    "Karar verilemeyen": "Belge bu kontrol için yetmedi (alan boş ya da okunamadı). İhlal değildir; sorulan bilgiyi "
                         "cevaplayın ya da belgenin aslından kontrol edin.",
    "Uygun": "Kural belgede karşılanmış ya da istisna kapsamında.",
    "Yapılamayan (belge yok)": "Kontrolün gerektirdiği belge dosyada yok; belge dosyaya eklenince kontrol yapılır.",
}


def status_label(status: str) -> str:
    """Motorun sonuç değerinin ekrandaki adı (değişmeyenler olduğu gibi döner)."""
    return STATUS_LABELS.get(status, status)


def plain_text(text: str) -> str:
    """Kanıt / bulgu metnindeki teknik adları sadeleştirir ("müfettiş beyanı" → "sizin cevabınız")."""
    return (text or "").replace(INSPECTOR, INSPECTOR_LABEL)


__all__ = ["STATUS_LABELS", "INSPECTOR_LABEL", "TERM_HELP", "status_label", "plain_text"]
