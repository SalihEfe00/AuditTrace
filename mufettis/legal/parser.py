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
from mufettis.corpus import (
    NOT_IN_FORCE_STATUSES,
    authority_prior,
    canonical_view,
    validity_tier,
    with_canonical,
)
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

    # Belgenin kendi beyan ettiği ad, gövdede geçen tür sözcüklerinden güvenilirdir: yönetmelikler
    # dayanak maddesinde kanuna atıf yapar, bu yüzden "gövdede 'kanun' geçiyor" kanıt değildir.
    declared = declared_document_type(extract_declared_title(text))
    if declared:
        return declared

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
def declared_document_type(declared_title: str) -> str:
    """Beyan edilen addaki baş isimden (sondaki tür sözcüğü) belge türü etiketi; yoksa ""."""
    from mufettis.corpus.model import InstrumentType

    if not declared_title:
        return ""
    instrument = _title_instrument(declared_title)
    if instrument == InstrumentType.GUIDELINE and "KILAVUZ" in declared_title.upper():
        return "Kılavuz"
    return {
        InstrumentType.CONSTITUTION: "Anayasa",
        InstrumentType.LAW: "Kanun",
        InstrumentType.PRESIDENTIAL_DECREE: "Cumhurbaşkanlığı Kararnamesi",
        InstrumentType.PRESIDENTIAL_DECISION: "Cumhurbaşkanı Kararı",
        InstrumentType.REGULATION: "Yönetmelik",
        InstrumentType.COMMUNIQUE: "Tebliğ",
        InstrumentType.PROCEDURE: "Usul ve Esas",
        InstrumentType.BOARD_DECISION: "Kurul Kararı",
        InstrumentType.CIRCULAR: "Genelge",
        InstrumentType.GUIDELINE: "Rehber",
        InstrumentType.CHART_OF_ACCOUNTS: "Hesap Planı",
        InstrumentType.DECISION: "Karar",
    }.get(instrument, "")


def extract_first(text: str, patterns: Iterable[str]) -> Optional[str]:
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I | re.M)
        if m:
            return m.group(1).strip()
    return None
# Hesap planı: belgenin omurgası "kod + başlık" satırlarıdır; belirleyici olan kodların metne göre
# YOĞUNLUĞUDUR (küçük bir hesap planı alıntısı da hesap planıdır). Ölçüm: hesap planlarında 1000
# karakterde 12-18 kod; hesap planına atıf yapan vergi kanunlarında <1; kurul kararlarında birkaç
# "NNNNN sayılı" sayısı (küçük harfle devam ettiği için kod sayılmaz).
ACCOUNTING_MIN_CODES = 5
ACCOUNTING_MIN_CODES_PER_1000_CHARS = 3.0
# Madde başlığındaki numara. Kesme işaretiyle ek alan numara ("Geçici Madde 2'ye paralel olarak",
# "MADDE 5'te belirtilen") metin içi atıftır, başlık değildir; aksi halde atıf yeni bir madde başlatır
# ve tek bir atıf satırı rehberin tüm numaralı bölüm yapısını kapatır (has_article -> guidance_mode yok).
# Madde başlığını arayan tüm kalıplar bu parçayı kullanır.
_ARTICLE_SUFFIX_GUARD = r"\b(?!\s?['’‘`´])"
ARTICLE_NUMBER = r"\d+[A-Za-z]?" + _ARTICLE_SUFFIX_GUARD
ARTICLE_NUMBER_GROUP = r"(?P<number>\d+[A-Za-z]?)" + _ARTICLE_SUFFIX_GUARD

_ACCOUNT_LINE_RE = re.compile(r"(?m)^\s*\d{3,6}\s+[A-ZÇĞİÖŞÜa-zçğıöşü0-9][^\n]{2,160}$")
_ACCOUNT_INLINE_RE = re.compile(r"(?<![\d.,/])\d{3,6}(?=\s+[A-ZÇĞİÖŞÜ])")


# Belgenin kendi başlığı onu hesap planı olarak adlandırıyorsa az sayıda kod yeter. İfade yalnızca
# başlıkta / metnin başlık bölgesinde aranır: gövdede geçmesi atıftır (ör. Vergi Usul Kanunu).
_ACCOUNTING_TITLE_RE = re.compile(r"(?i)hesap\s+plan[ıi]")
ACCOUNTING_HEADING_CHARS = 300


def is_accounting_plan_text(text: str, title: str = "") -> bool:
    body = str(text or "")
    if not body.strip():
        return False
    codes = max(len(_ACCOUNT_LINE_RE.findall(body)), len(_ACCOUNT_INLINE_RE.findall(body)))
    if codes >= 2 and _ACCOUNTING_TITLE_RE.search(f"{title}\n{body[:ACCOUNTING_HEADING_CHARS]}"):
        return True
    return codes >= ACCOUNTING_MIN_CODES and codes * 1000.0 / len(body) >= ACCOUNTING_MIN_CODES_PER_1000_CHARS


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
        r"(?im)^\s*(?:(?:GEÇİCİ|EK)\s+)?MADDE\s*[-:]?\s*" + ARTICLE_NUMBER,
        norm,
    ))
    inline_article_count=len(re.findall(
        r"(?i)(?<![A-ZÇĞİÖŞÜa-zçğıöşü0-9])(?:GEÇİCİ|EK)?\s*MADDE\s*[-:]?\s*" + ARTICLE_NUMBER,
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

    # Hesap planları hukuki MADDE değil, hesap kodu + başlık hiyerarşisi kullanır. Tespit
    # başlığa, belge türüne veya "Tekdüzen Hesap Planı" ifadesine değil (atıf yapan kanunlarda
    # da geçer) kod satırlarının sayı ve yoğunluğuna dayanır (is_accounting_plan_text).
    account_code_count=len(re.findall(
        r"(?m)^\s*(\d{3,6})\s{1,}([A-ZÇĞİÖŞÜa-zçğıöşü0-9][^\n]{2,160})$",
        norm,
    ))
    inline_account_count=len(re.findall(
        r"(?<!\d)\d{3,6}(?=\s+[A-ZÇĞİÖŞÜa-zçğıöşü0-9])",
        norm,
    ))
    account_code_count=max(account_code_count, inline_account_count)
    accounting_plan_context=is_accounting_plan_text(norm, str(title or ""))
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

# Kanun metinleri adlarını "Kanun Numarası : N" beyanından hemen önceki büyük harfli satırlarda
# verir (resmî mevzuat portalı formatı). Tarayıcı çıktısının sayfa başlığı ("4.09.2026 18:58
# Mevzuat Bilgi Sistemi") ad değildir.
_LAW_NUMBER_DECLARATION_RE = re.compile(r"(?m)^\s*Kanun\s+Numarası\s*[:№.-]?\s*\d{2,8}\b")
_PRINT_HEADER_RE = re.compile(r"^\d{1,2}[./]\d{1,2}[./]\d{4}\s+\d{1,2}:\d{2}\b")
_GENERIC_TITLE_WORDS = frozenset({"KANUN", "KANUNU", "HAKKINDA", "DAİR", "İLİŞKİN", "VE", "İLE"})
DECLARED_TITLE_MAX_LINES = 4
DECLARED_TITLE_MAX_CHARS = 250


# Resmî yazışmada belge konusunu "Konu:" satırında beyan eder; genelgeler ayrıca "GENELGE (2025/1)"
# numarası taşır. Konu bir sonraki satıra taşabilir; tür başlığında veya boş satırda biter.
_SUBJECT_RE = re.compile(r"(?m)^\s*Konu\s*:\s*(\S.*(?:\n(?!\s*$)(?!\s*(?:GENELGE|T\.\s*C|Sayı)\b).*)?)")
_CIRCULAR_NUMBER_RE = re.compile(r"(?m)^\s*GENELGE\s*\n?\s*\(?\s*(\d{4}\s*/\s*\d{1,3})\s*\)?")
DECLARED_SUBJECT_MAX_CHARS = 200


def extract_declared_subject(text: str) -> str:
    """Yazışma biçimindeki belgenin beyan ettiği konu ("Genelge 2025/1: <konu>"); yoksa ""."""
    head = str(text or "")[:LAW_REFERENCE_HEADER_CHARS]
    match = _SUBJECT_RE.search(head)
    if not match:
        return ""
    subject = re.sub(r"\s+", " ", match.group(1)).strip(" .")
    if not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3,}", subject) or len(subject) > DECLARED_SUBJECT_MAX_CHARS:
        return ""
    circular = _CIRCULAR_NUMBER_RE.search(head[match.end():])
    return f"Genelge {re.sub(r'\s+', '', circular.group(1))}: {subject}" if circular else subject


_TITLE_LOWERCASE_CONNECTORS = frozenset({"ve", "ile", "veya"})
# Resmî Gazete biçimi: yayımlayan kurum ayrılma hâlinde ve iki noktayla ayrı satırdadır
# ("Bankacılık Düzenleme ve Denetleme Kurumundan:"); hemen altındaki büyük harfli blok düzenlemenin
# adıdır. Kararların adı genel olduğundan ("... KURULU KARARI") karar sayısı ada eklenir.
_ISSUER_LINE_RE = re.compile(r"(?m)^[ \t]*\S[^\n]{2,150}?[dt][ae]n[ \t]*:[ \t]*$")
_DECISION_NUMBER_RE = re.compile(r"Karar\s+Sayısı\s*:\s*(\d{1,8})")


# Adın altındaki ilk bölüm başlığı ("BİRİNCİ KISIM", "BİRİNC İ BÖLÜM", "MADDE 1") ada dahil değildir.
_DIVISION_HEADING_RE = re.compile(r"(?:\b(?:KISIM|BÖLÜM)\s*$)|^(?:(?:GEÇİCİ|EK)\s+)?MADDE\b")


def _is_title_line(line: str) -> bool:
    words = re.findall(r"[^\W\d_]+", line)
    return bool(words) and not _PRINT_HEADER_RE.match(line) and not _DIVISION_HEADING_RE.search(line) and all(
        not any(ch.islower() for ch in w) or w in _TITLE_LOWERCASE_CONNECTORS for w in words)


def _title_block(lines: List[str]) -> str:
    block: List[str] = []
    for raw in lines:
        line = re.sub(r"\s+", " ", raw).strip()
        if not line and not block:
            continue
        if not line or not _is_title_line(line) or len(block) >= DECLARED_TITLE_MAX_LINES:
            break
        block.append(line)
    title = " ".join(block)
    words = re.findall(r"[A-ZÇĞİÖŞÜÂÎÛ]+", title)
    if not title or len(title) > DECLARED_TITLE_MAX_CHARS or not set(words) - _GENERIC_TITLE_WORDS:
        return ""
    return title


def extract_declared_title(text: str) -> str:
    """Belgenin kendi beyan ettiği ad; bulunamazsa "". Üç yayım biçimi tanınır:

    - Kanun: "Kanun Numarası" beyanının hemen üstündeki büyük harfli blok.
    - Resmî Gazete: yayımlayan kurum satırının ("...Kurumundan:") hemen altındaki büyük harfli blok.
    - Mevzuat metni: belgenin en başındaki büyük harfli blok; yalnızca bir düzenleme türüyle
      bitiyorsa ("... İLİŞKİN YÖNETMELİK") ad sayılır, aksi halde sayfa/kurum başlığı olabilir.

    Blok yalnızca genel sözcüklerden oluşuyorsa ("HAKKINDA KANUN": adın ilk satırı sayfa başlığına
    karışmış) ad eksiktir ve kullanılmaz."""
    head = str(text or "")[:LAW_REFERENCE_HEADER_CHARS]
    law = _LAW_NUMBER_DECLARATION_RE.search(head)
    if law:
        return _law_title(head[:law.start()])
    issuer = _ISSUER_LINE_RE.search(head)
    if not issuer:
        return _leading_title(head)
    title = _title_block(head[issuer.end():].splitlines())
    decision = _DECISION_NUMBER_RE.search(head[issuer.end():])
    if title and title.endswith("KARARI") and decision:
        return f"{title} {decision.group(1)}"
    return title


def _leading_title(head: str) -> str:
    title = _title_block(head.splitlines())
    if title and _title_instrument(title):
        return title
    return _leading_mixed_case_title(head)


# Web sayfasından alınan metinlerde ad büyük harfle yazılmaz ve önünde sayfa öğeleri bulunur
# ("Mevzuat Detay", "Kapat"): "Bankaların Kredi İşlemlerine İlişkin Yönetmelik". İlk satırlardaki,
# sözcükleri büyük harfle başlayan, bir düzenleme türüyle biten, etiket ("Konu:") ve cümle olmayan satır addır.
LEADING_TITLE_SCAN_LINES = 8
MIN_MIXED_CASE_TITLE_WORDS = 3
# Numaralı satır bir bölüm başlığıdır, belgenin adı değildir ("I. Tekdüzen Hesap Planı", "1- ...").
_ENUMERATED_HEADING_RE = re.compile(r"\W*(?:\d|(?:[IVXLC]+|[A-Za-z])[.)-]\s)")


def _leading_mixed_case_title(head: str) -> str:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in head.splitlines()]
    for line in [ln for ln in lines if ln][:LEADING_TITLE_SCAN_LINES]:
        words = re.findall(r"[^\W\d_]+", line)
        content = [w for w in words if w not in _TITLE_LOWERCASE_CONNECTORS]
        if (
            len(words) < MIN_MIXED_CASE_TITLE_WORDS
            or len(line) > DECLARED_TITLE_MAX_CHARS
            or line[-1] in ".,;" or ":" in line
            or _ENUMERATED_HEADING_RE.match(line)
            or not all(w[0].isupper() for w in content)
        ):
            continue
        if _title_instrument(line):
            return line
    return ""


def _title_tail(title: str) -> str:
    # Türün baş isim olarak sonda olması gerekir: "... KANUNU (SIRA NO: 531)" gibi ekler atılır,
    # ardından yalnızca son sözcükler (adın ortasındaki "KANUNU" atıftır) değerlendirilir.
    core = re.sub(r"\s*\([^()]*\)\s*$", "", title)
    return " ".join(core.split()[-3:])


# Tür adı eşgüdümle ikinci bir ada bağlanabilir ("... HESAP PLANI VE İZAHNAMESİ", "... YÖNETMELİĞİ
# VE EKLERİ"); sondaki "ve <sözcük>" baş ismi son üç sözcüğün dışına iter. Yalnızca asıl kuyruk tür
# vermezse denenir: "USUL VE ESASLAR" gibi adlar ilk denemede tanınır, sonuçları değişmez.
_TRAILING_COORDINATE_RE = re.compile(r"\s+(?:ve|VE|Ve)\s+[^\W\d_]+\s*$")


def _title_instrument(title: str) -> Optional[str]:
    """Addaki baş isimden kanonik tür (mufettis.corpus.identity.instrument_from_title); yoksa None."""
    from mufettis.corpus.identity import instrument_from_title

    found = instrument_from_title(_title_tail(title))
    if found:
        return found
    core = re.sub(r"\s*\([^()]*\)\s*$", "", title)
    stripped = _TRAILING_COORDINATE_RE.sub("", core)
    return instrument_from_title(_title_tail(stripped)) if stripped != core else None


def _law_title(before: str) -> str:
    lines = before.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    block: List[str] = []
    while lines and len(block) < DECLARED_TITLE_MAX_LINES:
        line = re.sub(r"\s+", " ", lines.pop()).strip()
        if not line or not _is_title_line(line):
            break
        block.insert(0, line)
    return _title_block(block)


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


LEGAL_HEAD_CHARS = 80000


def legal_head(text: str) -> str:
    """Kimlik alanlarının türetildiği metin: belgenin başı, hukuki boşlukları onarılmış halde."""
    return normalize_legal_whitespace(str(text or "")[:LEGAL_HEAD_CHARS])


def declared_names(text: str) -> Tuple[str, str]:
    """(declared_title, declared_subject), yüklemedeki extract_legal_metadata ile aynı türetme.
    Ham metinden türetmek ayrı sonuç verir (PDF'teki "FA İZ" gibi bölünmeler onarılmamış olur)."""
    head = legal_head(text)
    return extract_declared_title(head), extract_declared_subject(head)


def extract_legal_metadata(
    title: str,
    text: str,
    base_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Belge kimliği + hukuki yapı + parse kalitesi metadata'sı."""
    head=legal_head(text)
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
            r"\b(?:GEÇİCİ|EK)?\s*MADDE\s*[-:]?\s*(" + ARTICLE_NUMBER + ")",
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

    legal_meta = {
        **base_meta,
        "document_type":doc_type,
        "document_number":number,
        "law_number":law_number,
        "declared_title":extract_declared_title(head),
        "declared_subject":extract_declared_subject(head),
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
    # Kanonik kimlik/kaynak/normatif alanlar (mufettis.corpus). İçerik özeti her zaman bu
    # metinden yeniden hesaplanır; kayıttaki eski özet başka bir metne ait olabilir.
    return with_canonical(legal_meta, text)
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
    """Hukuk odaklı yeniden sıralama. Dört eksen birbirine karıştırılmaz:

    1. Alaka (_relevance_score): anlamsal benzerlik + sözcüksel örtüşme + sorgudaki açık
       kimliklerin (kanun no, belge kimliği, hesap kodu) belgeyle eşleşmesi.
    2. Otorite önceliği (_authority_prior): normatif hiyerarşi ve kopya doğrulamasından gelen,
       üst sınırı küçük (corpus.AUTHORITY_PRIOR_MAX) bir ek; yalnızca başa baş adayları ayırır.
       Kaynağın resmî olup olmaması alaka puanına ayrıca eklenmez.
    3. Geçerlilik (_validity_tier): puana eklenmez; yürürlükteki belgeler ayrı katman olarak önce gelir.
    4. Kimlik: aynı sürümün (expression) farklı kopyalarındaki aynı parça bir kez seçilir.

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

        canonical = canonical_view(meta)
        distance = float(item.get("distance") or 999.0)

        semantic_score = 1.0 / (1.0 + max(0.0, distance))
        title_overlap = _lexical_overlap(terms, title)
        doc_overlap = _lexical_overlap(terms, doc)
        lexical_score = min(
            1.0,
            0.75 * title_overlap + 0.25 * doc_overlap,
        )
        prior = authority_prior(canonical.normative_rank, canonical.verification, canonical.publisher_kind)
        tier = validity_tier(meta)

        title_bonus = 0.12 if title_overlap >= 0.34 else 0.0
        # Sorgu yayıncının adını anıyorsa ("BDDK ...") bu bir alaka sinyalidir, güven değil.
        source_bonus = 0.05 if _lexical_overlap(terms, source) > 0 else 0.0

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

        relevance = (
            0.40 * semantic_score
            + 0.22 * lexical_score
            + title_bonus
            + source_bonus
            + exact_bonus
            + document_bonus
            + account_bonus
            - exact_miss_penalty
            - document_miss_penalty
            - account_miss_penalty
        )

        enriched = dict(item)
        enriched["document"] = doc
        enriched["_rerank_score"] = round(relevance + prior, 6)
        enriched["_relevance_score"] = round(relevance, 6)
        enriched["_semantic_score"] = round(semantic_score, 6)
        enriched["_lexical_score"] = round(lexical_score, 6)
        enriched["_authority_prior"] = round(prior, 6)
        enriched["_validity_tier"] = tier
        enriched["_expression_id"] = canonical.expression_id
        enriched["_exact_legal_id"] = legal_id or ""
        enriched["_exact_identifier_match"] = exact_identifier
        enriched["_exact_document_match"] = exact_document_match
        enriched["_exact_account_match"] = exact_account_match
        scored.append(enriched)

    scored.sort(key=lambda x: (x["_validity_tier"], -x["_rerank_score"]))

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
        # Aynı expression'ın kopyaları (aynı metin, farklı format/kaynak) aynı parçaları üretir.
        key = (
            item["_expression_id"],
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
    if status in NOT_IN_FORCE_STATUSES:
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
    """Sorgu verilmişse hukuk odaklı reranking (_legal_rerank).

    Sorgu yoksa alaka sinyali yalnızca vektör uzaklığıdır: geçerlilik katmanı, ardından
    otorite önceliği ve uzaklık. (Önceden resmî olmayan her kopya 50 puan cezayla sona
    atılıyordu; kopyanın güveni artık corpus doğrulama ekseninden gelir.)
    """
    if not results:
        return []

    if query.strip():
        return _legal_rerank(results, query=query, limit=limit, resolved_refs=resolved_refs)

    def _key(item: Dict[str, Any]) -> Tuple[int, float, float]:
        meta = item.get("metadata", {}) or {}
        canonical = canonical_view(meta)
        prior = authority_prior(canonical.normative_rank, canonical.verification, canonical.publisher_kind)
        return validity_tier(meta), -prior, float(item.get("distance") or 999.0)

    return sorted(results, key=_key)[:limit]
INLINE_ARTICLE_HEADER_RE=re.compile(
    r"(?<=[.;:])[ \t]+(?=(?:(?:GEÇİCİ|EK)[ \t]+)?MADDE[ \t]+\d+[A-Za-z]?[ \t]*[–—-])"
)


HEADING_MAX_CHARS = 120
HEADING_MAX_WORDS = 14
_NOT_HEADING_START_RE = re.compile(r"(?i)^\s*(?:(?:(?:GEÇİCİ|EK)\s+)?MADDE\s*[-:]?\s*\d|\(?\s*\d{1,3}\s*[).-]|\(?[a-zçğıöşü]{1,2}\)\s)")
_SENTENCE_END_RE = re.compile(r"[.:;!?…)\]”\"’']\s*$")


def _is_heading_line(line: str) -> bool:
    words = line.split()
    first = next((ch for ch in line if ch.isalpha()), "")
    return (
        0 < len(words) <= HEADING_MAX_WORDS
        and len(line) <= HEADING_MAX_CHARS
        and first.isupper()
        and not re.search(r"[.,;:]\s*$", line)
        # Madde satırı ve numaralı fıkra/bent başlık değildir, metnin kendisidir.
        and not _NOT_HEADING_START_RE.match(line)
    )


def _take_trailing_headings(buffer: List[str]) -> List[str]:
    """Bloğun sonundaki başlık satırlarını çıkarıp döndürür (sonraki maddeye aittirler).

    Yalnızca öncesindeki satır bir cümleyle bitiyorsa (ya da blok yalnızca başlıklardan oluşuyorsa)
    alınır: aksi halde noktasız satır, alt satıra kaymış bir cümlenin parçası olabilir."""
    split = len(buffer)
    while split > 0 and _is_heading_line(buffer[split - 1]):
        split -= 1
    if split == len(buffer) or (split > 0 and not _SENTENCE_END_RE.search(buffer[split - 1])):
        return []
    headings = buffer[split:]
    del buffer[split:]
    return headings


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
    # PDF çıkarımı madde başlıklarını önceki cümleye yapıştırabiliyor ("… getirmek zorundadırlar.
    # GEÇİCİ MADDE 4 –Bu Kanunun …"); o zaman iki madde tek parça olur ve atıf yanlış maddeyi
    # gösterir. Başlık, cümle sonundan sonra gelen BÜYÜK harfli "MADDE <no> –" kalıbıdır; metin
    # içi atıflar ("24 üncü maddenin") küçük harfli olduğundan bölünmez.
    clean=INLINE_ARTICLE_HEADER_RE.sub("\n", clean)

    # Yapı tipi metnin kendisinden güvenli biçimde çıkarılır; mevcut çağrı
    # imzası korunur, dışarıdaki hiçbir kodun title/document_type göndermesi gerekmez.
    has_article=bool(re.search(r"(?i)(?:^|\n)\s*(?:(?:GEÇİCİ|EK)\s+)?MADDE\s*[-:]?\s*" + ARTICLE_NUMBER, clean))
    guidance_mode=(
        not has_article
        and bool(re.search(r"(?i)(İYİ\s+UYGULAMA\s+REHBERİ|\bREHBER\b|\bKILAVUZ\b|\bBİRİNCİ\s+KISIM\b)", clean))
    )
    # Birkaç "NNNNN sayılı" / "Karar Sayısı: NNNNN" sayısı belgeyi hesap planı yapmaz (önceden
    # kurul kararlarının gövdesi "hesap kodu" parçası olup sınıflama rolüne düşüyordu).
    accounting_mode=is_accounting_plan_text(clean, str(title or ""))

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
        + ARTICLE_NUMBER_GROUP + r"(?:\s*[–—-]\s*)?(?P<title>.*)$",
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
    article_heading=""
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
                "article_heading":article_heading,
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
                "article_heading":article_heading,
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
            # Maddenin başlığı ("Zamanaşımı") ve üstündeki kısım/bölüm başlıkları "MADDE" satırından
            # önce gelir; önceki maddenin sonunda kalırlarsa madde konusunu kaybeder, önceki madde
            # de yanıltıcı bir başlık kazanır. Başlık parça metnine eklenmez (gömme küçük metin
            # değişikliklerine duyarlı: ölçümde ilgili maddeyi sorgudan uzaklaştırdı); maddenin
            # parçalarına article_heading olarak taşınır ve sözcüksel indekste aranır.
            headings=[] if article_header else _take_trailing_headings(buffer)
            if article_header:
                flush_pending_article_header()
            else:
                flush()
            article_prefix=(am.group("prefix") or "").upper()
            article_no=am.group("number")
            article_title=(am.group("title") or "").strip()
            article_heading=" — ".join(headings)
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
            "article_heading":"",
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
