# -*- coding: utf-8 -*-
"""M16.2 AuditTrace kimliğinin renkleri ve amblem dosyaları — arayüz (`mufettis.ui.theme`) ve Word raporları
(`mufettis.exports.brand_docx`) ortak kullanır; raporlama arayüzü içe aktarmaz (katman kuralı)."""
from __future__ import annotations

from pathlib import Path

BRAND_DIR = Path(__file__).resolve().parent / "ui" / "brand"

# M19.2 palet "mürekkep, kırmızı yalnız bulguda": beyaz sayfa, sıcak gri panel, koyu gri mürekkep; kırmızı yalnız
# bulgu durumunda ve amblemdeki izde.
PAPER = "#FFFFFF"          # zemin
PAPER_2 = "#F5F4F1"        # ikincil zemin (kenar çubuğu, giriş kutuları, tablo başlığı)
LINE = "#DCDAD5"           # çizgi ve kenarlık
INK = "#1A1A1A"            # metin, amblem
INK_MUTED = "#5E5C57"      # ikincil metin
PRIMARY = "#1A1A1A"        # birincil işlem, bağlantı, "Trace"
RED_PENCIL = "#C8102E"     # amblemdeki iz (bulgu kırmızısı)

# Kural sonucu durumları: (metin, zemin, kenar). Metin/zemin karşıtlığı WCAG AA (4,5:1) üstünde (test edilir).
STATUS = {
    "bulgu": ("#A30D25", "#FBE6E9", "#C8102E"),
    "karar_verilemedi": ("#6B5300", "#F7F0D6", "#A88600"),
    "uygun": ("#2D5A2D", "#E7F0E4", "#3F7A3F"),
    "yapilamayan": ("#444444", "#EFEEEB", "#8A8A86"),
}
