# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Stage 3 Analysis Intelligence
Analysis Engine (Vertical Slice).

Executes end-to-end compliance & regulatory relevance analysis over operational
documents (complaints, internal correspondence) using existing RAG evidence.
Binds claims to grounded evidence, validates all citations, and produces
actionable Inspector Reports without fabricating legal findings.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from mufettis.analysis.citation_validator import CitationValidator
from mufettis.analysis.models import (
    AnalysisPlan,
    AnalysisResult,
    Citation,
    DocumentClaim,
    DocumentClassification,
    DocumentUnderstanding,
    Evidence,
    Finding,
    RetrievalPlan,
)
from mufettis.extraction.text_repair import clean_evidence_text, normalize_legal_whitespace


class AnalysisEngine:
    """Core analysis engine for operational document compliance inspection."""

    def __init__(self, citation_validator: Optional[CitationValidator] = None):
        self.validator = citation_validator or CitationValidator()

    @staticmethod
    def chunk_to_evidence(chunk: Dict[str, Any]) -> Evidence:
        """Adapts an existing LocalRAG.query() result item into a typed Evidence model."""
        meta = chunk.get("metadata", {}) or {}
        raw_doc = str(chunk.get("document", "") or "").strip()

        # Build clean hierarchical reference
        article_no = str(meta.get("article_no", "") or "").strip()
        paragraph_no = str(meta.get("paragraph_no", "") or "").strip()
        item_no = str(meta.get("item_no", "") or "").strip()
        section_no = str(meta.get("section_no", "") or "").strip()
        account_code = str(meta.get("account_code", "") or "").strip()
        chunk_type = str(meta.get("chunk_type", "document") or "document")

        ref_parts = []
        if article_no:
            ref_parts.append(f"Madde {article_no}")
        if paragraph_no:
            ref_parts.append(f"Fıkra {paragraph_no}")
        if item_no:
            ref_parts.append(f"Bent {item_no}")
        if section_no:
            ref_parts.append(f"Bölüm {section_no}")
        if account_code:
            ref_parts.append(f"Hesap {account_code}")

        chunk_ref = " ".join(ref_parts) if ref_parts else f"Parça ({chunk_type})"

        source_doc = meta.get("title") or meta.get("source") or "Resmi Mevzuat Belgesi"
        doc_identity = (
            str(meta.get("law_number", "") or "").strip()
            or str(meta.get("document_key", "") or "").strip()
            or str(meta.get("file_hash", "") or "").strip()
            or "mevzuat_kaydi"
        )

        return Evidence(
            source_document=clean_evidence_text(source_doc, max_chars=300),
            document_identity=doc_identity,
            chunk_reference=chunk_ref,
            text_span=clean_evidence_text(raw_doc, max_chars=4000),
            metadata=meta,
        )

    def retrieve_evidence(
        self,
        retrieval_plan: RetrievalPlan,
        rag_client: Any,
    ) -> List[Evidence]:
        """Executes retrieval queries against existing LocalRAG and returns deduplicated Evidence."""
        evidence_items: List[Evidence] = []
        seen_keys = set()

        for query_str in retrieval_plan.queries:
            if len(evidence_items) >= retrieval_plan.max_evidence_items:
                break

            try:
                results = rag_client.query(query_str, k=retrieval_plan.max_evidence_items)
            except Exception:
                continue

            for r in results:
                meta = r.get("metadata", {}) or {}
                key = (
                    meta.get("file_hash", ""),
                    meta.get("chunk_no", ""),
                    meta.get("article_no", ""),
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                ev = self.chunk_to_evidence(r)
                evidence_items.append(ev)

                if len(evidence_items) >= retrieval_plan.max_evidence_items:
                    break

        return evidence_items

    def analyze(
        self,
        doc: DocumentUnderstanding,
        classification: DocumentClassification,
        analysis_plan: AnalysisPlan,
        retrieval_plan: RetrievalPlan,
        rag_client: Optional[Any] = None,
        retrieved_evidence: Optional[List[Evidence]] = None,
    ) -> AnalysisResult:
        """Executes full vertical slice analysis and returns a validated AnalysisResult."""
        warnings: List[str] = []

        # 1. Acquire evidence
        if retrieved_evidence is not None:
            evidence_pool = list(retrieved_evidence)
        elif rag_client is not None:
            evidence_pool = self.retrieve_evidence(retrieval_plan, rag_client)
        else:
            evidence_pool = []

        # 2. Empty Retrieval Guard: If no evidence exists, do NOT fabricate findings
        if not evidence_pool:
            warnings.append(
                "Mevzuat veritabanında aranan konu ile ilgili doğrulanabilir kanıt parçası bulunamadı."
            )
            return AnalysisResult(
                document=doc,
                classification=classification,
                analysis_plan=analysis_plan,
                retrieval_plan=retrieval_plan,
                findings=[],
                unsupported_claims=list(doc.claims),
                warnings=warnings,
                status="completed_insufficient_evidence",
                inspector_summary=self._generate_summary(doc, [], doc.claims, warnings),
                reviewer_status="Bekliyor",
            )

        # 3. Match claims and subjects with evidence pool
        candidate_findings, unsupported_claims = self._match_claims_to_evidence(
            doc, analysis_plan, evidence_pool
        )

        # 4. Citation and Evidence Validation Guard
        validated_findings: List[Finding] = []
        for candidate in candidate_findings:
            report = self.validator.validate_finding(candidate, retrieved_evidence=evidence_pool)
            if report.is_valid:
                validated_findings.append(candidate)
            else:
                warnings.append(
                    f"Finding '{candidate.issue}' elendi: {'; '.join(report.errors)}"
                )
                # If finding failed, move claim to unsupported claims if present
                for cl in doc.claims:
                    if cl.claim_id == candidate.claim_reference_id and cl not in unsupported_claims:
                        unsupported_claims.append(cl)

        summary = self._generate_summary(doc, validated_findings, unsupported_claims, warnings)

        return AnalysisResult(
            document=doc,
            classification=classification,
            analysis_plan=analysis_plan,
            retrieval_plan=retrieval_plan,
            findings=validated_findings,
            unsupported_claims=unsupported_claims,
            warnings=warnings,
            status="completed",
            inspector_summary=summary,
            reviewer_status="Bekliyor",
        )

    def _match_claims_to_evidence(
        self,
        doc: DocumentUnderstanding,
        analysis_plan: AnalysisPlan,
        evidence_pool: List[Evidence],
    ) -> Tuple[List[Finding], List[DocumentClaim]]:
        findings: List[Finding] = []
        unsupported: List[DocumentClaim] = []

        claims_to_process = list(doc.claims)
        if not claims_to_process:
            # Fallback if no specific claims extracted: create a synthetic claim from subjects/summary
            synth_subject = doc.subjects[0] if doc.subjects else "genel_uyum"
            claims_to_process = [
                DocumentClaim(
                    claim_id="claim_general",
                    statement=f"Belgede belirtilen {synth_subject} kapsamındaki iddia ve işlemler",
                    subject=synth_subject,
                    involved_entities=[e.text for e in doc.entities[:2]],
                )
            ]

        for claim in claims_to_process:
            matched_ev = self._find_best_evidence_for_claim(claim, doc.subjects, evidence_pool, doc=doc)
            if not matched_ev:
                unsupported.append(claim)
                continue

            # Build grounded Finding
            verbatim_span = self._extract_verbatim_span(matched_ev.text_span)
            citation = Citation(
                source=str(matched_ev.metadata.get("source") or "BDDK / Resmi Gazete"),
                document_identity=matched_ev.document_identity,
                reference=f"{matched_ev.source_document} {matched_ev.chunk_reference}".strip(),
                text_span=verbatim_span,
                metadata={"file_hash": matched_ev.metadata.get("file_hash", "")},
            )

            # Determine risk level objectively
            risk = "Orta"
            statement_lower = claim.statement.lower()
            if any(w in statement_lower for w in ["yetkisiz", "dolandırıcılık", "şüpheli", "onayım olmadan"]):
                risk = "Yüksek"
            elif any(e.entity_type == "amount" for e in doc.entities):
                risk = "Orta"

            institution = next((e.text for e in doc.entities if e.entity_type == "institution"), "İlgili Banka")

            finding = Finding(
                issue=f"{claim.subject.replace('_', ' ').title()}: {claim.statement[:80]}...",
                evidence=[matched_ev],
                citations=[citation],
                regulation_reference=f"{matched_ev.source_document} {matched_ev.chunk_reference}".strip(),
                explanation=(
                    f"Belgede '{claim.statement}' hususu belirtilmektedir. "
                    f"Dayanak mevzuat metninde '{verbatim_span[:120]}...' hükmü yer almakta olup, "
                    f"işlemin anılan mevzuat şartları ve banka bilgilendirme yükümlülüğü çerçevesinde "
                    f"uygunluğu değerlendirilmelidir."
                ),
                inspector_action=(
                    f"Denetçi tarafından {institution} nezdindeki işlem kayıtları, müşteri onay/sözleşme "
                    f"örnekleri ve bildirim logları temin edilerek {matched_ev.chunk_reference} hükmüne "
                    f"uygunluğu doğrulanmalıdır."
                ),
                claim_reference_id=claim.claim_id,
                model_interpretation=(
                    "Mevzuat hükümleri ile belgedeki beyan arasında inceleme gerektiren olası bir "
                    "uyum riski bulunmaktadır. Nihai tespit yetkili denetçi incelemesine bağlıdır."
                ),
                risk_level=risk,
            )
            findings.append(finding)

        return findings, unsupported

    def _find_best_evidence_for_claim(
        self,
        claim: DocumentClaim,
        subjects: List[str],
        evidence_pool: List[Evidence],
        doc: Optional[DocumentUnderstanding] = None,
    ) -> Optional[Evidence]:
        """Finds the most semantically relevant Evidence item for a claim."""
        claim_text = (
            claim.statement.replace("_", " ")
            + " "
            + " ".join(subjects).replace("_", " ")
        )
        if doc and doc.title:
            claim_text += " " + doc.title.replace("_", " ")

        raw_terms = set(re.findall(r"[a-zçğıöşüA-ZÇĞİÖŞÜ0-9]{3,}", claim_text.lower()))
        stop_words = {
            "olan", "olarak", "veya", "için", "icin", "göre", "gore",
            "belgede", "belirtilen", "iddia", "işlemler", "kapsamındaki",
            "tarafından", "tarafindan", "edilmektedir", "edilmiştir",
        }
        claim_terms = {t for t in raw_terms if t not in stop_words}

        best_ev: Optional[Evidence] = None
        best_score = -1.0

        for ev in evidence_pool:
            score = 0.0
            ev_text_lower = ev.text_span.lower()
            ev_title_lower = ev.source_document.lower()

            for term in claim_terms:
                if term in ev_text_lower:
                    score += 1.0
                if term in ev_title_lower:
                    score += 1.5

            if score > best_score:
                best_score = score
                best_ev = ev

        # Require minimal relevance threshold
        if best_score >= 1.0:
            return best_ev
        return None

    def _extract_verbatim_span(self, text: str, max_chars: int = 250) -> str:
        """Extracts an exact verbatim sentence from the evidence text for truthful citation."""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        for s in sentences:
            if 30 <= len(s) <= max_chars:
                return s
        if sentences:
            return sentences[0][:max_chars]
        return text[:max_chars]

    def _generate_summary(
        self,
        doc: DocumentUnderstanding,
        findings: List[Finding],
        unsupported: List[DocumentClaim],
        warnings: List[str],
    ) -> str:
        """Generates a structured, transparent inspector summary."""
        lines = [
            f"# MÜFETTİŞ AI — ANALİZ RAPORU",
            f"**Belge Başlığı:** {doc.title}",
            f"**Belge Tarihi:** {doc.date or 'Tespit Edilemedi'}",
            f"**Belge Kaynağı:** {doc.source or 'Belirtilmedi'}",
            f"**Tespit Edilen Varlıklar:** {', '.join(e.text for e in doc.entities) if doc.entities else 'Yok'}",
            f"**Konu Başlıkları:** {', '.join(doc.subjects) if doc.subjects else 'Genel'}",
            "",
            f"## BULGULAR ({len(findings)} Doğrulanmış Bulgu)",
        ]

        if not findings:
            lines.append("Doğrulanabilir mevzuat dayanağı olan bulgu üretilemedi.")
        else:
            for idx, f in enumerate(findings, 1):
                lines.append(f"### {idx}. {f.issue}")
                lines.append(f"- **Risk Seviyesi:** {f.risk_level or 'Belirsiz'}")
                lines.append(f"- **Mevzuat Dayanağı:** {f.regulation_reference}")
                lines.append(f"- **Analitik Açıklama:** {f.explanation}")
                lines.append(f"- **Müfettiş Takip Adımı:** {f.inspector_action}")
                if f.citations:
                    lines.append(f"- **Alıntılanan Kanıt (Citation):** \"{f.citations[0].text_span}\"")
                lines.append("")

        if unsupported:
            lines.append(f"## MEVZUAT DAYANAĞI BULUNAMAYAN İDDİALAR ({len(unsupported)})")
            for cl in unsupported:
                lines.append(f"- {cl.statement} *(Mevzuat eşleşmesi bulunamadı)*")
            lines.append("")

        if warnings:
            lines.append(f"## SİSTEM UYARILARI")
            for w in warnings:
                lines.append(f"- ⚠️ {w}")
            lines.append("")

        lines.append(
            "*(Not: Bu rapor karar destek amaçlıdır; kesin hukuki ihlal tespiti içermez. "
            "Bulguların uygulanabilirliği yetkili denetçi tarafından incelenmelidir.)*"
        )
        return "\n".join(lines)
