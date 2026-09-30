# -*- coding: utf-8 -*-
"""Korpusa özgü sıralayıcı eşiği (M6.1): veri dizinindeki kalibrasyon dosyası modelin varsayılanının önüne geçer."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mufettis.rag.reranker import CALIBRATION_FILE, corpus_min_score


class TestCorpusCalibration(unittest.TestCase):
    def _with_file(self, content):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        if content is not None:
            (Path(tmp.name) / CALIBRATION_FILE).write_text(content, encoding="utf-8")
        patcher = mock.patch("mufettis.config.DATA_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_calibration_overrides_model_default(self):
        self._with_file(json.dumps({"mufettis-reranker-ft1": {"min_score": -2.95, "note": "M6.1"}}))
        self.assertEqual(corpus_min_score("mufettis-reranker-ft1", -4.48), -2.95)
        self.assertEqual(corpus_min_score("baska-model", -2.15), -2.15)

    def test_missing_or_broken_file_keeps_default(self):
        self._with_file(None)
        self.assertEqual(corpus_min_score("mufettis-reranker-ft1", -4.48), -4.48)
        self._with_file("{bozuk")
        with self.assertLogs("mufettis.rag.reranker", level="WARNING"):
            self.assertEqual(corpus_min_score("mufettis-reranker-ft1", -4.48), -4.48)


if __name__ == "__main__":
    unittest.main()
