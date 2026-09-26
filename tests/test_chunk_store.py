# -*- coding: utf-8 -*-
"""SqliteVectorCollection, Chroma koleksiyonunun projede kullanılan alt kümesiyle aynı sonucu verir.

Aynı işlem dizisi gerçek bir Chroma koleksiyonuna ve yeni depoya uygulanır; okuma sonuçları birebir,
arama sonuçları kesin (float32) sonuçla karşılaştırılır.
"""
from __future__ import annotations

import gc
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from mufettis.rag.store import SqliteVectorCollection

try:
    import chromadb
except Exception:  # pragma: no cover
    chromadb = None

DIM = 16


def _vectors(n, seed=0):
    x = np.random.default_rng(seed).normal(size=(n, DIM)).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _meta(i):
    meta = {"file_hash": f"h{i % 7}", "chunk_schema_version": "3", "n": i % 5, "status": "active"}
    if i % 3 == 0:
        meta["normative_role"] = "binding"
    elif i % 3 == 1:
        meta["normative_role"] = "guidance"
    if i % 4 == 0:
        meta["flag"] = bool(i % 8)
    if i % 11 == 0:
        meta["n"] = str(i % 5)  # aynı anahtarda farklı tip
    if i % 13 == 0:
        meta["score"] = i / 10
    return meta


WHERES = [
    None,
    {"file_hash": "h3"},
    {"n": 1},
    {"n": "0"},
    {"flag": True},
    {"flag": False},
    {"normative_role": {"$nin": ["guidance"]}},
    {"normative_role": {"$in": ["guidance", "yok"]}},
    {"normative_role": {"$ne": "binding"}},
    {"n": {"$gte": 3}},
    {"score": {"$lt": 5}},
    {"$and": [{"chunk_schema_version": "3"}, {"file_hash": "h1"}]},
    {"$or": [{"file_hash": "h2"}, {"flag": True}]},
    {"$and": [{"chunk_schema_version": "3"}, {"normative_role": {"$nin": ["guidance", "x"]}},
              {"$or": [{"n": 2}, {"n": {"$gt": 3}}]}]},
]


@unittest.skipIf(chromadb is None, "chromadb yok")
class TestChromaConformance(unittest.TestCase):
    N = 240

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        client = chromadb.PersistentClient(path=str(self.tmp / "chroma"))
        self.chroma = client.get_or_create_collection(name="uyum", embedding_function=None)
        self.store = SqliteVectorCollection(self.tmp / "store")
        self.vecs = _vectors(self.N)
        for c in (self.chroma, self.store):
            for start in range(0, self.N, 100):
                ids = [f"c{i}" for i in range(start, min(self.N, start + 100))]
                c.upsert(ids=ids, embeddings=self.vecs[start:start + 100].tolist(),
                         documents=[f"metin {i}" if i % 9 else None for i in range(start, start + len(ids))],
                         metadatas=[_meta(i) for i in range(start, start + len(ids))])

    def tearDown(self):
        self.chroma = None
        gc.collect()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _both(self, method, **kw):
        a, b = getattr(self.chroma, method)(**kw), getattr(self.store, method)(**kw)
        return a, b

    def assertSameGet(self, **kw):
        a, b = self._both("get", **kw)
        self.assertEqual(b["ids"], a["ids"], kw)
        for key in ("documents", "metadatas"):
            self.assertEqual(b[key], a[key], (key, kw))

    def test_get_filters_order_and_paging(self):
        for where in WHERES:
            with self.subTest(where=where):
                self.assertSameGet(where=where)
        self.assertSameGet(ids=["c5", "c1", "yok", "c200"])
        self.assertSameGet(ids=["c5", "c1", "c3"], where={"n": {"$ne": 1}})
        self.assertSameGet(where={"chunk_schema_version": "3"}, limit=50, offset=100)
        self.assertSameGet(offset=230)
        self.assertSameGet(limit=0 + 7, include=["metadatas"])
        self.assertSameGet(ids=["c2"], include=[])

    def test_writes_merge_like_chroma(self):
        for c in (self.chroma, self.store):
            c.update(ids=["c1", "c2", "yok"], metadatas=[{"status": "superseded", "n": None}, {"yeni": 1}, {"a": 1}])
            c.upsert(ids=["c4"], embeddings=[self.vecs[0].tolist()])  # metin ve metadata korunur
            c.upsert(ids=["c5", "c9999"], embeddings=self.vecs[:2].tolist(), metadatas=[{"x": "y"}, {"file_hash": "h0"}],
                     documents=["d5", "yeni"])
            c.delete(where={"file_hash": "h6"})
            c.delete(ids=["c7", "c8", "yok"])
        self.assertEqual(self.store.count(), self.chroma.count())
        self.assertSameGet()
        self.assertSameGet(where={"status": "superseded"})
        self.assertSameGet(where={"file_hash": "h0"})
        for c in (self.chroma, self.store):
            with self.assertRaises(Exception):
                c.upsert(ids=["yeni-gommesiz"], documents=["x"])
            with self.assertRaises(Exception):
                c.upsert(ids=["d", "d"], embeddings=self.vecs[:2].tolist())

    def test_query_matches_exact_search(self):
        queries = _vectors(25, seed=3)
        for where in WHERES:
            got = self.store.query(query_embeddings=queries.tolist(), n_results=10, where=where)
            ref = self.chroma.query(query_embeddings=queries.tolist(), n_results=10, where=where)
            allowed = [int(i[1:]) for i in self.store.get(where=where)["ids"]]
            for qi, q in enumerate(queries):
                with self.subTest(where=where, q=qi):
                    dist = ((self.vecs[allowed] - q) ** 2).sum(axis=1)
                    true_dist = {f"c{i}": d for i, d in zip(allowed, dist)}
                    # Sıra, neredeyse eşit mesafelerin (float16 yuvarlaması) yer değiştirmesi dışında kesindir.
                    np.testing.assert_allclose([true_dist[c] for c in got["ids"][qi]], np.sort(dist)[:10], atol=2e-3)
                    self.assertEqual(len(got["ids"][qi]), min(10, len(allowed)))
                    self.assertEqual(len(ref["ids"][qi]), min(10, len(allowed)))
                    np.testing.assert_allclose(got["distances"][qi], np.sort(dist)[:10], atol=2e-3)
                    # Chroma'nın HNSW'si filtreli aramada yaklaşıktır: kesin tarama ondan hiç kötü değildir.
                    self.assertTrue(np.all(np.asarray(got["distances"][qi]) <= np.asarray(ref["distances"][qi]) + 2e-3))
                    by_id = dict(zip(ref["ids"][qi], ref["metadatas"][qi]))
                    for cid, meta in zip(got["ids"][qi], got["metadatas"][qi]):
                        if cid in by_id:
                            self.assertEqual(meta, by_id[cid])
        empty = self.store.query(query_embeddings=[queries[0].tolist()], n_results=5, where={"file_hash": "yok"})
        self.assertEqual(empty["ids"], [[]])
        few = self.store.query(query_embeddings=[queries[0].tolist()], n_results=500, include=["distances"])
        self.assertEqual(len(few["ids"][0]), self.N)
        self.assertIsNone(few["documents"])

    def test_embeddings_round_trip(self):
        got = self.store.get(ids=["c3", "c10"], include=["embeddings"])
        np.testing.assert_allclose(got["embeddings"], self.vecs[[3, 10]], atol=1e-3)


class TestStoreDurability(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reopen_and_filter_cache_sees_writes_from_other_instance(self):
        vecs = _vectors(50)
        a = SqliteVectorCollection(self.tmp)
        a.upsert(ids=[f"c{i}" for i in range(50)], embeddings=vecs.tolist(), metadatas=[_meta(i) for i in range(50)])
        q = [vecs[7].tolist()]
        self.assertEqual(a.query(query_embeddings=q, n_results=1, where={"file_hash": "h0"})["ids"], [["c7"]])
        b = SqliteVectorCollection(self.tmp)  # ayrı nesne (ör. başka süreç)
        b.delete(ids=["c7"])
        b.update(ids=["c14"], embeddings=[vecs[7].tolist()])  # vektör değişir: yeni satır, eskisi çöp
        self.assertEqual(a.query(query_embeddings=q, n_results=1, where={"file_hash": "h0"})["ids"], [["c14"]])
        self.assertEqual(SqliteVectorCollection(self.tmp).count(), 49)
        self.assertEqual(a.garbage_rows(), 2)

    def test_interrupted_vector_append_is_realigned(self):
        store = SqliteVectorCollection(self.tmp)
        store.upsert(ids=["a"], embeddings=_vectors(1).tolist())
        with open(self.tmp / "vec_i8.bin", "ab") as h:  # yarıda kalmış ekleme: yalnız bir dosyaya yazıldı
            h.write(b"\x01" * 5)
        store.upsert(ids=["b"], embeddings=_vectors(1, seed=9).tolist())
        got = store.query(query_embeddings=_vectors(1, seed=9).tolist(), n_results=1)
        self.assertEqual(got["ids"], [["b"]])
        self.assertLess(got["distances"][0][0], 1e-3)

    def _filled(self, n=300):
        vecs = _vectors(n, seed=4)
        store = SqliteVectorCollection(self.tmp)
        for start in range(0, n, 50):
            store.upsert(ids=[f"c{i}" for i in range(start, start + 50)], embeddings=vecs[start:start + 50].tolist(),
                         documents=[f"metin {i}" for i in range(start, start + 50)],
                         metadatas=[_meta(i) for i in range(start, start + 50)])
        store.delete(where={"file_hash": "h2"})
        store.update(ids=[f"c{i}" for i in range(0, 60, 3)], embeddings=_vectors(20, seed=8).tolist())
        return store

    def _snapshot(self, store):
        got = store.get(include=["documents", "metadatas", "embeddings"])
        queries = _vectors(6, seed=12).tolist()
        return got, [store.query(query_embeddings=queries, n_results=15, where=w) for w in WHERES[:6]]

    def test_compaction_keeps_every_result_and_reclaims_space(self):
        store = self._filled()
        garbage = store.garbage_rows()
        self.assertGreater(garbage, 0)
        before_get, before_q = self._snapshot(store)
        other = SqliteVectorCollection(self.tmp)
        other.query(query_embeddings=_vectors(1).tolist(), n_results=3, where={"n": 1})  # önbellek dolu

        self.assertEqual(store.compact()["removed"], garbage)
        self.assertEqual(store.garbage_rows(), 0)
        after_get, after_q = self._snapshot(other)  # başka nesne: önbellek yazma sayacıyla geçersizleşir
        self.assertEqual(after_get["ids"], before_get["ids"])
        self.assertEqual(after_get["metadatas"], before_get["metadatas"])
        np.testing.assert_array_equal(after_get["embeddings"], before_get["embeddings"])
        for a, b in zip(before_q, after_q):
            self.assertEqual(a["ids"], b["ids"])
            self.assertEqual(a["distances"], b["distances"])

        # Sonraki yazmalar yeni kümeye eklenir; ikinci sıkıştırma ilk kümeyi siler.
        other.upsert(ids=["yeni"], embeddings=_vectors(1, seed=30).tolist(), metadatas=[{"file_hash": "h9"}])
        self.assertEqual(store.query(query_embeddings=_vectors(1, seed=30).tolist(), n_results=1)["ids"], [["yeni"]])
        other.delete(ids=["c1"])
        self.assertEqual(store.compact()["fileset"], 2)
        self.assertEqual(sorted(p.name for p in self.tmp.glob("vec_*.bin")),
                         sorted(f"vec_{k}.{n}.bin" for k in ("i8", "f16", "aux") for n in (1, 2)))
        self.assertEqual(store.compact()["removed"], 0)

    def test_compaction_triggers_only_past_threshold(self):
        from unittest.mock import patch

        from mufettis.rag import store as store_module

        store = self._filled()
        with patch.object(store_module, "COMPACT_MIN_GARBAGE", 10 ** 6):
            self.assertIsNone(store.compact_if_needed())
        with patch.object(store_module, "COMPACT_MIN_GARBAGE", 1):
            self.assertIsNone(store.compact_if_needed())  # oran eşiğinin (%30) altında
            with patch.object(store_module, "COMPACT_GARBAGE_RATIO", 0.1):
                self.assertIsNotNone(store.compact_if_needed())
        self.assertEqual(store.garbage_rows(), 0)

    def test_query_retries_when_compacted_mid_search(self):
        store = self._filled()
        expected = store.query(query_embeddings=_vectors(2, seed=5).tolist(), n_results=10)
        other = SqliteVectorCollection(self.tmp)
        original = store._nearest
        calls = []

        def nearest_then_compact(*args):
            result = original(*args)
            if not calls:
                calls.append(1)
                other.compact()  # arama ile sonuç okuma arasında satır numaraları değişir
            return result

        store._nearest = nearest_then_compact
        got = store.query(query_embeddings=_vectors(2, seed=5).tolist(), n_results=10)
        self.assertEqual(got["ids"], expected["ids"])

    def test_concurrent_writers_never_share_vector_rows(self):
        import threading

        vecs = _vectors(400, seed=6)
        SqliteVectorCollection(self.tmp).upsert(ids=["ilk"], embeddings=vecs[:1].tolist())
        errors = []

        def writer(offset):
            store = SqliteVectorCollection(self.tmp)  # ayrı nesne: ayrı süreç gibi (ortak iş parçacığı kilidi yok)
            try:
                for start in range(offset, 400, 40):
                    store.upsert(ids=[f"c{i}" for i in range(start, start + 20)], embeddings=vecs[start:start + 20].tolist())
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(o,)) for o in (0, 20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        got = SqliteVectorCollection(self.tmp).get(ids=[f"c{i}" for i in range(400)], include=["embeddings"])
        self.assertEqual(len(got["ids"]), 400)
        order = [int(i[1:]) for i in got["ids"]]
        np.testing.assert_allclose(got["embeddings"], vecs[order], atol=1e-3)

    def test_rejects_unsupported(self):
        store = SqliteVectorCollection(self.tmp)
        with self.assertRaises(ValueError):
            store.get(where={"a": 1, "b": 2})
        with self.assertRaises(ValueError):
            store.get(where={"a": {"$regex": "x"}})
        with self.assertRaises(ValueError):
            store.get(ids=[])
        store.upsert(ids=["b"], embeddings=_vectors(1).tolist(), documents=["eski"])
        with self.assertRaises(ValueError):
            store.update(ids=["b"], documents=["yeni metin, eski vektör"])
        with self.assertRaises(ValueError):
            store.upsert(ids=["a"], embeddings=_vectors(1).tolist(), metadatas=[{"liste": [1]}])


if __name__ == "__main__":
    unittest.main()
