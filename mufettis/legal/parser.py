# -*- coding: utf-8 -*-
from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

from mufettis.rag.index import IngestIndex

from mufettis.config import (
    CHUNK_SCHEMA_VERSION,
    SOURCE_DEFINITIONS,
)
from mufettis.common.utils import canonical_source_id, normalize_title, source_profile
from mufettis.extraction.text_repair import (
    clean_evidence_text,
    extract_legal_identifier,
    normalize_extracted_text,
    normalize_legal_whitespace,
    repair_account_code_spacing,
    repair_extracted_spacing,
    repair_mojibake,
)

def detect_doc_type(
    title: str,
    text: str,
    source_id: str = "",
) -> str:
    """Belge türünü önce başlıktan, sonra içerikten; gerektiğinde kaynaktan çıkar."""
    title_text=repair_mojibake(str(title or "")).lower()
    body=repair_mojibake(str(text or "")[:6000]).lower()
    sid=str(source_id or "").lower()

    # Başlık en güvenilir sinyaldir; belge içinde geçen 'kanun' kelimesi
    # bir BDDK rehberini Kanun sanmasın.
    title_patterns=[
        ("Kanun", [r"\bkanun\b", r"\bsayılı kanun\b"]),
        ("Yönetmelik", [r"\byönetmelik\b", r"\byönetmeliği\b", r"\byönetmeliğin\b"]),
        ("Tebliğ", [r"\btebliğ\b", r"\btebliği\b"]),
        ("Genelge", [r"\bgenelge\b"]),
        ("Cumhurbaşkanı Kararı", [r"\bcumhurbaşkanı kararı\b", r"\bcumhurbaşkanlığı kararı\b"]),
        ("Kurul Kararı", [r"\bkurul kararı\b"]),
        ("Usul ve Esas", [r"\busul ve esas\b"]),
        ("Rehber", [r"\brehber\b"]),
        ("Kılavuz", [r"\bkılavuz\b"]),
    ]
    for name,pats in title_patterns:
        if any(re.search(p,title_text,re.I) for p in pats):
            return name

    # BDDK kaynaklarında başlık sadece sayıysa, gövde içinde geçen Kanun
    # kelimesi belge türünü Kanun yapmasın.
    if "bddk" in sid:
        for name,pats in [
            ("Genelge",[r"\bgenelge\b"]),
            ("Rehber",[r"\brehber\b"]),
            ("Kılavuz",[r"\bkılavuz\b"]),
            ("Usul ve Esas",[r"\busul ve esas\b"]),
            ("Yönetmelik",[r"\byönetmelik\b"]),
            ("Tebliğ",[r"\btebliğ\b"]),
        ]:
            if any(re.search(p,body,re.I) for p in pats):
                return name
        return "BDDK Belge"

    combined=f"{title_text}\n{body}"
    for name, needles in [
        ("Kanun",["kanun"]),
        ("Yönetmelik",["yönetmelik","yönetmeliği","yönetmeliğin"]),
        ("Tebliğ",["tebliğ","tebliği"]),
        ("Genelge",["genelge"]),
        ("Cumhurbaşkanı Kararı",["cumhurbaşkanı kararı","cumhurbaşkanlığı kararı"]),
        ("Kurul Kararı",["kurul kararı"]),
        ("Usul ve Esas",["usul ve esas"]),
        ("Rehber",["rehber"]),
        ("Kılavuz",["kılavuz"]),
        ("Karar",["karar"]),
    ]:
        if any(n in combined for n in needles):
            return name
    return "Diğer"
def extract_first(text: str, patterns: Iterable[str]) -> Optional[str]:
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I | re.M)
        if m:
            return m.group(1).strip()
    return None
def classify_legal_structure(
    title: str,
    text: str,
    document_type: str,
) -> Dict[str, Any]:
    """Belgenin yapısını tespit eder; MADDE dışı BDDK rehber/hesap planlarını da tanır."""
    cleaned=normalize_extracted_text(text)
    head=cleaned[:80000]
    norm=normalize_legal_whitespace(repair_extracted_spacing(head, title, document_type))
    title_norm=repair_mojibake(str(title or "")).lower()
    doc_norm=str(document_type or "").lower()

    line_article_count=len(re.findall(
        r"(?im)^\s*(?:(?:GEÇİCİ|EK)\s+)?MADDE\s*[-:]?\s*\d+[A-Za-z]?\b",
        norm,
    ))
    inline_article_count=len(re.findall(
        r"(?i)(?<![A-ZÇĞİÖŞÜa-zçğıöşü0-9])(?:GEÇİCİ|EK)?\s*MADDE\s*[-:]?\s*\d+[A-Za-z]?\b",
        norm,
    ))
    article_count=max(line_article_count, inline_article_count)

    paragraph_count=len(re.findall(
        r"(?m)^\s*\(?\d{1,3}\)?[.)-]\s+",
        norm,
    ))
    # Numaralı başlıklar: 1), 2. ve hiyerarşik 2.3.2 gibi yapıları birlikte tanır.
    section_count=len(re.findall(
        r"(?m)^\s*\d{1,3}(?:\.\d{1,3}){0,4}\s*(?:[-.)]|(?=\s))\s+[A-ZÇĞİÖŞÜ0-9]",
        norm,
    ))

    # PDF/HTML çıkarımı satır sonlarını kaybetmişse numaralı rehber maddelerini
    # inline olarak da say. Yalnızca rehber/kılavuz bağlamında kullanılır.
    guidance_context=bool(
        doc_norm in {"rehber", "kılavuz"}
        or "rehber" in title_norm
        or "kılavuz" in title_norm
        or "iyi uygulama rehberi" in norm.lower()
    )
    guidance_inline_count=0
    if guidance_context and article_count == 0:
        guidance_inline_count=len(re.findall(
            r"(?i)(?<![\dA-Za-zÇĞİÖŞÜçğıöşü])\d{1,3}\s*[-.)]\s+(?=[A-ZÇĞİÖŞÜ])",
            norm,
        ))
        section_count=max(section_count, guidance_inline_count)

    # 1043 tipi BDDK Tekdüzen Hesap Planı: hukuki MADDE değil, hesap kodu + başlık
    # hiyerarşisi kullanır. Başlık PDF çıkarımında kaybolabildiği için yalnızca
    # başlığa güvenmiyoruz; tekrarlayan hesap-kodu satırlarını da güçlü sinyal
    # olarak kullanıyoruz. Böylece mevcut "BDDK Belge" başlığı olan 1043 de yakalanır.
    accounting_phrase=bool(re.search(
        r"(?i)tek\s*d[üu]zen\s+hesap\s+plan[ıi]",
        title_norm + "\n" + norm.lower(),
    ))
    account_code_count=len(re.findall(
        r"(?m)^\s*(\d{3,6})\s{1,}([A-ZÇĞİÖŞÜa-zçğıöşü0-9][^\n]{2,160})$",
        norm,
    ))
    inline_account_count=len(re.findall(
        r"(?<!\d)\d{3,6}(?=\s+[A-ZÇĞİÖŞÜa-zçğıöşü0-9])",
        norm,
    ))
    account_code_count=max(account_code_count, inline_account_count)
    # Hesap planlarında 010, 011, 012 gibi kök hesaplar tipiktir; tek bir
    # tesadüfi sayı satırının belgeyi yanlış sınıflandırmasını engelle.
    root_account_count=len(set(re.findall(
        r"(?m)^\s*(\d{3})\d{0,3}\s{1,}[A-ZÇĞİÖŞÜa-zçğıöşü0-9]",
        norm,
    )))
    inline_root_account_count=len(set(re.findall(
        r"(?<!\d)(\d{3})\d{0,3}(?=\s+[A-ZÇĞİÖŞÜa-zçğıöşü0-9])",
        norm,
    )))
    root_account_count=max(root_account_count, inline_root_account_count)
    accounting_plan_context=bool(
        accounting_phrase
        or (doc_norm == "bddk belge" and account_code_count >= 5)
    )
    if accounting_plan_context and account_code_count < 2:
        account_code_count=len(re.findall(
            r"(?i)(?<!\d)\d{3,6}\s+(?=[A-ZÇĞİÖŞÜa-zçğıöşü])",
            norm,
        ))

    meaningful_chars=len(re.sub(r"\s+","",norm))

    # KRİTİK AYRIM (v0.13.34): belge indekslenebilirliği ile hukuki yapı
    # çözümlemesi aynı kapı değildir. Bir belge MADDE/section/hesap kodu
    # şemasına uymasa bile gerçek metin içeriyorsa güvenli bir "document"
    # chunk şemasıyla RAG'a alınabilir. Hukuki hiyerarşi yalnızca ek metadata
    # kalitesi ve hukuk-odaklı sorgu kabiliyeti için belirlenir.
    if article_count>0:
        structure_type="article"
        parse_status="parsed"
        confidence=0.99
        legal_structure_resolved=True
        legal_structure_reason=(
            f"Hukuki madde yapısı tespit edildi ({article_count} MADDE belirteci)."
        )
    elif accounting_plan_context and account_code_count>=2:
        structure_type="accounting_plan"
        parse_status="parsed"
        confidence=0.96
        legal_structure_resolved=True
        legal_structure_reason=(
            f"Hesap planı yapısı tespit edildi ({account_code_count} hesap kodu belirteci); "
            "kanıt hiyerarşisi hesap kodu ve başlık üzerinden kurulabilir."
        )
    elif guidance_context and section_count>=2:
        structure_type="section"
        parse_status="parsed"
        confidence=0.95
        legal_structure_resolved=True
        legal_structure_reason=(
            f"Numaralı rehber/bölüm yapısı tespit edildi ({section_count} numaralı bölüm belirteci)."
        )
    elif section_count>=2:
        structure_type="section"
        parse_status="parsed"
        confidence=0.93
        legal_structure_resolved=True
        legal_structure_reason=(
            f"Numaralı hukuki bölüm yapısı tespit edildi ({section_count} bölüm)."
        )
    elif meaningful_chars<250:
        structure_type="title_only"
        parse_status="failed"
        confidence=0.99
        legal_structure_resolved=False
        legal_structure_reason=(
            f"Belge metni çok kısa ({meaningful_chars} karakter); yalnızca başlık veya yetersiz içerik."
        )
    else:
        # Hukuki parser'ın başarısız olması artık belgeyi çöpe atmaz.
        # Bu belge genel document chunk'ları olarak indekslenebilir.
        structure_type="document"
        parse_status="unstructured"
        confidence=0.60
        legal_structure_resolved=False
        legal_structure_reason=(
            f"Özel hukuki yapı bulunamadı: {article_count} MADDE, "
            f"{section_count} numaralı bölüm tespit edildi. Belge genel document "
            "chunk şemasıyla indekslenebilir; hukuki hiyerarşi metadata'sı sınırlıdır."
        )

    document_indexable=bool(legal_structure_resolved or meaningful_chars>=250)
    if document_indexable:
        indexability_reason=(
            "Belge gerçek metin içeriyor; hukuki yapı çözümlenmiş olsun veya olmasın "
            "genel document chunk şemasıyla indekslenebilir."
        )
        if legal_structure_resolved:
            indexability_reason=legal_structure_reason
    else:
        indexability_reason=legal_structure_reason

    # evidence_indexable eski UI/API alanıdır. v0.13.34'te anlamı artık
    # "RAG'a güvenli biçimde alınabilir" olarak korunur; hukuki yapı kalitesi
    # ayrı legal_structure_resolved alanında tutulur.
    evidence_indexable=bool(document_indexable)
    evidence_indexable_reason=indexability_reason

    return {
        "structure_type":structure_type,
        "parse_status":parse_status,
        "parse_confidence":float(confidence),
        "legal_structure_resolved":bool(legal_structure_resolved),
        "legal_structure_reason":legal_structure_reason,
        "document_indexable":bool(document_indexable),
        "indexability_reason":indexability_reason,
        "article_count":article_count,
        "paragraph_marker_count":paragraph_count,
        "section_count":section_count,
        "account_code_count":account_code_count,
        "evidence_indexable":bool(evidence_indexable),
        "evidence_indexable_reason":evidence_indexable_reason,
        "text_char_count":meaningful_chars,
    }
# Belge düzeyi durum yalnızca başlık bölgesinden okunur. Metnin gövdesindeki madde notları
# ("(Mülga: 4/12/1985-3239/138 md.)"), değiştirici hükümler ("… maddesi yürürlükten
# kaldırılmıştır") ve BDDK site menüsü ("Mülga Düzenlemeler") belgenin kendisinin
# kaldırıldığını göstermez; önceden bunlar yürürlükteki kanunları aramadan dışlıyordu.
STATUS_HEADER_CHARS = 600
LAW_REFERENCE_HEADER_CHARS = 1500
# Kaynak sistemin verdiği durumlar metinden yeniden hesaplanarak ezilmez (yeniden parçalama
# önceden "superseded" belgeleri tekrar "active" yapıyordu).
PRESERVED_STATUSES = {"superseded", "quarantined"}
# Madde notları hep iki noktalıdır ("(Mülga: …)"); başlıktaki "(Mülga)" gerçek durum bilgisidir.
_STATUS_NOISE = re.compile(r"mülga\s+düzenlemeler|\(\s*mülga\s*:[^)]*\)|mülga\s*:", re.I)


def detect_document_status(title: str, head: str) -> str:
    # Gürültü kesmeden önce temizlenir; aksi halde sınırda kalan "Mülga Düz…" eşleşmezdi.
    zone = f"{title}\n{_STATUS_NOISE.sub(' ', head[:STATUS_HEADER_CHARS * 4])[:STATUS_HEADER_CHARS]}"
    zone = _STATUS_NOISE.sub(" ", zone)
    if re.search(r"\byürürlükten\s+(?:kaldırılmıştır|kalkmıştır)\b", zone, re.I):
        return "repealed"
    if re.search(r"\bmülga\b", zone, re.I):
        return "repealed_or_changed"
    return "active"


def extract_legal_metadata(
    title: str,
    text: str,
    base_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Belge kimliği + hukuki yapı + parse kalitesi metadata'sı."""
    head=normalize_legal_whitespace(text[:80000])
    source_id=str(base_meta.get("source_id","") or "")
    doc_type=detect_doc_type(title,head,source_id)
    combined=f"{title}\n{head[:40000]}"

    # Belgenin kendi beyanı ("Kanun Numarası : 6361") metindeki atıflardan ("765 sayılı
    # Kanun") önce gelir; aksi halde kanun, atıf yaptığı ilk kanunun numarasını alırdı.
    # E8: "N sayılı Kanun" atfı yalnızca kanunun kendi başlık bölgesinde kimlik sayılır;
    # BDDK yönetmelikleri gövdede "5411 sayılı Bankacılık Kanunu"na atıf yapar ve önceden
    # kanun 5411 gibi görünüp aynı anahtarla birbirinin "sürümü" sayılıyordu.
    law_number=extract_first(
        combined,
        [
            r"\bKanun\s+Numarası\s*[:№.-]?\s*(\d{2,8})\b",
            r"\bKanun\s+No(?:'su|su|)\s*[:№.-]?\s*(\d{2,8})\b",
        ],
    ) or extract_first(
        str(title),
        [r"\b(\d{2,8})\s+sayılı\s+Kanun\b"],
    ) or (
        extract_first(
            f"{title}\n{head[:LAW_REFERENCE_HEADER_CHARS]}",
            [r"\b(\d{2,8})\s+sayılı\s+Kanun\b"],
        ) if doc_type == "Kanun" else ""
    ) or ""

    number=extract_first(
        combined,
        [
            r"\b(?:Karar\s+Sayısı|Sayı)\s*[:№.-]?\s*([A-Z0-9./-]{2,40})",
            r"\b(\d{2,8})\s+sayılı\s+(?:Yönetmelik|Tebliğ|Kanun)\b",
        ],
    ) or ""

    article=extract_first(
        head,
        [
            r"\b(?:GEÇİCİ|EK)?\s*MADDE\s*[-:]?\s*(\d+[A-Za-z]?)",
            r"\bMADDE\s+(\d+[A-Za-z]?)",
        ],
    ) or ""

    official_date=extract_first(
        head,
        [
            r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4})\b",
            r"\b(\d{4}-\d{2}-\d{2})\b",
        ],
    ) or ""

    effective_date=extract_first(
        head,
        [
            r"[Yy]ürürlük\s*[Tt]arihi\s*[:.-]?\s*([0-9./-]{8,10})",
            r"[Yy]ürürlüğe\s*girer\s*[:.-]?\s*([0-9./-]{8,10})",
        ],
    ) or ""

    base_status=str(base_meta.get("status","") or "").strip().lower()
    status=base_status if base_status in PRESERVED_STATUSES else detect_document_status(title, head)

    normalized_title=re.sub(r"\s+"," ",str(title)).strip().lower()
    source_document_id=extract_first(
        str(base_meta.get("url", "")),
        [r"/(\d+)(?:/)?(?:\?.*)?$"],
    ) or ""
    # Sürüm eşleştirme anahtarı: kaynağın kalıcı belge kimliği (BDDK DokumanGetir/<id>),
    # sonra kanun numarası, sonra başlık. "Sayı: 6827" (Resmî Gazete sayısı) gibi numaralar
    # farklı belgelerde ortak olduğundan anahtar olarak kullanılmaz.
    document_key=str(source_document_id or law_number or normalized_title)
    structure=classify_legal_structure(title,head,doc_type)

    return {
        **base_meta,
        "document_type":doc_type,
        "document_number":number,
        "law_number":law_number,
        "article_hint":article,
        "official_date_hint":official_date,
        "effective_date":effective_date,
        "status":status,
        "title_normalized":normalized_title,
        "document_key":document_key,
        "source_document_id":source_document_id,
        "version_number":int(base_meta.get("version_number",1) or 1),
        **structure,
    }
def extract_query_document_reference(query: str) -> Optional[str]:
    """Sorgunun başındaki BDDK belge kimliğini ayırır. Kanun numarasıysa belge kimliği sayılmaz."""
    q = str(query or "").strip()
    if not q or extract_legal_identifier(q):
        return None
    m = re.match(r"^(\d{4,6})\b", q)
    return m.group(1) if m else None
def extract_query_account_code(query: str) -> Optional[str]:
    """Sorgudaki açık hesap kodunu yakalar; belge numarası tek başına hesap kodu sayılmaz."""
    q = str(query or "")
    doc_ref = extract_query_document_reference(q)
    law_ref = extract_legal_identifier(q)  # "193 sayılı Gelir Vergisi Kanunu" hesap 193 değildir
    for token in re.findall(r"(?<!\d)(\d{3,6})(?!\d)", q):
        if token in (doc_ref, law_ref):
            continue
        # 3 haneli kökler ve 4-6 haneli hesap kodları kabul edilir.
        if len(token) >= 3:
            return token
    return None
def _resolve_query_document_reference_from_collection(
    rag: "LocalRAG",
    query: str,
    document_ref: Optional[str],
    account_ref: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Sorgu başındaki 3 haneli kimliğin belge mi hesap mı olduğunu veri kümesindeki
    gerçek kimliklerle çözer. Böylece örneğin ``111 Bankacılık ...`` belge kimliği
    olarak, ``010 KASA`` ise (111 gibi bir source_document_id yoksa) hesap kodu
    olarak kalır. Parse katmanındaki genel regex semantiğini değiştirmez.
    """
    if document_ref or not account_ref:
        return document_ref, account_ref
    q = str(query or "").strip()
    m = re.match(r"^(\d{3})\b", q)
    if not m or m.group(1) != str(account_ref):
        return document_ref, account_ref

    candidate = m.group(1)
    try:
        rows = rag.collection.get(
            where={
                "$and": [
                    {"chunk_schema_version": CHUNK_SCHEMA_VERSION},
                    {"source_document_id": candidate},
                ]
            },
            include=["documents", "metadatas"],
        )
    except Exception:
        return document_ref, account_ref

    docs = rows.get("documents", []) or []
    metas = rows.get("metadatas", []) or []
    if any(
        str(meta.get("source_document_id", "")) == candidate
        and _chunk_is_retrievable(meta, doc)
        for doc, meta in zip(docs, metas)
    ):
        return candidate, None
    return document_ref, account_ref
def _query_terms(query: str) -> List[str]:
    """Türkçe sorgudan anlamlı terimleri çıkar."""
    stop = {
        "ve", "veya", "ile", "için", "olan", "olarak", "bir", "bu", "şu",
        "ilgili", "dair", "kapsamında", "kapsam", "hakkında", "nedir", "hangi",
        "the", "and", "for", "with", "what", "how",
    }
    raw = re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]{3,}", str(query).lower())
    terms: List[str] = []
    for term in raw:
        if term in stop:
            continue
        normalized = term
        for suffix in (
            "larının", "lerinin", "ları", "leri", "ların", "lerin",
            "ından", "inden", "ına", "ine", "ında", "inde",
            "dan", "den", "dır", "dir", "ler", "lar", "nin",
            "nın", "nun", "nün",
        ):
            if len(normalized) > len(suffix) + 3 and normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
                break
        if normalized not in terms:
            terms.append(normalized)
    return terms[:24]
def _lexical_overlap(terms: List[str], text_value: str) -> float:
    """Terimlerin verilen metinde bulunma oranını 0..1 döndürür."""
    haystack = re.sub(
        r"[^0-9a-zçğıöşü ]+",
        " ",
        str(text_value).lower(),
    )
    if not terms:
        return 0.0
    hits = sum(1 for term in terms if term in haystack)
    return hits / len(terms)
def _legal_rerank(
    results: List[Dict[str, Any]],
    query: str,
    limit: int = 5,
    resolved_refs: Optional[Tuple[Optional[str], Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """Semantic + lexical + resmi kaynak + açık mevzuat kimliği yeniden sıralaması.

    resolved_refs: (belge kimliği, hesap kodu) LocalRAG.query'nin koleksiyona bakarak çözdüğü
    değerler. Verilmezse sorgudan yeniden çıkarılır; ancak o zaman "934 bilgi sistemleri"
    gibi bir sorgu, koleksiyonda 934 belgesi olduğu halde yeniden hesap kodu sayılırdı.
    """
    terms = _query_terms(query)
    legal_id = extract_legal_identifier(query)
    if resolved_refs is not None:
        document_ref, account_ref = resolved_refs
    else:
        document_ref = extract_query_document_reference(query)
        account_ref = extract_query_account_code(query)
    scored = []

    for item in results:
        meta = item.get("metadata", {}) or {}
        doc = clean_evidence_text(item.get("document", ""), 8000)
        title = clean_evidence_text(meta.get("title", ""), 1500)
        source = clean_evidence_text(meta.get("source", ""), 800)
        doc_number = str(meta.get("document_number", "") or "")
        law_number = str(meta.get("law_number", "") or "")
        document_key = str(meta.get("document_key", "") or "")
        source_document_id = str(meta.get("source_document_id", "") or "")
        meta_account_code = str(meta.get("account_code", "") or "")

        identity_text = " ".join([
            title,
            source,
            doc_number,
            law_number,
            document_key,
            doc[:6000],
        ])

        status = str(meta.get("status", "")).lower()
        source_type = str(meta.get("source_type", ""))
        distance = float(item.get("distance") or 999.0)

        semantic_score = 1.0 / (1.0 + max(0.0, distance))
        title_overlap = _lexical_overlap(terms, title)
        doc_overlap = _lexical_overlap(terms, doc)
        lexical_score = min(
            1.0,
            0.75 * title_overlap + 0.25 * doc_overlap,
        )
        authority_score = min(
            1.0,
            float(meta.get("authority_rank", 0) or 0) / 100.0,
        )

        official_bonus = 0.12 if source_type == "official" else 0.0
        title_bonus = 0.12 if title_overlap >= 0.34 else 0.0
        source_bonus = 0.05 if _lexical_overlap(terms, source) > 0 else 0.0
        stale_penalty = (
            0.35
            if status in {
                "superseded", "mülga", "mulga",
                "repealed", "repealed_or_changed",
            }
            else 0.0
        )

        exact_identifier = False
        if legal_id:
            pattern = rf"(?<!\d){re.escape(legal_id)}(?!\d)"
            exact_identifier = bool(
                re.search(pattern, law_number)
                or re.search(pattern, document_key)
                or re.search(pattern, title)
            )

            # Belge metadata'sında bulunmuyorsa metnin kanun adı kısmına bak.
            if not exact_identifier:
                exact_identifier = bool(
                    re.search(
                        rf"(?<!\d){re.escape(legal_id)}\s+sayılı\s+(?:Kanun|Yönetmelik|Tebliğ)",
                        identity_text,
                        flags=re.I,
                    )
                )

        exact_bonus = 0.70 if exact_identifier else 0.0
        exact_miss_penalty = 0.55 if legal_id and not exact_identifier else 0.0

        exact_document_match = bool(
            document_ref and source_document_id == document_ref
        )
        exact_account_match = bool(
            account_ref and meta_account_code == account_ref
        )
        document_bonus = 1.80 if exact_document_match else 0.0
        document_miss_penalty = 1.20 if document_ref and not exact_document_match else 0.0
        account_bonus = 2.00 if exact_account_match else 0.0
        account_miss_penalty = 1.35 if account_ref and not exact_account_match else 0.0

        score = (
            0.40 * semantic_score
            + 0.22 * lexical_score
            + 0.10 * authority_score
            + official_bonus
            + title_bonus
            + source_bonus
            + exact_bonus
            + document_bonus
            + account_bonus
            - exact_miss_penalty
            - document_miss_penalty
            - account_miss_penalty
            - stale_penalty
        )

        enriched = dict(item)
        enriched["document"] = doc
        enriched["_rerank_score"] = round(score, 6)
        enriched["_semantic_score"] = round(semantic_score, 6)
        enriched["_lexical_score"] = round(lexical_score, 6)
        enriched["_authority_score"] = round(authority_score, 6)
        enriched["_exact_legal_id"] = legal_id or ""
        enriched["_exact_identifier_match"] = exact_identifier
        enriched["_exact_document_match"] = exact_document_match
        enriched["_exact_account_match"] = exact_account_match
        scored.append(enriched)

    scored.sort(key=lambda x: x["_rerank_score"], reverse=True)

    # Açık numaralı sorguda eşleşen belgeler, eşleşmeyenleri kesin olarak geçer.
    if legal_id:
        exact = [x for x in scored if x["_exact_identifier_match"]]
        nonexact = [x for x in scored if not x["_exact_identifier_match"]]
        if exact:
            scored = exact + nonexact
        # Exact sonuç sayısı k'dan azsa, mevcut aday havuzu sınırlıdır; UI bunu belirtir.

    selected = []
    seen = set()
    for item in scored:
        meta = item.get("metadata", {}) or {}
        key = (
            str(meta.get("source_id", "")),
            str(meta.get("file_hash") or meta.get("document_key") or meta.get("title") or ""),
            str(meta.get("chunk_no", "")),
            str(meta.get("article_no", "")),
            str(meta.get("paragraph_no", "")),
            str(meta.get("item_no", "")),
            str(meta.get("subitem_no", "")),
            str(meta.get("section_no", "")),
            str(meta.get("account_code", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for item in scored:
            if item not in selected:
                selected.append(item)
                if len(selected) >= limit:
                    break

    return selected[:limit]
def _metadata_bool(value: Any, default: bool = False) -> bool:
    """Chroma metadata bool değerlerini güvenli biçimde çözer.

    Chroma metadata değerleri çoğu sürümde string olarak geri döner;
    bool("False") Python'da True olduğu için doğrudan bool() kullanılamaz.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float, np.integer, np.floating)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "evet", "e", "on"}:
        return True
    if text in {"false", "0", "no", "n", "hayır", "hayir", "h", "off", ""}:
        return False
    return default
def _chunk_is_retrievable(meta: Dict[str, Any], document: Any = "") -> bool:
    """Gerçek Chroma chunk'ının retrieval'a girip giremeyeceğini belirler.

    v0.13.35'te document_indexable birincil indekslenebilirlik sinyalidir.
    Eski Chroma kayıtlarında bu metadata yanlış/eksik kalmışsa, mevcut gerçek
    chunk metni anlamlı olduğu sürece kayıt retrieval'dan dışlanmaz.
    Hukuki yapı çözülmemiş olması tek başına retrieval engeli değildir.
    """
    meta = meta or {}
    if str(meta.get("chunk_schema_version", "")) != str(CHUNK_SCHEMA_VERSION):
        return False
    status = str(meta.get("status", "")).strip().lower()
    if status in {"superseded", "mülga", "mulga", "repealed", "repealed_or_changed"}:
        return False

    if _metadata_bool(meta.get("document_indexable"), False):
        return True
    if _metadata_bool(meta.get("evidence_indexable"), False):
        return True

    # Legacy chunk: v0.13.34 inventory metadata'yı source-cache'ten yeniden
    # hesaplayabilir; Chroma kaydının kendisi eski false flag taşıyabilir.
    # Gerçek ve yeterli metin varsa retrieval için güvenli fallback budur.
    text = str(document or "").strip()
    meaningful = len(re.sub(r"\s+", "", text))
    return meaningful >= 250
def prefer_official_matches(
    results: List[Dict[str, Any]],
    limit: int = 5,
    query: str = "",
    resolved_refs: Optional[Tuple[Optional[str], Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """Sorgu verilmişse hukuk odaklı reranking, yoksa eski resmi-kaynak sıralaması."""
    if not results:
        return []

    if query.strip():
        return _legal_rerank(results, query=query, limit=limit, resolved_refs=resolved_refs)

    ranked = []
    for item in results:
        meta = item.get("metadata", {}) or {}
        authority = int(meta.get("authority_rank", 0) or 0)
        source_type = str(meta.get("source_type", ""))
        penalty = 0 if source_type == "official" else 50
        distance = float(item.get("distance") or 999.0)
        ranked.append((authority - penalty, -distance, item))
    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in ranked[:limit]]
def split_legal_chunks(
    text: str,
    max_chars: int=1800,
    overlap: int=0,
    title: str = "",
    document_type: str = "",
) -> List[Dict[str,Any]]:
    """Kanun/BDDK metnini hukuki sınırları bozmadan chunk'lara ayırır.

    MADDE yapısının yanında BDDK rehberlerindeki numaralı hükümleri ve
    Tekdüzen Hesap Planı'ndaki hesap kodlarını da kendi hiyerarşileriyle tutar.
    """
    clean=normalize_legal_whitespace(text)
    clean=repair_extracted_spacing(clean)
    if not clean:
        return []

    # Yapı tipi metnin kendisinden güvenli biçimde çıkarılır; mevcut çağrı
    # imzası korunur, dışarıdaki hiçbir kodun title/document_type göndermesi gerekmez.
    lower_clean=clean.lower()
    has_article=bool(re.search(r"(?i)(?:^|\n)\s*(?:(?:GEÇİCİ|EK)\s+)?MADDE\s*[-:]?\s*\d+[A-Za-z]?\b", clean))
    guidance_mode=(
        not has_article
        and bool(re.search(r"(?i)(İYİ\s+UYGULAMA\s+REHBERİ|\bREHBER\b|\bKILAVUZ\b|\bBİRİNCİ\s+KISIM\b)", clean))
    )
    accounting_phrase=bool(re.search(
        r"(?i)tek\s*d[üu]zen\s+hesap\s+plan[ıi]",
        (str(title or "") + "\n" + lower_clean),
    ))
    account_code_signal=len(re.findall(
        r"(?m)^\s*\d{3,6}\s{1,}[A-ZÇĞİÖŞÜa-zçğıöşü0-9][^\n]{2,120}$",
        clean,
    ))
    account_code_signal=max(account_code_signal, len(re.findall(
        r"(?<!\d)\d{3,6}(?=\s+[A-ZÇĞİÖŞÜa-zçğıöşü0-9])", clean)
    ))
    root_account_signal=len(set(re.findall(
        r"(?m)^\s*(\d{3})\d{0,3}\s{1,}[A-ZÇĞİÖŞÜa-zçğıöşü0-9]",
        clean,
    )))
    root_account_signal=max(root_account_signal, len(set(re.findall(
        r"(?<!\d)(\d{3})\d{0,3}(?=\s+[A-ZÇĞİÖŞÜa-zçğıöşü0-9])", clean)
    )))
    accounting_mode=bool(
        accounting_phrase
        or (str(document_type or "").strip().lower() == "bddk belge" and account_code_signal >= 5)
        or account_code_signal >= 5
    )

    # PDF extraction satır sonlarını kaybettiyse rehber hükümlerini ayır.
    if guidance_mode:
        clean=re.sub(
            r"(?i)(?<![\dA-Za-zÇĞİÖŞÜçğıöşü])(?=(\d{1,3}\s*[-.)]\s+(?=[A-ZÇĞİÖŞÜ])))",
            "\n",
            clean,
        )

    # Hesap planında kodları ayrı bloklara ayır. Tarih/normal sayıların önüne
    # satır sonu eklenmez; başlık karakteri büyük harf olmalıdır.
    if accounting_mode:
        clean=re.sub(
            r"(?<!\n)(?<!\d)(?=(\d{3,6}\s+[A-ZÇĞİÖŞÜ]))",
            "\n",
            clean,
        )

    lines=[x.strip() for x in clean.splitlines() if x.strip()]

    article_re=re.compile(
        r"^(?:(?P<prefix>GEÇİCİ|EK)\s+)?MADDE\s*[-:]?\s*"
        r"(?P<number>\d+[A-Za-z]?)\b(?:\s*[–—-]\s*)?(?P<title>.*)$",
        re.I|re.U,
    )
    paragraph_re=re.compile(
        r"^\(?\s*(?P<number>\d{1,3})\s*\)?[.)-]\s+(?P<body>.*)$",
        re.U,
    )
    item_re=re.compile(
        r"^\(?\s*(?P<number>[a-zçğıöşü]{1,3})\s*\)?[.)-]\s+(?P<body>.*)$",
        re.I|re.U,
    )
    numeric_re=re.compile(
        r"^\(?\s*(?P<number>\d{1,3})\s*\)?[.)-]\s+(?P<body>.*)$",
        re.U,
    )
    section_re=re.compile(
        r"^(?P<number>\d{1,3}(?:\.\d{1,3}){0,4})\s*(?:[-.)]|(?=\s))\s+(?P<body>[A-ZÇĞİÖŞÜ0-9].*)$",
        re.U,
    )
    account_re=re.compile(
        r"^(?P<code>\d{3,6})\s+(?P<body>[A-ZÇĞİÖŞÜ0-9].*)$",
        re.U,
    )

    blocks=[]
    article_prefix=""
    article_no=""
    article_title=""
    paragraph_no=""
    item_no=""
    subitem_no=""
    section_no=""
    account_code=""
    article_header=""
    buffer=[]
    mode="document"

    def flush():
        nonlocal buffer
        body="\n".join(buffer).strip()
        if body:
            valid=not subitem_no or bool(article_no and paragraph_no and item_no)
            blocks.append({
                "text":body,
                "article_prefix":article_prefix,
                "article_no":article_no,
                "article_title":article_title,
                "paragraph_no":paragraph_no,
                "item_no":item_no,
                "subitem_no":subitem_no,
                "section_no":section_no,
                "account_code":account_code,
                "chunk_type":mode,
                "hierarchy_valid":valid,
            })
        buffer=[]

    def flush_pending_article_header():
        nonlocal article_header
        if article_header:
            blocks.append({
                "text":article_header,
                "article_prefix":article_prefix,
                "article_no":article_no,
                "article_title":article_title,
                "paragraph_no":"",
                "item_no":"",
                "subitem_no":"",
                "section_no":"",
                "account_code":"",
                "chunk_type":"article",
                "hierarchy_valid":True,
            })
            article_header=""

    for line in lines:
        am=article_re.match(line)
        if am:
            if article_header:
                flush_pending_article_header()
            else:
                flush()
            article_prefix=(am.group("prefix") or "").upper()
            article_no=am.group("number")
            article_title=(am.group("title") or "").strip()
            paragraph_no=item_no=subitem_no=section_no=account_code=""
            article_header=line
            mode="article"
            continue

        if accounting_mode and not article_no:
            ac=account_re.match(line)
            if ac:
                flush()
                section_no=""
                account_code=ac.group("code")
                mode="accounting_code"
                buffer=[line]
                continue

        pm=paragraph_re.match(line)
        if article_no and not item_no and pm:
            flush()
            paragraph_no=pm.group("number")
            item_no=subitem_no=""
            prefix=article_header+"\n" if article_header else ""
            article_header=""
            mode="paragraph"
            buffer=[prefix+f"({paragraph_no}) {pm.group('body')}"]
            continue

        im=item_re.match(line)
        if article_no and im:
            flush()
            item_no=im.group("number").lower()
            subitem_no=""
            prefix=article_header+"\n" if article_header else ""
            article_header=""
            mode="item"
            buffer=[prefix+f"{item_no}) {im.group('body')}"]
            continue

        nm=numeric_re.match(line)
        if article_no and nm:
            if paragraph_no and item_no:
                flush()
                subitem_no=nm.group("number")
                mode="subitem"
                buffer=[f"{subitem_no}) {nm.group('body')}"]
            elif not item_no:
                flush()
                paragraph_no=nm.group("number")
                item_no=subitem_no=""
                prefix=article_header+"\n" if article_header else ""
                article_header=""
                mode="paragraph"
                buffer=[prefix+f"({paragraph_no}) {nm.group('body')}"]
            else:
                flush()
                paragraph_no=item_no=subitem_no=""
                mode="unscoped_numeric"
                buffer=[f"{nm.group('number')}) {nm.group('body')}"]
            continue

        if not article_no:
            sm=section_re.match(line)
            if sm and (guidance_mode or not accounting_mode):
                flush()
                section_no=sm.group("number")
                account_code=""
                mode="section"
                buffer=[f"{section_no}) {sm.group('body')}"]
                continue

        if article_header:
            buffer.append(article_header)
            article_header=""
        buffer.append(line)

    if article_header:
        flush_pending_article_header()
    else:
        flush()

    if not blocks:
        blocks=[{
            "text":clean,
            "article_prefix":"",
            "article_no":"",
            "article_title":"",
            "paragraph_no":"",
            "item_no":"",
            "subitem_no":"",
            "section_no":"",
            "account_code":"",
            "chunk_type":"document",
            "hierarchy_valid":True,
        }]

    def split_sentences(value):
        value=re.sub(r"[ \t]+"," ",value).strip()
        if not value:return []
        return [
            p.strip() for p in re.split(
                r"(?<=[.!?…])\s+(?=[A-ZÇĞİÖŞÜ0-9“\"(])",
                value,
            ) if p.strip()
        ] or [value]

    def split_words(value,limit):
        words=value.split()
        out=[];cur=[];length=0
        for word in words:
            extra=len(word) if not cur else len(word)+1
            if cur and length+extra>limit:
                out.append(" ".join(cur))
                cur=[word]
                length=len(word)
            else:
                cur.append(word)
                length+=extra
        if cur:out.append(" ".join(cur))
        return out

    final=[]
    chunk_no=0
    for block in blocks:
        if block.get("subitem_no") and not (
            block.get("article_no") and block.get("paragraph_no") and block.get("item_no")
        ):
            block["subitem_no"]=""
            block["chunk_type"]="item" if block.get("item_no") else "unscoped_numeric"
            block["hierarchy_valid"]=False

        content=re.sub(r"[ \t]+"," ",block["text"]).strip()
        if not content:continue

        if len(content)<=max_chars:
            row=dict(block)
            row["chunk_no"]=chunk_no
            row["subchunk_no"]=0
            row["overlap_chars"]=0
            final.append(row)
            chunk_no+=1
            continue

        sentences=split_sentences(content)
        pieces=[];current=""
        for sentence in sentences:
            if len(sentence)>max_chars:
                if current:
                    pieces.append(current.strip())
                    current=""
                pieces.extend(split_words(sentence,max_chars))
                continue
            candidate=sentence if not current else f"{current} {sentence}"
            if current and len(candidate)>max_chars:
                pieces.append(current.strip())
                current=sentence
            else:
                current=candidate
        if current:pieces.append(current.strip())

        safe=[]
        for piece in pieces:
            safe.extend([piece] if len(piece)<=max_chars else split_words(piece,max_chars))

        for subchunk_no,piece in enumerate(safe):
            row=dict(block)
            row["text"]=piece.strip()
            row["chunk_no"]=chunk_no
            row["subchunk_no"]=subchunk_no
            row["overlap_chars"]=0
            final.append(row)
            chunk_no+=1

    return final
def _chunk_matches_document_ref(meta: Dict[str, Any], document: Any, document_ref: str) -> bool:
    """Belge referansını metadata + başlık + URL + metin kimliğiyle doğrular."""
    ref = str(document_ref or "").strip()
    if not ref:
        return False
    m = meta or {}
    for value in (m.get("source_document_id", ""), m.get("document_number", ""), m.get("document_key", ""), m.get("title", "")):
        if str(value or "").strip() == ref:
            return True
    url = str(m.get("url", "") or "")
    if re.search(rf"(?:^|[/_-]){re.escape(ref)}(?:[/_.?&#-]|$)", url, flags=re.I):
        return True
    text = str(document or "")[:1200]
    if re.search(rf"(?<!\d){re.escape(ref)}(?!\d)", text):
        if str(m.get("source", "")).strip().upper() == "BDDK":
            return True
    return False
def _chunk_matches_account_ref(meta: Dict[str, Any], document: Any, account_ref: str) -> bool:
    """Hesap kodunu metadata veya gerçek chunk metninden doğrular."""
    ref = str(account_ref or "").strip()
    if not re.fullmatch(r"\d{3,6}", ref):
        return False
    m = meta or {}
    if str(m.get("account_code", "") or "").strip() == ref:
        return True
    repaired = repair_account_code_spacing(str(document or ""), ref)
    return bool(re.search(rf"(?<!\d){re.escape(ref)}(?=\s*[A-ZÇĞİÖŞÜa-zçğıöşü])", repaired, flags=re.I))
def find_previous_same_title(index: IngestIndex, title: str, exclude_hash: Optional[str] = None) -> Optional[Dict[str, Any]]:
    target = normalize_title(title)
    candidates = []
    for h, meta in index.data.get("indexed_items", {}).items():
        if exclude_hash and h == exclude_hash:
            continue
        if normalize_title(meta.get("title", "")) == target:
            candidates.append((meta.get("timestamp", ""), meta))
    if not candidates:
        return None
    candidates.sort(reverse=True, key=lambda x: x[0])
    return candidates[0][1]
def legal_diff(old_text: str, new_text: str, max_lines: int = 250) -> Dict[str, Any]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    added = sum(1 for x in diff if x.startswith("+") and not x.startswith("+++"))
    removed = sum(1 for x in diff if x.startswith("-") and not x.startswith("---"))
    return {
        "added_lines": added,
        "removed_lines": removed,
        "changed": bool(diff),
        "diff_excerpt": "\n".join(diff[:max_lines]),
    }


__all__ = [
    "detect_doc_type",
    "extract_first",
    "classify_legal_structure",
    "detect_document_status",
    "extract_legal_metadata",
    "extract_query_document_reference",
    "extract_query_account_code",
    "prefer_official_matches",
    "split_legal_chunks",
    "find_previous_same_title",
    "legal_diff",
]
