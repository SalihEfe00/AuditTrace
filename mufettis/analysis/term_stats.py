# -*- coding: utf-8 -*-
"""
Müfettiş AI - Analysis Intelligence
Korpus terim istatistiği: hangi kökler "zayıf" (ayırt edici olmayan) sayılmalı?

Elle yazılmış WEAK_DOMAIN_WORDS listesi korpusla uyuşmuyordu: "kart" aktif belgelerin
yalnızca ~%8'inde geçtiği halde zayıf sayılıyor (bu yüzden "kredi kartı aidat ücreti"
iddiası Kart Kanunu'nda bile kanıt bulamıyordu); "düzen" (%94), "karar" (%77) gibi her
yerde geçen kökler ise güçlü sayılıyordu. Burada zayıflık, kökün aktif belgelerin ne
kadarında geçtiğine (belge frekansı) göre belirlenir. Parça frekansı kullanılmaz: tek bir
hesap planı binlerce parça üretip oranları çarpıtır.

İstatistik DATA_DIR/term_stats.json dosyasında, aktif belge kümesinin imzasıyla saklanır;
korpus değişince yeniden hesaplanır. Yeterli belge yoksa None döner ve çağıran sabit
listeye düşer.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, Optional

from mufettis.analysis.turkish_text import PRIVATIVE_SUFFIX_RE, MIN_PREFIX_MATCH, STEM_LENGTH, STOP_WORDS, is_weak_term, stem, tokenize
from mufettis.common.utils import now_iso
from mufettis.config import CHUNK_SCHEMA_VERSION, DATA_DIR
from mufettis.corpus import INACTIVE_STATUSES
from mufettis.rag.paging import iter_pages

logger = logging.getLogger(__name__)

TERM_STATS_FILE = DATA_DIR / "term_stats.json"
TERM_STATS_VERSION = 3
# Aktif belgelerin bu oranından fazlasında geçen kök tek başına alaka kanıtı değildir.
WEAK_DOC_RATIO = 0.50
# Bundan az belgeyle oranlar anlamsızdır; sabit listeye düşülür.
MIN_STATS_DOCS = 20


def term_key(token: str) -> str:
    """Belge frekansı anahtarı; terms_match ile aynı eşleşme kuralı:
    5+ harfli kökler ilk STEM_LENGTH harfle, 4 harfli kökler ("kart", "faiz") o 4 harfle
    başlayan tüm çekimlerle ("kartı", "kartın") eşleşir. 3 harfliler yalnızca kendisiyle.
    Yoksunluk ekli kelimeler ("yetkisiz", "bağımsız") kökten ayrı sayılır; terms_match de
    onları kökle eşleştirmez ("yetki" yaygın diye "yetkisiz" zayıf sayılmamalı)."""
    privative = PRIVATIVE_SUFFIX_RE.search(token, MIN_PREFIX_MATCH)
    if privative:
        return token[:privative.end()]
    s = stem(token)
    if len(s) >= STEM_LENGTH:
        return s[:STEM_LENGTH]
    if len(s) == MIN_PREFIX_MATCH:
        return f"{s}*"
    return s


def document_keys(text: str) -> FrozenSet[str]:
    """Bir metnin içerdiği anahtarlar: 4 harfli önek anahtarı ("kart*"), o önekle başlayan
    her kelimeden (kart, kartı, kartları) üretilir."""
    keys = set()
    for token in tokenize(text):
        if token in STOP_WORDS or token.isdigit():
            continue
        keys.add(term_key(token))
        s = stem(token)
        if len(s) > MIN_PREFIX_MATCH:
            keys.add(f"{s[:MIN_PREFIX_MATCH]}*")
    return frozenset(keys)


def corpus_signature(index) -> str:
    """Aktif belge kümesinin imzası (hash listesi); kümeye belge girip çıkınca değişir."""
    items = (getattr(index, "data", None) or {}).get("indexed_items", {})
    active = sorted(h for h, r in items.items() if str((r or {}).get("status", "")) not in INACTIVE_STATUSES)
    return hashlib.sha256("\n".join(active).encode("utf-8")).hexdigest()


def build_term_stats(rag, index) -> Dict[str, Any]:
    """Aktif belgelerin güncel şema parçalarından kök başına belge frekansı."""
    from mufettis.legal.parser import _chunk_is_retrievable

    items = index.data.get("indexed_items", {})
    active = {h for h, r in items.items() if str((r or {}).get("status", "")) not in INACTIVE_STATUSES}
    per_doc: Dict[str, set] = {}
    # Sayfalı okuma: tüm koleksiyon tek seferde belleğe alınmaz (düşük RAM'li laptoplar).
    for _ids, docs, metas in iter_pages(rag.collection, where={"chunk_schema_version": CHUNK_SCHEMA_VERSION}):
        for doc, meta in zip(docs, metas):
            file_hash = str(meta.get("file_hash", ""))
            if file_hash not in active or not _chunk_is_retrievable(meta, doc):
                continue
            per_doc.setdefault(file_hash, set()).update(document_keys(doc or ""))
    doc_df: Dict[str, int] = {}
    for keys in per_doc.values():
        for key in keys:
            doc_df[key] = doc_df.get(key, 0) + 1
    return {
        "version": TERM_STATS_VERSION,
        "signature": corpus_signature(index),
        "built_at": now_iso(),
        "doc_count": len(per_doc),
        "doc_df": doc_df,
    }


def load_term_stats(path: Path = TERM_STATS_FILE) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        stats = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Terim istatistiği okunamadı (%s): %s", path, exc)
        return None
    return stats if stats.get("version") == TERM_STATS_VERSION else None


def save_term_stats(stats: Dict[str, Any], path: Path = TERM_STATS_FILE) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def ensure_term_stats(rag, index, path: Path = TERM_STATS_FILE) -> Optional[Dict[str, Any]]:
    """Güncel istatistiği döndürür; korpus değiştiyse yeniden hesaplayıp kaydeder."""
    stats = load_term_stats(path)
    if stats and stats.get("signature") == corpus_signature(index):
        return stats
    stats = build_term_stats(rag, index)
    save_term_stats(stats, path)
    return stats


def corpus_weak_keys(stats: Optional[Dict[str, Any]], ratio: float = WEAK_DOC_RATIO) -> Optional[FrozenSet[str]]:
    if not stats or int(stats.get("doc_count", 0) or 0) < MIN_STATS_DOCS:
        return None
    limit = ratio * int(stats["doc_count"])
    return frozenset(k for k, n in (stats.get("doc_df") or {}).items() if int(n) > limit)


def weak_term_predicate(stats: Optional[Dict[str, Any]], ratio: float = WEAK_DOC_RATIO) -> Callable[[str], bool]:
    """Zayıf terim yüklemi: sabit WEAK_DOMAIN_WORDS listesi + korpusta çok yaygın kökler.

    Birleşim kullanılır: korpus yalnızca zayıf terim EKLER. Sabit listedeki terimler
    ("ödeme", "faiz") geçmişte gerçek yanlış eşleşmelere yol açtığı için korunur; korpus ise
    listenin kaçırdığı her yerde geçen kökleri ("veril", "düzen", "karar") yakalar.
    """
    keys = corpus_weak_keys(stats, ratio=ratio)
    if keys is None:
        return is_weak_term
    return lambda token: is_weak_term(token) or term_key(token) in keys


def describe(stats: Optional[Dict[str, Any]], terms: Iterable[str]) -> Dict[str, float]:
    """Tanı amaçlı: verilen terimlerin aktif belge oranı."""
    if not stats:
        return {}
    total = max(1, int(stats.get("doc_count", 0) or 0))
    df = stats.get("doc_df") or {}
    return {t: round(int(df.get(term_key(t), 0)) / total, 3) for t in terms}


__all__ = [
    "TERM_STATS_FILE",
    "WEAK_DOC_RATIO",
    "MIN_STATS_DOCS",
    "term_key",
    "document_keys",
    "corpus_signature",
    "build_term_stats",
    "load_term_stats",
    "save_term_stats",
    "ensure_term_stats",
    "corpus_weak_keys",
    "weak_term_predicate",
    "describe",
]
