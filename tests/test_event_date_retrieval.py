# -*- coding: utf-8 -*-
"""Olay tarihindeki metin (M7.4): önceki kanun metinleri güncel aramaya girmez; olay tarihi verilince o tarihte
yürürlükte olan metnin parçaları güncel metnin yerini alır."""
from __future__ import annotations

import datetime as dt
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from mufettis.analysis.engine import AnalysisEngine, evidence_display_title
from mufettis.config import CHUNK_SCHEMA_VERSION
from mufettis.rag.engine import LocalRAG
from mufettis.rag.store import SqliteVectorCollection

DIM = 8
OLD = {"status": "historical", "valid_from": "2012-06-29", "valid_to": "2013-03-28", "file_hash": "old",
       "amended_by": "6327 sayılı Kanun", "title": "BANKA KARTLARI VE KREDİ KARTLARI KANUNU (6327 sayılı Kanun ile "
       "değişik hali)"}
NEW = {"status": "active", "file_hash": "new", "title": "BANKA KARTLARI VE KREDİ KARTLARI KANUNU"}


def _vec(i):
    v = np.zeros(DIM, dtype=np.float32)
    v[i % DIM] = 1.0
    v[(i + 1) % DIM] = 0.3
    return (v / np.linalg.norm(v)).tolist()


def _meta(base, article, paragraph, chunk, law="5464"):
    return {**base, "chunk_schema_version": str(CHUNK_SCHEMA_VERSION), "law_number": law, "article_no": article,
            "article_prefix": "", "paragraph_no": paragraph, "item_no": "", "chunk_no": chunk,
            "document_indexable": "True"}


ROWS = [
    # (id, metin, meta, vektör)
    ("new-9-1", "kart ücreti sözleşme değişikliği bildirim yeni hüküm otuz gün", _meta(NEW, "9", "1", 0), 0),
    ("new-10-1", "kart ücreti sözleşme sonradan eklenen madde bildirim", _meta(NEW, "10", "1", 1), 1),
    ("old-8-1", "kart ücreti sözleşme bildirim sonradan kaldırılan madde", _meta(OLD, "8", "1", 0), 2),
    ("old-9-1", "kart ücreti sözleşme değişikliği bildirim eski hüküm on beş gün", _meta(OLD, "9", "1", 1), 0),
    ("other-1", "kart ücreti sözleşme bildirim başka kanun",
     _meta({"status": "active", "file_hash": "other", "title": "TÜKETİCİNİN KORUNMASI HAKKINDA KANUN"}, "4", "1", 0,
           law="6502"), 3),
]


class _Rag(LocalRAG):
    def __init__(self, path: Path):  # model ve veritabanı yüklenmez
        self.collection = SqliteVectorCollection(path)
        self.collection.upsert(ids=[r[0] for r in ROWS], documents=[r[1] for r in ROWS],
                               metadatas=[r[2] for r in ROWS], embeddings=[_vec(r[3]) for r in ROWS])

    def embed(self, texts, **_):
        return [_vec(0) for _ in texts]


class TestEventDateRetrieval(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rag = _Rag(self.tmp / "store")
        self.statement = "kart ücreti sözleşme değişikliği bildirim"

    def tearDown(self):
        self.rag = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ids(self, **kw):
        return [r["id"] for r in self.rag.claim_candidates(self.statement, 10, **kw)]

    def test_previous_texts_stay_out_of_current_retrieval(self):
        self.assertEqual(set(self.ids()), {"new-9-1", "new-10-1", "other-1"})
        # Olay tarihi son değişiklikten sonraysa güncel metin: sonuç olay tarihsiz aramayla aynı.
        self.assertEqual(self.ids(as_of=dt.date(2020, 1, 1)), self.ids())

    def test_event_date_inside_window_uses_the_text_in_force_then(self):
        got = self.ids(as_of="2012-12-01")
        self.assertIn("old-9-1", got)        # aynı madde, o tarihteki metin
        self.assertIn("old-8-1", got)        # sonradan kaldırılan madde, anlamsal olarak bulunur
        self.assertNotIn("new-9-1", got)     # güncel metin o tarihte yürürlükte değildi
        self.assertNotIn("new-10-1", got)    # o tarihte olmayan madde düşer
        self.assertIn("other-1", got)        # önceki metni olmayan kanun etkilenmez
        self.assertEqual(self.rag.versions_in_force(dt.date(2012, 12, 1)), {"5464": "old"})
        self.assertEqual(self.rag.versions_in_force(dt.date(2011, 1, 1)), {})

    def test_display_title_labels_the_text_in_force_at_the_event_date(self):
        hit = next(r for r in self.rag.claim_candidates(self.statement, 10, as_of="2012-12-01") if r["id"] == "old-9-1")
        title = evidence_display_title(hit["metadata"])
        self.assertIn("olay tarihindeki metin: 6327 sayılı Kanun ile değişik hali (2012-06-29 – 2013-03-28)", title)
        self.assertNotIn("olay tarihindeki", evidence_display_title(ROWS[0][2]))


class _Client:
    supports_claim_candidates = True

    def __init__(self, dated):
        self.supports_event_date = dated
        self.calls = []

    def claim_candidates(self, statement, n, eligibility=None, **kw):
        self.calls.append(kw)
        return []


class TestAnalysisPassesEventDate(unittest.TestCase):
    def _run(self, client, event_date):
        engine = AnalysisEngine.__new__(AnalysisEngine)
        engine.claims_for_matching = lambda doc: [SimpleNamespace(claim_id="c1", statement="kart ücreti")]
        doc = SimpleNamespace(event_date=event_date)
        plan = SimpleNamespace(source_scope=[], filters={})
        engine.retrieve_claim_candidates(doc, plan, client)
        return client.calls

    def test_event_date_reaches_retrieval_only_when_known_and_supported(self):
        self.assertEqual(self._run(_Client(True), "2012-12-01"), [{"as_of": "2012-12-01"}])
        self.assertEqual(self._run(_Client(True), None), [{}])
        self.assertEqual(self._run(_Client(False), "2012-12-01"), [{}])


if __name__ == "__main__":
    unittest.main()
