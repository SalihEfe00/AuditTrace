# -*- coding: utf-8 -*-
"""İnce ayar betiklerinin ortak parçaları: veri yolları ve ölçüm dayanaklarının dışlanması."""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mufettis.analysis.benchmark import load_cases  # noqa: E402

DATA_DIR = ROOT / "benchmarks" / "reranker_finetune"
CLAIMS_FILE = DATA_DIR / "claims.jsonl"
REVIEW_FILE = DATA_DIR / "claims_review.jsonl"
# Sentetik iddiaların yazıldığı kanunlar (birim anahtarı: "kanun|önek|madde").
LAWS = {"5411", "5464", "6361"}


def all_cases():
    """benchmarks/ altındaki TÜM vaka dosyaları (mühürlü küme dahil)."""
    cases = []
    for path in sorted(glob.glob(str(ROOT / "benchmarks" / "*cases*.json"))):
        cases += load_cases(Path(path))
    return cases


def eval_specs():
    """Ölçümde beklenen dayanaklar. Eğitimde ne pozitif ne negatif olarak görünürler: negatif olarak
    kullanılırlarsa model tam ölçülen maddeleri aşağı itmeyi öğrenir (ilk pilotta oldu); pozitif olarak
    kullanılırlarsa ölçüm ezberi ölçer."""
    return [spec for case in all_cases() for spec in case.expect]


def unit_key(meta) -> str:
    law = str(meta.get("law_number") or "")
    return f"{law}|{str(meta.get('article_prefix') or '').upper()[:1]}|{meta.get('article_no')}"


def load_claims(drop_reviewed: bool = False):
    """[(birim anahtarı, iddia)]. drop_reviewed: incelemede ZAYIF / YANLIŞ işaretlenenler atılır
    (claims_review.jsonl, "i" iddianın o anahtardaki 0 tabanlı sırası)."""
    flagged = set()
    if drop_reviewed:
        for line in REVIEW_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                flagged.add((r["key"], r["i"]))
    claims, seen = [], {}
    for line in CLAIMS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        for c in r.get("claims", []):
            i = seen.get(r["key"], 0)
            seen[r["key"]] = i + 1
            if (r["key"], i) not in flagged:
                claims.append((r["key"], c))
    return claims
