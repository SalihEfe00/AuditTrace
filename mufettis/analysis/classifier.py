# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Analysis Intelligence Core
Document Classifier Service.

Determines operational document types (complaint, internal_correspondence, unknown)
based on deterministic signal analysis with structured, explainable outputs.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from mufettis.analysis.turkish_text import turkish_lower
from mufettis.analysis.models import (
    DocumentClassification,
    DocumentType,
    DocumentUnderstanding,
)


# Birinci tekil kişi anlatımı (tüketici sesi). Yalnızca belirsiz olmayan işaretler sayılır: zamirler,
# 1. tekil geçmiş zaman ("bunaldım", "gittim"), şimdiki zaman ("istiyorum") ve gelecek zaman
# ("yapacağım"). İyelik eki ("kartım") sayılmaz: kökü -m ile biten adlarla ("sistem", "denetim")
# karışır. Resmî iç yazışma bu kişiyi kullanmaz; kapanışı ("arz ederim") geniş zamandır.
_FIRST_PERSON_PRONOUN_RE = re.compile(r"\b(?:ben|bana|beni|benim|bende|benden)\b")
# Geçmiş zaman eki -dım; sert ünsüzden sonra -tım ("gittim", "baktım"). "kartım" / "denetim" gibi
# yumuşak ünsüz veya ünlüden sonraki -tım iyelik ekidir ve sayılmaz.
_FIRST_PERSON_VERB_RE = re.compile(
    r"\b[a-zçğıöşü]+(?:[a-zçğıöşü]d[ıiuü]m|[çfhkpsşt]t[ıiuü]m|[ıiuü]?yorum|[ae]c[ae][ğk][ıi]m|m[ıiuü]şt[ıiuü]m)\b"
)
# -dım/-tım ile biten sık adlar fiil değildir.
_NOT_FIRST_PERSON_VERBS = frozenset({"yardım", "adım", "kadim"})
MIN_FIRST_PERSON_MARKERS = 1


def first_person_markers(lower_text: str) -> List[str]:
    found = _FIRST_PERSON_PRONOUN_RE.findall(lower_text) + [
        w for w in _FIRST_PERSON_VERB_RE.findall(lower_text) if w not in _NOT_FIRST_PERSON_VERBS
    ]
    return sorted(set(found))


class DocumentClassifierService:
    """Classifies operational documents into complaint, internal_correspondence, or unknown."""

    COMPLAINT_KEYWORDS = [
        "şikayet", "sikayet", "mağdur", "magdur", "haksız", "haksiz",
        "talep ediyorum", "iade edilmesini", "iadesini", "hesabımdan kesilen",
        "bilgim dışında", "bilgim disinda", "onayım olmadan", "onayim olmadan",
        "müşterisi olduğum", "musterisi oldugum", "tüketici", "kartımdan",
        "itiraz ediyorum", "zararımın karşılanması", "şikayetçiyim",
    ]

    CORRESPONDENCE_HEADERS = [
        r"^(?:sayı|sayi)\s*:",
        r"^konu\s*:",
        r"^(?:ilgi|ilgili)\s*:",
        r"^dağıtım\s*:",
        r"^kime\s*:",
        r"^kimden\s*:",
    ]

    CORRESPONDENCE_ORGANIZATIONAL = [
        r"(?:başkanlığına|baskanligina)\b",
        r"(?:müdürlüğüne|mudurlugune)\b",
        r"(?:genel\s+müdürlük|genel\s+mudurluk)\b",
        r"(?:teftiş\s+kurulu|teftis\s+kurulu)\b",
        r"(?:iç\s+denetim|ic\s+denetim)\b",
        r"(?:uyum\s+birimi|uyum\s+departmanı)\b",
        r"(?:operasyon\s+merkezi)\b",
    ]

    CORRESPONDENCE_CLOSINGS = [
        r"arz\s+ederim",
        r"rica\s+ederim",
        r"arz\s+ve\s+rica\s+ederim",
        r"bilgilerinize\s+(?:arz\s+olunur|sunulur)",
        r"gereğini\s+(?:arz|rica)\s+ederim",
    ]

    def classify(self, doc: DocumentUnderstanding) -> DocumentClassification:
        """Classifies the given DocumentUnderstanding into a structured DocumentClassification."""
        text = doc.normalized_text
        if not text or not text.strip():
            return DocumentClassification(
                document_type=DocumentType.UNKNOWN.value,
                confidence=0.0,
                reasons=["Input document has empty text."],
                signals=[],
            )

        complaint_score, complaint_signals, complaint_reasons = self._evaluate_complaint(text, doc)
        corr_score, corr_signals, corr_reasons = self._evaluate_correspondence(text)

        # Decision logic
        if complaint_score > corr_score and complaint_score >= 0.40:
            confidence = min(0.95, round(complaint_score, 2))
            return DocumentClassification(
                document_type=DocumentType.COMPLAINT.value,
                confidence=confidence,
                reasons=complaint_reasons,
                signals=complaint_signals,
            )
        elif corr_score > complaint_score and corr_score >= 0.40:
            confidence = min(0.95, round(corr_score, 2))
            return DocumentClassification(
                document_type=DocumentType.INTERNAL_CORRESPONDENCE.value,
                confidence=confidence,
                reasons=corr_reasons,
                signals=corr_signals,
            )
        else:
            # Low confidence or ambiguous signals
            signals = complaint_signals + corr_signals
            return DocumentClassification(
                document_type=DocumentType.UNKNOWN.value,
                confidence=0.30 if signals else 0.10,
                reasons=[
                    "Insufficient distinctive markers to confidently classify as complaint or internal correspondence."
                ],
                signals=signals,
            )

    def _evaluate_complaint(
        self,
        text: str,
        doc: DocumentUnderstanding,
    ) -> Tuple[float, List[str], List[str]]:
        signals: List[str] = []
        reasons: List[str] = []
        score = 0.0
        lower = turkish_lower(text)

        matched_kws = [kw for kw in self.COMPLAINT_KEYWORDS if kw in lower]
        if matched_kws:
            score += min(0.50, len(matched_kws) * 0.15)
            signals.extend([f"complaint_keyword:{kw}" for kw in matched_kws[:5]])
            reasons.append(f"Contains {len(matched_kws)} consumer complaint keywords: {', '.join(matched_kws[:4])}")

        # Grievance claims
        if doc.claims:
            score += min(0.35, len(doc.claims) * 0.15)
            signals.append(f"grievance_claims:{len(doc.claims)}")
            reasons.append(f"Identified {len(doc.claims)} specific grievance/claim statements")

        # Tüketici anlatımı: kişinin kendi hesabı/kartı/verisi hakkında birinci kişi anlatımı.
        voice = first_person_markers(lower)
        if len(voice) >= MIN_FIRST_PERSON_MARKERS:
            score += 0.25
            signals.append(f"first_person_voice:{len(voice)}")
            reasons.append(f"First-person consumer narrative: {', '.join(voice[:4])}")

        # Demand indicators
        if re.search(r"iadesini\s+talep|tutarın\s+hesabıma|mağduriyetimin\s+gideril", lower):
            score += 0.20
            signals.append("redress_demand_present")
            reasons.append("Customer explicit redress demand detected")

        return score, signals, reasons

    def _evaluate_correspondence(self, text: str) -> Tuple[float, List[str], List[str]]:
        signals: List[str] = []
        reasons: List[str] = []
        score = 0.0
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        # Headers in the first 10 lines
        matched_headers = []
        for line in lines[:10]:
            for pat in self.CORRESPONDENCE_HEADERS:
                if re.search(pat, line, re.IGNORECASE):
                    matched_headers.append(line[:40])
                    break
        if matched_headers:
            score += min(0.45, len(matched_headers) * 0.20)
            signals.extend([f"header:{h}" for h in matched_headers])
            reasons.append(f"Detected formal correspondence headers: {', '.join(matched_headers)}")

        # Organizational addressees
        matched_orgs = []
        for pat in self.CORRESPONDENCE_ORGANIZATIONAL:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                matched_orgs.append(m.group(0))
        if matched_orgs:
            score += min(0.30, len(matched_orgs) * 0.15)
            signals.extend([f"addressee:{o}" for o in matched_orgs[:3]])
            reasons.append(f"Addressed to internal unit: {', '.join(matched_orgs[:3])}")

        # Formal closing formulas
        matched_closings = []
        for pat in self.CORRESPONDENCE_CLOSINGS:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                matched_closings.append(m.group(0))
        if matched_closings:
            score += 0.30
            signals.extend([f"closing:{c}" for c in matched_closings[:2]])
            reasons.append(f"Formal bureaucratic closing formula: {', '.join(matched_closings[:2])}")

        return score, signals, reasons
