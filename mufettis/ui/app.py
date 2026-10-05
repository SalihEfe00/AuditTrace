# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import logging
import os
import unittest
from typing import Optional

import pandas as pd

try:
    import streamlit as st
except Exception:
    st = None

import re

from mufettis.analysis.risk_engine import (
    attach_regulation_matches,
    data_quality_report,
    findings_to_dataframe,
    run_analysis,
)
from mufettis.audit.service import append_audit, apply_reviews, load_reviews, save_review
from mufettis.common.utils import article_label, check_disk_health
from mufettis.corpus import canonical_view
from mufettis.config import (
    APP_VERSION,
    BASE_DIR,
    BULK_ERROR_FILE,
    BULK_STATE_FILE,
    CHUNK_SCHEMA_VERSION,
    DB_PATH,
    DEFAULT_LLM,
    EMBEDDING_MODEL,
    EXPORT_DIR,
    INGEST_INDEX_FILE,
    MAX_UPLOAD_MB,
    REVIEW_FILE,
    SOURCE_CACHE_DIR,
    SOURCE_REGISTRY_FILE,
)
from mufettis.exports.report_generator import _save_export, create_docx_report, create_xlsx_bytes
from mufettis.extraction.text_repair import (
    clean_evidence_text,
    extract_legal_identifier,
    repair_account_code_spacing,
    repair_extracted_spacing,
    repair_mojibake,
)
from mufettis.harvesting.web_harvester import sync_playwright
from mufettis.llm.client import (
    active_model,
    build_ai_report_prompt,
    installed_models,
    set_model,
    ollama_generate,
    ollama_health,
    prepare_report_findings,
    report_mevzuat_preflight,
)
from mufettis.rag import engine as rag_engine
from mufettis.rag.engine import LocalRAG
from mufettis.rag.maintenance import (
    _diagnostic_focus_text,
    run_rag_diagnostic,
    run_rag_provenance_compare,
)
from mufettis.scheduler.scheduler import BackgroundUpdateScheduler
from mufettis.ui.admin import render_mevzuat_admin
from mufettis.ui.analysis_view import render_analysis_page, render_review_history
from mufettis.ui.rules_view import render_authority_settings

logger = logging.getLogger(__name__)
def run_self_tests(*args, **kwargs):
    from mufettis.tests.runner import run_self_tests as _fn
    return _fn(*args, **kwargs)

def _create_rag(allow_download: bool) -> LocalRAG:
    return LocalRAG(allow_download=allow_download)


# Embedding modeli ve Chroma istemcisi süreç başına bir kez yüklenir ve tüm
# oturumlarca paylaşılır (session_state'te tutmak her oturumda modeli yeniden yükler).
# Oluşturma hatası önbelleğe alınmaz; sonraki çağrı yeniden dener.
_create_rag_cached = st.cache_resource(show_spinner=False)(_create_rag) if st is not None else _create_rag


def _get_rag(
    allow_download: bool = False,
    allow_embedding_download: Optional[bool] = None,
) -> Optional[LocalRAG]:
    if allow_embedding_download is not None:
        # Geriye dönük uyumluluk: eski çağrı adı da kabul edilir.
        allow_download = bool(allow_embedding_download)
    # Ağır kütüphaneler burada, RAG ilk istendiğinde yüklenir (M15.3).
    if rag_engine.sentence_transformer_class() is None:
        return _rag_unavailable(f"sentence-transformers yüklenemedi: {rag_engine.SENTENCE_TRANSFORMERS_ERROR}")
    from mufettis.rag.store import resolve_store_kind

    if resolve_store_kind(None, DB_PATH) == "chroma" and rag_engine.chromadb_module() is None:
        return _rag_unavailable("Eski Chroma veritabanı seçili ama chromadb kurulu değil.")
    try:
        rag = _create_rag_cached(bool(allow_download))
    except Exception as exc:
        return _rag_unavailable(str(exc))
    if st is not None:
        st.session_state.pop("rag_error", None)
    return rag


def _rag_unavailable(reason: str) -> None:
    """RAG kurulamadı: sebep günlüğe yazılır ve arayüzde "RAG hazır değil" mesajıyla gösterilir (admin.rag_not_ready)."""
    logger.warning("RAG hazır değil: %s", reason)
    if st is not None:
        st.session_state["rag_error"] = reason
    return None


def _rag_when_needed(allow_download: bool, needed: bool = False) -> Optional[LocalRAG]:
    """M15.3: mevzuat kütüphanesi sayfa açılırken kurulmaz (Streamlit bütün sekmeleri her seferinde çalıştırır).
    İlk gerektiğinde (needed=True: analiz, sorgu, teşhis, "yükle" düğmesi) kurulur ve oturum boyunca açık kalır."""
    if st is None:
        return _get_rag(allow_download=allow_download)
    if needed:
        st.session_state["rag_requested"] = True
    if not st.session_state.get("rag_requested"):
        return None
    with st.spinner("Mevzuat kütüphanesi yükleniyor (ilk seferde birkaç saniye sürer)..."):
        return _get_rag(allow_download=allow_download)


def _rag_load_button(key: str) -> None:
    """Kütüphane henüz yüklenmediyse kısa bir not ve "yükle" düğmesi gösterir; basılınca sayfa yüklü hâliyle
    yeniden çizilir."""
    if st.session_state.get("rag_requested"):
        return
    st.info("Mevzuat kütüphanesi henüz yüklenmedi; ilk aramada ya da bu düğmeyle yüklenir.")
    if st.button("Mevzuat kütüphanesini yükle", key=key):
        st.session_state["rag_requested"] = True
        st.rerun()  # bütün sekmeler kütüphane yüklü hâliyle yeniden çizilir


def render_self_test_result(result: unittest.result.TestResult) -> None:
    """Self-test sonucunu UI'da test isimleri ve hata özetiyle gösterir."""
    passed = result.testsRun - len(result.failures) - len(result.errors)
    st.write(f"Başarılı: {passed} / {result.testsRun}")

    failed = list(result.failures) + list(result.errors)
    if not failed:
        st.success("Tüm self-testler başarılı.")
        return

    st.error(f"Başarısız test: {len(failed)}")
    for test, traceback_text in failed:
        test_name = str(test)
        with st.expander(f"❌ {test_name}", expanded=False):
            st.code(traceback_text, language="text")
def render_llm_settings() -> None:
    """Yerel dil modeli (M15.2): kurulu modellerden seçim ve hazır / kurulu değil durumu. Seçim oturumda tutulur."""
    names = installed_models()
    if names is None:
        st.caption("Yerel dil modeli: Ollama çalışmıyor (isteğe bağlı; kural kontrolü bunsuz çalışır).")
        return
    usable = [n for n in names if n]
    if not usable:
        st.caption(f"Yerel dil modeli: Ollama çalışıyor ama kurulu model yok ('ollama pull {DEFAULT_LLM}').")
        return
    current = active_model(names)
    chosen = st.selectbox("Yerel dil modeli (Ollama)", usable,
                          index=usable.index(current) if current in usable else 0, key="llm_model_choice")
    set_model(chosen)
    st.caption(f"Yerel dil modeli hazır: {chosen}")


def run_streamlit() -> None:
    if st is None:
        raise RuntimeError("Streamlit kurulu değil")
    st.set_page_config(page_title=f"Müfettiş AI {APP_VERSION}", layout="wide")
    st.title(f"Müfettiş AI — v{APP_VERSION}")
    st.caption("Yerel karar destek / anomali analizi / aday mevzuat eşleştirme")

    if "analysis" not in st.session_state:
        st.session_state["analysis"] = None
    if "rag_answer" not in st.session_state:
        st.session_state["rag_answer"] = ""

    if st.session_state.get("llm_model_choice"):
        set_model(st.session_state["llm_model_choice"])
    with st.sidebar:
        # M15.5: kenar çubuğunda yalnız denetçi adı ve mevzuatın güncelliği; diğer ayarlar Admin → Ayarlar'da.
        auditor = st.text_input("Denetçi adı", value="", key="auditor_name")
        # M6.16: korpusun güncelliği; eskiyse sonuçların hangi tarihteki mevzuata göre olduğu söylenir.
        from mufettis.config import DATA_DIR
        from mufettis.corpus.currency import last_corpus_update, staleness_message
        last_update = last_corpus_update(DATA_DIR)
        st.write(f"Mevzuat korpusu son güncelleme: {last_update:%d.%m.%Y %H:%M}" if last_update else
                 "Mevzuat korpusu son güncelleme: bilinmiyor")
        stale = staleness_message(last_update)
        if stale:
            st.warning(stale)
        from mufettis.corpus.currency import running_update, start_update
        busy = running_update(DATA_DIR)
        if busy:
            st.caption(f"Mevzuat güncellemesi sürüyor (süreç {busy}).")
        elif st.button("Mevzuatı güncelle (internet gerekir)",
                       help="Son 7 günün Resmî Gazetesi taranır; değişen belgeler resmî kaynaktan yeniden indirilir."):
            ok, message = start_update(DATA_DIR, BASE_DIR, production=not os.environ.get("MUFETTIS_DATA_DIR"))
            (st.success if ok else st.error)(message)

    # M15.4: dört ana sekme; Mevzuat Arama, Admin'den önceki son sekme. Eski Veri Analizi / İnceleme / Rapor
    # sekmeleri silinmedi, Admin → Gelişmiş araçlar → İşlem verisi analizi (deneysel) altına taşındı; mevzuat
    # teşhis araçları Admin'de.
    tab_check, tab_history, tab_search, tab_admin = st.tabs([
        "1. Kredi Dosyası Kontrolü",
        "2. İnceleme Geçmişi",
        "3. Mevzuat Arama",
        "4. Admin",
    ])
    with tab_admin:
        st.subheader("Admin")
        st.caption(
            "Yerel mevzuat yükleme, toplu içe aktarma, resmi kaynak senkronizasyonu, "
            "bootstrap, RAG rebuild, sistem testleri ve gelişmiş araçlar."
        )
        admin_settings, admin_main, admin_diag, admin_advanced = st.tabs(
            ["Ayarlar", "Mevzuat yönetimi ve sistem", "Mevzuat teşhis araçları", "Gelişmiş araçlar"]
        )
    # M15.5: ayarlar diğer sekmelerden önce okunur (değerleri onlar kullanır); önceden kenar çubuğundaydı.
    with admin_settings:
        st.markdown("#### Mevzuat ve internet")
        local_only = st.checkbox("Tam yerel mod", value=True, help="Resmî Gazete senkronizasyonu ve ilk model indirmeyi kapatır.")
        # Devre dışı checkbox son değerini döndürmeye devam eder; tam yerel modda indirme izni zorla kapatılır.
        allow_embedding_download = st.checkbox("İlk embedding model indirmesine izin ver", value=False, disabled=local_only) and not local_only
        auto_update = st.checkbox("Arka planda günlük mevzuat güncellemesi", value=False, disabled=local_only, help="Uygulama açıkken her gün 00:05 Europe/Istanbul saatinde BDDK + Resmî Gazete senkronizasyonunu çalıştırır.")
        playwright_available = sync_playwright is not None
        use_browser_bddk = st.checkbox(
            "BDDK için Playwright kullan",
            value=playwright_available,
            disabled=local_only or not playwright_available,
            help=(
                "JavaScript ile yüklenen linklerde requests/BeautifulSoup yerine headless Chromium kullanır."
                if playwright_available
                else "Playwright kurulu değil (requirements-optional.txt); BDDK HTTP toplayıcıyla taranır."
            ),
        ) and playwright_available
        if not local_only and auto_update:
            scheduler = BackgroundUpdateScheduler.get_or_create(["bddk_official", "resmi_gazete"], use_browser_for_bddk=use_browser_bddk)
            scheduler.start()
            st.caption(f"Otomatik güncelleme aktif — sonraki çalışma yaklaşık {scheduler.seconds_until_next_run()/3600:.1f} saat sonra.")
        elif BackgroundUpdateScheduler._instance is not None:
            BackgroundUpdateScheduler._instance.stop()
        st.markdown("#### Yerel dil modeli")
        render_llm_settings()
        st.markdown("#### Kredi dosyası")
        render_authority_settings()
        st.markdown("#### Sistem")
        health = check_disk_health()
        st.write(f"Disk: {'OK' if health.get('ok') else 'HATA'} | Boş: {health.get('free_gb','?')} GB")
        if st.button("Self-test çalıştır"):
            result = run_self_tests()
            render_self_test_result(result)
    with admin_advanced:
        st.markdown("#### İşlem verisi analizi (deneysel)")
        st.caption(
            "Excel / CSV işlem verisinde istatistiksel aykırılık taraması. Kredi dosyası kontrolünün parçası "
            "değildir."
        )
        # M15.5: eşikler yalnız bu analizde kullanılır; önceden kenar çubuğundaydı (anahtarlar aynı).
        thr_z, thr_mad, thr_iqr = st.columns(3)
        with thr_z:
            z_threshold_ui = st.number_input(
                "Z-score eşiği",
                min_value=0.5,
                max_value=10.0,
                value=3.0,
                step=0.1,
                key="sidebar_z_threshold",
                help="Z-score ve grup içi Z-score için eşik.",
            )
        with thr_mad:
            mad_threshold_ui = st.number_input(
                "Medyan / MAD eşiği",
                min_value=0.5,
                max_value=15.0,
                value=3.5,
                step=0.1,
                key="sidebar_mad_threshold",
                help="Medyan merkezli robust modified z-score eşiği.",
            )
        with thr_iqr:
            iqr_multiplier_ui = st.number_input(
                "IQR katsayısı",
                min_value=0.5,
                max_value=5.0,
                value=1.5,
                step=0.1,
                key="sidebar_iqr_multiplier",
            )
        data_tab, data_review_tab, data_report_tab = st.tabs(["Veri analizi", "İnceleme", "Rapor"])

    # TAB 1 ------------------------------------------------------------------
    with data_tab:
        upload = st.file_uploader("Excel / CSV yükle", type=["xlsx", "xls", "csv"], accept_multiple_files=False)
        if upload:
            if upload.size > MAX_UPLOAD_MB * 1024 * 1024:
                st.error(f"Dosya {MAX_UPLOAD_MB} MB sınırını aşıyor.")
            else:
                try:
                    if upload.name.lower().endswith(".csv"):
                        df = pd.read_csv(upload)
                    else:
                        df = pd.read_excel(upload)
                    st.write(f"Satır: {len(df):,} | Kolon: {len(df.columns)}")
                    st.dataframe(df.head(20), use_container_width=True)
                    quality = data_quality_report(df)
                    with st.expander("Veri kalite raporu"):
                        st.json(quality)

                    amount_col = st.selectbox("Tutar kolonu", ["(otomatik)"] + list(df.columns))
                    amount_col = None if amount_col == "(otomatik)" else amount_col
                    method_labels = {
                        "zscore": "Z-score", "mad": "MAD", "iqr": "IQR", "high_amount": "Yüksek tutar sinyali",
                        "duplicates": "Duplicate", "keywords": "Anahtar kelime", "group": "Grup içi", "trend": "Trend",
                        "ratio": "Oran", "benchmark": "Benchmark"
                    }
                    methods = st.multiselect("Analiz yöntemleri", list(method_labels.keys()), default=["zscore", "mad", "iqr", "duplicates", "keywords"])
                    cols = list(df.columns)
                    duplicate_cols = st.multiselect("Duplicate karşılaştırma kolonları", cols, default=[])
                    group_cols = st.multiselect("Grup kolonları", cols, default=[])
                    trend_date = st.selectbox("Trend tarih kolonu", ["(yok)"] + cols)
                    trend_date = None if trend_date == "(yok)" else trend_date
                    high_threshold = None
                    if "high_amount" in methods:
                        high_threshold = st.number_input("Analitik yüksek tutar eşiği", min_value=0.0, value=100000.0, step=1000.0)
                        st.caption("Bu eşik MASAK/BDDK bildirim eşiği değildir.")
                    kw_text = st.text_input("Anahtar kelimeler (virgülle)", value="crypto, bahis, offshore, swift, kripto")
                    keywords = [x.strip() for x in kw_text.split(",") if x.strip()]

                    ratio_num = ratio_den = benchmark_col = None
                    ratio_threshold = 10.0
                    benchmark_threshold = 3.0
                    if "ratio" in methods:
                        c1, c2 = st.columns(2)
                        ratio_num = c1.selectbox("Oran payı", ["(seç)"] + cols)
                        ratio_den = c2.selectbox("Oran paydası", ["(seç)"] + cols)
                        ratio_num = None if ratio_num == "(seç)" else ratio_num
                        ratio_den = None if ratio_den == "(seç)" else ratio_den
                        ratio_threshold = st.number_input("Oran eşiği", min_value=0.0, value=10.0, step=0.5)
                    if "benchmark" in methods:
                        benchmark_col = st.selectbox("Benchmark kolonu", ["(seç)"] + cols)
                        benchmark_col = None if benchmark_col == "(seç)" else benchmark_col
                        benchmark_threshold = st.number_input("Benchmark oran eşiği", min_value=0.0, value=3.0, step=0.5)

                    if st.button("Analizi çalıştır", type="primary"):
                        findings, working, summary = run_analysis(
                            df,
                            amount_column=amount_col,
                            methods=methods,
                            z_threshold=float(z_threshold_ui),
                            iqr_multiplier=float(iqr_multiplier_ui),
                            mad_threshold=float(mad_threshold_ui),
                            high_amount_threshold=high_threshold,
                            keywords=keywords,
                            duplicate_columns=duplicate_cols,
                            group_columns=group_cols,
                            trend_date_column=trend_date,
                            ratio_numerator=ratio_num,
                            ratio_denominator=ratio_den,
                            ratio_threshold=ratio_threshold,
                            benchmark_column=benchmark_col,
                            benchmark_ratio_threshold=benchmark_threshold,
                            source_file=upload.name,
                        )
                        # RAG varsa yüksek öncelikli bulgulara eşleştirme
                        rag = _get_rag(allow_download=allow_embedding_download)
                        if rag:
                            try:
                                findings = attach_regulation_matches(findings, working, rag)
                            except Exception as exc:
                                st.warning(f"Mevzuat eşleştirme atlandı: {exc}")
                        st.session_state["analysis"] = {
                            "findings": findings, "working": working, "summary": summary,
                            "filename": upload.name,
                        }
                        st.success(f"Analiz tamamlandı: {summary['finding_count']} bulgu / {summary['affected_rows']} etkilenen satır")

                except Exception as exc:
                    st.error(f"Veri işlenemedi: {exc}")

        analysis = st.session_state.get("analysis")
        if analysis:
            summary = analysis["summary"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Bulgu", summary["finding_count"])
            c2.metric("Etkilenen satır", summary["affected_rows"])
            c3.metric("Yüksek", summary["high_count"])
            c4.metric("Orta", summary["medium_count"])
            st.dataframe(analysis["working"].head(1000), use_container_width=True)
            st.markdown("### Bulgular")
            fdf = findings_to_dataframe(analysis["findings"])
            if fdf.empty:
                st.success("Seçili analiz yöntemleriyle bulgu üretilmedi.")
            else:
                severities = sorted(str(x) for x in fdf["risk_seviyesi"].dropna().unique())
                selected_severities = st.multiselect(
                    "Risk seviyeleri",
                    severities,
                    default=severities,
                    key="finding_severity_filter",
                )
                min_risk = st.slider(
                    "Minimum risk skoru",
                    0,
                    100,
                    0,
                    key="finding_min_risk",
                )
                risk_numeric = pd.to_numeric(
                    fdf["risk_skoru"],
                    errors="coerce",
                ).fillna(0)
                view = fdf[
                    fdf["risk_seviyesi"].isin(selected_severities)
                    & (risk_numeric >= min_risk)
                ].copy()
                st.caption(
                    f"{len(view)} / {len(fdf)} bulgu gösteriliyor | "
                    f"Z-score ≥ {float(z_threshold_ui):.1f} | "
                    f"MAD ≥ {float(mad_threshold_ui):.1f} | "
                    f"IQR × {float(iqr_multiplier_ui):.1f}"
                )
                display_cols = [
                    c for c in [
                        "bulgu_id", "satir", "bulgu_tipi", "risk_skoru",
                        "risk_seviyesi", "deger", "gerekce", "inceleme_durumu"
                    ] if c in view.columns
                ]
                st.dataframe(view[display_cols], use_container_width=True)

    # TAB 2 (Müfettiş AI Belge Analizi) --------------------------------------
    with tab_check:
        render_analysis_page(rag_provider=lambda: _rag_when_needed(allow_embedding_download, needed=True))

    # TAB 3 ------------------------------------------------------------------
    with tab_history:
        render_review_history()

    with tab_search:
        st.subheader("Mevzuat Arama")
        st.caption("Sorunuzu ya da aradığınız konuyu yazın; ilgili kanun ve yönetmelik maddeleri listelenir.")
        # Durum satırı sekmenin sonunda yazılır: sorgu / teşhis kütüphaneyi bu çizimde yüklediyse "henüz
        # yüklenmedi" notu kalmaz.
        rag_status = st.empty()
        with rag_status.container():
            _rag_load_button("rag_load_search")
        rag = _rag_when_needed(allow_embedding_download)
        with admin_diag:
            st.caption(
                f"Aktif chunk şeması: {CHUNK_SCHEMA_VERSION}. "
                "Geliştirici teşhis araçları (önceden Mevzuat RAG sekmesindeydi); mevzuatı değiştirmez."
            )
            with st.expander("RAG DIAGNOSTIC — zinciri değiştirmeden incele", expanded=False):
                st.caption(
                    "Bu bölüm yalnızca teşhis içindir. Chroma silmez, indexlemez, source cache'i değiştirmez. "
                    "Önce exact belge + hesap eşleşmesini, ardından PDF extraction adaylarını gösterir."
                )
                dcol1, dcol2 = st.columns(2)
                diagnostic_doc = dcol1.text_input("Belge kimliği", value="1043", key="rag_diag_doc")
                diagnostic_account = dcol2.text_input("Hesap kodu", value="280", key="rag_diag_account")
                run_diag = st.button("Teşhisi çalıştır", key="rag_diag_run")
                if run_diag:
                    if rag is None:
                        rag = _rag_when_needed(allow_embedding_download, needed=True)
                    if rag is None:
                        st.error("RAG kullanılamıyor.")
                    else:
                        try:
                            diag = run_rag_diagnostic(rag, diagnostic_doc, diagnostic_account)
                            if not diag.get("ok"):
                                st.error(diag.get("error", "Teşhis başarısız."))
                            else:
                                st.success(
                                    f"Exact Chroma eşleşmesi: {diag.get('match_count', 0)} | "
                                    f"Toplam kayıt: {diag.get('chroma_count', 0)}"
                                )
                                src = diag.get("source", {})
                                st.write({
                                    "file_hash": src.get("file_hash", ""),
                                    "cache_exists": src.get("cache_exists", False),
                                    "cache_chars": src.get("cache_chars", 0),
                                    "source_url": src.get("url", ""),
                                    "source_fetch": src.get("fetch", {}),
                                })

                                st.markdown("**Exact kayıtlar — belge/kod kimliği**")
                                for item in diag.get("exact_matches", []):
                                    st.code(
                                        json.dumps(item, ensure_ascii=False, indent=2),
                                        language="json",
                                    )

                                st.markdown("**PDF extraction adayları — RAW → REPAIRED**")
                                extraction_rows = diag.get("extraction", [])
                                if not extraction_rows:
                                    st.warning(
                                        "Kaynak PDF bu oturumda indirilemedi; aşağıdaki cache/chunk/Chroma "
                                        "aşamaları yine de gösteriliyor."
                                    )
                                for candidate in extraction_rows:
                                    st.markdown(f"`{candidate.get('name')}`")
                                    st.code(
                                        "RAW\n" + _diagnostic_focus_text(candidate.get("raw", ""), diagnostic_account) +
                                        "\n\nREPAIRED\n" + _diagnostic_focus_text(candidate.get("repaired", ""), diagnostic_account),
                                        language="text",
                                    )
                                    st.write({
                                        "raw_quality": candidate.get("quality_raw", {}),
                                        "repaired_quality": candidate.get("quality_repaired", {}),
                                    })

                                st.markdown("**Aşamalar**")
                                for stage in diag.get("stages", []):
                                    with st.expander(stage.get("stage", "stage"), expanded=False):
                                        st.write(stage.get("quality", {}))
                                        st.code(stage.get("text", "[BOŞ]"), language="text")
                                        if stage.get("metadata"):
                                            st.json(stage.get("metadata"))

                                st.markdown("**Semantic retrieval — exact teşhisin ayrı çıktısı**")
                                sem = diag.get("semantic_results", [])
                                if sem:
                                    st.code(json.dumps(sem, ensure_ascii=False, indent=2), language="json")
                                else:
                                    st.info("Semantic retrieval sonucu yok.")
                        except Exception as exc:
                            st.error(f"RAG diagnostic hatası: {type(exc).__name__}: {exc}")

            st.divider()
            with st.expander("CACHE / CHROMA PROVENANCE — iki belgeyi yan yana karşılaştır", expanded=False):
                st.caption(
                    "Bu test de yalnızca okur. İki belge+hesap eşleşmesinin source_cache ve Chroma metinlerini "
                    "hash ve hedef çevresiyle karşılaştırır; indexleme, silme veya cache güncelleme yapmaz."
                )
                pcol1, pcol2 = st.columns(2)
                prov_doc_a = pcol1.text_input("Belge A", value="1043", key="rag_prov_doc_a")
                prov_doc_b = pcol2.text_input("Belge B", value="1045", key="rag_prov_doc_b")
                pcol3, pcol4 = st.columns(2)
                prov_acc_a = pcol3.text_input("Hesap A", value="280", key="rag_prov_acc_a")
                prov_acc_b = pcol4.text_input("Hesap B", value="280", key="rag_prov_acc_b")
                run_prov = st.button("Cache / Chroma karşılaştır", key="rag_prov_run")
                if run_prov:
                    if rag is None:
                        rag = _rag_when_needed(allow_embedding_download, needed=True)
                    if rag is None:
                        st.error("RAG kullanılamıyor.")
                    else:
                        try:
                            prov = run_rag_provenance_compare(
                                rag,
                                prov_doc_a,
                                prov_acc_a,
                                prov_doc_b,
                                prov_acc_b,
                            )
                            if not prov.get("ok"):
                                st.error(prov.get("error", "Provenance karşılaştırması başarısız."))
                            else:
                                if str(prov.get("diagnosis", "")).startswith("KIRILMA ADAYI"):
                                    st.error(prov.get("diagnosis"))
                                else:
                                    st.info(prov.get("diagnosis"))
                                st.write({
                                    "toplam_chroma": prov.get("chroma_count", 0),
                                    "aynı_cache_icerigi": prov.get("same_cache_content", False),
                                    "ayni_chunk_icerigi": prov.get("same_chunk_content", False),
                                })
                                for label, side_key in (("BELGE A", "document_a"), ("BELGE B", "document_b")):
                                    side = prov.get(side_key, {})
                                    st.markdown(f"**{label}: {side.get('document_ref', '')} / {side.get('account_ref', '')}**")
                                    st.write({
                                        "exact_match_count": side.get("exact_match_count", 0),
                                    })
                                    st.markdown("Cache kayıtları")
                                    st.code(
                                        json.dumps(side.get("source_caches", []), ensure_ascii=False, indent=2),
                                        language="json",
                                    )
                                    st.markdown("Exact Chroma chunk kayıtları")
                                    st.code(
                                        json.dumps(side.get("exact_chunks", []), ensure_ascii=False, indent=2),
                                        language="json",
                                    )
                        except Exception as exc:
                            st.error(f"Provenance karşılaştırma hatası: {type(exc).__name__}: {exc}")


        query = st.text_input("Sorunuz ya da aradığınız konu", value="", key="rag_search_query")
        k = st.slider("Gösterilecek sonuç sayısı", 1, 10, 5, key="rag_search_k")
        if st.button("Ara", key="rag_search_run") and query:
            if rag is None:
                rag = _rag_when_needed(allow_embedding_download, needed=True)
            if rag is None:
                st.error("RAG kullanılamıyor.")
            else:
                try:
                    results = rag.query(query, k=k)
                    if not results:
                        law_id = extract_legal_identifier(query)
                        if law_id:
                            st.warning(
                                f"{law_id} sayılı Kanun için doğrulanabilir güncel hukuki "
                                "chunk bulunamadı. Alakasız mevzuat gösterilmiyor; "
                                "kaynak belgeyi yeniden içe aktarmak gerekiyor."
                            )
                        else:
                            st.warning(
                                "Güncel chunk şemasına uygun sonuç bulunamadı."
                            )
                    for i, r in enumerate(results, 1):
                        m = r["metadata"]
                        hierarchy = []
                        if m.get("article_no"):
                            hierarchy.append(article_label(m.get("article_no"), repair_mojibake(m.get("article_prefix") or "")))
                        if m.get("paragraph_no"):
                            hierarchy.append(f"Fıkra {m.get('paragraph_no')}")
                        if m.get("item_no"):
                            hierarchy.append(f"Bent {m.get('item_no')}")
                        if m.get("subitem_no"):
                            hierarchy.append(f"Alt bent {m.get('subitem_no')}")

                        st.markdown(
                            f"**{i}. {repair_mojibake(m.get('title',''))}** | "
                            f"{' / '.join(hierarchy) if hierarchy else 'Belge'} | "
                            f"uzaklık {r.get('distance')} | "
                            f"yeniden sıralama {r.get('_rerank_score', '')}"
                        )
                        canonical = canonical_view(m)
                        st.caption(
                            f"Durum: {repair_mojibake(m.get('status',''))} | "
                            f"Yürürlük: {repair_mojibake(m.get('effective_date',''))} | "
                            f"Kaynak: {repair_mojibake(m.get('source',''))} "
                            f"({canonical.publisher_kind}, doğrulama: {canonical.verification}) | "
                            f"Tür: {canonical.instrument_type} / rol: {canonical.normative_role} | "
                            f"Kimlik: {canonical.work_id} | "
                            f"Chunk: {m.get('chunk_type','')} #{m.get('chunk_no','')}"
                        )
                        display_text = clean_evidence_text(r["document"], max_chars=1800)
                        display_text = repair_extracted_spacing(
                            display_text, m.get("title", ""), m.get("document_type", "")
                        )
                        display_account_code = str(m.get("account_code", "") or "").strip()
                        if (not re.fullmatch(r"\d{3,6}", display_account_code) or
                                not re.search(rf"(?<!\d){re.escape(display_account_code)}(?=\s*[A-ZÇĞİÖŞÜa-zçğıöşü])", display_text)):
                            inferred_code = re.search(r"(?<!\d)(\d{3,6})(?=\s*[A-ZÇĞİÖŞÜa-zçğıöşü])", display_text)
                            display_account_code = inferred_code.group(1) if inferred_code else ""
                        display_text = repair_account_code_spacing(
                            display_text, display_account_code
                        )
                        # Mevzuat metni whitespace'ını Markdown'ın sıkıştırmasına izin verme.
                        # Satır/paragraf sınırları ve cümle boşlukları ekranda da korunur.
                        st.markdown(
                            f"<div style=\"white-space:pre-wrap; line-height:1.65; word-break:normal; overflow-wrap:anywhere; margin:0.25rem 0 0.75rem 0;\">{html.escape(display_text)}</div>",
                            unsafe_allow_html=True,
                        )
                        if m.get("chunk_type") == "unscoped_numeric":
                            st.warning("Bu parçada fıkra üst öğesi güvenle tespit edilemedi; sistem alt-bent üretmedi.")
                        with st.expander("Chunk / hukuki hiyerarşi"):
                            st.write({
                                "chunk_schema_version": m.get("chunk_schema_version", ""),
                                "chunk_type": m.get("chunk_type", ""),
                                "article_prefix": m.get("article_prefix", ""),
                                "article_no": m.get("article_no", ""),
                                "paragraph_no": m.get("paragraph_no", ""),
                                "item_no": m.get("item_no", ""),
                                "subitem_no": m.get("subitem_no", ""),
                                "chunk_no": m.get("chunk_no", ""),
                                "subchunk_no": m.get("subchunk_no", ""),
                            })
                except Exception as exc:
                    st.error(f"RAG sorgu hatası: {exc}")

        if rag is not None:
            rag_status.write(f"Parça (kayıt) sayısı: {rag.count()}")
        elif st.session_state.get("rag_requested"):
            rag_status.error("RAG kullanılamıyor.")


    # TAB 4 ------------------------------------------------------------------
    with data_review_tab:
        st.subheader("İnsan inceleme / yanlış pozitif yönetimi")
        analysis = st.session_state.get("analysis")
        if not analysis:
            st.info("Önce veri analizi çalıştır.")
        else:
            findings = analysis["findings"]
            fdf = findings_to_dataframe(findings)
            if fdf.empty:
                st.info("İncelenecek bulgu yok.")
            else:
                reviewer = st.text_input("İnceleyen", value=auditor)
                finding_id = st.selectbox("Bulgu", fdf["bulgu_id"].tolist())
                selected = next(f for f in findings if f.get("bulgu_id") == finding_id)
                rc1, rc2, rc3 = st.columns(3)
                rc1.metric("Risk", str(selected.get("risk_skoru", 0)))
                rc2.metric("Seviye", str(selected.get("risk_seviyesi", "")))
                rc3.metric("Satır", str(selected.get("satir", "")))
                st.markdown(f"**Bulgu türü:** {repair_mojibake(selected.get('bulgu_tipi', ''))}")
                st.markdown(f"**Gerekçe:** {repair_mojibake(selected.get('gerekce', ''))}")
                st.markdown(f"**Değer:** {repair_mojibake(selected.get('deger', ''))}")
                st.markdown(f"**Kolonlar:** {repair_mojibake(', '.join(map(str, selected.get('kolonlar', []))))}")
                statuses = ["Bekliyor", "Doğrulandı", "Yanlış pozitif", "Ek inceleme gerekli"]
                current = load_reviews().get("items", {}).get(finding_id, {}).get("status", "Bekliyor")
                status = st.selectbox("Durum", statuses, index=statuses.index(current) if current in statuses else 0)
                note = st.text_area("Not", value=load_reviews().get("items", {}).get(finding_id, {}).get("note", ""))
                if st.button("İncelemeyi kaydet", type="primary"):
                    save_review(finding_id, status, reviewer, note)
                    st.success("İnceleme kaydedildi.")
                st.dataframe(apply_reviews(fdf), use_container_width=True)

    # TAB 5 ------------------------------------------------------------------
    with data_report_tab:
        st.subheader("Rapor / çıktı")
        analysis = st.session_state.get("analysis")
        if not analysis:
            st.info("Önce analiz çalıştır.")
        else:
            findings = analysis["findings"]
            summary = analysis["summary"]
            working = analysis["working"]
            fdf = findings_to_dataframe(findings)
            st.dataframe(fdf, use_container_width=True)

            col1, col2 = st.columns(2)
            if col1.button("DOCX rapor oluştur"):
                try:
                    with st.spinner("Rapor için mevzuat atıfları hazırlanıyor..."):
                        report_findings, match_status = prepare_report_findings(
                            findings,
                            working,
                            allow_embedding_download=allow_embedding_download,
                            k=3,
                        )
                    preflight = report_mevzuat_preflight(report_findings)

                    for msg in match_status.get("errors", []):
                        st.warning(msg)

                    st.info(
                        f"Mevzuat atfı hazırlığı: {preflight['matched_findings']} / "
                        f"{preflight['total_findings']} bulgu eşleşti; "
                        f"{preflight['unmatched_findings']} bulgu eşleşmesiz."
                    )

                    data = create_docx_report(
                        summary,
                        report_findings,
                        auditor=auditor,
                    )
                    path = _save_export(
                        data,
                        f"mufettis_calisma_kagidi_v0_13_18_{summary['audit_run_id']}.docx",
                    )
                    st.download_button(
                        "DOCX indir",
                        data=data,
                        file_name=path.name,
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                except Exception as exc:
                    st.error(f"DOCX üretilemedi: {exc}")
            if col2.button("XLSX paket oluştur"):
                try:
                    data = create_xlsx_bytes(working, fdf, summary)
                    path = _save_export(data, f"mufettis_{summary['audit_run_id']}.xlsx")
                    st.download_button("XLSX indir", data=data, file_name=path.name, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                except Exception as exc:
                    st.error(f"XLSX üretilemedi: {exc}")

            st.caption(
                "DOCX deterministiktir. DOCX oluşturma anında tüm bulgular için "
                "mevzuat eşleştirmesi tazelenir; AI taslağı ayrı TXT çıktısıdır."
            )
            st.divider()
            st.markdown("### Yerel LLM ile rapor taslağı")
            model = st.text_input("Ollama model", value=active_model())
            health = ollama_health(model)
            st.write(f"Ollama: {'hazır' if health.get('ok') else (health.get('error') or 'ulaşılamıyor')}")
            if st.button("AI rapor taslağı oluştur"):
                prompt = build_ai_report_prompt(summary, findings)
                draft = ollama_generate(prompt, model=model)
                st.session_state["rag_answer"] = draft
                if not draft.startswith("[LLM HATASI]"):
                    append_audit("llm_report_draft_completed", {"audit_run_id": summary["audit_run_id"], "model": model})
            if st.session_state.get("rag_answer"):
                st.text_area("AI rapor taslağı", value=st.session_state["rag_answer"], height=500)
                st.download_button("AI taslağını TXT indir", data=st.session_state["rag_answer"].encode("utf-8"), file_name=f"ai_taslak_{summary['audit_run_id']}.txt", mime="text/plain")

    # TAB 6 ------------------------------------------------------------------
    with admin_main:
        if not st.session_state.get("rag_requested"):
            st.subheader("Mevzuat kaynak yönetimi")  # yüklenince başlığı render_mevzuat_admin yazar
        _rag_load_button("rag_load_admin")
        rag_admin = _rag_when_needed(allow_embedding_download)
        if st.session_state.get("rag_requested"):
            render_mevzuat_admin(
                rag=rag_admin,
                local_only=local_only,
                use_browser_bddk=use_browser_bddk,
                allow_embedding_download=allow_embedding_download,
            )

        st.divider()
        st.subheader("Sistem / güvenlik / test")
        st.write({
            "app_version": APP_VERSION,
            "base_dir": str(BASE_DIR),
            "db_path": str(DB_PATH),
            "export_dir": str(EXPORT_DIR),
            "ingest_index": str(INGEST_INDEX_FILE),
            "review_registry": str(REVIEW_FILE),
            "embedding_model": EMBEDDING_MODEL,
            "default_llm": DEFAULT_LLM,
            "active_llm": active_model(),
            "local_only": local_only,
            "bulk_state_file": str(BULK_STATE_FILE),
            "bulk_error_file": str(BULK_ERROR_FILE),
            "source_registry": str(SOURCE_REGISTRY_FILE),
            "source_cache_dir": str(SOURCE_CACHE_DIR),
        })
        st.markdown(
            "**Güvenlik yaklaşımı:** veriler ve RAG yerelde tutulur; "
            "LLM logları varsayılan olarak yazılmaz; raporlar karar desteğidir."
        )
        if st.button("Tüm self-testleri yeniden çalıştır"):
            result = run_self_tests()
            render_self_test_result(result)
        if st.button("Ollama durumunu kontrol et"):
            st.json(ollama_health())


__all__ = [
    "run_self_tests",
    "render_self_test_result",
    "run_streamlit",
]
