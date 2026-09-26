# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    import streamlit as st
except Exception:
    st = None

import datetime as dt
import re
import uuid

from mufettis.audit.service import append_audit
from mufettis.common.utils import source_profile
from mufettis.config import (
    BASE_DIR,
    BULK_MAX_FILE_MB,
    CHUNK_SCHEMA_VERSION,
    DATA_DIR,
    DB_PATH,
    SOURCE_CACHE_DIR,
    WEB_SOURCE_MAX_DEPTH,
    WEB_SOURCE_MAX_PAGES,
)
from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper
from mufettis.harvesting.web_harvester import BrowserWebSourceHarvester, WebSourceHarvester
from mufettis.importers.bootstrapper import RegulationBootstrapper
from mufettis.importers.bulk_importer import BulkRegulationImporter, scan_regulation_folder
from mufettis.legal.parser import extract_legal_metadata
from mufettis.rag.index import IngestIndex
from mufettis.rag.maintenance import (
    build_rag_inventory,
    ingest_local_regulation_file,
    rebuild_rag_from_source_cache,
    reindex_cached_documents,
    reset_chroma_collection,
    versioned_ingest_text,
)
from repair.quarantine import (
    list_quarantined_documents,
    get_quarantine_detail,
    review_quarantine_document,
    retry_quarantined_document,
    verify_quarantine_payload_integrity,
)
from repair.telemetry import compute_operational_telemetry
from repair.audit import verify_audit_integrity, REPAIR_AUDIT_LOG_FILE, RepairAuditLogger

def _get_rag(**kwargs):
    from mufettis.ui.app import _get_rag as _fn
    return _fn(**kwargs)


def normalize_display_boolean(val: Any) -> bool:
    """Normalizes mixed bool/str/None representations to clean boolean for Arrow/UI display."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "t", "yes", "y", "evet"):
        return True
    if s in ("false", "0", "f", "no", "n", "hayir", "hayır", ""):
        return False
    return bool(val)


def render_mevzuat_admin(
    rag: Optional[LocalRAG],
    local_only: bool,
    use_browser_bddk: bool,
    allow_embedding_download: bool,
) -> None:
    """Kaynak ekleme/senkronizasyon/bootstrap/RAG rebuild yönetim alanı."""
    st.subheader("Mevzuat kaynak yönetimi")
    if rag is None:
        rag = _get_rag(allow_download=allow_embedding_download)
    st.write(f"Chroma kayıt sayısı: {rag.count() if rag else 0}")

    st.markdown("### RAG Envanteri")
    st.caption(
        "RAG içinde gerçekten indekslenmiş güncel belgeleri kaynak, belge ve chunk bazında gösterir. "
        "Bu bölüm yalnızca okur; indeksleme yapmaz."
    )
    inventory = build_rag_inventory(rag)
    if not inventory.get("available"):
        st.warning(inventory.get("error", "RAG envanteri kullanılamıyor."))
    else:
        ic1, ic2, ic3, ic4 = st.columns(4)
        ic1.metric("Belge", f"{inventory['total_documents']:,}")
        ic2.metric("Chunk", f"{inventory['total_chunks']:,}")
        ic3.metric("Aktif belge", f"{inventory['active_documents']:,}")
        ic4.metric("Kaynak", f"{len(inventory['sources']):,}")

        source_df = pd.DataFrame(inventory["sources"])
        if not source_df.empty:
            st.markdown("**Kaynak özeti**")
            st.dataframe(
                source_df[["source", "source_id", "documents", "chunks", "active_documents"]],
                use_container_width=True,
                hide_index=True,
            )

        doc_df = pd.DataFrame(inventory["documents"])
        if not doc_df.empty:
            st.markdown("**Belge envanteri**")
            inv_query = st.text_input(
                "Belge ara",
                value="",
                key="rag_inventory_query",
                placeholder="Belge adı, kanun no, kaynak veya belge anahtarı...",
            ).strip().lower()
            if inv_query:
                searchable = doc_df.fillna("").astype(str).agg(" ".join, axis=1).str.lower()
                doc_view = doc_df[searchable.str.contains(re.escape(inv_query), regex=True, na=False)].copy()
            else:
                doc_view = doc_df.copy()

            # Teknik parser durumunu kullanıcıya doğrudan "failed" olarak göstermeyiz.
            # Genel belge, hukuki yapı çözülemediği halde RAG'a alınabilir.
            if "parse_status" in doc_view.columns:
                doc_view = doc_view.copy()
                doc_view["parse_status"] = doc_view.apply(
                    lambda r: (
                        "Yapılandırılmış" if str(r.get("parse_status", "")).lower() == "parsed"
                        else "Genel belge" if str(r.get("structure_type", "")).lower() == "document"
                        else "Yetersiz içerik" if str(r.get("structure_type", "")).lower() == "title_only"
                        else "İşleme durumu bilinmiyor"
                    ),
                    axis=1,
                )
            display_inventory_cols = [
                "source", "title", "document_type", "law_number", "document_number",
                "status", "effective_date", "chunk_count",
                "document_indexable", "evidence_indexable",
                "legal_structure_resolved", "structure_type", "parse_status",
                "evidence_indexable_reason", "url",
            ]
            display_inventory_cols = [c for c in display_inventory_cols if c in doc_view.columns]

            # Boolean kolonları Arrow serileştirme hatasını (mixed type object) önlemek için normalize et
            for bool_col in ("legal_structure_resolved", "document_indexable", "evidence_indexable"):
                if bool_col in doc_view.columns:
                    doc_view[bool_col] = doc_view[bool_col].apply(normalize_display_boolean).astype(bool)

            st.caption(f"{len(doc_view):,} / {len(doc_df):,} belge gösteriliyor")
            st.dataframe(
                doc_view[display_inventory_cols],
                use_container_width=True,
                hide_index=True,
            )

    if rag is not None:
        st.caption(
            f"Chunk şeması: {CHUNK_SCHEMA_VERSION} — "
            "Madde / bölüm / hesap planı / genel belge / cümle-bazlı subchunk"
        )
        st.warning(
            "Eski/legacy Chroma kayıtları RAG sorgularına alınmaz. "
            "Aşağıdaki işlem eski koleksiyonu tamamen temizler ve source_cache "
            "belgelerini yeni chunk şemasıyla sıfırdan indeksler."
        )

        if st.button(
            "RAG'I TAMAMEN TEMİZLE VE SIFIRDAN KUR",
            type="primary",
            help=(
                "Eski Chroma koleksiyonunu siler. Mevcut source_cache "
                "belgelerini yeni hukuki chunk şemasıyla yeniden embed eder."
            ),
        ):
            confirm = st.checkbox(
                "Mevcut Chroma mevzuat kayıtlarının tamamen silinmesini onaylıyorum.",
                key="confirm_full_rag_reset",
            )
            if not confirm:
                st.info("Devam etmek için onay kutusunu işaretle.")
            else:
                rp = st.progress(0.0, text="Eski RAG temizleniyor...")
                rs = st.empty()
                try:
                    reset_chroma_collection(rag)

                    def _rebuild_progress(done, total, title, status, data):
                        pct = done / total if total else 1.0
                        rp.progress(
                            min(1.0, pct),
                            text=f"{done}/{total} — {status}",
                        )
                        rs.write(
                            f"Belge: **{title}** | Durum: **{status}** | "
                            f"Chunk: {data.get('chunks', 0)}"
                        )

                    rebuild = rebuild_rag_from_source_cache(
                        rag,
                        IngestIndex(),
                        progress_callback=_rebuild_progress,
                    )

                    if rebuild.get("errors", 0):
                        st.warning(
                            f"RAG yeniden kuruldu: "
                            f"{rebuild.get('documents_ok', 0)} belge / "
                            f"{rebuild.get('chunks', 0):,} chunk / "
                            f"{rebuild.get('errors', 0)} hata."
                        )
                    else:
                        st.success(
                            f"RAG yeniden kuruldu: "
                            f"{rebuild.get('documents_ok', 0)} belge / "
                            f"{rebuild.get('chunks', 0):,} chunk / 0 hata."
                        )
                    st.session_state["rag_rebuild_completed"] = True
                    st.write(f"Yeni Chroma kayıt sayısı: {rag.count():,}")
                except Exception as exc:
                    st.error(f"RAG sıfırdan kurulum hatası: {exc}")
    if rag is not None:
        st.caption(
            f"Chunk şeması: {CHUNK_SCHEMA_VERSION} — Madde / bölüm / hesap planı / genel belge"
        )
        if st.button(
            "RAG'I GÜNCEL YAPI VE METİN KALİTESİYLE YENİDEN İNDEKSLE",
            help="Mevcut source_cache belgelerini güncel yapı keşfi, metin boşluk onarımı ve chunk şemasıyla yeniden işler; Chroma koleksiyonunu silmez.",
        ):
            rp = st.progress(0.0, text="Chunk migration hazırlanıyor...")
            rs = st.empty()

            def _rechunk_progress(done, total, title, status, data):
                pct = done / total if total else 1.0
                rp.progress(min(1.0, pct), text=f"{done}/{total} — {status}")
                rs.write(f"Belge: **{title}** | Chunk: {data.get('chunks', 0)}")

            try:
                rr = reindex_cached_documents(
                    rag,
                    IngestIndex(),
                    progress_callback=_rechunk_progress,
                    force=True,
                )
                if rr.get("errors", 0):
                    st.warning(
                        f"Migration tamamlandı: {rr.get('reindexed', 0)} belge yenilendi, "
                        f"{rr.get('errors', 0)} hata."
                    )
                else:
                    st.success(
                        f"Migration tamamlandı: {rr.get('reindexed', 0)} belge yeni chunk şemasına geçirildi."
                    )
                st.write(f"Chroma kayıt sayısı: {rag.count():,}")
            except Exception as exc:
                st.error(f"Chunk migration hatası: {exc}")


    st.markdown("### İlk mevzuat kurulumu")
    bootstrapper = RegulationBootstrapper(rag=rag)
    bs = bootstrapper.status()
    if bs.get("completed"):
        st.success(f"İlk kurulum tamamlandı: {bs.get('completed_at')}")
    else:
        st.info("Henüz başlangıç mevzuat bilgi tabanı oluşturulmadı. Program resmi kaynaklardan çekip ChromaDB'ye aktarabilir.")
        include_rg = st.checkbox("Resmî Gazete son 7 günü de başlangıç paketine ekle", value=True, key="bootstrap_rg")
        include_secondary = st.checkbox("mevzuat.org'u yardımcı kaynak olarak ekle", value=False, key="bootstrap_secondary")
        use_browser_bootstrap = st.checkbox("BDDK için Playwright kullan", value=True, key="bootstrap_browser")
        if st.button("İLK MEVZUAT KURULUMUNU BAŞLAT", type="primary", disabled=local_only):
            if rag is None:
                st.error("RAG hazır değil. Embedding modeli ve Chroma bağlantısını kontrol et.")
            else:
                progress = st.progress(0.0, text="Başlatılıyor...")
                box = st.empty()
                def _bootstrap_progress(done, total, name, status, data):
                    pct = done / total if total else 0.0
                    progress.progress(min(1.0, pct), text=f"{done}/{total} — {name}: {status}")
                    box.write(f"Kaynak: **{name}** | Durum: **{status}**")
                try:
                    boot_result = bootstrapper.run(use_browser_for_bddk=use_browser_bootstrap, include_resmi_gazete=include_rg, include_secondary=include_secondary, progress_callback=_bootstrap_progress)
                    st.session_state["bootstrap_last_result"] = boot_result
                    if boot_result.get("completed"):
                        if boot_result.get("partial"):
                            st.warning(
                                f"İlk kurulum kısmi tamamlandı. "
                                f"Chroma kayıtları: {rag.count():,} | "
                                f"Hata: {boot_result.get('errors', 0)}"
                            )
                        else:
                            st.success(
                                f"İlk kurulum tamamlandı. "
                                f"Chroma kayıtları: {rag.count():,}"
                            )
                    else:
                        st.error(
                            "İlk mevzuat kurulumu veri üretemedi. "
                            "Kaynak özetini kontrol et."
                        )
                except Exception as exc:
                    st.error(f"İlk mevzuat kurulumu başarısız: {exc}")
        elif local_only:
            st.caption("Tam yerel mod açık. İlk kurulum için internet erişimine izin vermelisin.")
    if st.session_state.get("bootstrap_last_result"):
        with st.expander("İlk kurulum özeti", expanded=False):
            st.json({k: v for k, v in st.session_state["bootstrap_last_result"].items() if k not in {"sources"}})
            st.json(st.session_state["bootstrap_last_result"].get("sources", {}))

    st.markdown("### Toplu mevzuat içe aktarımı")
    st.caption("Alt klasörler dahil tarar; hash ile tekrarları atlar ve kaldığı yerden devam eder. PDF/TXT/MD/HTML/DOCX desteklenir.")
    folder_text = st.text_input("Mevzuat ana klasörü", value=str((BASE_DIR / "mevzuat_kaynak").resolve()))
    force_bulk = st.checkbox("Toplu aktarımda force re-ingest", value=False, help="Aynı hash'i Chroma'ya yeniden yazar.")
    max_file_mb = st.number_input("Maksimum dosya boyutu (MB)", min_value=1, max_value=2000, value=BULK_MAX_FILE_MB, step=10)
    bc1, bc2 = st.columns(2)
    if bc1.button("Klasörü tara", type="secondary"):
        try:
            scan_info = scan_regulation_folder(Path(folder_text), max_file_mb=int(max_file_mb))
            st.session_state["bulk_scan_info"] = scan_info
        except Exception as exc:
            st.error(f"Klasör taranamadı: {exc}")
    if st.session_state.get("bulk_scan_info"):
        st.json(st.session_state["bulk_scan_info"])
    if bc2.button("TOPLU İÇE AKTARIMI BAŞLAT", type="primary"):
        if rag is None:
            st.error("RAG bağımlılıkları hazır değil. Embedding modeli/Chroma kontrol et.")
        else:
            progress = st.progress(0.0, text="Hazırlanıyor...")
            status_box = st.empty()
            def _progress(done, total, filename, status, stats):
                pct = done / total if total else 1.0
                progress.progress(min(1.0, pct), text=f"{done}/{total} — {filename}")
                status_box.write(f"Durum: **{status}** | Yeni: {stats.get('ingested', 0)} | Atlanan: {stats.get('skipped_index', 0)} | Hata: {stats.get('errors', 0)} | Chunk: {stats.get('chunks', 0)}")
            try:
                importer = BulkRegulationImporter(Path(folder_text), rag=rag, max_file_mb=int(max_file_mb))
                result = importer.import_folder(force=force_bulk, progress_callback=_progress)
                st.session_state["bulk_last_result"] = result
                st.success(f"Toplu aktarım tamamlandı: {result['ingested']} yeni belge / {result['chunks']} chunk / {result['errors']} hata")
            except Exception as exc:
                st.error(f"Toplu aktarım başlatılamadı: {exc}")
    if st.session_state.get("bulk_last_result"):
        with st.expander("Son toplu aktarım özeti", expanded=True):
            last = st.session_state["bulk_last_result"]
            st.json({k: v for k, v in last.items() if k != "files"})

    st.divider()
    st.markdown("### Tekil dosya ekleme")
    local_file = st.file_uploader("Yerel mevzuat PDF/TXT yükle", type=["pdf", "txt"], key="reg_upload")
    col_a, col_b = st.columns(2)
    force_ingest = col_a.checkbox("Tekil dosya için force re-ingest", value=False)
    if local_file and col_b.button("Yerel mevzuata ekle"):
        if rag is None:
            st.error("RAG bağımlılıkları hazır değil.")
        else:
            temp = DATA_DIR / f"upload_{uuid.uuid4().hex}_{Path(local_file.name).name}"
            temp.write_bytes(local_file.getvalue())
            try:
                meta = ingest_local_regulation_file(temp, rag, force=force_ingest)
                st.success(f"İndeksleme tamamlandı: {meta.get('title')} / {meta.get('chunk_count', meta.get('count', 0))} chunk")
                pdf_diag = meta.get("pdf_diagnostic")
                if pdf_diag:
                    with st.expander("PDF EXTRACTION / QUALITY GATE — son yükleme", expanded=True):
                        st.write({
                            "pipeline": pdf_diag.get("pipeline", ""),
                            "seçilen_motor": pdf_diag.get("selected_engine", ""),
                            "seçilen_skor": pdf_diag.get("selected_score", 0.0),
                            "seçilen_durum": pdf_diag.get("selected_status", ""),
                            "OCR_kullanıldı": pdf_diag.get("ocr_used", False),
                        })
                        fast_gate = pdf_diag.get("fast_pass", {})
                        st.markdown("**Fast Pass — pypdfium2**")
                        st.write({
                            "durum": fast_gate.get("status", ""),
                            "skor": fast_gate.get("score", 0.0),
                            "retry_pages": fast_gate.get("retry_pages", []),
                            "ocr_pages": fast_gate.get("ocr_pages", []),
                            "table_pages": fast_gate.get("table_pages", []),
                        })
                        candidates = pdf_diag.get("candidates", [])
                        if candidates:
                            st.markdown("**Cross-validation adayları**")
                            st.dataframe(candidates, use_container_width=True, hide_index=True)
                        if pdf_diag.get("pypdf_error"):
                            st.warning(f"pypdf differential hatası: {pdf_diag.get('pypdf_error')}")
            finally:
                temp.unlink(missing_ok=True)

    st.divider()
    st.markdown("### Kaynak bazlı mevzuat senkronizasyonu")
    st.caption("Öncelik: BDDK ve Resmî Gazete (resmi). mevzuat.org yalnızca yardımcı/ikincil kaynak olarak işaretlenir.")
    source_options = ["bddk_official", "mevzuat_org", "resmi_gazete"]
    selected_sources = st.multiselect(
        "Kaynaklar", source_options,
        default=["bddk_official"],
        format_func=lambda x: source_profile(x).get("name", x),
    )
    crawl_depth = st.slider("Tarama derinliği", 0, WEB_SOURCE_MAX_DEPTH, 1)
    crawl_pages = st.number_input("Maksimum sayfa", 1, WEB_SOURCE_MAX_PAGES, 50)
    if st.button("Seçili kaynakları tara ve RAG'a ekle", type="primary"):
        if local_only:
            st.warning("Tam yerel mod açık: web kaynak senkronizasyonu kapalı.")
        elif rag is None:
            st.error("RAG hazır değil.")
        elif not selected_sources:
            st.info("En az bir kaynak seç.")
        else:
            total_found = 0
            total_ingested = 0
            total_skipped = 0
            total_errors = 0
            global_progress = st.progress(0.0, text="Hazırlanıyor...")
            global_status = st.empty()

            for source_index, sid in enumerate(selected_sources, 1):
                Harvester = (
                    BrowserWebSourceHarvester
                    if (sid == "bddk_official" and use_browser_bddk)
                    else WebSourceHarvester
                )
                harvester = Harvester(sid, local_only=False)

                try:
                    discovery_progress = st.empty()

                    def _discovery_progress(done, total, current_url, status):
                        pct = done / total if total else 0.0
                        base = (source_index - 1) / len(selected_sources)
                        span = 1.0 / len(selected_sources)
                        global_progress.progress(
                            min(1.0, base + pct * span),
                            text=f"{source_index}/{len(selected_sources)} — {status}: {done}/{total}",
                        )
                        discovery_progress.write(f"**{source_profile(sid).get('name', sid)}** → {done}/{total} | `{current_url}`")

                    candidates = harvester.collect_candidates(
                        max_depth=int(crawl_depth),
                        max_pages=int(crawl_pages),
                        progress_callback=_discovery_progress
                        if isinstance(harvester, BrowserWebSourceHarvester)
                        else None,
                    )

                    total_found += len(candidates)
                    source_ingested = 0
                    source_skipped = 0
                    source_errors = 0

                    # BDDK + Playwright için tek browser/context ile batch ingest.
                    if isinstance(harvester, BrowserWebSourceHarvester):
                        def _batch_progress(done, total, filename, status, data):
                            pct = done / total if total else 0.0
                            base = (source_index - 1) / len(selected_sources)
                            span = 1.0 / len(selected_sources)
                            global_progress.progress(
                                min(1.0, base + pct * span),
                                text=f"{source_index}/{len(selected_sources)} — {filename} — {status}",
                            )
                            global_status.write(
                                f"Kaynak: **{source_profile(sid).get('name', sid)}** | "
                                f"Belge: {done}/{total} | Durum: **{status}**"
                            )

                        for candidate, payload, error_text in harvester.fetch_candidates_batch_fast(
                            candidates,
                            progress_callback=_batch_progress,
                        ):
                            if not payload:
                                if error_text:
                                    source_errors += 1
                                continue
                            try:
                                if payload.get("status") == "not_modified":
                                    source_skipped += 1
                                    continue
                                meta = payload["meta"]
                                version_result = versioned_ingest_text(
                                    rag,
                                    harvester.index,
                                    payload["text"],
                                    meta,
                                    force=False,
                                    raw_bytes=payload.get("raw_bytes"),
                                )
                                meta = version_result.get("meta", meta)
                                meta["chunk_count"] = version_result.get("count", 0)
                                harvester.index.upsert(meta["file_hash"], meta)
                                if version_result.get("status") == "skipped":
                                    source_skipped += 1
                                else:
                                    source_ingested += 1
                            except Exception as exc:
                                source_errors += 1
                                append_audit(
                                    "web_source_sync_item_error",
                                    {
                                        "source": sid,
                                        "url": candidate.get("url"),
                                        "error": str(exc),
                                    },
                                )
                    else:
                        # Browser'sız kaynaklarda mevcut requests tabanlı akış.
                        for i, candidate in enumerate(candidates, 1):
                            try:
                                payload = harvester.fetch_candidate(candidate)
                                if not payload:
                                    continue
                                meta = payload["meta"]
                                version_result = versioned_ingest_text(
                                    rag,
                                    harvester.index,
                                    payload["text"],
                                    meta,
                                    force=False,
                                    raw_bytes=payload.get("raw_bytes"),
                                )
                                meta = version_result.get("meta", meta)
                                meta["chunk_count"] = version_result.get("count", 0)
                                harvester.index.upsert(meta["file_hash"], meta)
                                if version_result.get("status") == "skipped":
                                    source_skipped += 1
                                else:
                                    source_ingested += 1

                                pct = i / len(candidates) if candidates else 1.0
                                base = (source_index - 1) / len(selected_sources)
                                span = 1.0 / len(selected_sources)
                                global_progress.progress(
                                    min(1.0, base + pct * span),
                                    text=f"{source_index}/{len(selected_sources)} — {i}/{len(candidates)}",
                                )
                            except Exception as exc:
                                source_errors += 1
                                st.warning(
                                    f"{source_profile(sid).get('name', sid)}: "
                                    f"{candidate.get('url')} → {exc}"
                                )

                    total_ingested += source_ingested
                    total_skipped += source_skipped
                    total_errors += source_errors
                    st.write(
                        f"{source_profile(sid).get('name', sid)}: "
                        f"{source_ingested} yeni | {source_skipped} atlandı | {source_errors} hata"
                    )

                except Exception as exc:
                    total_errors += 1
                    st.error(
                        f"{source_profile(sid).get('name', sid)} tarama hatası: {exc}"
                    )

            global_progress.progress(1.0, text="Kaynak senkronizasyonu tamamlandı.")
            append_audit(
                "web_source_sync_completed",
                {
                    "sources": selected_sources,
                    "found": total_found,
                    "ingested": total_ingested,
                    "skipped": total_skipped,
                    "errors": total_errors,
                },
            )

            if total_errors and not total_ingested:
                st.error(
                    f"Senkronizasyon veri eklemeden sona erdi. "
                    f"Bulunan: {total_found}, hata: {total_errors}"
                )
            elif total_errors:
                st.warning(
                    f"Senkronizasyon kısmi tamamlandı — bulunan: {total_found}, "
                    f"yeni: {total_ingested}, atlanan: {total_skipped}, hata: {total_errors}"
                )
            else:
                st.success(
                    f"Tamamlandı — bulunan: {total_found}, yeni: {total_ingested}, "
                    f"atlanan: {total_skipped}, hata: 0"
                )

    st.divider()
    st.markdown("**Resmî Gazete senkronizasyonu**")
    sync_date = st.date_input("Tarih", value=dt.date.today())
    if st.button("Fihristi tara ve ilgili belgeleri getir"):
        scraper = ResmiGazeteScraper(local_only=local_only)
        if local_only:
            st.warning("Tam yerel mod açık: internet senkronizasyonu kapalı.")
        else:
            docs = scraper.fetch_daily_indices(sync_date, filter_keywords=False)
            st.write(f"Uygun başlık: {len(docs)}")
            fetched = 0
            for doc_info in docs[:30]:
                text, meta = scraper.fetch_document_content(doc_info)
                if not text or not meta:
                    continue
                meta = extract_legal_metadata(meta["title"], text, meta)
                if rag:
                    try:
                        version_result = versioned_ingest_text(rag, scraper.index, text, meta, force=False)
                        meta = version_result.get("meta", meta)
                        meta["chunk_count"] = version_result.get("count", 0)
                    except Exception as exc:
                        st.warning(f"RAG ingest hatası: {exc}")
                        continue
                scraper.index.upsert(meta["file_hash"], meta)
                fetched += 1
            append_audit("official_gazette_sync", {"date": sync_date.isoformat(), "fetched": fetched})
            st.success(f"Senkronizasyon tamamlandı: {fetched} belge")

    st.divider()
    render_quarantine_admin_panel(rag=rag)

    st.divider()
    render_telemetry_admin_panel()

    st.divider()
    render_production_health_panel(rag=rag)


def render_quarantine_admin_panel(
    rag: Optional[LocalRAG] = None,
    index: Optional[IngestIndex] = None,
    audit_logger: Optional[RepairAuditLogger] = None,
    cache_dir: Optional[Path] = None,
) -> None:
    """Quarantine management panel: list, inspect, review, and retry quarantined documents."""
    if st is None:
        return

    st.subheader("🛡️ Karantina Yönetimi")
    st.caption(
        "Self-healing veya kalite eşiklerinden geçemeyen belgelerin izolasyon havuzu. "
        "Yeniden deneme (retry) işlemleri doğrudan Chroma'ya yazmaz; "
        "tüm VERIFY, RESCAN ve kalite kapılarını çalıştıran AutoHealingIngestionGate üzerinden yürütülür."
    )

    if index is None:
        index = IngestIndex()

    all_docs = list_quarantined_documents(index=index, cache_dir=cache_dir)
    total_count = len(all_docs)
    reviewed_count = sum(1 for d in all_docs if d.get("reviewed"))
    unreviewed_count = total_count - reviewed_count
    cached_payload_count = sum(1 for d in all_docs if d.get("has_cached_payload"))

    # Summary metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Karantinada", f"{total_count:,}")
    m2.metric("İncelenmemiş", f"{unreviewed_count:,}")
    m3.metric("İncelenmiş", f"{reviewed_count:,}")
    m4.metric("Önbellekte (Payload)", f"{cached_payload_count:,}")

    if not all_docs:
        st.info("Karantinada bekleyen belge bulunmamaktadır.")
        return

    # Filter selection
    filter_choice = st.radio(
        "Filtrele",
        options=["Tümü", "İncelenmemiş (Bekleyen)", "İncelenmiş"],
        horizontal=True,
        key="quarantine_filter_choice",
    )

    if filter_choice == "İncelenmemiş (Bekleyen)":
        filtered_docs = [d for d in all_docs if not d.get("reviewed")]
    elif filter_choice == "İncelenmiş":
        filtered_docs = [d for d in all_docs if d.get("reviewed")]
    else:
        filtered_docs = all_docs

    st.caption(f"{len(filtered_docs)} / {total_count} belge listeleniyor")

    # Table view
    display_rows = []
    for d in filtered_docs:
        display_rows.append({
            "Dosya Hash": d.get("file_hash", "")[:12] + "...",
            "Belge": d.get("title") or d.get("document_id") or "İsimsiz",
            "Kaynak": d.get("source_id") or d.get("source_type") or "-",
            "Karantina Tarihi": d.get("quarantined_at", "")[:19] if d.get("quarantined_at") else "-",
            "Gerekçe": d.get("quarantine_reason", "")[:60] + ("..." if len(d.get("quarantine_reason", "")) > 60 else ""),
            "Retry": d.get("retry_count", 0),
            "İncelendi": "Evet" if d.get("reviewed") else "Hayır",
            "Payload": "Var" if d.get("has_cached_payload") else "Yok",
        })
    if display_rows:
        st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

    # Document selection for details & operations
    st.markdown("#### Belge Detayı ve Operasyonlar")
    doc_options = {
        d["file_hash"]: f"{d.get('title') or d.get('document_id') or d['file_hash'][:12]} ({d['file_hash'][:8]}...)"
        for d in filtered_docs
    }
    selected_hash = st.selectbox(
        "İşlem yapılacak belgeyi seçin",
        options=list(doc_options.keys()),
        format_func=lambda h: doc_options.get(h, h),
        key="quarantine_selected_hash",
    )

    if not selected_hash:
        return

    detail = get_quarantine_detail(selected_hash, index=index, cache_dir=cache_dir)
    if not detail:
        st.warning("Belge detayları bulunamadı.")
        return

    with st.expander(f"Detaylar: {detail.get('title') or detail.get('document_id') or selected_hash[:12]}", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Belge Başlığı:** {detail.get('title') or '-'}")
            st.markdown(f"**Belge Kimliği:** `{detail.get('document_id') or '-'}`")
            st.markdown(f"**Kaynak:** `{detail.get('source_id') or detail.get('source_type') or '-'}`")
            st.markdown(f"**Dosya Hash (SHA256):** `{selected_hash}`")
            if detail.get("url"):
                st.markdown(f"**URL:** {detail.get('url')}")
        with c2:
            st.markdown(f"**Karantina Zamanı:** {detail.get('quarantined_at') or '-'}")
            st.markdown(f"**Karantina Gerekçesi:** `{detail.get('quarantine_reason') or '-'}`")
            st.markdown(f"**Retry Sayısı:** {detail.get('retry_count', 0)}")
            if detail.get("last_retry_at"):
                st.markdown(f"**Son Retry:** {detail.get('last_retry_at')}")

        issues = detail.get("quarantine_issues") or []
        if issues:
            st.markdown("**Tespit Edilen Sorunlar:**")
            for iss in issues:
                st.markdown(f"- {iss}")

        # Payload integrity
        valid, msg = verify_quarantine_payload_integrity(selected_hash, cache_dir=cache_dir)
        p_col1, p_col2 = st.columns(2)
        with p_col1:
            st.markdown(f"**Önbellek Durumu:** Binary: {'Var' if detail.get('has_binary_cache') else 'Yok'} | Metin: {'Var' if detail.get('has_text_cache') else 'Yok'}")
        with p_col2:
            if valid:
                st.success(f"Bütünlük Doğrulandı: {msg}")
            else:
                st.warning(f"Bütünlük Uyarısı: {msg}")

        # Review section
        st.divider()
        st.markdown("**Operatör İncelemesi (Review & Notes)**")
        if detail.get("reviewed"):
            st.info(f"Bu belge daha önce incelendi: {detail.get('reviewed_by')} ({detail.get('reviewed_at')})")
            if detail.get("review_notes"):
                st.markdown(f"**Mevcut Not:** {detail.get('review_notes')}")

        rev_user = st.text_input("İnceleyen Kullanıcı", value=detail.get("reviewed_by") or "operator", key=f"rev_user_{selected_hash}")
        rev_notes = st.text_area("İnceleme Notu", value=detail.get("review_notes") or "", key=f"rev_notes_{selected_hash}")
        if st.button("İncelemeyi Kaydet", key=f"btn_save_review_{selected_hash}"):
            rev_res = review_quarantine_document(
                file_hash=selected_hash,
                reviewer=rev_user.strip() or "operator",
                notes=rev_notes.strip(),
                index=index,
                audit_logger=audit_logger,
            )
            if rev_res.get("status") == "reviewed":
                st.success(f"İnceleme kaydedildi: {rev_res.get('reviewed_by')} @ {rev_res.get('reviewed_at')}")
            else:
                st.error(f"İnceleme kaydedilemedi: {rev_res.get('error')}")

        # Canonical Retry section
        st.divider()
        st.markdown("**Yeniden Deneme (Canonical Pipeline Retry)**")
        st.caption(
            "Bu işlem belgeyi AutoHealingIngestionGate üzerinden tam kalite, self-heal, staged ingest, "
            "VERIFY ve RESCAN adımlarından geçirerek yeniden dener. Başarılı olursa karantinadan çıkar."
        )
        if st.button("Yeniden Dene (Retry Ingestion)", type="primary", key=f"btn_retry_{selected_hash}"):
            if rag is None:
                st.error("RAG hazır değil. Embedding modeli ve Chroma bağlantısını kontrol edin.")
            else:
                with st.spinner("Belge yeniden işleniyor (Self-healing & verification gate)..."):
                    try:
                        retry_res = retry_quarantined_document(
                            rag=rag,
                            index=index,
                            file_hash=selected_hash,
                            trigger="admin_ui_retry",
                            actor=rev_user.strip() or "operator",
                            cache_dir=cache_dir,
                            audit_logger=audit_logger,
                        )
                        try:
                            st.session_state[f"last_retry_result_{selected_hash}"] = retry_res
                        except Exception:
                            pass
                        status = str(retry_res.get("status") or "").lower()
                        if status == "quarantined" or retry_res.get("quarantined"):
                            st.warning(f"Belge doğrulanamadı ve tekrar karantinaya alındı: {retry_res.get('quarantine_reason')}")
                        elif status in ("indexed", "active", "updated", "ingested") or retry_res.get("count", 0) > 0:
                            st.success(f"Belge başarıyla iyileştirildi ve indekslendi! Oluşturulan chunk sayısı: {retry_res.get('count', 0)}")
                        elif status == "blocked":
                            st.error(f"Retry engellendi: {retry_res.get('error')}")
                        else:
                            st.info(f"Sonuç: {retry_res.get('status')} {retry_res.get('error') or ''}")
                    except Exception as exc:
                        st.error(f"Retry işlemi sırasında hata oluştu: {exc}")


def render_telemetry_admin_panel(
    audit_path: Optional[Path] = None,
    index: Optional[IngestIndex] = None,
) -> None:
    """Operational telemetry and quality improvement panel (Strictly Read-Only)."""
    if st is None:
        return

    st.subheader("📊 Operasyonel Telemetri ve Self-Healing Metrikleri")
    st.caption(
        "Audit log ve IngestIndex verilerinden doğrudan türetilen operasyonel metrikler. "
        "Olay (Event) sayıları ile Tekil Belge (Unique Document) sayıları net biçimde ayrıştırılmıştır."
    )

    if index is None:
        index = IngestIndex()

    telemetry = compute_operational_telemetry(log_path=audit_path, index=index)
    ev = telemetry.get("event_metrics", {})
    ud = telemetry.get("unique_document_metrics", {})
    qm = telemetry.get("quality_metrics", {})

    # Top metrics (Event vs Unique)
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("İşlem Olayı / Belge", f"{ev.get('total_ingest_events', 0):,} / {ud.get('unique_documents_processed', 0):,}")
    t2.metric("Kalite Geçen / Temiz", f"{ev.get('quality_passed_events', 0):,} / {ud.get('unique_documents_clean', 0):,}")
    t3.metric("Onarılan (Self-Healed)", f"{ev.get('self_healed_events', 0):,} / {ud.get('unique_documents_healed', 0):,}")
    t4.metric("Karantinada Olay / Belge", f"{ev.get('quarantined_events', 0):,} / {ud.get('unique_documents_in_quarantine', 0):,}")

    # Rates & quality improvement
    st.markdown("#### İyileştirme ve Başarı Oranları")
    q1, q2, q3, q4 = st.columns(4)

    avg_delta = qm.get("avg_quality_delta")
    delta_str = f"+{avg_delta:.2f}" if avg_delta is not None and avg_delta > 0 else (f"{avg_delta:.2f}" if avg_delta is not None else "N/A")
    q1.metric("Ortalama Δ Kalite", delta_str, help="Self-healing adımlarında ham metne kıyasla sağlanan ortalama kalite skoru artışı")

    res_rate = qm.get("quarantine_resolution_rate")
    res_str = f"%{res_rate * 100:.1f}" if res_rate is not None else "N/A"
    q2.metric("Onarım Çözüm Oranı", res_str, help="Sorun tespit edilen belgelerin onarılarak başarıyla sisteme kazandırılma oranı")

    ret_rate = qm.get("retry_success_rate")
    ret_str = f"%{ret_rate * 100:.1f}" if ret_rate is not None else "N/A"
    q3.metric("Retry Başarı Oranı", ret_str, help="Karantinadan başlatılan yeniden deneme işlemlerinin başarı yüzdesi")

    q4.metric("Aktif Belge Havuzu", f"{ud.get('unique_documents_active', 0):,}")

    # Stage Distribution and Failure Metrics
    st.markdown("#### Aşama Dağılımı ve Güvenlik Olayları")
    s1, s2 = st.columns(2)

    with s1:
        st.markdown("**Self-Healing Aşama Dağılımı**")
        stages = qm.get("stage_distribution") or {}
        st.write({
            "Stage 1 - Mojibake Onarımı": stages.get("stage_1_mojibake", 0),
            "Stage 2 - Bitişik Kelime / Boşluk": stages.get("stage_2_glued", 0),
            "Stage 3 - Kaynak Fallback (Re-Extract)": stages.get("stage_3_source_fallback", 0),
        })

    with s2:
        st.markdown("**Boru Hattı Koruma ve Geri Alma (Rollback) Olayları**")
        st.write({
            "Doğrulama Başarısızlığı (VERIFY)": ev.get("verification_failure_events", 0),
            "Yeniden Tarama Başarısızlığı (RESCAN)": ev.get("rescan_failure_events", 0),
            "Geri Alma (Rollback) Tetiklenmesi": ev.get("rollback_events", 0),
            "Yeniden Deneme Girişimi (Retry)": ev.get("retry_attempt_events", 0),
        })


def render_production_health_panel(
    rag: Optional[LocalRAG] = None,
    index: Optional[IngestIndex] = None,
    audit_path: Optional[Path] = None,
) -> None:
    """Read-only production data integrity & storage health check.
    
    CRITICAL: Strictly read-only; zero write mutations, zero rebuild/fix triggers.
    """
    if st is None:
        return

    st.subheader("🩺 Üretim Sağlık Durumu (Salt Okunur)")
    st.caption(
        "Üretim veritabanı, indeks, önbellek ve denetim zincirinin anlık sağlık durumu. "
        "Bu panel kesinlikle salt okunurdur; hiçbir yazma, sıfırlama veya tetikleme işlemi yapmaz."
    )

    if index is None:
        index = IngestIndex()

    # 1. Chroma Embeddings Count
    chroma_count = rag.count() if rag else 0

    # 2. IngestIndex count
    index_items = index.data.get("indexed_items", {}) if hasattr(index, "data") else (getattr(index, "indexed_items", {}) or {})
    index_count = len(index_items)

    # 3. SourceCache count
    sc_dir = Path(SOURCE_CACHE_DIR)
    source_cache_count = len(list(sc_dir.glob("*.txt"))) if sc_dir.is_dir() else 0

    # 4. Audit entries count & integrity
    actual_audit_path = Path(audit_path or REPAIR_AUDIT_LOG_FILE)
    audit_count = 0
    if actual_audit_path.is_file():
        try:
            with open(actual_audit_path, "r", encoding="utf-8") as f:
                audit_count = sum(1 for line in f if line.strip())
        except Exception:
            audit_count = 0

    chain_valid, chain_errors = verify_audit_integrity(actual_audit_path)

    # 5. SQLite PRAGMA integrity_check
    sqlite_status = "Bilinmiyor"
    sqlite_file = Path(DB_PATH) / "chroma.sqlite3"
    if sqlite_file.is_file():
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{sqlite_file.resolve()}?mode=ro", uri=True)
            cur = conn.cursor()
            res = cur.execute("PRAGMA integrity_check;").fetchone()
            sqlite_status = str(res[0]) if res else "Bilinmiyor"
            conn.close()
        except Exception as exc:
            sqlite_status = f"Hata: {exc}"

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Chroma Vektör Sayısı", f"{chroma_count:,}")
    h2.metric("İndeks Kayıt Sayısı", f"{index_count:,}")
    h3.metric("Kaynak Önbellek (TXT)", f"{source_cache_count:,}")
    h4.metric("Denetim Kayıtları (Audit)", f"{audit_count:,}")

    c_status1, c_status2 = st.columns(2)
    with c_status1:
        if sqlite_status.lower() == "ok":
            st.success(f"SQLite Bütünlüğü: {sqlite_status.upper()} (Sorunsuz)")
        else:
            st.error(f"SQLite Bütünlüğü: {sqlite_status}")

    with c_status2:
        if chain_valid:
            st.success("Audit Hash-Zinciri: GEÇERLİ (Kriptografik Bütünlük Korundu)")
        else:
            st.error(f"Audit Hash-Zinciri: BOZUK ({len(chain_errors)} hata tespit edildi)")


__all__ = [
    "normalize_display_boolean",
    "render_mevzuat_admin",
    "render_quarantine_admin_panel",
    "render_telemetry_admin_panel",
    "render_production_health_panel",
]
