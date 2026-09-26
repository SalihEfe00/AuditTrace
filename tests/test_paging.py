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


if __name__ == "__main__":
    unittest.main()
