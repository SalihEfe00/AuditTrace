# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Analysis Intelligence Core
Document Understanding Service.

Stateless service responsible for ingesting raw operational documents
(complaints, correspondence, etc.), normalizing text, extracting entities,
dates, subjects, claims, and events into a structured DocumentUnderstanding contract.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

from mufettis.analysis.models import (
    DocumentClaim,
    DocumentEntity,
    DocumentEvent,
    DocumentType,
    DocumentUnderstanding,
)
from mufettis.analysis.turkish_text import contains_phrase, content_terms, turkish_lower
from mufettis.extraction.text_repair import (
    normalize_extracted_text,
    repair_mojibake,
)


# İç yazışmalarda denetim tespitini taşıyan cümle işaretleri (iddia çıkarılamadığında).
_OBSERVATION_MARKERS = re.compile(
    r"tespit\s+edil|belirlen|aşılarak|aşıldığı|ihlal|yenilenmedi|gecikme|gecikti|bildirilmedi|kesinti",
)


# Olgu bildiren yüklem: Türkçede yüklem cümle sonundadır. Geçmiş zaman (-dı, -tı), öğrenilen geçmiş /
# kesinlik (-mış, -mıştır) ve 3. kişi şimdiki zaman (-ıyor, -maktadır; olumsuzları dahil) bir olay
# veya durum bildirir: "aktarmıştır", "sunulmamıştır", "ilgilenmedi", "yazmıyor".
_FACT_PREDICATE_RE = re.compile(
    r"(?:m[ıiuü]ş(?:t[ıiuü]r|lar(?:d[ıi]r)?)?|[dt][ıiuü](?:lar)?|[ıiuü]?yor(?:lar)?|m[ae]kt[ae]d[ıi]r)$"
)
# Birinci kişi talep/dilek yüklemleri olgu bildirmez ("talep ediyorum", "rica ederim").
_REQUEST_PREDICATE_RE = re.compile(r"(?:[ıiuü]?yorum|[ae]rim|[ıiuü]r[ıiuü]m|m[ae]kt[ae]y[ıi]m)$")
MIN_CLAIM_CONTENT_TERMS = 2


def states_a_fact(sentence: str) -> bool:
    """Cümlenin yüklemi bir olay/durum bildiriyor mu? (Marker listesinden bağımsız genel kural.)"""
    words = re.findall(r"[0-9a-zçğıöşüâîû]+", turkish_lower(sentence))
    if len(words) < 3:
        return False
    predicate = words[-1]
    if _REQUEST_PREDICATE_RE.search(predicate):
        return False
    return bool(_FACT_PREDICATE_RE.search(predicate)) and len(content_terms(sentence)) >= MIN_CLAIM_CONTENT_TERMS


class DocumentUnderstandingService:
    """Stateless service for analyzing and extracting structure from operational text."""

    DATE_PATTERNS = [
        r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b",
        r"\b(\d{4}[./-]\d{1,2}[./-]\d{1,2})\b",
        r"\b(\d{1,2}\s+(?:Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s+\d{4})\b",
    ]

    AMOUNT_PATTERN = re.compile(
        r"(?<!\w)(\d{1,3}(?:\.\d{3})*|\d+)(?:,\d{1,2})?\s*(?:TL|TRY|USD|EUR|\$|€|₺|Türk\s*Lirası)",
        re.IGNORECASE,
    )

    IBAN_PATTERN = re.compile(r"\bTR\d{2}\s*(?:\d{4}\s*){5}\d{2}\b", re.IGNORECASE)

    INSTITUTION_KEYWORDS = [
        "BDDK", "TCMB", "MASAK", "SPK", "TBB",
        "Ziraat Bankası", "Halkbank", "VakıfBank", "İş Bankası", "Garanti BBVA",
        "Yapı Kredi Bankası", "Yapı Kredi", "Akbank", "QNB Finansbank", "DenizBank", "TEB",
        "Kuveyt Türk", "Türkiye Finans", "Albaraka Türk", "Vakıf Katılım", "Ziraat Katılım",
    ]

    SUBJECT_KEYWORDS = {
        "ücret_ve_komisyon": ["ücret", "komisyon", "masraf", "hesap işletim", "kart aidatı", "yıllık üyelik"],
        "kredi_ve_faiz": ["kredi", "faiz", "anapara", "taksit", "erken kapama", "yapılandırma", "ipotek"],
        "yetkisiz_islem": ["yetkisiz", "bilgim dışında", "onayım olmadan", "dolandırıcılık", "şüpheli işlem", "kartımdan çekilen"],
        "mevduat_ve_hesap": ["mevduat", "vadesiz hesap", "vadeli hesap", "hesap kapatma", "bloke"],
        "odeme_ve_transfer": ["eft", "havale", "fast", "para transferi", "swift"],
        "gecikme_ve_takip": ["yasal takip", "icra", "gecikme", "ihtarname", "sicil", "kredi notu"],
    }

    CLAIM_MARKERS = [
        r"kesildi", r"kesilmiş", r"tahsil\s*edildi", r"tahsil\s*et(?:ti|miştir)", r"iade\s*edilmedi", r"onayım\s*olmadan",
        r"bilgim\s*dışında", r"mağdur\s*edildim", r"haksız\s*(?:yere|olarak)?",
        r"talep\s*ediyorum", r"şikayetçiyim", r"zarara\s*uğratıldım",
        r"bilgi\s*verilmedi", r"teslim\s*edilmedi", r"cevap\s*verilmedi",
        r"kesintisi", r"(?:hukuka|yasaya|mevzuata)\s*aykırı", r"usulsüz",
    ]

    def analyze(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DocumentUnderstanding:
        """Processes raw text and metadata into a validated DocumentUnderstanding object."""
        if not text or not text.strip():
            raise ValueError("Input text cannot be empty or whitespace.")

        meta = dict(metadata or {})
        cleaned = repair_mojibake(text)
        normalized = normalize_extracted_text(cleaned)

        title = self._detect_title(normalized, meta)
        date = self._detect_date(normalized, meta)
        entities = self._extract_entities(normalized)
        subjects = self._extract_subjects(normalized)
        claims = self._extract_claims(normalized, subjects, entities)
        events = self._extract_events(normalized, entities)

        return DocumentUnderstanding(
            text=text,
            normalized_text=normalized,
            title=title,
            document_type=meta.get("document_type", DocumentType.UNKNOWN.value),
            source=meta.get("source", meta.get("source_file", "")),
            date=date,
            entities=entities,
            subjects=subjects,
            claims=claims,
            events=events,
            metadata=meta,
        )

    def _detect_title(self, text: str, meta: Dict[str, Any]) -> str:
        if meta.get("title"):
            return str(meta["title"]).strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        # Check for explicit 'Konu:' in the header lines
        for line in lines[:8]:
            m = re.match(r"^konu\s*:\s*(.+)$", line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        if lines:
            first_line = lines[0]
            if len(first_line) <= 120:
                return first_line
        return "Belge Metni"

    def _detect_date(self, text: str, meta: Dict[str, Any]) -> Optional[str]:
        if meta.get("date"):
            return str(meta["date"]).strip()
        for pattern in self.DATE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_entities(self, text: str) -> List[DocumentEntity]:
        entities: List[DocumentEntity] = []
        seen = set()

        # 1. Institutions
        for inst in self.INSTITUTION_KEYWORDS:
            if re.search(rf"\b{re.escape(inst)}\b", text, re.IGNORECASE):
                key = ("institution", inst.lower())
                if key not in seen:
                    seen.add(key)
                    entities.append(
                        DocumentEntity(
                            entity_type="institution",
                            text=inst,
                            normalized_value=inst,
                        )
                    )

        # 2. Monetary amounts
        for match in self.AMOUNT_PATTERN.finditer(text):
            val_str = match.group(0).strip()
            key = ("amount", val_str.lower())
            if key not in seen:
                seen.add(key)
                entities.append(
                    DocumentEntity(
                        entity_type="amount",
                        text=val_str,
                        normalized_value=val_str,
                    )
                )

        # 3. IBAN
        for match in self.IBAN_PATTERN.finditer(text):
            iban = match.group(0).strip()
            key = ("iban", iban.replace(" ", "").upper())
            if key not in seen:
                seen.add(key)
                entities.append(
                    DocumentEntity(
                        entity_type="iban",
                        text=iban,
                        normalized_value=iban.replace(" ", "").upper(),
                    )
                )

        # 4. Units / Departments
        unit_pattern = re.compile(
            r"\b([A-ZÇĞİÖŞÜa-zçğıöşü\s]+(?:Şubesi|Müdürlüğü|Başkanlığı|Servisi))\b"
        )
        for match in unit_pattern.finditer(text):
            unit = match.group(1).strip()
            if len(unit) <= 60:
                key = ("unit", unit.lower())
                if key not in seen:
                    seen.add(key)
                    entities.append(
                        DocumentEntity(
                            entity_type="unit",
                            text=unit,
                            normalized_value=unit,
                        )
                    )

        return entities

    @staticmethod
    def observation_statement(doc: DocumentUnderstanding, max_chars: int = 400) -> str:
        """İddia çıkarılamayan belgede tespit cümlelerini (yoksa başlığı) iddia metni olarak döndürür."""
        text = doc.normalized_text or doc.text or ""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) >= 15]
        observations = [s for s in sentences if _OBSERVATION_MARKERS.search(turkish_lower(s))]
        if observations:
            return " ".join(observations)[:max_chars]
        if doc.title and doc.title != "Belge Metni":
            return doc.title
        return ""

    def _extract_subjects(self, text: str) -> List[str]:
        """Metindeki konu kategorileri; Türkçe ekler tolere edilir (kredi -> kredisi)."""
        return [
            category
            for category, keywords in self.SUBJECT_KEYWORDS.items()
            if any(contains_phrase(text, kw) for kw in keywords)
        ]

    def _claim_subject(self, sentence: str, doc_subjects: List[str]) -> str:
        """İddianın kendi cümlesindeki en belirgin konu; yoksa belgenin ilk konusu.

        Eşleşen anahtar kelime sayısı eşitse daha uzun (daha özgül) ifade kazanır:
        "kredi kartımdan bilgim dışında" -> "bilgim dışında" (yetkisiz_islem) > "kredi".
        """
        best, best_rank = "", (0, 0)
        for category in doc_subjects:
            hits = [kw for kw in self.SUBJECT_KEYWORDS.get(category, []) if contains_phrase(sentence, kw)]
            rank = (len(hits), max((len(kw) for kw in hits), default=0))
            if rank > best_rank:
                best, best_rank = category, rank
        if best:
            return best
        return doc_subjects[0] if doc_subjects else "genel_sikayet"

    def _extract_claims(
        self,
        text: str,
        subjects: List[str],
        entities: List[DocumentEntity],
    ) -> List[DocumentClaim]:
        claims: List[DocumentClaim] = []
        sentences = re.split(r"(?<=[.!?])\s+", text)
        claim_idx = 1
        seen_statements = set()

        marker_re = re.compile(r"|".join(self.CLAIM_MARKERS), re.IGNORECASE)

        for sentence in sentences:
            sentence_clean = sentence.strip()
            if len(sentence_clean) < 15 or len(sentence_clean) > 500:
                continue

            # Marker listesi bilinen kalıpları yakalar; olgu bildiren yüklem kuralı listede olmayan
            # iddiaları ("… üçüncü kişilere aktarmıştır", "Sözleşmede böyle bir ücret yazmıyor").
            if marker_re.search(sentence_clean) or states_a_fact(sentence_clean):
                stmt_key = turkish_lower(sentence_clean)
                if stmt_key not in seen_statements:
                    seen_statements.add(stmt_key)
                    involved = [
                        e.text for e in entities if turkish_lower(e.text) in stmt_key
                    ]
                    subj = self._claim_subject(sentence_clean, subjects)
                    claims.append(
                        DocumentClaim(
                            claim_id=f"claim_{claim_idx}",
                            statement=sentence_clean,
                            subject=subj,
                            involved_entities=involved,
                        )
                    )
                    claim_idx += 1

        return claims

    def _extract_events(
        self,
        text: str,
        entities: List[DocumentEntity],
    ) -> List[DocumentEvent]:
        events: List[DocumentEvent] = []
        sentences = re.split(r"(?<=[.!?])\s+", text)
        event_idx = 1

        for sentence in sentences:
            sentence_clean = sentence.strip()
            if len(sentence_clean) < 15:
                continue

            for pattern in self.DATE_PATTERNS:
                m = re.search(pattern, sentence_clean, re.IGNORECASE)
                if m:
                    date_val = m.group(1).strip()
                    involved = [
                        e.text for e in entities if turkish_lower(e.text) in turkish_lower(sentence_clean)
                    ]
                    events.append(
                        DocumentEvent(
                            event_id=f"event_{event_idx}",
                            description=sentence_clean,
                            date=date_val,
                            involved_entities=involved,
                        )
                    )
                    event_idx += 1
                    break

        return events
