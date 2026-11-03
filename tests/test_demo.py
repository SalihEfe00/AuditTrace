# -*- coding: utf-8 -*-
"""Kurulumsuz demo (python app.py --demo): örnek kredi dosyasında arayüzdeki sayılar ve dört bulgu."""
import subprocess
import sys
from pathlib import Path

from mufettis.demo import run_demo

ROOT = Path(__file__).resolve().parent.parent


def test_demo_matches_the_sample_file_on_screen():
    lines = []
    findings = run_demo(out=lines.append)
    text = "\n".join(lines)
    assert "15 kontrolden 4 bulgu | 1 karar verilemedi | 3 uygun | 7 belge yok" in text
    assert len(findings) == 4
    assert {f.rule_id for f in findings} == {
        "ONAY-teminat-once-ipotek", "ONAY-kullandirim-tutari", "MASAK-7-imzalayan-yetkili", "MASAK-7-musterek-imza"}
    assert "satır 4: Kullandırılan Tutar: 1.200.000 TL" in text  # kanıt belgedeki satırıyla


def test_app_demo_flag_runs():
    proc = subprocess.run([sys.executable, str(ROOT / "app.py"), "--demo"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert "15 kontrolden 4 bulgu" in proc.stdout
