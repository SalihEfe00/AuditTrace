# -*- coding: utf-8 -*-
"""Gerçek korpus doğruluk tabanı (benchmarks/baseline.json) korunuyor mu? İSTEĞE BAĞLI.

Birkaç dakika sürer ve bir korpus kopyası ile yeniden sıralayıcı modeli gerektirir; bu yüzden
yalnızca MUFETTIS_BENCHMARK_DATA_DIR verildiğinde çalışır:

    MUFETTIS_BENCHMARK_DATA_DIR=<data kopyası> python -m pytest tests/test_accuracy_baseline.py

Kopya olmalıdır: ölçüm BM25 indeksini ve terim istatistiğini o dizine yazabilir. Taban bilinçli
olarak güncellenir: scripts/eval_accuracy.py --split all --write-baseline "<not>".
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = os.environ.get("MUFETTIS_BENCHMARK_DATA_DIR", "")


@unittest.skipUnless(DATA_DIR, "MUFETTIS_BENCHMARK_DATA_DIR verilmedi (isteğe bağlı doğruluk tabanı testi)")
class TestAccuracyBaseline(unittest.TestCase):
    def test_no_metric_is_worse_than_baseline(self):
        env = dict(os.environ, MUFETTIS_DATA_DIR=DATA_DIR, HF_HUB_OFFLINE="1", OMP_NUM_THREADS="2",
                   PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "eval_accuracy.py"), "--split", "all", "--check-baseline"],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=3600,
        )
        tail = "\n".join(proc.stdout.splitlines()[-12:])
        self.assertEqual(proc.returncode, 0, f"{tail}\n{proc.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main()
