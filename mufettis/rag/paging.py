# -*- coding: utf-8 -*-
"""Koleksiyonu sayfa sayfa okuma.

Chroma `get` çağrısı tüm sonucu tek seferde belleğe alır; ~26 bin parçalık okuma, kalıcı veri 1 GB
iken sürecin tepe belleğini 2.8 GB'a çıkarıyordu. Mevzuatın tamamı yüklendiğinde bu 8 GB RAM'li
bir ofis laptopunda çalışmayı imkânsız kılar. Tüm-koleksiyon taramaları bu yardımcıyla yapılır:
tepe bellek sayfa boyutuyla sınırlı kalır.
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# Parça metinleri birkaç KB'dir; 1000 parçalık sayfa birkaç MB tutar.
READ_PAGE_SIZE = 1000

Page = Tuple[List[str], List[Optional[str]], List[Dict[str, Any]]]


def iter_pages(
    collection: Any,
    where: Optional[Dict[str, Any]] = None,
    include: Sequence[str] = ("documents", "metadatas"),
    page_size: int = READ_PAGE_SIZE,
) -> Iterator[Page]:
    """(ids, documents, metadatas) sayfaları. İstenmeyen alanlar boş liste yerine None listesidir."""
    offset = 0
    while True:
        got = collection.get(where=where, include=list(include), limit=page_size, offset=offset)
        ids = list(got.get("ids") or [])
        if not ids:
            return
        docs = list(got.get("documents") or [None] * len(ids))
        metas = [m or {} for m in (got.get("metadatas") or [None] * len(ids))]
        yield ids, docs, metas
        if len(ids) < page_size:
            return
        offset += len(ids)


__all__ = ["READ_PAGE_SIZE", "iter_pages"]
