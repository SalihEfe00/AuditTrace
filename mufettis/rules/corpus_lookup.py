# -*- coding: utf-8 -*-
"""Katalog yükleyicisine korpustaki madde metnini veren bağlantı (M9.5).

`load_catalog(article_text=article_text_from_rag(rag))`: her kuralın ve istisnanın alıntısı güncel korpustaki madde
metninde aranır; madde korpusta yoksa ya da alıntı geçmiyorsa kural yüklenmez. Dayanak `law` kanun numarasıysa
(ör. "6098") parçalar `law_number` ile, mevzuat koduysa (ör. "7.5.20767" yönetmelik) `legislation_code` ile bulunur.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Optional, Tuple

from mufettis.rag.engine import current_where


def article_text_from_rag(rag) -> Callable[[str, str], Optional[str]]:
    cache: Dict[Tuple[str, str], Optional[str]] = {}

    def lookup(law: str, article: str) -> Optional[str]:
        key = (str(law), str(article))
        if key in cache:
            return cache[key]
        if re.fullmatch(r"\d+", key[0]):
            rows = rag._article_chunks(key[0], ("", key[1]))
            docs = [r["document"] for r in rows]
        else:
            try:
                got = rag.collection.get(where=current_where({"legislation_code": key[0]}, {"article_no": key[1]}),
                                         include=["documents"])
                docs = list(got.get("documents") or [])
            except Exception:
                docs = []
        cache[key] = "\n".join(docs) if docs else None
        return cache[key]

    return lookup


__all__ = ["article_text_from_rag"]
