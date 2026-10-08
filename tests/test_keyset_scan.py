# -*- coding: utf-8 -*-
"""M17.3a: tam taramanın hızlandırılması sonucu değiştirmez.

- `SqliteVectorCollection.iter_get` (seq'e göre keyset sayfalama) eski `get(limit, offset)` sayfalarıyla aynı satırları
  aynı sırada verir; silme ve güncellemelerden sonra da.
- `contains` ön süzgeci, metninde ya da (taban dahil) meta verisinde dizgi geçen her satırı bırakır.
- `_literal_prefilter`: hesap kodu süzgece girer; harf içeren ya da JSON'da kaçışlanan belge kimliği girmez.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from mufettis.rag.engine import _literal_prefilter
from mufettis.rag.paging import iter_pages
from mufettis.rag.store import SqliteVectorCollection
from tests.test_chunk_store import WHERES, _meta

DIM = 8
N = 230


def _offset_pages(col, where, size):
    out, offset = [], 0
    while True:
        got = col.get(where=where, include=["documents", "metadatas"], limit=size, offset=offset)
        if not got["ids"]:
            return out
        out.append(got)
        if len(got["ids"]) < size:
            return out
        offset += len(got["ids"])


def _flat(pages):
    return [(i, d, m) for got in pages for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])]


class KeysetScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.col = SqliteVectorCollection(self.tmp / "store")
        vecs = np.random.default_rng(1).normal(size=(N, DIM)).astype(np.float32)
        for start in range(0, N, 50):
            ids = [f"c{i}" for i in range(start, min(N, start + 50))]
            self.col.upsert(ids=ids, embeddings=vecs[start:start + len(ids)].tolist(),
                            documents=[f"metin {i} hesap {100 + i % 7}KASA" if i % 9 else None
                                       for i in range(start, start + len(ids))],
                            metadatas=[{**_meta(i), "account_code": str(100 + i % 5)} for i in range(start, start + len(ids))])
        self.col.delete(ids=[f"c{i}" for i in range(0, N, 17)])
        self.col.upsert(ids=["c3", "c50"], embeddings=vecs[:2].tolist(), documents=["yeni 5411", "yeni"],
                        metadatas=[{"status": "active", "chunk_schema_version": "3"}, {"n": 4}])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_rows_same_order_as_offset_paging(self):
        for where in WHERES:
            for size in (1, 7, 50, 1000):
                with self.subTest(where=where, size=size):
                    keyset = list(self.col.iter_get(where=where, include=["documents", "metadatas"], page_size=size))
                    self.assertEqual(_flat(keyset), _flat(_offset_pages(self.col, where, size)))
                    self.assertTrue(all(len(p["ids"]) <= size for p in keyset))

    def test_iter_pages_uses_keyset_with_same_result(self):
        got = [row for ids, docs, metas in iter_pages(self.col, page_size=13) for row in zip(ids, docs, metas)]
        self.assertEqual(got, [(i, d, m or {}) for i, d, m in _flat(_offset_pages(self.col, None, 13))])

    def test_contains_keeps_every_row_with_the_literal(self):
        everything = _flat(_offset_pages(self.col, None, 1000))
        for literal in ("102", "5411", "KASA", "binding", "yok-boyle-bir-sey"):
            with self.subTest(literal=literal):
                kept = {row[0] for row in _flat(self.col.iter_get(page_size=11, contains=[literal]))}
                expected = {i for i, d, m in everything
                            if literal in (d or "") or literal in json.dumps(m or {}, ensure_ascii=False)}
                self.assertEqual(kept, expected)
        both = {row[0] for row in _flat(self.col.iter_get(contains=["102", "KASA"]))}
        self.assertTrue(both <= {row[0] for row in _flat(self.col.iter_get(contains=["102"]))})


class LiteralPrefilterTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(_literal_prefilter(None, "120"), ("120",))
        self.assertEqual(_literal_prefilter("5411", None), ("5411",))
        self.assertEqual(_literal_prefilter("2007/1", "100"), ("100", "2007/1"))
        self.assertEqual(_literal_prefilter("BDDK-123", None), ())  # URL harfe duyarsız karşılaştırılır
        self.assertEqual(_literal_prefilter('12"3', None), ())
        self.assertEqual(_literal_prefilter(None, None), ())
        self.assertEqual(_literal_prefilter("", " "), ())


if __name__ == "__main__":
    unittest.main()
