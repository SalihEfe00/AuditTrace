# -*- coding: utf-8 -*-
"""
repair.extractor.extract_candidate — HTML (raw_bytes) yolu regresyonu.

Bu yol önceden split_legal_chunks'a max_chars yerine meta sözlüğü geçiriyor ve
ExtractionCandidate'te bulunmayan `chunks` alanını kullanıyordu; her çağrı
EXTRACTION_FAILED ile sonuçlanıyordu.
"""
from __future__ import annotations

import hashlib
import unittest

from mufettis.config import CHUNK_SCHEMA_VERSION
from repair.extractor import extract_candidate

HTML = (
    "<html><body><h1>Yönetmelik</h1>"
    "<p>MADDE 1 - Amaç</p><p>(1) Bu yönetmeliğin amacı bankaların iç sistemlerini düzenlemektir.</p>"
    "<p>MADDE 2 - Kapsam</p><p>(1) Bu yönetmelik tüm bankaları kapsar.</p>"
    "</body></html>"
).encode("utf-8")


class TestExtractCandidateHtml(unittest.TestCase):
    def test_html_candidate_succeeds_with_enriched_legal_chunks(self):
        source_hash = hashlib.sha256(HTML).hexdigest()
        candidate = extract_candidate(
            raw_bytes=HTML,
            expected_hash=source_hash,
            document_id="doc_html",
            meta={"title": "Test Yönetmeliği", "source_url": "https://ornek/y.html", "content_type": "text/html"},
        )
        self.assertEqual(candidate.status, "SUCCESS", candidate.metadata.get("error"))
        self.assertGreaterEqual(len(candidate.legal_chunks), 1)
        for chunk in candidate.legal_chunks:
            self.assertEqual(chunk["source_hash"], source_hash)
            self.assertEqual(chunk["title"], "Test Yönetmeliği")
            self.assertEqual(chunk["chunk_schema_version"], str(CHUNK_SCHEMA_VERSION))
            self.assertEqual(chunk["status"], "active")
        self.assertIn("iç sistemlerini", candidate.raw_text)


if __name__ == "__main__":
    unittest.main()
