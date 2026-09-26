# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import shutil
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import chromadb
except Exception:
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

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
from mufettis.common.utils import canonical_source_id, now_iso, safe_json, mask_sensitive
from mufettis.extraction.text_repair import normalize_extracted_text, repair_extracted_spacing
from mufettis.legal.parser import (
    _chunk_is_retrievable,
    _chunk_matches_account_ref,
    _chunk_matches_document_ref,
    _legal_rerank,
    _resolve_query_document_reference_from_collection,
    clean_evidence_text,
    detect_doc_type,
    extract_first,
    extract_legal_identifier,
    extract_legal_metadata,
    extract_query_account_code,
    extract_query_document_reference,
    prefer_official_matches,
    split_legal_chunks,
)
from mufettis.rag.index import IngestIndex

class LocalRAG:
    def __init__(self, db_path: Path = DB_PATH, model_name: str = EMBEDDING_MODEL, allow_download: bool = False):
        if chromadb is None:
            raise RuntimeError("chromadb kurulu değil. `pip install chromadb`")
        if SentenceTransformer is None:
            raise RuntimeError("sentence-transformers kurulu değil. `pip install sentence-transformers`")
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.allow_download = allow_download
        self._model = None
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
            if existing and existing.get("ids"):
                return {"status": "skipped", "count": len(existing["ids"])}

        chunks = split_legal_chunks(text)
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

        for ch in chunks:
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
                "paragraph_no": ch.get("paragraph_no") or "",
                "item_no": ch.get("item_no") or "",
                "subitem_no": ch.get("subitem_no") or "",
                "section_no": ch.get("section_no") or "",
                "account_code": ch.get("account_code") or "",
                "chunk_type": ch.get("chunk_type") or "document",
                "structure_type": effective_structure_type,
                "subchunk_no": int(ch.get("subchunk_no", 0) or 0),
                "chunk_no": int(ch.get("chunk_no", 0) or 0),
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

        return {"status": "ingested", "count": total, "chunks": chunks}

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
            new_metas.append(m)
        self.collection.update(ids=ids, metadatas=new_metas)
        return len(ids)

    def mark_hash_superseded(self, old_hash: str, new_hash: str, effective_to: Optional[str] = None) -> int:
        return self.update_hash_metadata(old_hash, {
            "status": "superseded",
            "superseded_by": new_hash,
            "valid_to": effective_to or now_iso(),
        })

    def _exact_law_candidates(self, law_number: str) -> List[Dict[str, Any]]:
        """Chroma metadata + document taramasıyla exact-law adaylarını deterministik bul."""
        try:
            got=self.collection.get(
                where={"chunk_schema_version":CHUNK_SCHEMA_VERSION},
                include=["documents","metadatas"],
            )
        except Exception as exc:
            raise RuntimeError(f"Exact-law belge taraması başarısız: {exc}") from exc

        docs=got.get("documents",[]) or []
        metas=got.get("metadatas",[]) or []

        pattern=re.compile(
            rf"(?<!\d){re.escape(str(law_number))}\s*sayılı\s*Kanun\b",
            re.I,
        )
        exact=[]
        for doc,meta in zip(docs,metas):
            meta=meta or {}
            if str(meta.get("chunk_schema_version",""))!=str(CHUNK_SCHEMA_VERSION):
                continue
            if not _chunk_is_retrievable(meta, doc):
                continue
            if str(meta.get("status","")).lower() in {
                "superseded","mülga","mulga","repealed","repealed_or_changed"
            }:
                continue

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
        return exact


    def query(self, query: str, k: int=5) -> List[Dict[str,Any]]:
        if not query.strip():
            return []

        embedding=self.embed([query])[0]
        explicit_law=extract_legal_identifier(query)
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
            return prefer_official_matches(exact, limit=max(1,min(k,MAX_RAG_RESULTS)), query=query)

        # Açık belge/h hesap kimliğinde metadata where filtresi tek otorite değildir.
        # Rebuild sonrası eksik/stale kimlik metadata'sını gerçek chunk metniyle de doğrula.
        if document_ref or account_ref:
            try:
                current=self.collection.get(where={"chunk_schema_version":CHUNK_SCHEMA_VERSION}, include=["documents","metadatas"])
            except Exception as exc:
                raise RuntimeError(f"Güncel RAG kayıtları okunamadı: {exc}") from exc
            docs=current.get("documents",[]) or []
            metas=current.get("metadatas",[]) or []
            out=[]
            for doc,meta in zip(docs,metas):
                meta=meta or {}
                if not _chunk_is_retrievable(meta, doc):
                    continue
                if document_ref and not _chunk_matches_document_ref(meta, doc, document_ref):
                    continue
                if account_ref and not _chunk_matches_account_ref(meta, doc, account_ref):
                    continue
                out.append({"document":clean_evidence_text(doc,12000),"metadata":meta,"distance":None})
            if out:
                return prefer_official_matches(out, limit=max(1,min(k,MAX_RAG_RESULTS)), query=query)
            return []

        # Serbest sorgular semantic retrieval kullanır.
        result=self.collection.query(
            query_embeddings=[embedding],
            n_results=min(max(k*20,50),MAX_RAG_RESULTS),
            where={"chunk_schema_version":CHUNK_SCHEMA_VERSION},
        )
        docs=result.get("documents",[[]])[0]
        metas=result.get("metadatas",[[]])[0]
        distances=result.get("distances",[[]])[0]
        out=[]
        for doc,meta,distance in zip(docs,metas,distances):
            meta=meta or {}
            if not _chunk_is_retrievable(meta, doc):
                continue
            out.append({"document":clean_evidence_text(doc,12000),"metadata":meta,"distance":float(distance) if distance is not None else None})
        return prefer_official_matches(out, limit=max(1,min(k,MAX_RAG_RESULTS)), query=query)

    def count(self) -> int:
        try:
            return int(self.collection.count())
        except Exception:
            return 0


__all__ = [k for k in dict(globals()).keys() if not k.startswith('__')]
