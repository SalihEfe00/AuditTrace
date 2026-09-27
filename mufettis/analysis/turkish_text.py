# -*- coding: utf-8 -*-
"""
Müfettiş AI - Analysis Intelligence
Türkçe metin normalizasyonu ve terim eşleştirme yardımcıları.

Python'un `str.lower()` işlevi Türkçe için hatalıdır: "İ" harfini "i" + birleşik
nokta (U+0307) dizisine, "I" harfini ise "i" harfine çevirir. Bu yüzden
"İşlem".lower() sonucu kelime sınırı kalıplarını ve alt dizi aramalarını bozar.
Analiz katmanındaki tüm büyük/küçük harf duyarsız karşılaştırmalar bu modülden
geçmelidir.
"""
from __future__ import annotations

import re
from typing import FrozenSet, Iterable, Set

_TURKISH_UPPER_MAP = str.maketrans({"İ": "i", "I": "ı"})
_COMBINING_DOT_ABOVE = "̇"

# Türkçe harfler dahil kelime karakterleri (rakamlar dahil; kanun numaraları vb.)
_WORD_RE = re.compile(r"[0-9a-zçğıöşüâîû]+")

# Sona gelen yumuşamış ünsüzlerin sert karşılıkları (hesab-ım -> hesap)
_FINAL_CONSONANT_HARDENING = str.maketrans({"b": "p", "c": "ç", "d": "t", "ğ": "k"})

# Eşleşme için anlam taşımayan kelimeler (bağlaç, yardımcı fiil, dilekçe kalıbı).
STOP_WORDS: FrozenSet[str] = frozenset({
    "ve", "veya", "ile", "için", "icin", "gibi", "göre", "gore", "olan", "olarak", "olup",
    "bir", "bu", "şu", "da", "de", "ki", "ne", "ya", "ise", "ama", "fakat", "ancak",
    "daha", "çok", "en", "her", "hiç", "herhangi", "tüm", "bütün", "kadar", "sonra", "önce",
    "tarafından", "tarafindan", "nedeniyle", "halinde", "hâlinde", "üzere", "ilgili",
    "belgede", "belirtilen", "iddia", "kapsamındaki", "kapsamında", "işlemler",
    "edilmektedir", "edilmiştir", "edildi", "edilmiş", "edilen", "edilsin", "etti", "ediyorum",
    "yapılmıştır", "yapıldı", "yapılan", "olmuştur", "olmadan", "rağmen", "derhal",
    "talep", "ederim", "rica", "arz", "gereğini", "bilgilerinize", "şikayetçiyim", "şikâyetçiyim",
    "mağdur", "edildim", "istiyorum", "tarihinde", "tarihli", "sayılı", "madde", "fıkra", "bent",
    "konu", "sayı", "ilgi",  # resmî yazı başlık alanları
})

# Bankacılık metinlerinde neredeyse her parçada geçen, tek başına alaka kanıtı
# olmayan genel kelimeler. Kök düzeyinde karşılaştırılır (bkz. GENERIC_DOMAIN_STEMS).
GENERIC_DOMAIN_WORDS = (
    "banka", "müşteri", "işlem", "hesap", "şube", "tutar", "kurul", "kurum", "kurucu",
    "yönetim", "hüküm", "hükmü", "mevzuat", "kanun", "yöntem", "uygulama", "ilişkin", "hakkında",
    "esaslar", "genel", "tarih", "süre", "olarak", "bulunan", "belirlenen", "gereken",
    "yapılan", "edilen", "türk", "lira", "para",
)

# Bankacılık mevzuatında çok sık geçen, anlam taşıyan ama AYIRT EDİCİ olmayan terimler.
# Alaka hesabında sayılırlar; ancak bir eşleşme yalnızca bunlardan oluşamaz (en az bir
# "güçlü" terim gerekir). Örn. "konut kredisi erken ödeme" iddiası ile "Kredi Kartı
# Ödemelerinden ... izlenir" satırı yalnızca kredi+ödeme ortaklığı taşır.
# Gerçek korpusla çalışırken bu listeye korpusta çok yaygın kökler eklenir
# (bkz. mufettis.analysis.term_stats.weak_term_predicate).
WEAK_DOMAIN_WORDS = (
    "kredi", "kart", "ödeme", "faiz", "mevduat", "finansal", "ücret", "borç", "alacak",
    "sözleşme", "tüketici", "hizmet", "bilgi", "kuruluş", "sermaye", "risk",
)

# İddia metnindeki eş anlamlı ifadeleri mevzuatta kullanılan kavrama bağlar
# (kavram -> ifadeler). Konu anahtar kelime listelerinin aksine bunlar gerçek eş
# anlamlılardır; bu yüzden iddianın kendi terimi sayılır.
CONCEPT_SYNONYMS = {
    "yetkisiz": ("bilgim dışında", "onayım olmadan", "onayım alınmadan", "rızam olmadan",
                 "izinsiz", "habersiz", "haberim olmadan", "talimatım olmadan"),
}

# Yoksunluk eki (-sız/-siz/-suz/-süz): kökün tersini anlatır.
PRIVATIVE_SUFFIX_RE = re.compile(r"s[ıiuü]z")

MIN_TERM_LENGTH = 3
STEM_LENGTH = 5
MIN_PREFIX_MATCH = 4


def turkish_lower(text: str) -> str:
    """Türkçe kurallarına uygun küçük harfe çevirme (İ->i, I->ı)."""
    if not text:
        return ""
    return str(text).translate(_TURKISH_UPPER_MAP).lower().replace(_COMBINING_DOT_ABOVE, "")


def tokenize(text: str, min_length: int = MIN_TERM_LENGTH) -> list:
    """Türkçe küçük harfe çevrilmiş kelime listesi."""
    return [t for t in _WORD_RE.findall(turkish_lower(text)) if len(t) >= min_length]


def stem(token: str) -> str:
    """Basit Türkçe kök: ilk STEM_LENGTH harf; son harf yumuşak ünsüzse sertleştirilir.

    Her iki tarafa aynı işlem uygulandığı için "hesabımdan" ve "hesap" aynı köke
    ("hesap") iner.
    """
    base = token[:STEM_LENGTH]
    return base[:-1] + base[-1:].translate(_FINAL_CONSONANT_HARDENING)


GENERIC_DOMAIN_STEMS: FrozenSet[str] = frozenset(stem(w) for w in GENERIC_DOMAIN_WORDS)


def is_generic_term(token: str) -> bool:
    """Genel bankacılık terimi mi? (bankası, müşterinin, parası ...)"""
    return any(terms_match(token, generic) for generic in GENERIC_DOMAIN_WORDS)


def is_weak_term(token: str) -> bool:
    """Sık geçen, tek başına ayırt edici olmayan bankacılık terimi mi? (kredisi, ödemeler ...)"""
    return any(terms_match(token, weak) for weak in WEAK_DOMAIN_WORDS)


def content_terms(text: str) -> Set[str]:
    """Durak kelimeleri ve genel alan terimlerini dışarıda bırakan içerik terimleri."""
    out: Set[str] = set()
    for token in tokenize(text):
        if token in STOP_WORDS or token.isdigit():
            continue
        if is_generic_term(token):
            continue
        out.add(token)
    return out


def terms_match(a: str, b: str) -> bool:
    """İki terim aynı köke mi işaret ediyor? (kart ~ kartımdan, hesabı ~ hesap, ücret ~ ücretin)"""
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < MIN_PREFIX_MATCH:
        return False
    # Yoksunluk eki anlamı tersine çevirir: "yetkisiz" ~ "yetki", "ücretsiz" ~ "ücret" değildir.
    if PRIVATIVE_SUFFIX_RE.match(longer[len(shorter):]) and longer.startswith(shorter):
        return False
    need = min(STEM_LENGTH, len(shorter))
    return stem(shorter)[:need] == stem(longer)[:need]


def matched_terms(query_terms: Iterable[str], target_terms: Iterable[str]) -> Set[str]:
    """query_terms içinden target_terms ile kök düzeyinde eşleşenler."""
    targets = list(target_terms)
    return {q for q in query_terms if any(terms_match(q, t) for t in targets)}


def concept_terms(text: str) -> Set[str]:
    """Metinde eş anlamlı ifadesi geçen kavramlar (ör. 'bilgim dışında' -> {'yetkisiz'})."""
    return {
        concept
        for concept, phrases in CONCEPT_SYNONYMS.items()
        if any(contains_phrase(text, phrase) for phrase in phrases)
    }


def distinct_stems(terms: Iterable[str]) -> Set[str]:
    """Aynı köke inen terimleri tek temsilcide toplar (raporları ~ raporlarının -> 1).

    Yalnızca sayma amaçlıdır; temsilciler arası küme farkı almak yerine
    `terms_match` / `matches_any` kullanılmalıdır.
    """
    representatives: list = []
    for term in sorted(set(terms), key=lambda t: (len(t), t)):
        if not any(terms_match(term, rep) for rep in representatives):
            representatives.append(term)
    return set(representatives)


def matches_any(term: str, terms: Iterable[str]) -> bool:
    return any(terms_match(term, other) for other in terms)


def _phrase_tokens(text: str) -> list:
    """Eşdizim araması için kelime dizisi: 2 harfli kelimeler ("iç", "dış") de tutulur."""
    return tokenize(text, min_length=2)


def _token_match(a: str, b: str) -> bool:
    if len(a) < MIN_PREFIX_MATCH or len(b) < MIN_PREFIX_MATCH:
        return a == b
    return terms_match(a, b)


def statement_bigrams(text: str) -> list:
    """Cümledeki bitişik kelime çiftleri ("iç denetim", "denetim sistemi", "hesap kesim").

    Tek tek her belgede geçen kelimeler bitişik geçtiklerinde belirli bir kavramı adlandırır.
    Durak kelime veya sayı içeren çiftler ile yalnızca genel/zayıf bankacılık kelimelerinden
    oluşan çiftler ("kredi kartı") alınmaz: bunlar korpusun her yerinde bitişik de geçer."""
    tokens = _phrase_tokens(text)
    out = []
    for a, b in zip(tokens, tokens[1:]):
        if a in STOP_WORDS or b in STOP_WORDS or a.isdigit() or b.isdigit():
            continue
        if all(is_generic_term(t) or is_weak_term(t) for t in (a, b)):
            continue
        if (a, b) not in out:
            out.append((a, b))
    return out


def matched_bigrams(bigrams: Iterable[tuple], text: str) -> list:
    """bigrams içinden metinde (kök düzeyinde) bitişik geçenler."""
    tokens = _phrase_tokens(text)
    pairs = list(zip(tokens, tokens[1:]))
    return [bg for bg in bigrams if any(_token_match(bg[0], x) and _token_match(bg[1], y) for x, y in pairs)]


def contains_phrase(text: str, phrase: str) -> bool:
    """Türkçe ekleri tolere ederek ifade araması: her kelime başında eşleşir, sonu serbesttir.

    Örn. "kredi" -> "kredisi", "erken ödeme" -> "erken ödemesi".
    """
    words = tokenize(phrase, min_length=1)
    if not words:
        return False
    pattern = r"\b" + r"\w*\s+".join(re.escape(w) for w in words)
    return re.search(pattern, turkish_lower(text)) is not None


__all__ = [
    "STOP_WORDS",
    "GENERIC_DOMAIN_WORDS",
    "GENERIC_DOMAIN_STEMS",
    "is_generic_term",
    "WEAK_DOMAIN_WORDS",
    "is_weak_term",
    "turkish_lower",
    "tokenize",
    "stem",
    "content_terms",
    "terms_match",
    "matched_terms",
    "CONCEPT_SYNONYMS",
    "concept_terms",
    "distinct_stems",
    "matches_any",
    "contains_phrase",
    "statement_bigrams",
    "matched_bigrams",
]
