# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

try:
    import chromadb
except Exception:
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_ERROR = ""
except Exception as _exc:
    # Sebep saklanır ve kullanıcıya gösterilir (ör. temiz makinede eksik bir DLL torch'u yükletmez).
    SentenceTransformer = None
    SENTENCE_TRANSFORMERS_ERROR = f"{type(_exc).__name__}: {_exc}"

from mufettis.config import (
    CHROMA_COLLECTION,
    CHROMA_UPSERT_BATCH_SIZE,
    CHUNK_SCHEMA_VERSION,
    DB_PATH,
    EMBED_BATCH_SIZE_CPU,
    EMBED_BATCH_SIZE_GPU,
    EMBEDDING_MODEL,
    MAX_RAG_RESULTS,
)
from mufettis.common.utils import now_iso, mask_sensitive
from mufettis.corpus import NOT_IN_FORCE_STATUSES, EligibilityPolicy, refresh_canonical, with_canonical
from mufettis.corpus.model import CANONICAL_SCHEMA
from mufettis.extraction.text_repair import (
    extract_query_article,
    extract_series_number,
    normalize_extracted_text,
    repair_extracted_spacing,
)
from mufettis.legal.amendments import amendment_metadata
from mufettis.legal.parser import (
    _chunk_is_retrievable,
    _chunk_matches_account_ref,
    _chunk_matches_document_ref,
    _resolve_query_document_reference_from_collection,
    clean_evidence_text,
    extract_legal_identifier,
    extract_query_account_code,
    extract_query_document_reference,
    prefer_official_matches,
    split_legal_chunks,
)
from mufettis.rag.fusion import LEXICAL_WEIGHT, SEMANTIC_WEIGHT, reciprocal_rank_fusion
from mufettis.rag.lexical import LEXICAL_INDEX_VERSION, DiskLexicalIndex, LexicalIndex, query_terms
from mufettis.rag.paging import iter_pages

# Serbest aramada vektör adaylarına eklenen sözcüksel (BM25) aday sayısı: max(alt sınır, k * çarpan).
LEXICAL_CANDIDATES_MIN = 10
LEXICAL_CANDIDATES_PER_K = 2
# Sorguda seri numarası varken havuza eklenen, o seri üyesinden en çok parça sayısı.
SERIES_CANDIDATES = 20
# İddia adaylarında (claim_candidates) her kaynaktan (BM25, vektör) okunan aday sayısı.
CLAIM_SOURCE_DEPTH = 100


class LocalRAG:
    def __init__(self, db_path: Path = DB_PATH, model_name: str = EMBEDDING_MODEL, allow_download: bool = False,
                 store: Optional[str] = None):
        from mufettis.rag.store import SqliteStoreClient, resolve_store_kind, store_root_for

        self.store_kind = resolve_store_kind(store, db_path)
        if self.store_kind == "chroma" and chromadb is None:
            raise RuntimeError("chromadb kurulu değil. `pip install chromadb`")
        if SentenceTransformer is None:
            raise RuntimeError(f"sentence-transformers yüklenemedi: {SENTENCE_TRANSFORMERS_ERROR}")
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.allow_download = allow_download
        self._model = None
        if self.store_kind == "sqlite":
            self.client = SqliteStoreClient(store_root_for(self.db_path))
        else:
            self.client = chromadb.PersistentClient(path=str(self.db_path))
        self.collection = self.client.get_or_create_collection(name=CHROMA_COLLECTION, embedding_function=None)

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            # İndirme izni yoksa yalnızca yerel önbellek kullanılır. HF_HUB_OFFLINE ortam
            # değişkeni huggingface_hub import edilirken okunduğu için çalışma anında
            # ayarlanması etkisizdir; çevrimdışı mod burada zorlanır.
            self._model = SentenceTransformer(self.model_name, local_files_only=not self.allow_download)
            return self._model
        except Exception as exc:
            if not self.allow_download:
                raise RuntimeError(
                    "Embedding modeli yerelde bulunamadı. Yerel modda internet kapalıdır; "
                    "modeli önceden indirip MODEL CACHE'e koyun veya ilk kurulumda indirmeye izin verin."
                ) from exc
            raise

    def embed(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
    ) -> List[List[float]]:
        """Embedding'i batch halinde üretir; model/RAG semantiği değişmez."""
        if not texts:
            return []

        model = self._load_model()
        if batch_size is None:
            device = str(getattr(model, "device", "")).lower()
            batch_size = (
                EMBED_BATCH_SIZE_GPU
                if "cuda" in device
                else EMBED_BATCH_SIZE_CPU
            )

        arr = model.encode(
            texts,
            batch_size=int(batch_size),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(arr, dtype=np.float32).tolist()

    def delete_hash(self, file_hash: str) -> None:
        self._mutations = getattr(self, "_mutations", 0) + 1
        try:
            self.collection.delete(where={"file_hash": file_hash})
        except Exception:
            # Eski Chroma sürümlerinde where davranışı değişebiliyor; metadata ids fallback
            try:
                found = self.collection.get(where={"file_hash": file_hash}, include=["metadatas"])
                ids = found.get("ids", []) if found else []
                if ids:
                    self.collection.delete(ids=ids)
            except Exception as exc:
                raise RuntimeError(f"Chroma eski kayıt silinemedi: {exc}") from exc
        # SQLite deposu: silinen parçaların vektörleri dosyada çöp kalır; yeterince birikince geri kazanılır.
        compact = getattr(self.collection, "compact_if_needed", None)
        if callable(compact):
            compact()

    def ingest_text(self, text: str, meta: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
        """Belgeyi batch embedding + batch Chroma upsert ile indeksler."""
        text = normalize_extracted_text(text)
        text = repair_extracted_spacing(
            text, meta.get("title", ""), meta.get("document_type", "")
        )
        if not text.strip():
            return {"status": "empty", "count": 0}

        file_hash = meta["file_hash"]
        if force:
            self.delete_hash(file_hash)
        else:
            existing = self.collection.get(
                where={"file_hash": file_hash},
                include=["metadatas"],
            )
            existing_ids = (existing or {}).get("ids") or []
            if existing_ids:
                current = [
                    m for m in (existing.get("metadatas") or [])
                    if str((m or {}).get("chunk_schema_version", "")) == str(CHUNK_SCHEMA_VERSION)
                ]
                if current:
                    return {"status": "skipped", "count": len(existing_ids)}
                # Yalnızca eski şema parçaları var: bunlar sorgularda dışlandığı için belge fiilen
                # aranamaz durumdadır. Atlamak yerine eski parçalar silinip güncel şemayla yüklenir.
                self.delete_hash(file_hash)

        chunks = split_legal_chunks(text, title=str(meta.get("title", "") or ""))
        if not chunks:
            return {"status": "empty_chunks", "count": 0, "chunks": []}

        docs: List[str] = []
        metadatas: List[Dict[str, str]] = []
        ids: List[str] = []

        # Chunk üretimi gerçek yapının son otoritesidir. Discovery ile splitter
        # farklılaşırsa metadata chunk'ın gerçek türünü takip eder.
        chunk_types = Counter(str(ch.get("chunk_type", "document") or "document") for ch in chunks)
        dominant_type = chunk_types.most_common(1)[0][0] if chunk_types else "document"
        if dominant_type in {"article", "paragraph", "item", "subitem"}:
            effective_structure_type = "article"
        elif dominant_type in {"section", "unscoped_numeric"} and any(
            str(ch.get("chunk_type", "")) == "section" for ch in chunks
        ):
            effective_structure_type = "section"
        elif dominant_type == "accounting_code":
            effective_structure_type = "accounting_plan"
        else:
            effective_structure_type = str(meta.get("structure_type", "document") or "document")

        # Tüm parça yazımları buradan geçer: kanonik alanlar (kimlik, kaynak, normatif rol) her
        # parçada bulunur. Rol parçaların gerçek yapısından türediği için yapı tipi önce belirlenir.
        # İçerik özeti çağıranın (extract_legal_metadata) ham metinden hesapladığıdır; index kaydı
        # ile parçalar aynı expression kimliğini taşısın diye burada yalnızca eksikse hesaplanır.
        meta = with_canonical(
            {**meta, "structure_type": effective_structure_type},
            None if str(meta.get("content_fingerprint") or "").strip() else text,
        )

        # Madde / fıkra değişiklik notları (M7.2): olay tarihinden sonra değişen hükmü işaretlemek için.
        amendments = amendment_metadata(chunks)
        for ch, amendment in zip(chunks, amendments):
            chunk_text = repair_extracted_spacing(
                ch["text"], meta.get("title", ""), meta.get("document_type", "")
            )
            chunk_text = mask_sensitive(chunk_text)
            record_meta = {
                **meta,
                "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                "article_prefix": ch.get("article_prefix") or "",
                "article_no": ch.get("article_no") or "",
                "article_title": ch.get("article_title") or "",
                "article_heading": ch.get("article_heading") or "",
                "paragraph_no": ch.get("paragraph_no") or "",
                "item_no": ch.get("item_no") or "",
                "subitem_no": ch.get("subitem_no") or "",
                "section_no": ch.get("section_no") or "",
                "account_code": ch.get("account_code") or "",
                "chunk_type": ch.get("chunk_type") or "document",
                "structure_type": effective_structure_type,
                "subchunk_no": int(ch.get("subchunk_no", 0) or 0),
                "chunk_no": int(ch.get("chunk_no", 0) or 0),
                **amendment,
            }
            docs.append(chunk_text)
            metadatas.append({
                k: str(v) if v is not None else ""
                for k, v in record_meta.items()
                if k != "content_size" or isinstance(v, (str, int, float))
            })
            ids.append(f"{file_hash[:24]}-{ch.get('chunk_no', 0):06d}")

        total = len(docs)
        embed_batch = None
        device = str(getattr(self._model, "device", "")).lower() if self._model is not None else ""
        if device:
            embed_batch = (
                EMBED_BATCH_SIZE_GPU if "cuda" in device else EMBED_BATCH_SIZE_CPU
            )

        # Embedding ve upsert aynı batch akışında: büyük belgelerde bütün
        # embedding matrisini RAM'de tutmayız.
        for start in range(0, total, EMBED_BATCH_SIZE_GPU if "cuda" in device else EMBED_BATCH_SIZE_CPU):
            end = min(
                total,
                start + (
                    EMBED_BATCH_SIZE_GPU if "cuda" in device
                    else EMBED_BATCH_SIZE_CPU
                ),
            )
            batch_docs = docs[start:end]
            batch_embeddings = self.embed(batch_docs, batch_size=embed_batch)

            for up_start in range(0, len(batch_docs), CHROMA_UPSERT_BATCH_SIZE):
                up_end = min(
                    len(batch_docs),
                    up_start + CHROMA_UPSERT_BATCH_SIZE,
                )
                self.collection.upsert(
                    ids=ids[start + up_start:start + up_end],
                    documents=batch_docs[up_start:up_end],
                    embeddings=batch_embeddings[up_start:up_end],
                    metadatas=metadatas[start + up_start:start + up_end],
                )

        self._mutations = getattr(self, "_mutations", 0) + 1
        return {"status": "ingested", "count": total, "chunks": chunks, "canonical": {
            k: meta[k] for k in ("work_id", "expression_id", "normative_role", "verification")
        }}

    def update_hash_metadata(self, file_hash: str, updates: Dict[str, Any]) -> int:
        """Bir belgenin tüm chunk metadata kayıtlarını günceller."""
        found = self.collection.get(where={"file_hash": file_hash}, include=["metadatas"])
        ids = found.get("ids", []) if found else []
        metas = found.get("metadatas", []) if found else []
        if not ids:
            return 0
        new_metas = []
        for meta in metas:
            m = dict(meta or {})
            for k, v in updates.items():
                m[k] = str(v) if v is not None else ""
            # Kimlik girdisi değiştiyse (ör. law_number düzeltmesi) kanonik alanlar bayat kalmasın.
            m = {k: str(v) for k, v in refresh_canonical(m, updates.keys()).items()}
            new_metas.append(m)
        # Chroma tek çağrıda en fazla ~5461 kayıt kabul eder; hesap planları (1043, 1334) daha büyük.
        for start in range(0, len(ids), CHROMA_UPSERT_BATCH_SIZE):
            end = start + CHROMA_UPSERT_BATCH_SIZE
            self.collection.update(ids=ids[start:end], metadatas=new_metas[start:end])
        return len(ids)

    def mark_hash_superseded(self, old_hash: str, new_hash: str, effective_to: Optional[str] = None) -> int:
        return self.update_hash_metadata(old_hash, {
            "status": "superseded",
            "superseded_by": new_hash,
            "valid_to": effective_to or now_iso(),
        })

    def _exact_law_candidates(self, law_number: str) -> List[Dict[str, Any]]:
        """Chroma metadata + document taramasıyla exact-law adaylarını deterministik bul."""
        pattern=re.compile(
            rf"(?<!\d){re.escape(str(law_number))}\s*sayılı\s*Kanun\b",
            re.I,
        )
        exact=[]
        try:
            # Sayfalı: tepe bellek koleksiyon boyutuyla değil sayfa boyutuyla sınırlı kalır.
            for doc,meta in self._iter_current_chunks():
                self._collect_exact_law(doc,meta,law_number,pattern,exact)
        except Exception as exc:
            raise RuntimeError(f"Exact-law belge taraması başarısız: {exc}") from exc
        return exact

    def _iter_current_chunks(self):
        for _ids,docs,metas in iter_pages(self.collection, where={"chunk_schema_version":CHUNK_SCHEMA_VERSION}):
            yield from zip(docs,metas)

    @staticmethod
    def _collect_exact_law(doc, meta, law_number, pattern, exact) -> None:
        if str(meta.get("chunk_schema_version",""))!=str(CHUNK_SCHEMA_VERSION):
            return
        if not _chunk_is_retrievable(meta, doc):
            return
        if str(meta.get("status","")).lower() in NOT_IN_FORCE_STATUSES:
            return

        law=str(meta.get("law_number","") or "")
        key=str(meta.get("document_key","") or "")
        title=str(meta.get("title","") or "")
        doc_text=str(doc or "")

        matched=(
            law==str(law_number)
            or key==str(law_number)
            or bool(pattern.search(title))
            or bool(pattern.search(doc_text[:6000]))
        )
        if matched:
            exact.append({
                "document":clean_evidence_text(doc_text,12000),
                "metadata":meta,
                "distance":1.0,
            })


    # AnalysisEngine.retrieve_evidence bu yeteneği kontrol ederek uygunluk politikası gönderir.
    supports_eligibility_policy = True

    def named_law_number(self, query: str) -> Optional[str]:
        """Sorguda adıyla anılan kanunun numarası ("İcra ve İflas Kanunu 89 uncu madde" → 2004); korpustaki
        kanun kataloğuna karşı atıf çözücüsüyle (analysis.cited_references) çözülür. Numarasıyla anılan
        kanun extract_legal_identifier'ın işidir; burada yalnızca adla anılanlar."""
        from mufettis.analysis.cited_references import IN_CORPUS, extract_citations, resolve

        citations = [c for c in extract_citations(query)
                     if not c["law_number"] and not c["internal"] and re.search(r"(?i)kanun\w*$", c["label"])]
        if not citations:
            return None
        catalog = self._law_catalog()
        for citation in citations:
            ref = resolve(citation, catalog)
            if ref.status == IN_CORPUS and ref.law_number.isdigit():
                return ref.law_number
        return None

    def _law_catalog(self):
        """Etkin kanunların kataloğu; dizin dosyası değişene kadar önbellekte."""
        from mufettis.analysis.cited_references import catalog_from_index
        from mufettis.rag.index import IngestIndex

        index = IngestIndex()
        try:
            stamp = index.path.stat().st_mtime_ns
        except OSError:
            stamp = 0
        cached = getattr(self, "_law_catalog_cache", None)
        if cached is None or cached[0] != stamp:
            items = {h: r for h, r in index.iter_records() if str(r.get("law_number") or "").isdigit()}
            entries = [e for e in catalog_from_index(items, lambda r: str(r.get("declared_title") or r.get("title") or ""))
                       if e.is_law]
            self._law_catalog_cache = cached = (stamp, entries)
        return cached[1]

    def _identity_matches(self, document_ref: Optional[str], account_ref: Optional[str]) -> List[Dict[str, Any]]:
        """Belge kimliği (source_document_id) / hesap kodu (account_code) meta veride birebir eşleşen,
        erişilebilir güncel parçalar."""
        clauses: List[Dict[str, Any]] = [{"chunk_schema_version": CHUNK_SCHEMA_VERSION}]
        if document_ref:
            clauses.append({"source_document_id": str(document_ref)})
        if account_ref:
            clauses.append({"account_code": str(account_ref)})
        try:
            got = self.collection.get(where={"$and": clauses}, include=["documents", "metadatas"])
        except Exception as exc:
            raise RuntimeError(f"Güncel RAG kayıtları okunamadı: {exc}") from exc
        return [
            {"document": clean_evidence_text(doc, 12000), "metadata": meta or {}, "distance": None}
            for doc, meta in zip(got.get("documents") or [], got.get("metadatas") or [])
            if _chunk_is_retrievable(meta or {}, doc)
        ]

    def query(
        self,
        query: str,
        k: int=5,
        eligibility: Optional[EligibilityPolicy] = None,
    ) -> List[Dict[str,Any]]:
        """eligibility: kanıt uygunluk politikası (normatif rol / yayıncı niteliği). Alaka
        sıralamasından önce uygulanır. Serbest aramada Chroma sorgusuna ön filtre olarak da
        eklenir; sonradan elemek yetmez: hesap planları (~17 bin parça) bir şikâyet cümlesinin
        ilk 100 anlamsal sonucunun tamamını doldurabiliyor."""
        if not query.strip():
            return []
        if eligibility:
            return [
                r for r in self._query(query, k, eligibility)
                if eligibility.admits(r.get("metadata") or {})
            ]
        return self._query(query, k, None)

    def _query(self, query: str, k: int, eligibility: Optional[EligibilityPolicy]) -> List[Dict[str,Any]]:

        embedding=self.embed([query])[0]
        explicit_law=extract_legal_identifier(query) or self.named_law_number(query)
        document_ref=extract_query_document_reference(query)
        account_ref=extract_query_account_code(query)
        document_ref, account_ref = _resolve_query_document_reference_from_collection(
            self, query, document_ref, account_ref
        )

        if explicit_law:
            exact=[]
            exact_where={"$and":[{"chunk_schema_version":CHUNK_SCHEMA_VERSION},{"law_number":str(explicit_law)}]}
            try:
                result=self.collection.query(query_embeddings=[embedding], n_results=min(max(k*10,30),MAX_RAG_RESULTS), where=exact_where)
                docs=result.get("documents",[[]])[0]
                metas=result.get("metadatas",[[]])[0]
                distances=result.get("distances",[[]])[0]
                for doc,meta,distance in zip(docs,metas,distances):
                    meta=meta or {}
                    if str(meta.get("law_number",""))!=str(explicit_law) or not _chunk_is_retrievable(meta, doc):
                        continue
                    exact.append({"document":clean_evidence_text(doc,12000),"metadata":meta,"distance":float(distance) if distance is not None else 1.0})
            except Exception:
                exact=[]
            if not exact:
                exact=self._exact_law_candidates(str(explicit_law))
            limit=max(1,min(k,MAX_RAG_RESULTS))
            # Madde numarası da verilmişse ("54 üncü madde", "md. 89") o maddenin parçaları doğrudan başa gelir;
            # anlamsal benzerlik maddeyi seçmez. Madde korpusta yoksa kanunun diğer parçalarıyla devam edilir.
            article=extract_query_article(query)
            direct=self._article_chunks(str(explicit_law), article) if article else []
            if direct:
                keys={(r["metadata"].get("file_hash"), r["metadata"].get("chunk_no")) for r in direct}
                rest=[r for r in exact if (r["metadata"].get("file_hash"), r["metadata"].get("chunk_no")) not in keys]
                return (direct + prefer_official_matches(rest, limit=limit, query=query))[:limit]
            return prefer_official_matches(exact, limit=limit, query=query)

        # Açık belge/h hesap kimliğinde metadata where filtresi tek otorite değildir.
        # Rebuild sonrası eksik/stale kimlik metadata'sını gerçek chunk metniyle de doğrula.
        if document_ref or account_ref:
            limit=max(1,min(k,MAX_RAG_RESULTS))
            # Hızlı yol: kimliği meta veride birebir eşleşen parçalar sıralamada (belge +1.80,
            # hesap +2.00 bonusu; eşleşmeyene -1.20/-1.35 ceza) tam taramadaki her aday parçanın
            # önündedir. En az `limit` tane varsa ilk sonuçlar tam taramayla aynıdır; tüm
            # koleksiyonu (~26 bin parça) okumak hesap kodu sorgusunu ~22 sn sürdürüyordu.
            fast=self._identity_matches(document_ref, account_ref)
            if len(fast) >= limit:
                return prefer_official_matches(fast, limit=limit, query=query, resolved_refs=(document_ref, account_ref))
            out=[]
            try:
                # Sayfalı tarama: yalnızca eşleşen parçalar bellekte tutulur.
                for _ids,docs,metas in iter_pages(self.collection, where={"chunk_schema_version":CHUNK_SCHEMA_VERSION}):
                    for doc,meta in zip(docs,metas):
                        if not _chunk_is_retrievable(meta, doc):
                            continue
                        if document_ref and not _chunk_matches_document_ref(meta, doc, document_ref):
                            continue
                        if account_ref and not _chunk_matches_account_ref(meta, doc, account_ref):
                            continue
                        out.append({"document":clean_evidence_text(doc,12000),"metadata":meta,"distance":None})
            except Exception as exc:
                raise RuntimeError(f"Güncel RAG kayıtları okunamadı: {exc}") from exc
            if out:
                return prefer_official_matches(out, limit=limit, query=query, resolved_refs=(document_ref, account_ref))
            return []

        # Serbest sorgular semantic retrieval kullanır.
        semantic_where: Dict[str, Any] = {"chunk_schema_version":CHUNK_SCHEMA_VERSION}
        policy_clauses = eligibility.chroma_clauses() if eligibility else []
        if policy_clauses:
            semantic_where = {"$and":[semantic_where, *policy_clauses]}
        result=self.collection.query(
            query_embeddings=[embedding],
            n_results=min(max(k*20,50),MAX_RAG_RESULTS),
            where=semantic_where,
        )
        docs=result.get("documents",[[]])[0]
        metas=result.get("metadatas",[[]])[0]
        distances=result.get("distances",[[]])[0]
        ids=(result.get("ids") or [[]])[0] or [None]*len(docs)
        out=[]
        for cid,doc,meta,distance in zip(ids,docs,metas,distances):
            meta=meta or {}
            if not _chunk_is_retrievable(meta, doc):
                continue
            out.append({"id":cid,"document":clean_evidence_text(doc,12000),"metadata":meta,"distance":float(distance) if distance is not None else None})
        # Hibrit aday üretimi: vektör aramasının kaçırdığı, sorgu terimlerini taşıyan parçalar da
        # aynı alaka sıralamasına girer (uzaklıkları gerçek embedding'lerden hesaplanır).
        seen={r["id"] for r in out if r["id"] is not None}
        lexical_n=max(LEXICAL_CANDIDATES_MIN, k*LEXICAL_CANDIDATES_PER_K)
        out.extend(r for r in self._lexical_candidates(query, embedding, lexical_n, eligibility) if r["id"] not in seen)
        # Seri numarası verilmişse ("Sıra No: 5", "Tebliğ No: 2008-32/34") o seri üyesinin parçaları da aday olur:
        # aynı adlı kardeşler anlamsal ve sözcüksel adayları doldurunca aranan üye havuza hiç girmiyordu.
        series_no=extract_series_number(query)
        if series_no:
            seen={r["id"] for r in out if r["id"] is not None}
            out.extend(r for r in self._series_candidates(series_no, eligibility) if r["id"] not in seen)
        return prefer_official_matches(out, limit=max(1,min(k,MAX_RAG_RESULTS)), query=query)

    def _article_chunks(self, law_number: str, article: Tuple[str, str]) -> List[Dict[str, Any]]:
        """Kanunun o maddesinin erişilebilir güncel parçaları, madde içi sırayla (fıkra, bent). Önek: "" asıl madde,
        "geçici" / "ek" (meta verideki article_prefix ilk üç harfiyle karşılaştırılır)."""
        prefix, number = article
        try:
            got = self.collection.get(
                where={"$and": [{"chunk_schema_version": CHUNK_SCHEMA_VERSION}, {"law_number": str(law_number)},
                                {"article_no": str(number)}]},
                include=["documents", "metadatas"])
        except Exception:
            return []
        out = []
        for doc, meta in zip(got.get("documents") or [], got.get("metadatas") or []):
            meta = meta or {}
            meta_prefix = str(meta.get("article_prefix") or "").strip()[:1].upper()
            if meta_prefix != prefix[:1].upper() or not _chunk_is_retrievable(meta, doc):
                continue
            out.append({"document": clean_evidence_text(doc, 12000), "metadata": meta, "distance": None,
                        "_article_match": True})

        def order(item):
            m = item["metadata"]
            return tuple(int(v) if str(v).isdigit() else 0 for v in (m.get("paragraph_no"), m.get("item_no"),
                                                                      m.get("chunk_no")))
        return sorted(out, key=order)

    def _series_candidates(self, series_no: str, eligibility: Optional[EligibilityPolicy]) -> List[Dict[str, Any]]:
        """Başlığındaki seri işareti bu numarayı taşıyan belgelerin parçaları (belge numarası meta verisiyle)."""
        try:
            got = self.collection.get(
                where={"$and": [{"chunk_schema_version": CHUNK_SCHEMA_VERSION}, {"document_number": str(series_no)}]},
                limit=SERIES_CANDIDATES, include=["documents", "metadatas"])
        except Exception:
            return []
        out = []
        for cid, doc, meta in zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []):
            meta = meta or {}
            if not _chunk_is_retrievable(meta, doc) or extract_series_number(meta.get("title")) != str(series_no):
                continue
            if eligibility and not eligibility.admits(meta):
                continue
            out.append({"id": cid, "document": clean_evidence_text(doc, 12000), "metadata": meta, "distance": None})
        return out

    # AnalysisEngine iddia başına aday ister (bkz. AnalysisEngine.retrieve_claim_candidates).
    supports_claim_candidates = True

    def claim_candidates(
        self,
        statement: str,
        n: int,
        eligibility: Optional[EligibilityPolicy] = None,
    ) -> List[Dict[str, Any]]:
        """Bir iddia cümlesi için aday parçalar: BM25 ve vektör sıralamaları ağırlıklı RRF ile
        birleştirilir (mufettis.rag.fusion), ilk n döner. query()'den farkı: yayıncı/başlık
        bonuslu hukuki yeniden sıralama (_legal_rerank) uygulanmaz; adaylar yeniden sıralayıcıya
        (cross-encoder) ve iddia-kanıt seçimine ham alaka sırasıyla gider. Her aday "distance"
        (gerçek gömmeden kare L2) ve "fusion_score" taşır."""
        if not statement.strip() or n <= 0:
            return []
        embedding=self.embed([statement])[0]
        where: Dict[str, Any] = {"chunk_schema_version":CHUNK_SCHEMA_VERSION}
        policy_clauses = eligibility.chroma_clauses() if eligibility else []
        if policy_clauses:
            where = {"$and":[where, *policy_clauses]}
        depth=min(CLAIM_SOURCE_DEPTH, MAX_RAG_RESULTS)
        result=self.collection.query(query_embeddings=[embedding], n_results=depth, where=where)
        semantic=[]
        for cid,doc,meta,distance in zip(
            (result.get("ids") or [[]])[0], result.get("documents",[[]])[0],
            result.get("metadatas",[[]])[0], result.get("distances",[[]])[0],
        ):
            meta=meta or {}
            if cid is None or not _chunk_is_retrievable(meta, doc):
                continue
            if eligibility and not eligibility.admits(meta):
                continue
            semantic.append({"id":cid,"document":clean_evidence_text(doc,12000),"metadata":meta,
                             "distance":float(distance) if distance is not None else None})
        lexical=self._lexical_candidates(statement, embedding, depth, eligibility)
        by_id={r["id"]:r for r in lexical}
        by_id.update({r["id"]:r for r in semantic})
        scores=reciprocal_rank_fusion([
            ([r["id"] for r in lexical], LEXICAL_WEIGHT),
            ([r["id"] for r in semantic], SEMANTIC_WEIGHT),
        ])
        ranked=sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
        return [dict(by_id[cid], fusion_score=score) for cid, score in ranked]

    def lexical_index_path(self, signature: str) -> Optional[Path]:
        """Kalıcı BM25 indeksinin yeri: Chroma veritabanının yanında (veri dizinine özgü). Dosya adı
        imzayı içerir: Windows'ta başka bir süreçte (ör. açık arayüz) açık olan dosyanın üzerine
        yazılamaz; yeni indeks yeni dosyaya kurulur, eskisi kullanılmıyorsa silinir."""
        db_path=getattr(self, "db_path", None)
        if not db_path:
            return None
        digest=hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
        return Path(db_path).with_name(f"{Path(db_path).name}_lexical_{digest}.sqlite3")

    def _remove_stale_lexical_indexes(self, keep: Path) -> None:
        for old in keep.parent.glob(f"{Path(self.db_path).name}_lexical_*.sqlite3*"):
            if old != keep:
                try:
                    old.unlink()
                except OSError:
                    pass  # başka bir süreç hâlâ kullanıyor; sonraki kurulumda silinir

    def _collection_signature(self) -> str:
        """Chroma her yazmayı (ekleme, silme, meta veri güncellemesi) artan bir sıra numarasıyla
        kuyruğa yazar; okumalar numarayı değiştirmez. İmza bu numara ve parça sayısıdır. (Dosya
        zamanı kullanılamaz: Chroma veritabanını her açışta dosyaya yazar.) SQLite deposunda aynı
        rolü kalıcı yazma sayacı görür."""
        # Kanonik şema: indeks uygunluk alanlarını kurulumda saklar; türetme kuralı değişince yenilenmeli.
        parts=[LEXICAL_INDEX_VERSION, str(CHUNK_SCHEMA_VERSION), CANONICAL_SCHEMA, str(self.collection.count())]
        sqlite_path=Path(self.db_path)/"chroma.sqlite3"
        if callable(getattr(self.collection, "generation", None)):
            parts.append(f"gen:{self.collection.generation()}")
        elif sqlite_path.exists():
            conn=sqlite3.connect(f"{sqlite_path.as_uri()}?mode=ro", uri=True)
            try:
                seq=conn.execute(
                    "SELECT MAX(s) FROM (SELECT MAX(seq_id) AS s FROM embeddings_queue "
                    "UNION ALL SELECT MAX(seq_id) FROM max_seq_id)"
                ).fetchone()[0]
            finally:
                conn.close()
            parts.append(f"seq:{seq}")
        return "|".join(parts)

    def _lexical_index(self) -> Union[LexicalIndex, DiskLexicalIndex]:
        """Güncel şemadaki parçaların BM25 indeksi; koleksiyon değişince yeniden kurulur.

        Gerçek veritabanında indeks diskte tutulur (düşük RAM'li laptoplar, tam mevzuat) ve
        uygulama yeniden açıldığında yeniden kurulmaz. Yolu olmayan koleksiyonlarda bellek içidir."""
        pages=lambda: iter_pages(self.collection, where={"chunk_schema_version":CHUNK_SCHEMA_VERSION})
        if not getattr(self, "db_path", None):
            signature=(self.collection.count(), getattr(self, "_mutations", 0))
            cached=getattr(self, "_lexical_cache", None)
            if cached is not None and cached[0]==signature:
                return cached[1]
            index=LexicalIndex.from_pages(pages())
            self._lexical_cache=(signature, index)
            return index
        signature=self._collection_signature()
        cached=getattr(self, "_lexical_cache", None)
        if isinstance(cached, DiskLexicalIndex) and cached.signature==signature:
            return cached
        if isinstance(cached, DiskLexicalIndex):
            cached.close()
        path=self.lexical_index_path(signature)
        index=DiskLexicalIndex.open(path, signature)
        if index is None:
            index=DiskLexicalIndex.build(path, pages(), signature)
            self._remove_stale_lexical_indexes(path)
        self._lexical_cache=index
        return index

    def _lexical_candidates(
        self,
        query: str,
        embedding: List[float],
        n: int,
        eligibility: Optional[EligibilityPolicy],
    ) -> List[Dict[str, Any]]:
        terms=query_terms(query)
        if not terms:
            return []
        hits=self._lexical_index().search(terms, n, admit=eligibility.admits if eligibility else None)
        if not hits:
            return []
        got=self.collection.get(ids=[cid for cid,_ in hits], include=["documents","metadatas","embeddings"])
        embeddings=got.get("embeddings")
        if embeddings is None:
            return []
        q=np.asarray(embedding, dtype=np.float32)
        # get() sırayı korumaz; adaylar BM25 sırasıyla döner.
        order={cid:i for i,(cid,_) in enumerate(hits)}
        rows=sorted(
            zip(got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or [], embeddings),
            key=lambda row: order.get(row[0], len(order)),
        )
        out=[]
        for cid,doc,meta,emb in rows:
            meta=meta or {}
            if not _chunk_is_retrievable(meta, doc):
                continue
            if eligibility and not eligibility.admits(meta):
                continue
            # Koleksiyonun uzaklığı (Chroma varsayılanı) normalize vektörlerde kare L2'dir.
            distance=float(np.sum((np.asarray(emb, dtype=np.float32)-q)**2))
            out.append({"id":cid,"document":clean_evidence_text(doc,12000),"metadata":meta,"distance":distance})
        return out

    def count(self) -> int:
        try:
            return int(self.collection.count())
        except Exception:
            return 0


__all__ = [
    "LocalRAG",
]
