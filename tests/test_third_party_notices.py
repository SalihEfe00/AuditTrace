# -*- coding: utf-8 -*-
"""M17.7b: lisans denetimi ve bildirim; Tesseract'tan yalnız OCR'ın yüklediği DLL'ler pakete girer."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import notices  # noqa: E402

spec = importlib.util.spec_from_file_location("audittrace_build_notices", ROOT / "packaging" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
TESSERACT = Path(r"C:\mufettis_ai")


class LicenseCheckTests(unittest.TestCase):
    def test_free_permissive_licenses_pass(self):
        for label in ("MIT", "BSD-3-Clause", "Apache-2.0", "PSF-2.0", "MPL-2.0", "Apache-2.0 OR BSD-3-Clause",
                      "LGPL-2.1-or-later", "GPLv2-or-later with a special exception", "MIT-0", "0BSD"):
            self.assertIsNone(notices.problem(label), label)

    def test_paid_or_restricted_licenses_stop_the_build(self):
        for label in ("AGPL-3.0", "GNU Affero General Public License v3", "SSPL-1.0", "BUSL-1.1",
                      "Proprietary", "CC-BY-NC-4.0 non-commercial", "GPL-3.0"):
            self.assertIsNotNone(notices.problem(label), label)
        self.assertIsNotNone(notices.problem("Custom", ["This software may be used for non-commercial purposes."]))

    def test_every_bundled_tesseract_dll_is_listed(self):
        listed = {n.strip().lower() for files, _, _ in notices.TESSERACT_COMPONENTS for n in files.split(",")}
        if not (TESSERACT / "tesseract.exe").is_file():
            self.skipTest("Tesseract kaynak klasörü yok")
        needed = {n.lower() for n in build.dll_closure(TESSERACT / "tesseract.exe")}
        self.assertTrue(needed)
        self.assertLessEqual(needed, listed)
        # eğitim araçlarının LGPL ağırlıklı kütüphaneleri OCR için yüklenmez
        for name in ("libglib-2.0-0.dll", "libpango-1.0-0.dll", "libcairo-2.dll", "libicuuc78.dll"):
            self.assertNotIn(name, needed)

    def test_installer_carries_the_notice(self):
        iss = (ROOT / "packaging" / "audittrace.iss").read_text(encoding="utf-8")
        self.assertIn(r'Source: "{#Root}\dist\AuditTrace\*"', iss)  # dosya paket kökünde
        source = (ROOT / "packaging" / "build.py").read_text(encoding="utf-8")
        self.assertIn("third_party_notices()", source)


if __name__ == "__main__":
    unittest.main()
