# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Stage 4 Analysis Intelligence
Streamlit Müfettiş Analiz Ekranı (Analysis View).

Provides the user-facing Streamlit interface for operational document inspection:
- Document input (text, title, source, date)
- Quick realistic test templates
- End-to-end pipeline execution indicator
- Document Understanding & Classification summary
- Formulated Analysis & Retrieval Plans
- Grounded Mevzuat Evidence inspector
- Structured Inspector Findings (Observation, Evidence, Regulation Reference,
  Model Interpretation, Inspector Follow-up)
- Verbatim Citation verification display
- Unsupported Claims guard panel
- Insufficient Evidence & safety warnings
- Human Reviewer panel (Reviewer status, notes, feedback)

Zero direct mutation to Chroma or production databases.
"""
from __future__ import annotations

import html
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import streamlit as st
except Exception:
    st = None

from mufettis.analysis.analysis_presenter import (
    PIPELINE_STAGES,
    AnalysisExecutionResult,
    AnalysisPresenter,
)
from mufettis.analysis.cited_references import (
    IN_CORPUS,
    INTERNAL,
    NOT_CHECKED,
    NOT_IN_CORPUS,
    describe as cited_describe,
    missing_note as cited_missing_note,
)
from mufettis.analysis.models import (
    AnalysisPlan,
    AnalysisResult,
    Citation,
    DocumentClassification,
    DocumentUnderstanding,
)
from mufettis.exports.analysis_report import create_analysis_docx
from mufettis.extraction.files import extract_file_text


# ── Sample Templates for Instant Inspection ───────────────────────────────────

SAMPLE_COMPLAINT_TEXT = (
    "Ziraat Bankası Kadıköy Şubesi nezdinde bulunan TR12 0001 0002 0003 nolu vadesiz TL hesabımdan "
    "01.03.2024 tarihinde 150 TL tutarında 'Hesap İşletim Ücreti' kesintisi yapılmıştır. "
    "Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği uyarınca vadesiz mevduat hesaplarından "
    "hesap işletim ücreti alınması hukuka aykırıdır. Kesilen 150 TL tutarın hesabıma iadesini talep ediyorum."
)

SAMPLE_INTERNAL_TEXT = (
    "Teftiş Kurulu Başkanlığı - İç Denetim Notu\n"
    "Tarih: 15.02.2024\n"
    "Konu: Levent Şubesi Kredi Tahsis Yetki Aşımı İncelemesi\n\n"
    "Yapılan denetim sonucunda, Levent Şubesi kredi tahsis komitesinin 5411 sayılı Bankacılık Kanunu "
    "kredi sınırları ve bankamız Kredi Tahsis Yönetmeliği Madde 12 hükmünde belirtilen 5.000.000 TL "
    "şube onay limitini aşarak, Genel Müdürlük onayı almaksızın bir firmaya 7.500.000 TL ticari kredi "
    "kullandırdığı tespit edilmiştir. İlgili yetki aşımının risk ve uyum açısından incelenmesi gerekmektedir."
)

SAMPLE_UNSUPPORTED_TEXT = (
    "Bankanız Kadıköy şubesindeki müşteri temsilcisinin tavır ve davranışlarından şikayetçiyim. "
    "Bana güler yüz göstermedi ve işlemlerimi çok yavaş yaptı. Kendisine disiplin cezası verilmesini "
    "ve bana 50.000 TL manevi tazminat ödenmesini talep ediyorum."
)


CITATION_SCOPE_NOTE = (
    "Alıntı doğrulaması, iktibas edilen cümlenin getirilen mevzuat parçasında (boşluk ve "
    "noktalama farkları gözetilmeden) geçtiğini gösterir. Hükmün somut olaya uygulanabilirliği "
    "denetçi değerlendirmesine bağlıdır."
)


UPLOAD_TYPES = ["pdf", "docx", "txt"]
MIN_DOCUMENT_CHARS = 15


@dataclass
class UploadedDocument:
    text: str
    title: str
    info: str


def load_uploaded_document(name: str, data: bytes) -> UploadedDocument:
    """Yüklenen belgeden analiz metni ve başlık. PDF'ler mevzuat içe aktarmayla aynı çıkarma
    hattından (kalite kapısı, yedek motorlar, OCR) geçer. Metin çıkmazsa ValueError."""
    extracted = extract_file_text(name, data)
    text = extracted.text.strip()
    if len(text) < MIN_DOCUMENT_CHARS:
        raise ValueError(
            "Belgeden okunabilir metin çıkarılamadı. Taranmış bir PDF ise OCR kurulumu gerekir; "
            "metni kopyalayıp aşağıdaki alana yapıştırabilirsiniz."
        )
    parts = [f"{len(text):,} karakter".replace(",", ".")]
    if extracted.pages:
        parts.append(f"{extracted.pages} sayfa")
    if extracted.ocr_used:
        parts.append("OCR kullanıldı")
    engine = extracted.diagnostic.get("selected_engine")
    if engine:
        parts.append(f"motor: {engine}")
    return UploadedDocument(text=text, title=Path(name).stem, info=", ".join(parts))


REVIEW_HISTORY_COLUMNS = [
    ("reviewed_at", "İnceleme zamanı"),
    ("document_title", "Belge"),
    ("document_type", "Tür"),
    ("reviewer_status", "Karar"),
    ("reviewer_name", "Denetçi"),
    ("finding_count", "Bulgu"),
    ("unsupported_claim_count", "Dayanaksız iddia"),
    ("reviewer_note", "Not"),
    ("analysis_id", "Analiz kimliği"),
]


def review_history_rows(history: List[Dict[str, Any]], status: str = "") -> List[Dict[str, Any]]:
    """Denetim kaydındaki inceleme kararlarını tablo satırlarına çevirir; status verilirse süzer."""
    return [
        {label: row.get(key, "") for key, label in REVIEW_HISTORY_COLUMNS}
        for row in history
        if not status or row.get("reviewer_status") == status
    ]


def render_review_history() -> None:
    """İnceleme Geçmişi sekmesi (M15.4; önceden belge sayfasının altında kapalı bölümdü)."""
    from mufettis.audit.service import analysis_review_history

    st.subheader("İnceleme geçmişi")
    st.caption("Kaydedilmiş denetçi kararları (yalnız okunur).")
    try:
        history, unreadable = analysis_review_history()
    except Exception as exc:
        st.error(f"Denetim kaydı okunamadı: {exc}")
        return
    if unreadable:
        st.warning(f"Denetim kaydında okunamayan {unreadable} satır atlandı.")
    if not isinstance(history, list) or not history:
        st.caption("Henüz kaydedilmiş inceleme yok.")
        return
    statuses = sorted({str(r.get("reviewer_status") or "") for r in history} - {""})
    status = st.selectbox("Karara göre süz", [""] + statuses, format_func=lambda s: s or "Tümü",
                          key="analysis_review_history_filter")
    st.dataframe(review_history_rows(history, status if isinstance(status, str) else ""),
                 use_container_width=True, hide_index=True)


CITED_STATUS_ICONS = {IN_CORPUS: "✅", NOT_IN_CORPUS: "⚠️", INTERNAL: "🏢", NOT_CHECKED: "⚪"}


def render_cited_regulations(result: AnalysisResult) -> None:
    """Belgenin kendi atıf yaptığı mevzuat ve veritabanında olup olmadığı (bulgu yokluğunun nedeni)."""
    refs = result.cited_regulations
    if not refs:
        return
    st.markdown("**Belgenin atıf yaptığı mevzuat:**")
    st.markdown("\n".join(f"- {CITED_STATUS_ICONS.get(r.status, '•')} {cited_describe(r)}" for r in refs))
    note = cited_missing_note(refs, has_findings=bool(result.findings))
    if note:
        st.warning(f"📥 {note}")


def citation_badge(citation: Citation) -> str:
    """Alıntının doğrulama durumunu, yalnızca motorun yazdığı sonuca göre gösterir."""
    status = citation.metadata.get("is_valid")
    if status is True:
        return "✅ Alıntı kanıt metninde doğrulandı"
    if status is False:
        return "❌ Alıntı doğrulanamadı"
    return "⚪ Doğrulama bilgisi yok"


def render_evidence_text_html(text_span: str) -> str:
    """Formats evidence text into a high-contrast HTML block resilient to theme changes.

    Guarantees explicit, accessible contrast (dark charcoal text #111827 on soft light background #f8f9fa)
    with !important declarations to override any dark-theme foreground color inheritance.
    """
    escaped_text = html.escape(text_span)
    return (
        f'<div style="background-color: #f8f9fa !important; color: #111827 !important; '
        f'border: 1px solid #e2e8f0; border-left: 4px solid #0d6efd !important; '
        f'border-radius: 4px; padding: 0.85rem; margin-top: 0.5rem; margin-bottom: 0.5rem; '
        f'white-space: pre-wrap; font-family: -apple-system, BlinkMacSystemFont, '
        f'\'Segoe UI\', Roboto, sans-serif; font-size: 0.92rem; line-height: 1.6; '
        f'word-break: break-word;">{escaped_text}</div>'
    )


# Yeni kontrol başlatılınca temizlenen oturum değerleri (M15.6); ayarlar ve yetki tablosu kalır.
_CHECK_STATE_PREFIXES = ("analysis_ui_", "check_", "rules_", "credit_file_")
_CHECK_STATE_KEEP = {"analysis_ui_llm_verification", "credit_file_authority", "credit_file_authority_editor",
                     "check_upload_nonce"}


SAMPLE_TEMPLATES = {
    "📑 Örnek Tüketici Şikayeti": (SAMPLE_COMPLAINT_TEXT, "Hesap İşletim Ücreti Kesintisi İtirazı",
                                  "Tüketici Şikayet Başvurusu"),
    "🏢 Örnek İç Denetim Notu": (SAMPLE_INTERNAL_TEXT, "Şube Kredi Tahsis Yetki Aşımı Denetimi", "Teftiş Kurulu Notu"),
    "⚖️ Örnek Dayanıksız İddia (Negatif)": (SAMPLE_UNSUPPORTED_TEXT, "Müşteri Memnuniyeti Şikayeti",
                                           "Genel Şikayet Dilekçesi"),
}


def _load_template(sample) -> None:
    """Hızlı şablon (M15.8: mevzuat yardımcısı bölümünde): düğmenin geri çağrısında metin alanları doldurulur."""
    text, title, source = sample
    st.session_state["analysis_ui_text"] = text
    st.session_state["analysis_ui_title"] = title
    st.session_state["analysis_ui_source"] = source
    st.session_state["analysis_exec_result"] = None


SAMPLE_DIR = Path(__file__).resolve().parent / "samples" / "kredi_dosyasi"


def sample_items() -> List[Any]:
    """Örnek kredi dosyası (M15.9): uydurma bilgili 4 belge (onay, kullandırım, ipotek, imza sirküleri)."""
    cache = st.session_state.setdefault("check_texts", {})
    out = []
    for path in sorted(SAMPLE_DIR.glob("*.docx")):
        key = f"sample:{path.name}"
        if key not in cache:
            cache[key] = load_uploaded_document(path.name, path.read_bytes())
        out.append((path.name, cache[key].text, cache[key]))
    return out


def _load_sample() -> None:
    st.session_state["check_sample"] = True
    st.session_state["analysis_exec_result"] = None


def render_first_steps() -> None:
    """Boş ekran (M15.9): üç adım ve örnek dosya."""
    with st.container(border=True):
        st.markdown("**Başlamak için belge yükleyin:** tek belge (ör. kefalet sözleşmesi) ya da kredi dosyasının "
                    "bütün belgeleri.")
        st.markdown("1. **Belgeleri yükleyin** — üstteki alana sürükleyin ya da \"Dosya seç\" ile seçin.\n"
                    "2. **Sonucu okuyun** — en üstte tek cümlelik durum ve bulgular; belgeden okunamayan bilgiler "
                    "size sorulur, cevabınızla kontrol yenilenir.\n"
                    "3. **Word çalışma kâğıdını indirin** — bulgular, dayanakları ve kanıtlarıyla.")
        st.button("🧪 Örnek dosyayla dene", key="check_sample_button", on_click=_load_sample,
                  help="Uydurma bilgili 4 belgelik örnek kredi dosyası (onay, kullandırım, ipotek, imza sirküleri) "
                       "yüklenmiş gibi kontrol edilir.")


def reset_check() -> None:
    """"Yeni kontrol başlat": yüklenenler, metin, cevaplar, tür seçimleri ve sonuçlar silinir; yükleyici boşalsın
    diye anahtarı yenilenir. Düğmenin geri çağrısında çalışır (widget değerleri ancak çizimden önce değişebilir)."""
    for key in list(st.session_state.keys()):
        if key in _CHECK_STATE_KEEP:
            continue
        if key == "analysis_exec_result" or str(key).startswith(_CHECK_STATE_PREFIXES):
            del st.session_state[key]
    st.session_state["check_upload_nonce"] = st.session_state.get("check_upload_nonce", 0) + 1


def _read_uploads(files) -> List[Any]:
    """Yüklenen dosyalar (ad, metin, UploadedDocument); her dosya bir kez okunur (OCR yavaş olabilir)."""
    cache = st.session_state.setdefault("check_texts", {})
    out = []
    for f in files:
        data = f.getvalue()
        if not isinstance(data, (bytes, bytearray)):
            continue
        fid = hashlib.sha256(data).hexdigest()
        if fid not in cache:
            try:
                with st.spinner(f"{f.name} okunuyor… (taranmış PDF'lerde birkaç dakika sürebilir)"):
                    cache[fid] = load_uploaded_document(f.name, data)
            except Exception as exc:
                st.error(f"{f.name} okunamadı: {exc}")
                continue
        out.append((f.name, cache[fid].text, cache[fid]))
    return out


def _assistant_sentence(result: AnalysisResult) -> str:
    """Mevzuat yardımcısının kısa sonucu (M15.8)."""
    if result.findings:
        text = f"{len(result.findings)} olası bulgu mevzuat kanıtıyla eşleşti"
    elif result.status == "completed_insufficient_evidence":
        text = "Mevzuat kütüphanesinde ilgili kanıt bulunamadı; değerlendirme yapılamadı (uygunluk teyidi değildir)"
    else:
        text = "Doğrulanmış bulgu üretilemedi (uygunluk teyidi değildir)"
    if result.unsupported_claims:
        text += f"; {len(result.unsupported_claims)} iddia için mevzuat dayanağı bulunamadı"
    return text + "."


def render_analysis_page(
    rag_client: Optional[Any] = None,
    presenter: Optional[AnalysisPresenter] = None,
    rag_provider: Optional[Callable[[], Any]] = None,
) -> None:
    """Renders the main Analysis Intelligence screen inside Streamlit.

    rag_provider (M15.3): mevzuat kütüphanesi sayfa açılırken değil, ANALİZİ BAŞLAT'a basılınca kurulur."""
    if st is None:
        return

    # M15.9: sayfa başlığı işi anlatıyor; eski "Belge Analizi & Mevzuat Uyum Denetimi" başlığı kalktı.

    # Scoped contrast and theme protection for code/evidence blocks
    st.markdown(
        """<style>
        [data-testid="stCode"] {
            border: 1px solid rgba(128, 128, 128, 0.2);
            border-radius: 6px;
        }
        [data-testid="stCode"] pre {
            background-color: rgba(128, 128, 128, 0.08) !important;
            color: inherit !important;
        }
        [data-testid="stCode"] code, [data-testid="stCode"] code span {
            color: inherit !important;
            background-color: transparent !important;
        }
        </style>""",
        unsafe_allow_html=True,
    )

    # Initialize Session State
    if "analysis_exec_result" not in st.session_state:
        st.session_state["analysis_exec_result"] = None
    if "analysis_ui_text" not in st.session_state:
        st.session_state["analysis_ui_text"] = ""
    if "analysis_ui_title" not in st.session_state:
        st.session_state["analysis_ui_title"] = ""
    if "analysis_ui_source" not in st.session_state:
        st.session_state["analysis_ui_source"] = ""

    if presenter is None:
        if "analysis_presenter" not in st.session_state:
            st.session_state["analysis_presenter"] = AnalysisPresenter()
        presenter = st.session_state["analysis_presenter"]

    # ── Input Area (M15.6: tek giriş) ────────────────────────────────────────
    st.markdown("### 1. Belge Girişi")
    nonce = st.session_state.get("check_upload_nonce", 0)
    files = st.file_uploader(
        "Belge yükle (PDF, DOCX, TXT) — kredi dosyasının bütün belgelerini birlikte seçebilirsiniz",
        type=UPLOAD_TYPES,
        accept_multiple_files=True,
        key=f"check_upload_{nonce}",
    )
    items = _read_uploads(files if isinstance(files, list) else [])
    if not items and st.session_state.get("check_sample"):
        items = sample_items()
    st.button("🔄 Yeni kontrol başlat", key="check_new", on_click=reset_check,
              help="Yüklenen belgeleri, metni, cevaplarınızı ve sonuçları temizler; ayarlar ve yetki tablosu kalır.")
    # Yükleyici metin alanından önce: metin alanının anahtarına yalnızca widget oluşmadan yazılabilir.
    if items:
        upload_id = hashlib.sha256("\n".join(t for _, t, _ in items).encode("utf-8")).hexdigest()
        # Aynı dosyalar her yeniden çizimde tekrar yazılmaz; kullanıcının metinde yaptığı düzeltme korunur.
        if st.session_state.get("analysis_ui_upload_id") != upload_id:
            st.session_state["analysis_ui_upload_id"] = upload_id
            if len(items) == 1:
                name, text, loaded = items[0]
                st.session_state["analysis_ui_text"] = text
                if not st.session_state.get("analysis_ui_title", "").strip():
                    st.session_state["analysis_ui_title"] = loaded.title
                st.session_state["analysis_ui_upload_info"] = f"{name}: {loaded.info}"
            else:  # mevzuat yardımcısı dosyanın bütün metnine bakar
                st.session_state["analysis_ui_text"] = "\n\n".join(f"=== {n} ===\n{t}" for n, t, _ in items)
                st.session_state["analysis_ui_upload_info"] = f"{len(items)} belge"
            st.session_state["analysis_exec_result"] = None
        info = st.session_state.get("analysis_ui_upload_info")
        if info:
            st.caption(f"📄 Yüklenen: {info}.")

    with st.expander("Metni yapıştırarak kontrol (dosya yerine) ve metni düzeltme", expanded=False):
        in_col1, in_col2 = st.columns(2)
        doc_title = in_col1.text_input(
            "Belge Başlığı (Opsiyonel)",
            placeholder="Örn: 2024/03 Şube Denetim Raporu",
            key="analysis_ui_title",
        )
        doc_source = in_col2.text_input(
            "Kaynak / Birim (Opsiyonel)",
            placeholder="Örn: Bireysel Bankacılık / BDDK İletişim / Müşteri",
            key="analysis_ui_source",
        )
        doc_text = st.text_area(
            "Belge Metni",
            height=180,
            placeholder="Kontrol edilecek belgenin metnini buraya yapıştırınız...",
            key="analysis_ui_text",
            help="Belge yüklediyseniz metni burada düzeltebilirsiniz. En az 15 karakter.",
        )

    # Bir belge (ya da yapıştırılan metin) → belgenin kendi kontrolü; birden çok → kredi dosyası kontrolü
    from mufettis.ui.rules_view import render_collateral_check, render_credit_file_check
    if len(items) > 1:
        render_credit_file_check([(n, t) for n, t, _ in items])
    elif isinstance(doc_text, str) and doc_text.strip():
        render_collateral_check(doc_text)
    else:
        render_first_steps()

    st.divider()
    st.markdown("### Mevzuat yardımcısı")
    st.caption("Belgedeki iddiaları yerel mevzuat kütüphanesiyle karşılaştırır ve olası bulguları kanıtıyla "
               "listeler. Kural kontrolünden ayrıdır; sonuçları karar desteğidir.")
    with st.expander("Örnek metinle dene (şikâyet / iç not)", expanded=False):
        col_t1, col_t2, col_t3 = st.columns(3)
        for col, (label, sample) in zip((col_t1, col_t2, col_t3), SAMPLE_TEMPLATES.items()):
            col.button(label, use_container_width=True, on_click=_load_template, args=(sample,),
                       key=f"analysis_template_{label}")

    from mufettis.llm.client import ollama_health
    llm_health = ollama_health()
    use_llm = st.checkbox(
        "Yerel dil modeliyle kanıt doğrulaması (Ollama)"
        + (f" — {llm_health['model']}" if llm_health.get("ok") else " — model hazır değil"),
        value=False,
        key="analysis_ui_llm_verification",
        disabled=not llm_health.get("ok"),
        help="Model, yalnızca getirilen mevzuat parçaları arasından iddiaya uygulanabilir hükmü seçer ve "
             "alıntısı birebir doğrulanır. Kullanılan model Admin sekmesinden seçilir; Ollama çalışmıyorsa "
             "ya da model kurulu değilse sözcüksel eşleştirme kullanılır.",
    ) and bool(llm_health.get("ok"))
    analyze_clicked = st.button("🚀 ANALİZİ BAŞLAT", type="primary", use_container_width=True)

    # ── Pipeline Execution ────────────────────────────────────────────────────
    if analyze_clicked:
        doc_text = st.session_state.get("analysis_ui_text", "")
        doc_title = st.session_state.get("analysis_ui_title", "")
        doc_source = st.session_state.get("analysis_ui_source", "")
        if rag_client is None and rag_provider is not None:
            rag_client = rag_provider()
        llm_note = presenter.configure_llm_verification(use_llm is True)
        if llm_note:
            st.warning(llm_note)

        # Aşama satırları presenter bir aşamaya geçtiği anda yazılır (gerçek ilerleme).
        with st.status("Analiz Intelligence hattı çalıştırılıyor...", expanded=True) as status:
            def _on_stage(index: int, _stage_key: str, label: str) -> None:
                st.write(f"{index}/{len(PIPELINE_STAGES)}. {label}")

            exec_result = presenter.execute_analysis(
                text=doc_text,
                title=doc_title if doc_title.strip() else None,
                source=doc_source if doc_source.strip() else None,
                rag_client=rag_client,
                progress_callback=_on_stage,
            )

            if exec_result.success:
                status.update(
                    label=f"Analiz tamamlandı ({exec_result.execution_time_sec:.2f} sn)",
                    state="complete",
                    expanded=False,
                )
                st.session_state["analysis_exec_result"] = exec_result
            else:
                status.update(
                    label="Analiz başlatılamadı veya durduruldu.",
                    state="error",
                    expanded=True,
                )
                st.error(exec_result.error_message or "Bilinmeyen bir hata oluştu.")
                st.session_state["analysis_exec_result"] = None
                return

    # ── Display Analysis Results ──────────────────────────────────────────────
    exec_result: Optional[AnalysisExecutionResult] = st.session_state.get("analysis_exec_result")
    if not exec_result or not exec_result.result:
        st.caption("Mevzuat yardımcısı için 'ANALİZİ BAŞLAT'a basın.")
        return

    result: AnalysisResult = exec_result.result

    st.divider()
    # ── Kısa sonuç (M15.8) ──
    st.markdown(f"**{_assistant_sentence(result)}**")
    cited_note = cited_missing_note(result.cited_regulations or [], has_findings=bool(result.findings))
    if cited_note:
        st.warning(f"📥 {cited_note}")

    # ── Safety / Insufficient Evidence Warnings ───────────────────────────────
    if result.status == "completed_insufficient_evidence" or result.warnings:
        with st.container():
            if result.status == "completed_insufficient_evidence":
                st.warning(
                    "⚠️ **Yetersiz Kanıt Uyarısı:** Yerel mevzuat veri tabanında bu iddiaları doğrudan destekleyen "
                    "doğrulanabilir kanıt parçası bulunamadı. Sahte veya dayanıksız denetim bulgusu üretilmedi."
                )
            for warn in result.warnings:
                st.caption(f"ℹ️ {warn}")

    # ── Bulgular (M15.8: sonuç önce, mevzuat kanıtı bulgunun altında açılır) ──
    st.markdown(f"#### Olası bulgular ({len(result.findings)})")
    if not result.findings:
        # Bulgu olmaması uygunluk teyidi değildir; kanıt yokluğu ile eşleşme yokluğu ayrı gösterilir.
        if result.status == "completed_insufficient_evidence":
            st.warning(
                "⚠️ **Değerlendirme Yapılamadı:** Mevzuat tabanında ilgili kanıt bulunamadığı için "
                "bulgu üretilemedi. Bu sonuç belgenin mevzuata uygun olduğu anlamına gelmez; "
                "konunun denetçi tarafından manuel incelenmesi gerekir."
            )
        else:
            st.info(
                "ℹ️ **Doğrulanmış Bulgu Üretilemedi:** Getirilen mevzuat kanıtları belgedeki iddialarla "
                "doğrulanabilir biçimde eşleşmedi. Bu sonuç bir uygunluk teyidi değildir; "
                "nihai değerlendirme denetçiye aittir."
            )
    for idx, finding in enumerate(result.findings, 1):
        risk_color = {"Yüksek": "🔴", "Orta": "🟡", "Düşük": "🟢", "Belirsiz": "⚪"}.get(finding.risk_level or "Orta", "🟡")
        confidence_badge = {
            "yüksek": " `Dayanak güveni: yüksek`",
            "düşük": " `⚠ Dayanak güveni: düşük — atfı doğrulayın`",
        }.get(finding.match_confidence or "", "")
        with st.container(border=True):
            st.markdown(f"**{idx}. {risk_color} {finding.issue}** `Risk: {finding.risk_level or 'Orta'}`"
                        f"{confidence_badge}")
            st.markdown(f"**Gözlem:** {finding.explanation}")
            st.markdown(f"**Yapılacak iş:** 👉 {finding.inspector_action}")
            with st.expander("Mevzuat dayanağı, model yorumu ve alıntılar", expanded=False):
                st.markdown("**Mevzuat atfı:**")
                st.code(finding.regulation_reference, language="text")
                st.markdown("**Model yorumu:**")
                st.caption(
                    finding.model_interpretation
                    or "Model, olay ile ilgili mevzuat hükmü arasında uyumsuzluk değerlendirmiştir."
                )
                if finding.citations:
                    st.markdown("**Mevzuat alıntıları:**")
                    for c_idx, cit in enumerate(finding.citations, 1):
                        val_badge = citation_badge(cit)
                        st.markdown(
                            f"- **Alıntı #{c_idx} — {val_badge}**\n"
                            f"  - **Kaynak:** `{cit.document_identity}` ({cit.reference})\n"
                            f"  - **Birebir İktibas:** *\"{cit.text_span}\"*"
                        )
                    st.caption(CITATION_SCOPE_NOTE)

    # ── Section 6: Inspector Review & Actions Panel ───────────────────────────
    st.markdown("#### Müfettiş değerlendirmesi")
    st.caption(
        "Kaydedilen inceleme kararları (durum, denetçi, maskelenmiş not) denetim kaydına yazılır. "
        "Yapay zeka analizleri kesin karar niteliğinde olmayıp insan denetçinin onayına tabidir."
    )

    r_col1, r_col2 = st.columns([1, 2])
    status_options = ["Bekliyor", "İncelendi", "Kabul edildi", "Düzeltilmeli", "Reddedildi"]
    current_status = result.reviewer_status or "Bekliyor"
    status_index = status_options.index(current_status) if current_status in status_options else 0

    new_status = r_col1.selectbox(
        "İnceleme Durumu",
        options=status_options,
        index=status_index,
        # Anahtar analize özgü: yeni analizde önceki inceleme durumu/notu taşınmaz.
        key=f"analysis_reviewer_status_select_{result.analysis_id}",
    )

    reviewer_name = r_col1.text_input(
        "Denetçi Adı / Sicili",
        value=result.reviewer_name or st.session_state.get("analysis_reviewer_name", ""),
        key="analysis_reviewer_name_input",
    )

    reviewer_note = r_col2.text_area(
        "Müfettiş Notu / Değerlendirme Açıklaması",
        value=result.reviewer_note or "",
        height=100,
        placeholder="Bulguya ilişkin nihai kanaatinizi veya yapılması gereken ilave kontrolleri yazınız...",
        key=f"analysis_reviewer_notes_input_{result.analysis_id}",
    )

    if st.button("💾 İncelemeyi Kaydet", key="save_analysis_review_btn"):
        updated_result = presenter.apply_reviewer_action(
            result=result,
            status=new_status,
            note=reviewer_note,
            reviewer=reviewer_name,
        )
        exec_result.result = updated_result
        st.session_state["analysis_exec_result"] = exec_result
        st.session_state["analysis_reviewer_name"] = reviewer_name
        try:
            presenter.record_review(updated_result)
        except Exception as exc:
            st.error(f"İnceleme oturuma uygulandı ancak denetim kaydına yazılamadı: {exc}")
        else:
            st.success(f"İnceleme kaydedildi: {new_status} (Zaman: {updated_result.reviewed_at})")

    # ── Section 7: Export ─────────────────────────────────────────────────────
    report_result = exec_result.result
    try:
        report_bytes = create_analysis_docx(report_result)
    except Exception as exc:
        st.error(f"DOCX çalışma kağıdı oluşturulamadı: {exc}")
    else:
        st.download_button(
            "📄 Çalışma kağıdını indir (DOCX)",
            data=report_bytes,
            file_name=f"mufettis_analiz_{report_result.analysis_id[:8]}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key=f"analysis_docx_{report_result.analysis_id}",
        )


    # ── Teknik ayrıntı (kapalı; M15.8) ──
    st.divider()
    if st.toggle("Teknik ayrıntıları göster", value=False, key=f"analysis_technical_{result.analysis_id}",
                 help="Belge özeti, arama planı, getirilen bütün mevzuat kanıtları, belgede atıf yapılan mevzuat ve "
                      "dayanağı bulunamayan iddialar."):
        # ── Section 1: Document Summary ───────────────────────────────────────────
        st.markdown("#### Belge özeti ve sınıflandırma")
        doc_u: DocumentUnderstanding = result.document
        doc_c: DocumentClassification = result.classification

        # Document type badges
        doc_type_labels = {
            "complaint": ("🔴 Tüketici Şikayeti", "Şikayet / İtiraz Metni"),
            "consumer_complaint": ("🔴 Tüketici Şikayeti", "Şikayet / İtiraz Metni"),
            "internal_correspondence": ("🟡 İç Denetim / Yazışma", "Kurum İçi Not / Rapor"),
            "unknown": ("⚪ Bilinmeyen Doküman Türü", "Genel / Sınıflandırılamayan Metin"),
        }
        badge_label, badge_desc = doc_type_labels.get(
            doc_c.document_type,
            ("⚪ " + doc_c.document_type, "Belge")
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Doküman Türü", badge_label)
        main_subject = doc_u.subjects[0] if doc_u.subjects else "Genel"
        c2.metric("Tespit Edilen Konu", main_subject.replace("_", " ").title())
        c3.metric(
            "Sınıflandırma Güveni",
            f"%{doc_c.confidence * 100:.0f}",
            help="Modelin kural/sezgisel eşleşme skoru. Kalibre edilmiş genel olasılık değildir.",
        )
        c4.metric("İddia / Olay Sayısı", len(doc_u.claims))

        with st.expander("🔍 Belge Varlıkları ve İddia Detayları", expanded=False):
            st.markdown(f"**Başlık:** {doc_u.title or 'Belirtilmemiş'}")
            if doc_u.source:
                st.markdown(f"**Kaynak:** {doc_u.source}")
            if doc_u.date:
                st.markdown(f"**Tarih:** {doc_u.date}")

            if doc_c.signals:
                st.markdown("**Sınıflandırma Sinyalleri:**")
                st.write(doc_c.signals)

            st.markdown("**Çıkarılan Varlıklar (Entities):**")
            if doc_u.entities:
                entity_rows = [
                    {"Varlık": e.text, "Tür": e.entity_type, "Değer / Bilgi": str(e.normalized_value or "-")}
                    for e in doc_u.entities
                ]
                st.table(entity_rows)
            else:
                st.caption("Özel varlık tespit edilmedi.")

            st.markdown("**Belgedeki İddialar (Claims):**")
            if doc_u.claims:
                for idx, cl in enumerate(doc_u.claims, 1):
                    st.markdown(f"- **İddia #{idx} ({cl.subject}):** {cl.statement}")
            else:
                st.caption("Spesifik iddia çıkarılmadı.")

        # ── Section 2: Analysis Plan ──────────────────────────────────────────────
        st.markdown("#### Analiz ve arama planı")
        plan: AnalysisPlan = result.analysis_plan
        with st.expander(f"📋 Plan: {plan.analysis_type} ({plan.objective[:60]}...)", expanded=False):
            st.markdown(f"**Analiz Türü:** `{plan.analysis_type}`")
            st.markdown(f"**Hedef:** {plan.objective}")
            if plan.parameters.scope:
                st.markdown(f"**Kapsam:** {', '.join(plan.parameters.scope)}")
            if plan.parameters.target_entities:
                st.markdown(f"**Hedef Varlıklar:** {', '.join(plan.parameters.target_entities)}")
            if plan.parameters.constraints:
                st.markdown(f"**Kısıtlar:** {plan.parameters.constraints}")
            if plan.parameters.requested_outputs:
                st.markdown(f"**Beklenen Çıktılar:** {', '.join(plan.parameters.requested_outputs)}")

            st.markdown("---")
            st.markdown(f"**Retrieval Planı Sorguları ({len(result.retrieval_plan.queries)} adet):**")
            for q in result.retrieval_plan.queries:
                st.code(q, language="text")
            if result.retrieval_plan.source_scope:
                st.caption(f"Hedef kaynaklar: {', '.join(result.retrieval_plan.source_scope)}")
            if result.retrieval_plan.evidence_requirements:
                st.caption(f"Delil kriterleri: {', '.join(result.retrieval_plan.evidence_requirements)}")
            st.caption(f"Maksimum kanıt sayısı: {result.retrieval_plan.max_evidence_items}")

        # ── Section 3: Retrieved Evidence ─────────────────────────────────────────
        st.markdown("#### Getirilen bütün mevzuat kanıtları")
        # Kopya: aşağıdaki yedek doldurma oturumda saklanan listeyi değiştirmemeli.
        evidence_items = list(exec_result.retrieved_evidence)
        if not evidence_items:
            # Fallback to evidence attached to findings if retrieved_evidence pool was empty
            seen_ev = set()
            for f in result.findings:
                for ev in f.evidence:
                    k = (ev.document_identity, ev.chunk_reference)
                    if k not in seen_ev:
                        seen_ev.add(k)
                        evidence_items.append(ev)

        st.caption(f"Toplam {len(evidence_items)} adet mevzuat kanıt parçası incelendi.")

        if not evidence_items:
            st.info("Bu analiz için mevzuat kanıtı getirilemedi.")
        else:
            for i, ev in enumerate(evidence_items, 1):
                with st.expander(
                    f"📜 Kanıt #{i}: {ev.source_document} — {ev.chunk_reference} (ID: {ev.document_identity})",
                    expanded=False,
                ):
                    meta_cols = st.columns(3)
                    meta_cols[0].markdown(f"**Kaynak:** {ev.source_document}")
                    meta_cols[1].markdown(f"**Mevzuat Kimliği:** `{ev.document_identity}`")
                    meta_cols[2].markdown(f"**Referans:** {ev.chunk_reference}")

                    st.markdown("**Kanıt Metni (Resmi Mevzuat Hükmü):**")
                    st.code(ev.text_span or "", language="text")

        render_cited_regulations(result)

        # ── Section 5: Unsupported Claims Panel ───────────────────────────────────
        if result.unsupported_claims:
            st.markdown("#### Mevzuat dayanağı bulunamayan iddialar")
            st.warning(
                "🛡️ **Dayanıksız İddia Koruması:** Aşağıdaki iddialar dokümanda yer almakta olup, "
                "yerel mevzuat tabanında bu iddiayı doğrudan destekleyen doğrulanabilir bir mevzuat hükmü "
                "bulunamamıştır. Halüsinasyonu önlemek amacıyla bu iddialar için sahte bulgu üretilmemiştir."
            )
            for u_idx, u_cl in enumerate(result.unsupported_claims, 1):
                st.markdown(
                    f"- **İddia #{u_idx} ({u_cl.subject}):** {u_cl.statement} "
                    f"→ *Mevzuatsal kanıt yetersiz.*"
                )
            st.divider()


__all__ = ["render_analysis_page", "render_evidence_text_html", "citation_badge", "load_uploaded_document"]
