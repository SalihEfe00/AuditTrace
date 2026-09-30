# -*- coding: utf-8 -*-
"""Sentetik örnekleri belge vakalarına çevirir (M4.3; mufettis/analysis/synthetic_import.py).

    python scripts/import_synthetic.py            # incoming/ → benchmarks/documents/synthetic_cases.json
    python scripts/import_synthetic.py --sealed   # sealed/   → benchmarks/documents/sealed_cases.json

Çıktı her çalıştırmada aynıdır (dosyalar sıralı, bölüm dosya yolundan). Reddedilen dosyalar ve uyarılar
sebebiyle listelenir; mühürlü örneklerde yalnızca sayılar basılır (içerik ve uyarı metni görünmez).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mufettis.analysis.document_benchmark import CASES_DIR, write_cases  # noqa: E402
from mufettis.analysis.synthetic_import import convert_tree  # noqa: E402

SYNTHETIC_DIR = ROOT / "benchmarks" / "synthetic"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sealed", action="store_true", help="mühürlü örnekleri çevir (yalnızca son doğrulamada)")
    parser.add_argument("--source", type=Path, help="örnek klasörü (varsayılan synthetic/incoming ya da sealed)")
    parser.add_argument("--out", type=Path, help="vaka dosyası (varsayılan benchmarks/documents/...)")
    args = parser.parse_args()

    source = args.source or SYNTHETIC_DIR / ("sealed" if args.sealed else "incoming")
    out = args.out or CASES_DIR / ("sealed_cases.json" if args.sealed else "synthetic_cases.json")
    if not source.is_dir():
        print(f"Örnek klasörü yok: {source}")
        return 1
    cases, rejected, warnings = convert_tree(source, sealed=args.sealed)
    write_cases(out, cases, note=f"scripts/import_synthetic.py ile {source.relative_to(ROOT) if source.is_relative_to(ROOT) else source} klasöründen üretildi; elle düzenlemeyin")
    print(f"{len(cases)} vaka yazıldı: {out}")
    print("  bölüm: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(c.split for c in cases).items())))
    print("  model: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(c.source['model'] for c in cases).items())))
    print(f"{len(rejected)} dosya reddedildi, {len(warnings)} dosyada uyarı.")
    if not args.sealed:
        for rel, reason in rejected.items():
            print(f"  RED   {rel}: {reason}")
        for rel, items in warnings.items():
            for item in items:
                print(f"  UYARI {rel}: {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
