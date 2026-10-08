# -*- coding: utf-8 -*-
"""M16.2 AuditTrace görsel kimliği: renkler, yazı tipleri ve Streamlit teması tek yerde.

Fikir "kalem izi": müfettişin çalışma kâğıdına attığı tik işareti ve ona varan iz. Kâğıt zemin, mavi-siyah mürekkep,
yeşil kalem (birincil işlem), kırmızı kalem yalnızca bulgu ve amblemdeki iz için. Yazı tipleri Streamlit'le gelen
açık lisanslı (SIL OFL) Source ailesi: başlıklar Source Serif, metin Source Sans, sayılar ve madde atıfları Source Code
Pro; internetsiz çalışır, ayrıca indirme yok. Paket başlatıcısı temayı bayrak olarak verir (`theme_flags`), kaynaktan
çalıştırmada `.streamlit/config.toml` aynı değerleri taşır (tests/test_theme.py ikisini eşit tutar).
"""
from __future__ import annotations

from mufettis.brand import (  # noqa: F401 (BRAND_DIR, RED_PENCIL dışarıya da)
    BRAND_DIR, GREEN_INK, INK, INK_MUTED, LINE, PAPER, PAPER_2, RED_PENCIL, STATUS,
)


def theme_flags() -> dict:
    """Streamlit tema ayarları, `bootstrap.load_config_options` bayrak biçiminde (bölüm_ayar)."""
    return {
        "theme_base": "light",
        "theme_primaryColor": GREEN_INK,
        "theme_backgroundColor": PAPER,
        "theme_secondaryBackgroundColor": PAPER_2,
        "theme_textColor": INK,
        "theme_linkColor": GREEN_INK,
        "theme_borderColor": LINE,
        "theme_dataframeBorderColor": LINE,
        "theme_dataframeHeaderBackgroundColor": PAPER_2,
        "theme_showWidgetBorder": True,
        "theme_baseRadius": "6px",
        "theme_buttonRadius": "6px",
        "theme_font": "sans-serif",
        "theme_headingFont": "serif",
        "theme_codeFont": "monospace",
        "theme_redColor": STATUS["bulgu"][2],
        "theme_redBackgroundColor": STATUS["bulgu"][1],
        "theme_redTextColor": STATUS["bulgu"][0],
        "theme_orangeColor": STATUS["karar_verilemedi"][2],
        "theme_orangeBackgroundColor": STATUS["karar_verilemedi"][1],
        "theme_orangeTextColor": STATUS["karar_verilemedi"][0],
        "theme_yellowColor": STATUS["karar_verilemedi"][2],
        "theme_yellowBackgroundColor": STATUS["karar_verilemedi"][1],
        "theme_yellowTextColor": STATUS["karar_verilemedi"][0],
        "theme_greenColor": STATUS["uygun"][2],
        "theme_greenBackgroundColor": STATUS["uygun"][1],
        "theme_greenTextColor": STATUS["uygun"][0],
        "theme_blueColor": GREEN_INK,
        "theme_blueBackgroundColor": "#E4ECEA",
        "theme_blueTextColor": "#1A4F47",
        "theme_grayColor": STATUS["yapilamayan"][2],
        "theme_grayBackgroundColor": STATUS["yapilamayan"][1],
        "theme_grayTextColor": STATUS["yapilamayan"][0],
    }


def contrast(fg: str, bg: str) -> float:
    """WCAG göreli parlaklık karşıtlığı."""
    def lum(hex_color: str) -> float:
        rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def mark_svg(size: int = 40) -> str:
    """Amblem (çalışma kâğıdı, iz ve tik) satır içi SVG olarak; dosya `brand/mark.svg` ile aynı çizim."""
    body = (BRAND_DIR / "mark.svg").read_text(encoding="utf-8")
    return body.replace('width="64" height="64"', f'width="{size}" height="{size}" aria-hidden="true"', 1)


def header_html(subtitle: str) -> str:
    """Sayfa başı: amblem + AuditTrace yazısı + işin tek cümlesi (st.title'ın yerine)."""
    import html

    return (
        '<div class="at-header">' + mark_svg(44)
        + '<div><div class="at-wordmark">Audit<i>Trace</i></div>'
        f'<div class="at-sub">{html.escape(subtitle)}</div></div></div>'
    )


def status_style(kind: str) -> str:
    """Durum kutusu ve kartı için satır içi stil (sol kenar şeridi, zemin, metin rengi)."""
    text, bg, edge = STATUS[kind]
    return f"background:{bg};border-left:3px solid {edge};color:{text}"


# Streamlit bileşenlerine kimlik katmanı. Seçiciler Streamlit 1.64'ün data-testid adları; sürüm yükseltmesinde
# ekran görüntüsüyle denetlenir (M16.3).
BRAND_CSS = f"""<style>
[data-testid="stDecoration"] {{display:none;}}
header[data-testid="stHeader"] {{background:transparent;}}
.block-container {{padding-top:2.2rem; max-width:1180px;}}
.at-header {{display:flex; align-items:center; gap:14px; padding:4px 0 18px; margin-bottom:6px;
  border-bottom:1px solid {LINE};}}
.at-header svg {{flex:none;}}
.at-wordmark {{font-family:"Source Serif", Georgia, serif; font-weight:600; font-size:30px; line-height:1;
  letter-spacing:-.01em; color:{INK};}}
.at-wordmark i {{font-weight:500; color:{GREEN_INK};}}
.at-sub {{font-size:14px; color:{INK_MUTED}; margin-top:6px;}}
h1, h2, h3, h4 {{letter-spacing:-.005em;}}
[data-testid="stTabs"] [role="tab"] p {{font-size:15px;}}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p {{color:{INK}; font-weight:600;}}
[data-testid="stMarkdownContainer"] table {{border-collapse:collapse; font-size:14px;}}
[data-testid="stMarkdownContainer"] th {{background:{PAPER_2}; font-weight:600; text-align:left;}}
[data-testid="stMarkdownContainer"] td, [data-testid="stMarkdownContainer"] th {{border-color:{LINE};
  vertical-align:top;}}
[data-testid="stMarkdownContainer"] code {{color:{INK_MUTED}; background:{PAPER_2}; font-size:.85em;}}
[data-testid="stMarkdownContainer"] mark {{background:#F3DDA8; color:{INK}; padding:0 2px;}}
details > summary {{color:{GREEN_INK};}}
[class*="st-key-at-finding"] {{border-left:3px solid {STATUS["bulgu"][2]} !important; background:#FFFDFA;}}
[class*="st-key-at-manual"] {{border-left:3px solid {STATUS["karar_verilemedi"][2]} !important; background:#FFFDFA;}}
.at-tiles {{display:flex; gap:10px; flex-wrap:wrap; margin:4px 0 14px;}}
.at-tile {{flex:1; min-width:140px; padding:10px 14px; border-radius:4px;}}
.at-tile .n {{font-family:"Source Serif", Georgia, serif; font-size:28px; font-weight:600; line-height:1.15;}}
.at-tile .l {{font-size:13px;}}
.at-mark {{font-weight:700; display:inline-block; width:1.1em; text-align:center;}}
section[data-testid="stSidebar"] {{border-right:1px solid {LINE};}}
</style>"""
