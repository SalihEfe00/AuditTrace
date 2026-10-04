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
from typing import Any, Dict, List, Optional

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
    from mufettis.audit.service import analysis_review_history

    with st.expander("📚 İnceleme geçmişi (kaydedilmiş denetçi kararları)", expanded=False):
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


def render_analysis_page(
    rag_client: Optional[Any] = None,
    presenter: Optional[AnalysisPresenter] = None,
) -> None:
    """Renders the main Analysis Intelligence screen inside Streamlit."""
    if st is None:
        return

    st.subheader("📋 Müfettiş Belge Analizi & Mevzuat Uyum Denetimi")
    st.caption(
        "Operasyonel belgelerin (müşteri şikayetleri, teftiş notları, iç yazışmalar) "
        "mevzuat uygunluğunu analiz eden, kanıtları yerel RAG ile doğrulayan ve "
        "denetim bulgusu üreten karar destek sistemi."
    )

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

    # ── Sample Templates Row ──────────────────────────────────────────────────
    st.markdown("##### Hızlı Şablonlar")
    col_t1, col_t2, col_t3 = st.columns(3)
    if col_t1.button("📑 Örnek Tüketici Şikayeti", use_container_width=True):
        st.session_state["analysis_ui_text"] = SAMPLE_COMPLAINT_TEXT
        st.session_state["analysis_ui_title"] = "Hesap İşletim Ücreti Kesintisi İtirazı"
        st.session_state["analysis_ui_source"] = "Tüketici Şikayet Başvurusu"
        st.session_state["analysis_exec_result"] = None

    if col_t2.button("🏢 Örnek İç Denetim Notu", use_container_width=True):
        st.session_state["analysis_ui_text"] = SAMPLE_INTERNAL_TEXT
        st.session_state["analysis_ui_title"] = "Şube Kredi Tahsis Yetki Aşımı Denetimi"
        st.session_state["analysis_ui_source"] = "Teftiş Kurulu Notu"
        st.session_state["analysis_exec_result"] = None

    if col_t3.button("⚖️ Örnek Dayanıksız İddia (Negatif)", use_container_width=True):
        st.session_state["analysis_ui_text"] = SAMPLE_UNSUPPORTED_TEXT
        st.session_state["analysis_ui_title"] = "Müşteri Memnuniyeti Şikayeti"
        st.session_state["analysis_ui_source"] = "Genel Şikayet Dilekçesi"
        st.session_state["analysis_exec_result"] = None

    st.divider()

    # ── Input Area ────────────────────────────────────────────────────────────
    st.markdown("### 1. Belge Girişi")
    # Yükleyici metin alanından önce: metin alanının anahtarına yalnızca widget oluşmadan yazılabilir.
    uploaded = st.file_uploader(
        "Belge yükle (PDF, DOCX, TXT) — veya metni aşağıya yapıştırın",
        type=UPLOAD_TYPES,
        key="analysis_ui_upload",
    )
    data = uploaded.getvalue() if uploaded is not None else None
    if isinstance(data, (bytes, bytearray)):
        upload_id = hashlib.sha256(data).hexdigest()
        # Aynı dosya her yeniden çizimde tekrar çıkarılmaz; kullanıcının metinde yaptığı düzeltme korunur.
        if st.session_state.get("analysis_ui_upload_id") != upload_id:
            st.session_state["analysis_ui_upload_id"] = upload_id
            try:
                loaded = load_uploaded_document(uploaded.name, data)
            except Exception as exc:
                st.error(f"Belge okunamadı: {exc}")
            else:
                st.session_state["analysis_ui_text"] = loaded.text
                if not st.session_state.get("analysis_ui_title", "").strip():
                    st.session_state["analysis_ui_title"] = loaded.title
                st.session_state["analysis_ui_upload_info"] = f"{uploaded.name}: {loaded.info}"
                st.session_state["analysis_exec_result"] = None
        info = st.session_state.get("analysis_ui_upload_info")
        if info:
            st.caption(f"📄 Yüklenen belge — {info}. Metni analizden önce aşağıda düzeltebilirsiniz.")

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
        "Belge Metni *",
        height=180,
        placeholder="Analiz edilecek şikayet, teftiş notu veya iç yazışma metnini buraya yapıştırınız...",
        key="analysis_ui_text",
        help="En az 15 karakter içeren metin girilmelidir.",
    )

    # M9: teminat belgesi kuralları (belirsiz alanlar müfettişe sorulur)
    from mufettis.ui.rules_view import render_collateral_check, render_credit_file_check
    render_collateral_check(doc_text)
    # M10: kredi dosyası (birden çok belge, belgeler arası kurallar)
    render_credit_file_check()

    use_llm = st.checkbox(
        "Yerel dil modeliyle kanıt doğrulaması (Ollama)",
        value=False,
        key="analysis_ui_llm_verification",
        help="Model, yalnızca getirilen mevzuat parçaları arasından iddiaya uygulanabilir hükmü seçer ve "
             "alıntısı birebir doğrulanır. Model erişilemezse sözcüksel eşleştirme kullanılır.",
    )
    analyze_clicked = st.button("🚀 ANALİZİ BAŞLAT", type="primary", use_container_width=True)

    # ── Pipeline Execution ────────────────────────────────────────────────────
    if analyze_clicked:
        doc_text = st.session_state.get("analysis_ui_text", "")
        doc_title = st.session_state.get("analysis_ui_title", "")
        doc_source = st.session_state.get("analysis_ui_source", "")
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

    render_review_history()

    # ── Display Analysis Results ──────────────────────────────────────────────
    exec_result: Optional[AnalysisExecutionResult] = st.session_state.get("analysis_exec_result")
    if not exec_result or not exec_result.result:
        st.info("Analiz başlatmak için bir belge yükleyin veya metni yapıştırıp 'ANALİZİ BAŞLAT' butonuna basınız.")
        return

    result: AnalysisResult = exec_result.result

    st.divider()

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

    # ── Section 1: Document Summary ───────────────────────────────────────────
    st.markdown("### 2. Belge Özeti & Sınıflandırma")
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
    st.markdown("### 3. Analiz & Arama Planı")
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
    st.markdown("### 4. İlgili Mevzuat Kanıtları (Retrieved Evidence)")
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

    # ── Section 4: Findings ───────────────────────────────────────────────────
    st.markdown("### 5. Müfettiş Denetim Bulguları")
    render_cited_regulations(result)
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
    else:
        for idx, finding in enumerate(result.findings, 1):
            risk_color = {
                "Yüksek": "🔴",
                "Orta": "🟡",
                "Düşük": "🟢",
                "Belirsiz": "⚪",
            }.get(finding.risk_level or "Orta", "🟡")

            with st.container():
                confidence_badge = {
                    "yüksek": " `Dayanak güveni: yüksek`",
                    "düşük": " `⚠ Dayanak güveni: düşük — atfı doğrulayın`",
                }.get(finding.match_confidence or "", "")
                st.markdown(
                    f"#### Bulgu #{idx}: {risk_color} {finding.issue} "
                    f"`Risk: {finding.risk_level or 'Orta'}`{confidence_badge}"
                )

                # Strict Separation of the 5 Inspector Dimensions
                b_col1, b_col2 = st.columns([1, 1])

                with b_col1:
                    st.markdown("**1. Gözlem (Observation / Somut Olay):**")
                    st.info(finding.explanation)

                    st.markdown("**2. Model Yorumu (Model Interpretation):**")
                    st.caption(
                        finding.model_interpretation
                        or "Model, olay ile ilgili mevzuat hükmü arasında uyumsuzluk değerlendirmiştir."
                    )

                with b_col2:
                    st.markdown("**3. Mevzuat Atfı (Regulation Reference):**")
                    st.code(finding.regulation_reference, language="text")

                    st.markdown("**4. Müfettiş Takip Aksiyonu (Inspector Follow-up):**")
                    st.warning(f"👉 {finding.inspector_action}")

                # Citations Sub-Panel
                if finding.citations:
                    st.markdown("**5. Mevzuat Alıntıları (Citations):**")
                    for c_idx, cit in enumerate(finding.citations, 1):
                        val_badge = citation_badge(cit)
                        st.markdown(
                            f"- **Alıntı #{c_idx} — {val_badge}**\n"
                            f"  - **Kaynak:** `{cit.document_identity}` ({cit.reference})\n"
                            f"  - **Birebir İktibas:** *\"{cit.text_span}\"*"
                        )
                    st.caption(CITATION_SCOPE_NOTE)

                st.divider()

    # ── Section 5: Unsupported Claims Panel ───────────────────────────────────
    if result.unsupported_claims:
        st.markdown("### 6. Kökeni Doğrulanamayan İddialar (Dayanıksız İddia Guard)")
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

    # ── Section 6: Inspector Review & Actions Panel ───────────────────────────
    st.markdown("### 7. İnceleme & Müfettiş Değerlendirme Paneli")
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


__all__ = ["render_analysis_page", "render_evidence_text_html", "citation_badge", "load_uploaded_document"]
