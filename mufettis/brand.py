# -*- coding: utf-8 -*-
"""M16.2 AuditTrace kimliğinin renkleri ve amblem dosyaları — arayüz (`mufettis.ui.theme`) ve Word raporları
(`mufettis.exports.brand_docx`) ortak kullanır; raporlama arayüzü içe aktarmaz (katman kuralı)."""
from __future__ import annotations

from pathlib import Path

BRAND_DIR = Path(__file__).resolve().parent / "ui" / "brand"

PAPER = "#F7F5F0"          # zemin
PAPER_2 = "#EFEBE3"        # ikincil zemin (kenar çubuğu, giriş kutuları, tablo başlığı)
LINE = "#DDD6C8"           # çizgi ve kenarlık
INK = "#1E2A32"            # metin, amblem
INK_MUTED = "#5B6770"      # ikincil metin
GREEN_INK = "#1F5E55"      # birincil işlem, bağlantı, "Trace"
RED_PENCIL = "#E0654A"     # amblemdeki iz

# Kural sonucu durumları: (metin, zemin, kenar). Metin/zemin karşıtlığı WCAG AA (4,5:1) üstünde (test edilir).
STATUS = {
    "bulgu": ("#8E2B21", "#F6E5E1", "#B5382B"),
    "karar_verilemedi": ("#6E4C10", "#F5ECD8", "#A3741E"),
    "uygun": ("#245640", "#E3EEE6", "#2F6B52"),
    "yapilamayan": ("#434C54", "#EAE8E3", "#7A8289"),
}
