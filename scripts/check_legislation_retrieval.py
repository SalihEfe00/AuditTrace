# -*- coding: utf-8 -*-
"""Listedeki yüklenmiş her belgenin aramada bulunabildiğini ölçer (M6.6 / M6.7 ölçütü).

Belge başına iki sorgu: (1) resmî adı, (2) belgeye özgü bir madde cümlesi (metinden; bkz.
distinctive_sentence). Belge ilk k sonuç içinde (parçanın `legislation_code` alanıyla) çıkmalı. Salt okur; veri dizinine yazmaz.

Kullanım:
    MUFETTIS_DATA_DIR=<kopya> python scripts/check_legislation_retrieval.py --priority 1
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OMP_NUM_THREADS", "2")

from mufettis.analysis.turkish_text import turkish_lower  # noqa: E402
from mufettis.corpus.legislation_sources import load_sources, select_sources  # noqa: E402

TOP_K = 10


def distinctive_sentence(text: str) -> str:
    """Belgeye özgü bir madde cümlesi: tanımlar ve dayanak sonrası (MADDE 4+) ilk uzun cümle; başka mevzuata
    atıf ("N sayılı") ya da değişiklik notu içermez. Atıflı cümle motoru atıf yapılan kanuna yönlendirir;
    bu, aramanın değil sorgunun seçimidir."""
    flat = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?i)\bmadde\s*(\d+)\s*[-–—:]", flat)
    articles = [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    ordered = [body for no, body in articles if no >= 4] + [body for no, body in articles if no < 4] or [flat]
    for body in ordered:
        for sentence in re.split(r"(?<=[.;:])\s+", body):
            sentence = re.sub(r"^\(\d+\)\s*", "", sentence.strip())
            if 80 <= len(sentence) <= 400 and not re.search(r"(?i)sayılı|değişik|mülga|ek:|\(\d+\)", sentence):
                return sentence
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--priority", type=int, default=1, choices=(1, 2))
    args = ap.parse_args(argv)
    if not os.environ.get("MUFETTIS_DATA_DIR"):
        print("MUFETTIS_DATA_DIR bir veri kopyasını göstermeli.")
        return 2

    from mufettis.rag.engine import LocalRAG
    from mufettis.rag.index import IngestIndex
    from mufettis.rag.maintenance import load_source_cache

    rag = LocalRAG(allow_download=False)
    by_code = {}
    for file_hash, rec in IngestIndex().iter_records():
        if rec.get("legislation_code") and rec.get("status") == "active":
            by_code[rec["legislation_code"]] = file_hash
    sources = select_sources(load_sources(), args.priority)
    found = {"title": 0, "content": 0}
    missing = []
    for s in sources:
        file_hash = by_code.get(s.key)
        if not file_hash:
            missing.append(s.key)
            print(f"YÜKLÜ DEĞİL  {s.key}")
            continue
        probes = {"title": turkish_lower(s.title), "content": distinctive_sentence(load_source_cache(file_hash))}
        marks = []
        for name, query in probes.items():
            results = rag.query(query, k=TOP_K) if query else []
            ranks = [i for i, r in enumerate(results, 1)
                     if (r.get("metadata") or {}).get("legislation_code") == s.key]
            if ranks:
                found[name] += 1
            marks.append(f"{name}:{ranks[0] if ranks else '-'}")
        print(f"{s.key:<16} {'  '.join(marks):<22} {s.title[:70]}", flush=True)
    n = len(sources) - len(missing)
    print(f"özet: ad sorgusu {found['title']}/{n}, içerik sorgusu {found['content']}/{n}, yüklü olmayan {len(missing)}")
    return 0 if found["title"] == n and found["content"] == n and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
