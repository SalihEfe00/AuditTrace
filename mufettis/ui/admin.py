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

from mufettis.config import *
from mufettis.common.utils import *
from mufettis.audit.service import *
from mufettis.extraction.text_repair import *
from mufettis.extraction.pdf import *
from mufettis.legal.parser import *
from mufettis.harvesting.web_harvester import *
from mufettis.harvesting.resmi_gazete import *
from mufettis.rag.index import *
from mufettis.rag.engine import *
from mufettis.rag.maintenance import *
from mufettis.analysis.risk_engine import *
from mufettis.importers.bulk_importer import *
from mufettis.importers.bootstrapper import *
from mufettis.scheduler.scheduler import *

def _get_rag(**kwargs):
    _dyn = resolve_dynamic("_get_rag", _get_rag)
    if _dyn is not None and _dyn is not _get_rag:
        return _dyn(**kwargs)
    from mufettis.ui.app import _get_rag as _fn
    return _fn(**kwargs)

def render_mevzuat_admin(
    rag: Optional[LocalRAG],
    local_only: bool,
    use_browser_bddk: bool,
    allow_embedding_download: bool,
) -> None:
    """Kaynak ekleme/senkronizasyon/bootstrap/RAG rebuild yönetim alanı."""
    st.subheader("Mevzuat kaynak yönetimi")
    rag = resolve_dynamic('_get_rag', _get_rag)(allow_download=allow_embedding_download)
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


__all__ = [k for k in dict(globals()).keys() if not k.startswith('__')]
