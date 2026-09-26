# -*- coding: utf-8 -*-
"""scripts/recover_legacy_laws.py — eski parçalardan metin yeniden oluşturma."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "recover_legacy_laws.py"
_spec = importlib.util.spec_from_file_location("recover_legacy_laws", _SCRIPT)
recover = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recover)


class TestReconstructText(unittest.TestCase):
    def test_orders_by_page_then_subchunk_numerically(self):
        rows = [
            ("abc-10-1", {}, "onuncu sayfa"),
            ("abc-2-2", {}, " devamı."),
            ("abc-1-1", {}, "BANKACILIK KANUNU"),
            ("abc-2-1", {}, "Madde 3 — tanımlar"),
        ]
        self.assertEqual(
            recover.reconstruct_text(rows),
            "BANKACILIK KANUNU\nMadde 3 — tanımlar devamı.\nonuncu sayfa",
        )

    def test_chunk_order_parses_numeric_parts(self):
        self.assertEqual(recover._chunk_order("707f3de15e7b7a0b-12-3"), (12, 3))
        self.assertLess(recover._chunk_order("h-2-1"), recover._chunk_order("h-10-1"))


if __name__ == "__main__":
    unittest.main()
