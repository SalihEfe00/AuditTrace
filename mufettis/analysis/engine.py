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

import hashlib
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from mufettis.analysis.citation_validator import CitationValidator
from mufettis.analysis.document_understanding import DocumentUnderstandingService
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
from mufettis.analysis.turkish_text import (
    concept_terms,
    content_terms,
    distinct_stems,
    is_weak_term,
    matched_terms,
    matches_any,
    turkish_lower,
)
from mufettis.common.utils import canonical_source_id
from mufettis.extraction.text_repair import clean_evidence_text, normalize_legal_whitespace

# Bir kanıtın bir iddiayla ilişkilendirilmesi için gereken en az farklı içerik terimi
# (genel bankacılık kelimeleri ve durak kelimeler sayılmaz).
MIN_MATCHED_TERMS = 2
# Terimlerden en az biri iddia cümlesinin kendisinden gelmeli (konu anahtar
# kelimeleri veya belge başlığı tek başına yetmez).
MIN_CLAIM_STATEMENT_TERMS = 1
# Normalize gömmelerde L2^2 mesafesi (0 = aynı, 2 = dik). Ölçümlerde ilgisiz
# sorguların en iyi sonuçları 0.8-1.3 aralığında kaldığından mesafe tek başına
# alaka kanıtı değildir; yalnızca açıkça uzak sonuçları elemek için kullanılır.
MAX_RELEVANT_DISTANCE = 1.2

# Kanıt metnini alaka için cümle/satır birimlerine böler (çıkarım kaynaklı yapışık
# metinlerde nokta olmayabileceği için satır sonları ve ; : da sınır sayılır).
_EVIDENCE_UNIT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+")


def _evidence_units(text: str) -> List[str]:
    return [u.strip() for u in _EVIDENCE_UNIT_SPLIT.split(text or "") if u.strip()]


class RetrievalUnavailableError(RuntimeError):
    """Hiçbir mevzuat sorgusu çalıştırılamadı (veritabanı erişim hatası)."""


def retrieval_error_warnings(retrieval_plan: RetrievalPlan, errors: List[str]) -> List[str]:
    """Sorgu hatalarını değerlendirir.

    Tüm sorgular başarısızsa RetrievalUnavailableError fırlatır: erişim hatası, denetçiye
    "mevzuatta kanıt bulunamadı" gibi gösterilmemelidir. Kısmi hatalar uyarı olarak döner.
    """
    if not errors:
        return []
    if len(errors) >= len(retrieval_plan.queries):
        raise RetrievalUnavailableError(
            f"Mevzuat veritabanına erişilemedi; {len(errors)} sorgunun hiçbiri çalıştırılamadı. İlk hata: {errors[0]}"
        )
    return [
        f"{len(errors)}/{len(retrieval_plan.queries)} mevzuat sorgusu hata verdi; kanıt havuzu eksik olabilir. "
        f"İlk hata: {errors[0]}"
    ]


class AnalysisEngine:
    """Core analysis engine for operational document compliance inspection."""

    def __init__(self, citation_validator: Optional[CitationValidator] = None):
        self.validator = citation_validator or CitationValidator()

    @staticmethod
    def chunk_to_evidence(chunk: Dict[str, Any]) -> Evidence:
        """Adapts an existing LocalRAG.query() result item into a typed Evidence model."""
        meta = dict(chunk.get("metadata", {}) or {})
        raw_doc = str(chunk.get("document", "") or "").strip()
        if chunk.get("distance") is not None:
            meta["retrieval_distance"] = float(chunk["distance"])

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
        # Kimlik bulunamazsa belge başlığından türetilir; sabit bir yedek değer farklı
        # belgeleri aynı kimlikte birleştirip belgeler arası tutarlılık kontrolünü etkisizleştirirdi.
        doc_identity = (
            str(meta.get("law_number", "") or "").strip()
            or str(meta.get("document_key", "") or "").strip()
            or str(meta.get("file_hash", "") or "").strip()
            or "kayit_" + hashlib.sha1(str(source_doc).encode("utf-8")).hexdigest()[:12]
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
        errors: Optional[List[str]] = None,
    ) -> List[Evidence]:
        """Executes retrieval queries against existing LocalRAG and returns deduplicated Evidence.

        - Kaynak kapsamı (source_scope) dışındaki kaynaklardan gelen parçalar alınmaz;
          source_id taşımayan parçalar kapsam bilinmediği için elenmez.
        - filters["law_number"] verildiyse o kanuna ait parçalar sıralamada öne alınır.
        - Tekilleştirme belge kimliği + parça konumu + metin özeti ile yapılır; metadata
          eksik olsa bile farklı parçalar birleşmez.
        """
        scope = {canonical_source_id(s) for s in (retrieval_plan.source_scope or [])}
        law_filter = str((retrieval_plan.filters or {}).get("law_number", "") or "").strip()
        limit = retrieval_plan.max_evidence_items
        collected: List[Tuple[int, int, Evidence]] = []
        seen_keys = set()

        for query_str in retrieval_plan.queries:
            if len(collected) >= limit:
                break

            try:
                results = rag_client.query(query_str, k=limit)
            except Exception as exc:
                # Hata çağırana bildirilir; aksi halde erişim hatası "kanıt yok" gibi görünürdü.
                if errors is not None:
                    errors.append(f"{query_str[:60]}: {type(exc).__name__}: {exc}")
                continue

            for r in results:
                meta = r.get("metadata", {}) or {}
                source_id = str(meta.get("source_id", "") or "").strip()
                if scope and source_id and canonical_source_id(source_id) not in scope:
                    continue
                text = str(r.get("document", "") or "")
                key = (
                    meta.get("file_hash") or meta.get("document_key") or meta.get("title") or "",
                    meta.get("chunk_no", ""),
                    meta.get("article_no", ""),
                    meta.get("paragraph_no", ""),
                    meta.get("item_no", ""),
                    hashlib.sha1(normalize_legal_whitespace(text).encode("utf-8")).hexdigest()[:16],
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                law_rank = 0 if law_filter and str(meta.get("law_number", "")).strip() == law_filter else 1
                collected.append((law_rank, len(collected), self.chunk_to_evidence(r)))

                if len(collected) >= limit:
                    break

        collected.sort(key=lambda item: (item[0], item[1]))
        return [ev for _, _, ev in collected]

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
            retrieval_errors: List[str] = []
            evidence_pool = self.retrieve_evidence(retrieval_plan, rag_client, errors=retrieval_errors)
            warnings.extend(retrieval_error_warnings(retrieval_plan, retrieval_errors))
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
                # Doğrulama sonucu alıntıya işlenir; arayüz rozeti bu değeri gösterir.
                for citation in candidate.citations:
                    citation.metadata["is_valid"] = True
                    citation.metadata["validation_method"] = "grounded_in_retrieved_evidence"
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
            # Şikâyet kalıbı taşımayan belgeler (ör. iç denetim notu) için iddia, belgenin
            # tespit cümlelerinden kurulur; içeriksiz sentetik bir cümle eşleşme üretmemeli.
            synth_subject = doc.subjects[0] if doc.subjects else "genel_uyum"
            statement = DocumentUnderstandingService.observation_statement(doc)
            if statement:
                claims_to_process = [
                    DocumentClaim(
                        claim_id="claim_general",
                        statement=statement,
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
            statement_terms, context_terms = self._claim_terms(claim, doc)
            verbatim_span = self._extract_verbatim_span(
                matched_ev.text_span, focus_terms=statement_terms | context_terms
            )
            citation = Citation(
                source=str(matched_ev.metadata.get("source") or "BDDK / Resmi Gazete"),
                document_identity=matched_ev.document_identity,
                reference=f"{matched_ev.source_document} {matched_ev.chunk_reference}".strip(),
                text_span=verbatim_span,
                metadata={"file_hash": matched_ev.metadata.get("file_hash", "")},
            )

            # Determine risk level objectively
            risk = "Orta"
            statement_lower = turkish_lower(claim.statement)
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
        """İddiayla en ilgili kanıtı seçer; yeterli alaka yoksa None döner.

        Kabul ölçütü (hepsi gerekir), kanıtın TEK BİR cümlesi/satırı için:
        - O cümleyle kök düzeyinde en az MIN_MATCHED_TERMS farklı içerik kökü ortak olmalı
          (genel bankacılık kelimeleri, durak kelimeler ve kurum adları sayılmaz). Terimlerin
          aynı cümlede birlikte geçmesi şartı, uzun parçalara dağılmış tesadüfi eşleşmeleri
          (ör. Tekdüzen Hesap Planı'nda farklı hesaplardaki "finansal" ve "ücret") eler.
        - Bu köklerden en az MIN_CLAIM_STATEMENT_TERMS tanesi iddia cümlesinin kendisinden gelmeli.
        - İddia cümlesinden gelen terimlerden en az biri ayırt edici olmalı; yalnızca sık geçen
          terimler (kredi, ödeme, faiz ..., bkz. WEAK_DOMAIN_WORDS) ya da konu anahtar kelime
          listesinden gelen bağlam terimleri (ör. "anapara") ortaklığı yetmez.
        - RAG mesafesi biliniyorsa MAX_RELEVANT_DISTANCE değerini aşmamalı.
        Kanıt başlığı tek başına yetmez, yalnızca sıralamada kullanılır. Adaylar en iyi cümlenin
        eşleşmesine, parçanın toplam eşleşmesine, başlık eşleşmesine ve mesafeye göre sıralanır.
        """
        statement_terms, context_terms = self._claim_terms(claim, doc)
        all_terms = statement_terms | context_terms

        best_ev: Optional[Evidence] = None
        best_rank: Tuple[float, ...] = (-1.0,)

        for ev in evidence_pool:
            distance = ev.metadata.get("retrieval_distance")
            if distance is not None and float(distance) > MAX_RELEVANT_DISTANCE:
                continue
            best_unit_stems = 0
            for unit in _evidence_units(ev.text_span):
                unit_matched = matched_terms(all_terms, content_terms(unit))
                unit_stems = len(distinct_stems(unit_matched))
                if (
                    unit_stems >= MIN_MATCHED_TERMS
                    and len(distinct_stems(unit_matched & statement_terms)) >= MIN_CLAIM_STATEMENT_TERMS
                    and any(not is_weak_term(t) for t in unit_matched & statement_terms)
                ):
                    best_unit_stems = max(best_unit_stems, unit_stems)
            if not best_unit_stems:
                continue
            text_matched = matched_terms(all_terms, content_terms(ev.text_span))
            title_only = {
                t for t in matched_terms(all_terms, content_terms(ev.source_document))
                if not matches_any(t, text_matched)
            }
            rank = (
                float(best_unit_stems),
                float(len(distinct_stems(text_matched))),
                float(len(distinct_stems(title_only))),
                -float(distance) if distance is not None else 0.0,
            )
            if rank > best_rank:
                best_rank, best_ev = rank, ev

        return best_ev

    @staticmethod
    def _claim_terms(
        claim: DocumentClaim,
        doc: Optional[DocumentUnderstanding],
    ) -> Tuple[Set[str], Set[str]]:
        """(iddia cümlesi terimleri, bağlam terimleri: konu anahtar kelimeleri + belge başlığı).

        Kurum adları (ör. "Finansbank") mevzuatla anlam ilişkisi taşımadığı ve önek
        eşleşmesinde yanıltıcı olduğu ("finansal") için terimlerden çıkarılır.
        """
        excluded: Set[str] = set()
        if doc:
            for entity in doc.entities:
                if entity.entity_type == "institution":
                    excluded |= content_terms(entity.text)
        # Eş anlamlı ifadeler ("bilgim dışında") iddianın kendi kavramı ("yetkisiz") sayılır.
        statement_terms = (content_terms(claim.statement) | concept_terms(claim.statement)) - excluded
        context_text = " ".join(DocumentUnderstandingService.SUBJECT_KEYWORDS.get(claim.subject, []))
        if doc and doc.title and doc.title != "Belge Metni":
            context_text += " " + doc.title
        context_terms = {
            t for t in content_terms(context_text) - excluded if not matches_any(t, statement_terms)
        }
        return statement_terms, context_terms

    def _extract_verbatim_span(
        self,
        text: str,
        max_chars: int = 250,
        focus_terms: Optional[Set[str]] = None,
    ) -> str:
        """Kanıt metninden birebir bir cümle seçer; focus_terms verilirse en çok terim içeren cümle."""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        candidates = [s for s in sentences if 30 <= len(s) <= max_chars]
        if candidates and focus_terms:
            scored = [(len(matched_terms(focus_terms, content_terms(s))), -i, s) for i, s in enumerate(candidates)]
            best_hits, _, best_sentence = max(scored)
            if best_hits > 0:
                return best_sentence
        if candidates:
            return candidates[0]
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
