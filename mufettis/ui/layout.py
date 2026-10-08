# -*- coding: utf-8 -*-
"""M16.5 düzen C: solda simge şeridi + bağlam paneli, sağda sayfa.

Sayfalar eskiden `st.tabs` içindeydi; Streamlit sekmelerin hepsini her çizimde çalıştırır. Burada da dört sayfanın
kodu aynen çalışır, yalnız seçili olan görünür (diğerlerinin kabı CSS ile gizlenir). Böylece sayfa geçişi işlevi
değiştirmez. Kontrol sayfasının parçaları (yükleme, belgeler, olay tarihi, adımlar) panele `slot()` ile yazılır;
kabuk yoksa (testlerde, tek başına çizimde) `slot()` boş bağlam verir ve parçalar eskisi gibi ana alana düşer.
"""
from __future__ import annotations

import contextlib
import html
import threading
from typing import Dict, Optional, Sequence, Tuple

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

from mufettis.brand import PRIMARY, INK, INK_MUTED, LINE, PAPER, PAPER_2, STATUS

# (anahtar, şeritteki ad, Material simgesi, paneldeki başlık, paneldeki açıklama)
PAGES: Tuple[Tuple[str, str, str, str, str], ...] = (
    ("kontrol", "Kontrol", ":material/fact_check:", "Kredi dosyası kontrolü",
     "Belgeleri yükleyin; kurallar mevzuata göre kontrol edilir."),
    ("gecmis", "Geçmiş", ":material/history:", "İnceleme geçmişi",
     "Kaydettiğiniz incelemeler ve kararlarınız."),
    ("mevzuat", "Mevzuat", ":material/search:", "Mevzuat arama",
     "Sorunuzu ya da aradığınız konuyu yazın; ilgili kanun ve yönetmelik maddeleri listelenir."),
    ("admin", "Admin", ":material/settings:", "Admin",
     "Yetki tablosu, ayarlar, mevzuat yönetimi ve tanılama."),
)
PAGE_KEYS = tuple(p[0] for p in PAGES)
DEFAULT_PAGE = "kontrol"

_local = threading.local()


def current_page() -> str:
    page = st.session_state.get("at_page", DEFAULT_PAGE)
    return page if page in PAGE_KEYS else DEFAULT_PAGE


def _go(page: str) -> None:
    st.session_state["at_page"] = page


def set_slots(slots: Optional[Dict[str, object]]) -> None:
    """Bu çizimin panel yuvaları (kabuk kurar; her çizim kendi iş parçacığında çalışır)."""
    _local.slots = slots or {}


def slot(name: str):
    """Panel yuvası varsa onun bağlamı, yoksa boş bağlam (parça ana alana düşer)."""
    box = getattr(_local, "slots", {}).get(name)
    return box if box is not None else contextlib.nullcontext()


def has_slot(name: str) -> bool:
    return getattr(_local, "slots", {}).get(name) is not None


def steps_html(steps: Sequence[Tuple[str, str, str]]) -> str:
    """Adım listesi: (durum, başlık, alt satır); durum "done" (tik), "current" (kırmızı halka) ya da "todo"."""
    rows = []
    for i, (state, title, sub) in enumerate(steps, 1):
        mark = "✓" if state == "done" else str(i)
        rows.append(f'<div class="at-step {state}"><span class="n">{mark}</span><div><div class="t">'
                    f'{html.escape(title)}</div><div class="s">{html.escape(sub)}</div></div></div>')
    return f'<div class="at-steps">{"".join(rows)}</div>'


def show_steps(steps: Sequence[Tuple[str, str, str]]) -> None:
    """Adımları paneldeki yerine yazar (kabuk yoksa hiçbir şey yazmaz)."""
    if has_slot("steps"):
        _local.slots["steps"].markdown(steps_html(steps), unsafe_allow_html=True)


def shell_css(page: str) -> str:
    """Kabuk CSS'i: seçili olmayan sayfa ve panel kapları gizlenir."""
    hidden = ", ".join(f".st-key-at-page-{p}, .st-key-at-panel-{p}" for p in PAGE_KEYS if p != page)
    b, k, u, y = (STATUS[s][2] for s in ("bulgu", "karar_verilemedi", "uygun", "yapilamayan"))
    return f"""<style>
{hidden} {{display:none !important;}}
section[data-testid="stSidebar"] {{width:376px !important; min-width:376px !important; max-width:376px !important;
  background:{PAPER_2};}}
[data-testid="stSidebarHeader"], [data-testid="stSidebarCollapseButton"] {{display:none;}}
[data-testid="stSidebarContent"] {{padding:0 !important; scrollbar-gutter:auto !important;}}
[data-testid="stSidebarUserContent"] {{padding:24px 22px 24px 98px; margin:0 !important; width:100% !important; position:relative;
  min-height:100vh;}}
.st-key-at-rail {{position:absolute; left:0; top:0; bottom:0; width:76px; background:#fff;
  border-right:1px solid {LINE}; padding:18px 8px; align-items:center; gap:6px; z-index:5;}}
.st-key-at-rail .at-rail-mark {{margin-bottom:12px;}}
.st-key-at-rail [data-testid="stElementContainer"], .st-key-at-rail .stButton {{width:60px !important;}}
.st-key-at-rail button {{width:60px; min-width:60px; min-height:56px; padding:6px 0; border:0; background:transparent;
  color:{INK_MUTED}; box-shadow:none; white-space:nowrap;}}
.st-key-at-rail button > div {{flex-direction:column; gap:2px;}}
.st-key-at-rail button p {{font-size:10.5px; line-height:1.2;}}
.st-key-at-rail button [data-testid="stIconMaterial"] {{font-size:22px; margin:0;}}
.st-key-at-rail button:hover {{background:{PAPER}; color:{INK};}}
.st-key-at-rail button[kind="primary"] {{background:{PAPER_2}; color:{INK};}}
.st-key-at-rail button[kind="primary"] p {{font-weight:600;}}
.st-key-at-nav-admin {{margin-top:auto;}}
.at-panel-brand {{font-family:"Libre Baskerville", Georgia, serif; font-size:15px; color:{INK_MUTED};}}
.at-panel-brand i {{color:{PRIMARY};}}
.at-panel-title {{font-family:"Libre Baskerville", Georgia, serif; font-weight:600; font-size:21px; line-height:1.25;
  color:{INK}; margin:4px 0 2px;}}
.at-steps {{margin:14px 0 6px;}}
.at-step {{display:flex; gap:12px; padding:0 0 16px; position:relative;}}
.at-step::before {{content:""; position:absolute; left:12px; top:26px; bottom:2px; width:2px; background:{LINE};}}
.at-step:last-child::before {{display:none;}}
.at-step .n {{width:26px; height:26px; border-radius:50%; display:flex; align-items:center; justify-content:center;
  font-size:12.5px; font-weight:700; flex:none; background:#fff; border:2px solid {LINE}; color:{INK_MUTED};}}
.at-step.done .n {{background:{PRIMARY}; border-color:{PRIMARY}; color:#fff;}}
.at-step.current .n {{border-color:{INK}; color:{INK};}}
.at-step .t {{font-weight:600; color:{INK}; font-size:14.5px;}}
.at-step .s {{font-size:12.5px; color:{INK_MUTED};}}
.at-label {{font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:{INK_MUTED}; margin:6px 0 2px;}}
.at-doc b {{display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:13.5px;}}
.at-doc span {{display:block; font-size:12.5px; color:{INK_MUTED};}}
[class*="st-key-at-questions"] h4 {{font-size:17px; padding:0 0 2px;}}
[class*="st-key-at-questions"] h4 a, .at-headline a {{display:none;}}
.st-key-at-panel-foot {{border-top:1px solid {LINE}; padding-top:12px; margin-top:18px;}}
.at-result-head {{margin:0 0 4px;}}
.at-headline {{font-family:"Libre Baskerville", Georgia, serif; font-weight:600; font-size:28px; line-height:1.2;
  color:{INK};}}
.at-bar {{display:flex; height:10px; border-radius:5px; overflow:hidden; margin:10px 0 6px; max-width:440px;
  background:{LINE};}}
.at-legend {{display:flex; flex-wrap:wrap; gap:16px; font-size:13px; color:{INK_MUTED};}}
.at-legend i {{display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px;}}
[class*="st-key-at-questions"] {{background:{STATUS["karar_verilemedi"][1]} !important; border-color:#E2D5A6 !important;}}
.at-table {{width:100%; border-collapse:separate; border-spacing:0; background:#fff; border:1px solid {LINE};
  border-radius:8px; overflow:hidden; font-size:14px; margin:4px 0 12px;}}
.at-table th {{text-align:left; font-size:11.5px; letter-spacing:.08em; text-transform:uppercase;
  color:{INK_MUTED}; font-weight:600; background:{PAPER_2}; padding:9px 14px; border-bottom:1px solid {LINE};}}
.at-table td {{padding:10px 14px; border-bottom:1px solid #ECEAE6; vertical-align:top; color:{INK};}}
.at-table tr:last-child td {{border-bottom:0;}}
.at-table td.ref {{font-family:"Source Code Pro", monospace; font-size:12px; color:{INK_MUTED};
  border-left:4px solid transparent; white-space:nowrap;}}
.at-table tr.finding td.ref {{border-left-color:{b};}} .at-table tr.undecided td.ref {{border-left-color:{k};}}
.at-table tr.passed td.ref {{border-left-color:{u};}} .at-table tr.missing td.ref {{border-left-color:{y};}}
.at-table td.res {{white-space:nowrap;}}
.at-table td.basis {{color:{INK_MUTED}; font-size:13px;}}
.at-table .note {{display:block; font-size:12.5px; color:{INK_MUTED}; margin-top:2px;}}
.at-chip {{display:inline-flex; align-items:center; justify-content:center; width:20px; height:20px;
  border-radius:50%; font-size:12px; font-weight:700; margin-right:6px; border:1.5px solid;}}
</style>"""


def render_rail(page: str) -> None:
    """Simge şeridi: amblem ve dört sayfa; Admin en altta."""
    from mufettis.ui.theme import mark_svg
    with st.container(key="at-rail"):
        st.markdown(f'<div class="at-rail-mark">{mark_svg(38)}</div>', unsafe_allow_html=True)
        for key, label, icon, _title, _desc in PAGES:
            st.button(label, icon=icon, key=f"at-nav-{key}", type="primary" if key == page else "secondary",
                      on_click=_go, args=(key,))


def panel_header(key: str) -> None:
    title, desc = next((p[3], p[4]) for p in PAGES if p[0] == key)
    st.markdown(f'<div class="at-panel-brand">Audit<i>Trace</i></div><div class="at-panel-title">'
                f'{html.escape(title)}</div>', unsafe_allow_html=True)
    if key != "kontrol":
        st.caption(desc)


__all__ = ["PAGES", "PAGE_KEYS", "current_page", "has_slot", "panel_header", "render_rail", "set_slots",
           "shell_css", "show_steps", "slot", "steps_html"]
