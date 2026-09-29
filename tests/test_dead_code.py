# -*- coding: utf-8 -*-
"""Kalıcı ölü kod koruması (M1.6): yeni kullanılmayan kod birikirse test başarısız olur.

- ruff (ruff.toml): kullanılmayan içe aktarım / değişken, gölgelenen tanım, tanımsız ad — tüm depo.
- vulture: kullanılmayan fonksiyon, sınıf, yöntem, değişken — uygulama kodu, betikler; testler kullanım kaynağı.
  Yanlış alarmlar gerekçesiyle vulture_allowlist.py'de.
Araçlar geliştirme bağımlılığıdır (requirements-dev.txt); kurulu değilse test atlanır.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VULTURE_PATHS = ["mufettis", "repair", "app.py", "scripts", "conftest.py", "tests"]
VULTURE_MIN_CONFIDENCE = "60"
# Test dosyalarındaki bulgular denetlenmez (sahte nesne imzaları, kullanılmayan kurulum değişkenleri).
TEST_FILE = re.compile(r"^(tests[\\/]|mufettis[\\/]tests[\\/]test_)")


def _run(module: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


class TestDeadCode(unittest.TestCase):
    def _require(self, module: str) -> None:
        if importlib.util.find_spec(module) is None:
            self.skipTest(f"{module} kurulu değil (pip install -r requirements-dev.txt)")

    def test_ruff_clean(self):
        self._require("ruff")
        proc = _run("ruff", "check", "--output-format", "concise", ".")
        self.assertEqual(proc.returncode, 0, "ruff bulguları:\n" + proc.stdout + proc.stderr)

    def test_vulture_clean(self):
        self._require("vulture")
        proc = _run("vulture", *VULTURE_PATHS, "vulture_allowlist.py",
                    "--min-confidence", VULTURE_MIN_CONFIDENCE)
        self.assertIn(proc.returncode, (0, 3), proc.stderr)  # 3: kullanılmayan kod bulundu
        findings = [line for line in proc.stdout.splitlines() if line.strip() and not TEST_FILE.match(line)]
        self.assertEqual(findings, [], "kullanılmayan kod (siliniz ya da gerekçesiyle "
                         "vulture_allowlist.py'ye ekleyiniz):\n" + "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
