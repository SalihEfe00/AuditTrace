# -*- coding: utf-8 -*-
"""Tüm-koleksiyon okumaları sayfalıdır: hiçbir çağrı sayfa boyutundan fazla parça istemez."""
from __future__ import annotations

import unittest

from mufettis.rag.paging import iter_pages


class _Collection:
    def __init__(self, n):
        self.rows = [(f"c{i}", f"metin {i}", {"n": i, "keep": i % 2 == 0}) for i in range(n)]
        self.calls = []

    def get(self, where=None, include=None, limit=None, offset=0):
        self.calls.append((limit, offset))
        hit = [r for r in self.rows if not where or r[2]["keep"] == where["keep"]]
        page = hit[offset:offset + limit]
        return {"ids": [r[0] for r in page], "documents": [r[1] for r in page], "metadatas": [r[2] for r in page]}


class TestIterPages(unittest.TestCase):
    def test_every_row_once_and_bounded_pages(self):
        coll = _Collection(2500)
        ids = [cid for page_ids, _, _ in iter_pages(coll, page_size=1000) for cid in page_ids]
        self.assertEqual(ids, [f"c{i}" for i in range(2500)])
        self.assertTrue(all(limit == 1000 for limit, _ in coll.calls))
        self.assertEqual([offset for _, offset in coll.calls], [0, 1000, 2000])

    def test_filter_and_exact_multiple_of_page_size(self):
        coll = _Collection(8)
        pages = list(iter_pages(coll, where={"keep": True}, page_size=2))
        self.assertEqual([p[0] for p in pages], [["c0", "c2"], ["c4", "c6"]])
        self.assertEqual(len(coll.calls), 3)  # son boş sayfa taramayı bitirir

    def test_empty_collection(self):
        self.assertEqual(list(iter_pages(_Collection(0))), [])


class _StrictCollection:
    """Sayfasız (limit'siz) okumayı reddeden koleksiyon: tüm-koleksiyon taramalarının sayfalı
    kaldığını doğrular."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def get(self, where=None, include=None, limit=None, offset=0, ids=None):
        if limit is None:
            raise AssertionError("sayfasız koleksiyon okuması")
        self.calls += 1
        page = self.rows[offset:offset + limit]
        return {"ids": [r[0] for r in page], "documents": [r[1] for r in page], "metadatas": [dict(r[2]) for r in page]}


def _rows(n):
    from mufettis.config import CHUNK_SCHEMA_VERSION

    rows = []
    for i in range(n):
        meta = {"chunk_schema_version": CHUNK_SCHEMA_VERSION, "document_indexable": True, "status": "active",
                "file_hash": f"h{i // 10}", "title": f"Belge {i // 10}", "source_id": "bddk", "source": "BDDK"}
        if i == n - 1:
            meta.update(law_number="5411", source_document_id="999", account_code="100")
        rows.append((f"c{i}", f"parça metni {i}", meta))
    return rows


class TestFullScansArePaged(unittest.TestCase):
    N = 2 * 1000 + 5  # READ_PAGE_SIZE'dan büyük: son sayfadaki kayıt da bulunmalı

    def _rag(self):
        from types import SimpleNamespace

        return SimpleNamespace(collection=_StrictCollection(_rows(self.N)))

    def test_exact_law_candidates(self):
        from mufettis.rag.engine import LocalRAG

        rag = LocalRAG.__new__(LocalRAG)
        rag.collection = _StrictCollection(_rows(self.N))
        found = rag._exact_law_candidates("5411")
        self.assertEqual([r["metadata"]["law_number"] for r in found], ["5411"])
        self.assertEqual(rag.collection.calls, 3)

    def test_inventory_counts_every_chunk(self):
        from mufettis.rag import maintenance

        inventory = maintenance.build_rag_inventory(self._rag())
        self.assertEqual(inventory["total_chunks"], self.N)
        self.assertEqual(len(inventory["documents"]), (self.N + 9) // 10)

    def test_document_account_matches(self):
        from mufettis.rag import maintenance

        rows = maintenance._collect_exact_document_account_matches(self._rag(), "999", "100")
        self.assertEqual([r["id"] for r in rows], [f"c{self.N - 1}"])


if __name__ == "__main__":
    unittest.main()
