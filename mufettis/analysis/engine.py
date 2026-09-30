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
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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
    matched_bigrams,
    matched_terms,
    matches_any,
    statement_bigrams,
    tokenize,
    turkish_lower,
)
from mufettis.common.utils import article_label, canonical_source_id
from mufettis.config import MAX_RAG_RESULTS
from mufettis.corpus import HISTORICAL_STATUSES, EligibilityPolicy, InstrumentType, canonical_view
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

# Mesafe sınırını aşan bir kanıt, tek cümlede bu kadar farklı kök eşleşiyorsa yine kabul edilir
# (bu köklerden en az STRONG_LEXICAL_DISTINCTIVE tanesi iddia cümlesinden ve ayırt edici olmalı).
STRONG_LEXICAL_STEMS = 4
STRONG_LEXICAL_DISTINCTIVE = 2

# Uygunluk politikası varken RAG'dan istenen sonuç çarpanı (bkz. retrieve_evidence).
EXCLUSION_OVERFETCH = 4

# İddia başına, iddia cümlesiyle aranan aday sayısı (bkz. retrieve_claim_candidates). Ölçümde
# (benchmarks/) beklenen hüküm BM25+vektör RRF'nin ilk 30'unda dev vakalarının çoğunda vardı.
CLAIM_CANDIDATES = 30
# Yeniden sıralayıcıya (cross-encoder) gönderilen en fazla aday: sözcüksel kapıyı geçenler, aday sırasıyla.
RERANK_MAX_CANDIDATES = 30
# Düşük güvenli eşleşmede (reranker.CONFIDENT_SCORES) denetçinin ilk adımı atfın kendisini doğrulamaktır.
LOW_CONFIDENCE_ACTION = (
    "Dayanak eşleşmesi düşük güvenlidir: önce atıf yapılan hükmün belgedeki olayı gerçekten "
    "düzenleyip düzenlemediği doğrulanmalıdır. "
)

# Kanıt metnini alaka için cümle/satır birimlerine böler (çıkarım kaynaklı yapışık
# metinlerde nokta olmayabileceği için satır sonları ve ; : da sınır sayılır).
_EVIDENCE_UNIT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+")


def _evidence_units(text: str) -> List[str]:
    return [u.strip() for u in _EVIDENCE_UNIT_SPLIT.split(text or "") if u.strip()]


_TITLE_CONNECTORS = frozenset({"ve", "ile", "veya", "ya", "da", "de", "dair", "hakkında", "ilişkin"})


def _is_transitional(meta: Dict[str, Any]) -> bool:
    return turkish_lower(str(meta.get("article_prefix", "") or "")).startswith("geç")


def _turkish_title_case(text: str) -> str:
    words = turkish_lower(text).split()
    return " ".join(
        w if i and w in _TITLE_CONNECTORS else (w[:1].replace("i", "İ").upper() + w[1:])
        for i, w in enumerate(words)
    )


def evidence_display_title(meta: Dict[str, Any]) -> str:
    """Kanıtın atıflarda görünen adı. Belge kendi adını beyan ediyorsa (kanun numarası beyanının
    üstündeki ad) o kullanılır: dosya adları ("55555") ve tarayıcı sayfa başlıkları ("Mevzuat Bilgi
    Sistemi") belgenin adı değildir. Ad yoksa ve başlık yalnızca bir numaraysa ("1296") belgenin
    beyan ettiği konu, o da yoksa "7440 sayılı Kanun" gösterilir. Kayıt değişmez.

    Kanunun önceki metni (M7.3, olay tarihine göre seçilmiş, M7.4) adıyla birlikte etiketlenir: "… — olay tarihindeki
    metin: 6327 sayılı Kanun ile değişik hali (2012-06-29 – 2013-03-28)"."""
    name = _display_name(meta)
    if str(meta.get("status") or "") in HISTORICAL_STATUSES:
        amended_by = str(meta.get("amended_by") or "").strip()
        version = f"{amended_by} ile değişik hali" if amended_by and amended_by != "ilk metin" else "ilk metin"
        window = " – ".join(str(meta.get(k) or "?") for k in ("valid_from", "valid_to"))
        return f"{name} — olay tarihindeki metin: {version} ({window})"
    return name


def _display_name(meta: Dict[str, Any]) -> str:
    declared = str(meta.get("declared_title") or "").strip()
    law_number = str(meta.get("law_number") or "").strip()
    if declared:
        name = _turkish_title_case(declared)
        return f"{law_number} sayılı {name}" if law_number.isdigit() else name
    title = str(meta.get("title") or "").strip()
    descriptive = bool(re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3,}", title))
    subject = str(meta.get("declared_subject") or "").strip()
    if not descriptive and subject:
        return subject
    if not descriptive and law_number.isdigit() and canonical_view(meta).instrument_type == InstrumentType.LAW:
        return f"{law_number} sayılı Kanun"
    return title or str(meta.get("source") or "") or "Resmi Mevzuat Belgesi"


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
        # Varsayılan: sabit WEAK_DOMAIN_WORDS. Gerçek korpusla çalışırken use_term_stats ile
        # belge frekansına dayalı yüklem verilir (bkz. mufettis.analysis.term_stats).
        self.is_weak_term: Callable[[str], bool] = is_weak_term
        # İsteğe bağlı dil modeli doğrulayıcısı (bkz. mufettis.analysis.claim_verifier). None ise
        # eşleştirme yalnızca sözcükseldir.
        self.claim_verifier: Optional[Any] = None
        # İsteğe bağlı yeniden sıralayıcı (mufettis.rag.reranker): score(sorgu, metinler) -> puanlar.
        # None ise seçim yalnızca sözcüksel sıralamayla yapılır.
        self.reranker: Optional[Any] = None

    def use_term_stats(self, stats: Optional[Dict[str, Any]]) -> None:
        """Korpus terim istatistiğini kullan; None veya yetersizse sabit listeye döner."""
        from mufettis.analysis.term_stats import weak_term_predicate

        self.is_weak_term = weak_term_predicate(stats)

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
            ref_parts.append(article_label(article_no, meta.get("article_prefix")))
        if paragraph_no:
            ref_parts.append(f"Fıkra {paragraph_no}")
        if item_no:
            ref_parts.append(f"Bent {item_no}")
        if section_no:
            ref_parts.append(f"Bölüm {section_no}")
        if account_code:
            ref_parts.append(f"Hesap {account_code}")

        chunk_ref = " ".join(ref_parts) if ref_parts else f"Parça ({chunk_type})"

        source_doc = evidence_display_title(meta)
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
        - Uygunluk politikası (filters: exclude_roles, exclude_publisher_kinds) kanonik corpus
          modeline göre uygulanır: kural koymayan parçalar veya ikincil yayıncı kopyaları elenir.
        - Tekilleştirme sürüm kimliği (expression) + parça konumu + metin özeti ile yapılır: aynı
          sürümün farklı format/kaynaktaki kopyaları tek kanıt sayılır, farklı parçalar birleşmez.
        - Sorguların sonuçları sıra sıra harmanlanır (1. sorgunun 1. sonucu, 2. sorgunun 1. sonucu,
          ...): ilk sorgu (çoğu zaman belge başlığı) havuzu tek başına doldurup iddia cümlesinin
          sorgusunu dışarıda bırakamaz. Bir sorgu en fazla query_depths'teki sayıda yeni kanıt katar.
        """
        scope = {canonical_source_id(s) for s in (retrieval_plan.source_scope or [])}
        law_filter = str((retrieval_plan.filters or {}).get("law_number", "") or "").strip()
        policy = EligibilityPolicy.from_filters(retrieval_plan.filters)
        limit = retrieval_plan.max_evidence_items
        # Elenen parçalar sonuçların çoğunu tutabildiğinden (hesap planları) fazladan istenir.
        fetch_k = min(MAX_RAG_RESULTS, limit * EXCLUSION_OVERFETCH) if policy else limit
        query_kwargs: Dict[str, Any] = {}
        # Yetenek açıkça bildirilmeli (LocalRAG); test sahteleri her argümanı kabul ediyor görünebilir.
        if policy and getattr(rag_client, "supports_eligibility_policy", False) is True:
            query_kwargs["eligibility"] = policy

        per_query: List[Tuple[List[Dict[str, Any]], Optional[int]]] = []
        for query_str in retrieval_plan.queries:
            try:
                results = rag_client.query(query_str, k=fetch_k, **query_kwargs)
            except Exception as exc:
                # Hata çağırana bildirilir; aksi halde erişim hatası "kanıt yok" gibi görünürdü.
                if errors is not None:
                    errors.append(f"{query_str[:60]}: {type(exc).__name__}: {exc}")
                continue
            admitted = [r for r in results if self._admits(r, scope, policy)]
            per_query.append((admitted, retrieval_plan.query_depths.get(query_str)))

        collected: List[Tuple[int, int, Evidence]] = []
        contributed = [0] * len(per_query)
        seen_keys = set()
        for rank in range(max((len(rs) for rs, _ in per_query), default=0)):
            if len(collected) >= limit:
                break
            for qi, (results, depth) in enumerate(per_query):
                if rank >= len(results) or (depth is not None and contributed[qi] >= depth):
                    continue
                r = results[rank]
                meta = r.get("metadata", {}) or {}
                key = self._result_key(meta, str(r.get("document", "") or ""))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                contributed[qi] += 1
                law_rank = 0 if law_filter and str(meta.get("law_number", "")).strip() == law_filter else 1
                collected.append((law_rank, len(collected), self.chunk_to_evidence(r)))
                if len(collected) >= limit:
                    break

        collected.sort(key=lambda item: (item[0], item[1]))
        return [ev for _, _, ev in collected]

    @staticmethod
    def _admits(result: Dict[str, Any], scope: Set[str], policy: Optional[EligibilityPolicy]) -> bool:
        """Kaynak kapsamı ve uygunluk politikası (bkz. retrieve_evidence)."""
        meta = result.get("metadata", {}) or {}
        source_id = str(meta.get("source_id", "") or "").strip()
        if scope and source_id and canonical_source_id(source_id) not in scope:
            return False
        return not (policy and not policy.admits(meta))

    @staticmethod
    def _result_key(meta: Dict[str, Any], text: str) -> Tuple[Any, ...]:
        """Tekilleştirme anahtarı: sürüm kimliği + parça konumu + metin özeti."""
        return (
            canonical_view(meta).expression_id,
            meta.get("chunk_no", ""),
            meta.get("article_no", ""),
            meta.get("paragraph_no", ""),
            meta.get("item_no", ""),
            hashlib.sha1(normalize_legal_whitespace(text).encode("utf-8")).hexdigest()[:16],
        )

    def retrieve_claim_candidates(
        self,
        doc: DocumentUnderstanding,
        retrieval_plan: RetrievalPlan,
        rag_client: Any,
        errors: Optional[List[str]] = None,
    ) -> Dict[str, List[Evidence]]:
        """Her iddia için, iddia cümlesinin kendisiyle aranan aday kanıtlar (claim_id -> sıralı liste).

        Belge düzeyindeki sorgular (başlık, konu anahtar kelimeleri) havuzu belgenin genel konusuna
        göre doldurur; bir iddianın dayanağı o havuzda olmayabilir. Adaylar LocalRAG.claim_candidates
        ile (BM25 + vektör RRF, hukuki yeniden sıralama yok) alınır; istemci desteklemiyorsa boş döner.
        Kapsam ve uygunluk politikası retrieve_evidence ile aynıdır."""
        if getattr(rag_client, "supports_claim_candidates", False) is not True:
            return {}
        scope = {canonical_source_id(s) for s in (retrieval_plan.source_scope or [])}
        policy = EligibilityPolicy.from_filters(retrieval_plan.filters)
        # Olay tarihi (M7.1) biliniyorsa dayanak o tarihte yürürlükte olan metinden aranır (M7.4).
        dated = {"as_of": doc.event_date} if doc.event_date and getattr(rag_client, "supports_event_date", False) is True \
            else {}
        out: Dict[str, List[Evidence]] = {}
        for claim in self.claims_for_matching(doc):
            try:
                results = rag_client.claim_candidates(claim.statement, CLAIM_CANDIDATES, eligibility=policy, **dated)
            except Exception as exc:
                if errors is not None:
                    errors.append(f"{claim.statement[:60]}: {type(exc).__name__}: {exc}")
                continue
            out[claim.claim_id] = self.merge_evidence(
                [self.chunk_to_evidence(r) for r in results if self._admits(r, scope, policy)]
            )
        return out

    @classmethod
    def merge_evidence(cls, *groups: List[Evidence]) -> List[Evidence]:
        """Kanıt listelerini sırayla birleştirir; aynı parçanın sonraki kopyaları alınmaz."""
        seen: Set[Tuple[Any, ...]] = set()
        merged: List[Evidence] = []
        for group in groups:
            for ev in group:
                key = cls._result_key(ev.metadata, ev.text_span)
                if key not in seen:
                    seen.add(key)
                    merged.append(ev)
        return merged

    @staticmethod
    def claims_for_matching(doc: DocumentUnderstanding) -> List[DocumentClaim]:
        """Eşleştirilecek iddialar. Şikâyet kalıbı taşımayan belgeler (ör. iç denetim notu) için
        iddia, belgenin tespit cümlelerinden kurulur; içeriksiz sentetik bir cümle eşleşme üretmemeli."""
        if doc.claims:
            return list(doc.claims)
        statement = DocumentUnderstandingService.observation_statement(doc)
        if not statement:
            return []
        return [
            DocumentClaim(
                claim_id="claim_general",
                statement=statement,
                subject=doc.subjects[0] if doc.subjects else "genel_uyum",
                involved_entities=[e.text for e in doc.entities[:2]],
            )
        ]

    def analyze(
        self,
        doc: DocumentUnderstanding,
        classification: DocumentClassification,
        analysis_plan: AnalysisPlan,
        retrieval_plan: RetrievalPlan,
        rag_client: Optional[Any] = None,
        retrieved_evidence: Optional[List[Evidence]] = None,
        claim_candidates: Optional[Dict[str, List[Evidence]]] = None,
    ) -> AnalysisResult:
        """Executes full vertical slice analysis and returns a validated AnalysisResult.

        claim_candidates: iddia başına aday kanıtlar (retrieve_claim_candidates); verilmezse ve
        rag_client ve yeniden sıralayıcı varsa burada alınır. Adaylar havuza eklenir; doğrulama ikisini
        birlikte görür."""
        warnings: List[str] = []

        # 1. Acquire evidence
        if retrieved_evidence is not None:
            evidence_pool = list(retrieved_evidence)
        elif rag_client is not None:
            retrieval_errors: List[str] = []
            evidence_pool = self.retrieve_evidence(retrieval_plan, rag_client, errors=retrieval_errors)
            warnings.extend(retrieval_error_warnings(retrieval_plan, retrieval_errors))
            # Geniş aday havuzu yalnızca yeniden sıralayıcıyla (bkz. AnalysisPresenter.execute_analysis).
            if claim_candidates is None and self.reranker is not None:
                claim_candidates = self.retrieve_claim_candidates(doc, retrieval_plan, rag_client)
        else:
            evidence_pool = []
        claim_candidates = claim_candidates or {}
        evidence_pool = self.merge_evidence(evidence_pool, *claim_candidates.values())

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
            doc, analysis_plan, evidence_pool, claim_candidates
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
        claim_candidates: Optional[Dict[str, List[Evidence]]] = None,
    ) -> Tuple[List[Finding], List[DocumentClaim]]:
        findings: List[Finding] = []
        unsupported: List[DocumentClaim] = []

        for claim in self.claims_for_matching(doc):
            # Önce iddianın kendi cümlesiyle bulunan adaylar, sonra belge düzeyindeki havuz.
            claim_pool = self.merge_evidence((claim_candidates or {}).get(claim.claim_id, []), evidence_pool)
            ranked = self._gated_ranking(claim, claim_pool, doc)
            matched_ev, rerank_score = self._pick(ranked)
            verdict = None
            if self.claim_verifier is not None and self.reranker is None:
                # Sözcüksel mod: geçerli bir model kararı sözcüksel seçimin yerine geçer; "hiçbiri" kararı
                # eşleşmeyi kaldırır. Karar geçersizse (erişim/biçim/alıntı hatası) sözcüksel seçim kalır.
                candidates = self._verification_candidates(
                    claim, claim_pool, doc, getattr(self.claim_verifier, "max_candidates", 5), ranked=ranked
                )
                verdict = self.claim_verifier.choose(claim.statement, candidates) if candidates else None
                if verdict is not None:
                    matched_ev = candidates[verdict.evidence_index] if verdict.evidence_index is not None else None
            elif self.claim_verifier is not None and matched_ev is not None and self._low_confidence(rerank_score):
                # Yeniden sıralayıcı modu: model yalnızca düşük güvenli seçimi denetler ve yalnızca veto
                # edebilir (bkz. docs/architecture/claim_matching.md, "Dil modeli doğrulayıcısı").
                # Başka adayı seçerse ya da hiçbirini seçmezse bulgu çıkmaz; eşiğin altında kalan ya da
                # yüksek güvenli iddialara dokunmaz (ölçümde yeni bulgu eklemek yanlış pozitif üretti).
                candidates = self._verification_candidates(
                    claim, claim_pool, doc, getattr(self.claim_verifier, "max_candidates", 5), ranked=ranked
                )
                verdict = self.claim_verifier.choose(claim.statement, candidates)
                if verdict is not None and (verdict.evidence_index is None
                                            or candidates[verdict.evidence_index] is not matched_ev):
                    matched_ev, verdict = None, None
            if not matched_ev:
                unsupported.append(claim)
                continue

            # Build grounded Finding
            statement_terms, context_terms = self._claim_terms(claim, doc)
            verbatim_span = verdict.quote if verdict is not None else self._extract_verbatim_span(
                matched_ev.text_span, focus_terms=statement_terms | context_terms
            )
            citation = Citation(
                source=str(matched_ev.metadata.get("source") or "BDDK / Resmi Gazete"),
                document_identity=matched_ev.document_identity,
                reference=f"{matched_ev.source_document} {matched_ev.chunk_reference}".strip(),
                text_span=verbatim_span,
                metadata={"file_hash": matched_ev.metadata.get("file_hash", "")},
            )
            confidence = None
            if rerank_score is not None:
                citation.metadata["rerank_score"] = round(rerank_score, 4)
                confident = getattr(self.reranker, "confident_score", None)
                if confident is not None:
                    confidence = "yüksek" if rerank_score >= confident else "düşük"

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
                    (LOW_CONFIDENCE_ACTION if confidence == "düşük" else "")
                    + f"Denetçi tarafından {institution} nezdindeki işlem kayıtları, müşteri onay/sözleşme "
                    f"örnekleri ve bildirim logları temin edilerek {matched_ev.chunk_reference} hükmüne "
                    f"uygunluğu doğrulanmalıdır."
                ),
                claim_reference_id=claim.claim_id,
                model_interpretation=(
                    f"Yerel dil modeli bu hükmü iddia edilen davranışa uygulanabilir buldu: {verdict.reason} "
                    "Nihai tespit yetkili denetçi incelemesine bağlıdır."
                    if verdict is not None else
                    "Mevzuat hükümleri ile belgedeki beyan arasında inceleme gerektiren olası bir "
                    "uyum riski bulunmaktadır. Nihai tespit yetkili denetçi incelemesine bağlıdır."
                ),
                risk_level=risk,
                match_confidence=confidence,
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
        - RAG mesafesi biliniyorsa MAX_RELEVANT_DISTANCE değerini aşmamalı; ancak tek cümlede
          STRONG_LEXICAL_STEMS farklı kök (en az STRONG_LEXICAL_DISTINCTIVE tanesi iddia
          cümlesinden ve ayırt edici) eşleşiyorsa mesafe engel değildir: kısa başlık parçaları
          ("4) Kişisel Verilerin Üçüncü Kişilere Aktarılması") anlamsal olarak uzak düşebilir.
        Kanıt başlığı tek başına yetmez, yalnızca sıralamada kullanılır. Adaylar en iyi cümlenin
        eşleşmesine, parçanın toplam eşleşmesine, başlık eşleşmesine ve mesafeye göre sıralanır.
        """
        ranked = self._rank_evidence_for_claim(claim, evidence_pool, doc)
        return ranked[0] if ranked else None

    def _select_evidence(
        self,
        claim: DocumentClaim,
        candidates: List[Evidence],
        doc: Optional[DocumentUnderstanding] = None,
    ) -> Tuple[Optional[Evidence], Optional[float]]:
        """(seçilen kanıt, yeniden sıralayıcı puanı).

        Sözcüksel ölçüt (_rank_evidence_for_claim) bir KAPIDIR: iddiayla aynı cümlede ayırt edici
        terim paylaşmayan kanıt bulgu olamaz. Yeniden sıralayıcı (cross-encoder) varsa kapıyı geçenler
        arasından iddia cümlesiyle birlikte okunduğunda en ilgili olan seçilir; puanı modelin eşiğinin
        (reranker.min_score) altındaysa bulgu çıkmaz. Yeniden sıralayıcı yoksa sözcüksel sıralamanın ilki seçilir."""
        return self._pick(self._gated_ranking(claim, candidates, doc))

    def _low_confidence(self, score: Optional[float]) -> bool:
        """Seçimin puanı modelin güven bandının altında mı; bandı olmayan modelde her seçim düşük sayılır."""
        confident = getattr(self.reranker, "confident_score", None)
        return score is not None and (confident is None or score < confident)

    def _pick(self, ranked: List[Tuple[Evidence, Optional[float]]]) -> Tuple[Optional[Evidence], Optional[float]]:
        """Sıralamanın ilki; yeniden sıralayıcı puanı modelin eşiğinin altındaysa hiçbiri."""
        if not ranked:
            return None, None
        best, score = ranked[0]
        min_score = getattr(self.reranker, "min_score", None)
        if score is not None and min_score is not None and score < min_score:
            return None, score
        return best, score

    def _gated_ranking(
        self,
        claim: DocumentClaim,
        candidates: List[Evidence],
        doc: Optional[DocumentUnderstanding] = None,
    ) -> List[Tuple[Evidence, Optional[float]]]:
        """Sözcüksel kapıyı geçen kanıtlar, en iyiden kötüye, (kanıt, yeniden sıralayıcı puanı).
        Seçim (_select_evidence) ve dil modeli doğrulayıcısının adayları aynı sıralamayı kullanır."""
        accepted = self._rank_evidence_for_claim(claim, candidates, doc)
        if self.reranker is None or not accepted:
            return [(ev, None) for ev in accepted]
        # Aday sırası (iddianın kendi adayları önce) korunarak kapıyı geçenlerin ilk N'i puanlanır.
        passed = {id(ev) for ev in accepted}
        shortlist = [ev for ev in candidates if id(ev) in passed][:RERANK_MAX_CANDIDATES]
        scores = self.reranker.score(claim.statement, [ev.text_span for ev in shortlist])
        order = sorted(range(len(shortlist)), key=lambda i: (-scores[i], i))
        return [(shortlist[i], scores[i]) for i in order]

    def _rank_evidence_for_claim(
        self,
        claim: DocumentClaim,
        evidence_pool: List[Evidence],
        doc: Optional[DocumentUnderstanding] = None,
    ) -> List[Evidence]:
        """Sözcüksel kabul ölçütünü geçen kanıtlar, en iyiden kötüye (bkz. _find_best_evidence_for_claim)."""
        statement_terms, context_terms = self._claim_terms(claim, doc)
        all_terms = statement_terms | context_terms
        bigrams = self._claim_bigrams(claim, doc)

        accepted: List[Tuple[Tuple[float, ...], int, Evidence]] = []

        for position, ev in enumerate(evidence_pool):
            distance = ev.metadata.get("retrieval_distance")
            too_far = distance is not None and float(distance) > MAX_RELEVANT_DISTANCE
            best_unit_stems = 0
            strong_lexical = False
            for unit in _evidence_units(ev.text_span):
                unit_matched = matched_terms(all_terms, content_terms(unit))
                unit_stems = len(distinct_stems(unit_matched))
                distinctive = distinct_stems(t for t in unit_matched & statement_terms if not self.is_weak_term(t))
                # İddiadaki bitişik kelime çifti ("iç denetim sistemi") birimde de bitişik geçiyorsa
                # ayırt edicidir: kelimeleri tek tek korpusun her yerinde geçse bile birlikte bir
                # kavramı adlandırırlar. Her eşdizim sıralamada ayrıca bir eşleşme sayılır.
                phrases = matched_bigrams(bigrams, unit) if bigrams and unit_stems >= MIN_MATCHED_TERMS else []
                if (
                    unit_stems >= MIN_MATCHED_TERMS
                    and len(distinct_stems(unit_matched & statement_terms)) >= MIN_CLAIM_STATEMENT_TERMS
                    and (distinctive or phrases)
                ):
                    best_unit_stems = max(best_unit_stems, unit_stems + len(phrases))
                    strong_lexical = strong_lexical or (
                        unit_stems >= STRONG_LEXICAL_STEMS
                        and len(distinctive) + len(phrases) >= STRONG_LEXICAL_DISTINCTIVE
                    )
            if not best_unit_stems or (too_far and not strong_lexical):
                continue
            text_matched = matched_terms(all_terms, content_terms(ev.text_span))
            title_only = {
                t for t in matched_terms(all_terms, content_terms(ev.source_document))
                if not matches_any(t, text_matched)
            }
            rank = (
                float(best_unit_stems),
                # Geçici madde yürürlük anındaki geçişi düzenler (ör. eski kart borçlarının
                # yapılandırılması); eşit eşleşmede güncel olaya asıl madde uygulanır.
                0.0 if _is_transitional(ev.metadata) else 1.0,
                float(len(distinct_stems(text_matched))),
                float(len(distinct_stems(title_only))),
                -float(distance) if distance is not None else 0.0,
            )
            accepted.append((rank, position, ev))

        # Eşit sıralamada havuzdaki ilk kanıt kazanır (önceki davranış).
        accepted.sort(key=lambda item: (tuple(-x for x in item[0]), item[1]))
        return [ev for _, _, ev in accepted]

    def _verification_candidates(
        self,
        claim: DocumentClaim,
        evidence_pool: List[Evidence],
        doc: Optional[DocumentUnderstanding],
        limit: int,
        ranked: Optional[List[Tuple[Evidence, Optional[float]]]] = None,
    ) -> List[Evidence]:
        """Dil modeline gösterilecek adaylar: kapıyı geçenler (seçimle aynı sıralama; yeniden sıralayıcı
        varsa onun sırası), ardından retrieval yakınlığına göre diğerleri (terim paylaşmayan doğru
        hüküm de değerlendirilebilsin)."""
        if ranked is None:
            ranked = self._gated_ranking(claim, evidence_pool, doc)
        lexical = [ev for ev, _ in ranked]
        rest = sorted(
            (ev for ev in evidence_pool if not any(ev is chosen for chosen in lexical)),
            key=lambda ev: float(ev.metadata.get("retrieval_distance") if ev.metadata.get("retrieval_distance") is not None else 99.0),
        )
        return (lexical[:2] + rest + lexical[2:])[:limit]

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

    @staticmethod
    def _claim_bigrams(claim: DocumentClaim, doc: Optional[DocumentUnderstanding]) -> List[Tuple[str, str]]:
        """İddia cümlesinin eşdizimleri; kurum adı içerenler ("ziraat bankası") alınmaz."""
        excluded: Set[str] = set()
        if doc:
            for entity in doc.entities:
                if entity.entity_type == "institution":
                    excluded |= set(tokenize(entity.text, min_length=2))
        return [bg for bg in statement_bigrams(claim.statement) if not (set(bg) & excluded)]

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
