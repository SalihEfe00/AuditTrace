# -*- coding: utf-8 -*-
"""M6.10 / M6.15: veri kopyasında doğrulanmış indirmeler üretime kopyalanır (indirme tekrarlanmaz)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from mufettis.common.utils import sha256_bytes

_SPEC = importlib.util.spec_from_file_location(
    "apply_legislation_corpus", Path(__file__).resolve().parent.parent / "scripts" / "apply_legislation_corpus.py")
apply_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(apply_mod)


class TestCopyVerifiedDownloads(unittest.TestCase):
    def test_only_verified_files_are_copied_and_existing_records_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            src.mkdir()
            dst.mkdir()
            good, bad = b"%PDF-1.7 iyi", b"%PDF-1.7 bozuk"
            (src / "a.pdf").write_bytes(good)
            (src / "b.pdf").write_bytes(bad)
            records = {
                "a": {"status": "downloaded", "path": "a.pdf", "sha256": sha256_bytes(good)},
                "b": {"status": "downloaded", "path": "b.pdf", "sha256": "baska-ozet"},     # dosya değişmiş
                "c": {"status": "rejected", "path": "", "sha256": ""},                       # doğrulanmamış
                "d": {"status": "cached", "path": "d.pdf", "sha256": "x"},                   # dosya yok
                "e": {"status": "downloaded", "path": "a.pdf", "sha256": sha256_bytes(good)},  # hedefte var
            }
            (src / "downloads.json").write_text(json.dumps(records), encoding="utf-8")
            (dst / "downloads.json").write_text(json.dumps({"e": {"status": "cached", "path": "e.pdf"}}),
                                                encoding="utf-8")
            self.assertEqual(apply_mod.copy_verified_downloads(src, dst), 1)
            out = json.loads((dst / "downloads.json").read_text(encoding="utf-8"))
            self.assertEqual(set(out), {"a", "e"})
            self.assertEqual(out["e"]["path"], "e.pdf")
            self.assertEqual((dst / "a.pdf").read_bytes(), good)
            self.assertFalse((dst / "b.pdf").exists())


if __name__ == "__main__":
    unittest.main()
