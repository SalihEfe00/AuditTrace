# -*- coding: utf-8 -*-
"""Madde numarasıyla doğrudan erişim (M6.14): sorgudaki madde çözülür, o maddenin parçaları sırayla gelir."""
from __future__ import annotations

import unittest

from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.extraction.text_repair import extract_query_article
from mufettis.rag.engine import LocalRAG


class TestQueryArticle(unittest.TestCase):
    def test_forms(self):
        cases = {
            "5411 sayılı Kanun 54 üncü madde": ("", "54"),
            "İcra ve İflas Kanunu md. 89 haciz ihbarnamesi": ("", "89"),
            "Türk Borçlar Kanunu m. 584 eş rızası": ("", "584"),
            "5464 sayılı Kanun madde 10": ("", "10"),
            "Türk Borçlar Kanunu 584. madde": ("", "584"),
            "5411 sayılı Kanunun 73'üncü maddesi": ("", "73"),
            "5464 sayılı Kanun geçici madde 5": ("geçici", "5"),
            "GEÇİCİ MADDE 3": ("geçici", "3"),
            "ek madde 2": ("ek", "2"),
        }
        for query, expected in cases.items():
            self.assertEqual(extract_query_article(query), expected, query)
        self.assertIsNone(extract_query_article("5411 sayılı Bankacılık Kanunu kredi sınırları"))


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    def get(self, where=None, include=None, **_):
        clauses = where["$and"]
        wanted = {k: v for c in clauses for k, v in c.items()}

        def ok(m, k, v):
            return str(m.get(k)) not in v["$nin"] if isinstance(v, dict) else str(m.get(k)) == str(v)

        hits = [(d, m) for d, m in self.rows if all(ok(m, k, v) for k, v in wanted.items())]
        return {"documents": [d for d, _ in hits], "metadatas": [m for _, m in hits]}


def _meta(article, paragraph, prefix="", chunk=0):
    return {"chunk_schema_version": str(CHUNK_SCHEMA_VERSION), "law_number": "5411", "article_no": article,
            "article_prefix": prefix, "paragraph_no": paragraph, "chunk_no": chunk, "document_indexable": "True",
            "status": "active", "file_hash": "h"}


class TestArticleChunks(unittest.TestCase):
    def test_article_chunks_in_paragraph_order_and_prefix_separated(self):
        text = "x" * 300
        rag = LocalRAG.__new__(LocalRAG)
        rag.collection = _Collection([(text, _meta("54", "2", chunk=2)), (text, _meta("54", "1", chunk=1)),
                                      (text, _meta("54", "1", prefix="GEÇİCİ", chunk=9)),
                                      (text, {**_meta("54", "3", chunk=3), "status": "excluded"})])
        main = rag._article_chunks("5411", ("", "54"))
        self.assertEqual([r["metadata"]["paragraph_no"] for r in main], ["1", "2"])
        self.assertTrue(all(r["_article_match"] for r in main))
        temporary = rag._article_chunks("5411", ("geçici", "54"))
        self.assertEqual([r["metadata"]["chunk_no"] for r in temporary], [9])
        self.assertEqual(rag._article_chunks("5411", ("", "999")), [])


if __name__ == "__main__":
    unittest.main()
