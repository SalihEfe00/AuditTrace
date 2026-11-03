# -*- coding: utf-8 -*-
"""
E8: PDF çıkarmada U+FFFE, belge kimliği (document_key) ve sürüm zinciri düzeltmeleri.

* PDFium eşlemesiz glifleri U+FFFE döndürüyordu ("Kabul Tar\\uFFFEh\\uFFFE"); kalite ölçümü yalnızca
  U+FFFD saydığından bozuk sayfa HIGH_CONFIDENCE alıp seçiliyordu.
* BDDK yönetmelikleri atıf yaptıkları "5411 sayılı Bankacılık Kanunu"nu kendi kanun numarası,
  Resmî Gazete "Sayı: 6827"yi belge anahtarı sanıyordu; farklı yönetmelikler birbirinin
  "sürümü" sayılıp aramadan gizlenebiliyordu.
"""
from __future__ import annotations

import unittest

from mufettis.extraction.pdf import _pdf_page_quality, _select_pdf_engine_b3
from mufettis.legal.parser import extract_legal_metadata
from scripts.repair_e8_corpus import glued_count, invalid_count, plan_version_chains

CLEAN_PAGE = (
    "MADDE 1 – Amaç\n(1) Bu Yönetmeliğin amacı, bankaların kredi işlemlerine ilişkin usul ve esasları "
    "belirlemektir. Bankalar, kredi tahsis ve izleme süreçlerini yazılı politikalarla yürütür.\n"
    "MADDE 2 – Kapsam\n(1) Bu Yönetmelik, Türkiye'de faaliyet gösteren bankaları kapsar ve kredi "
    "risklerinin yönetimine ilişkin asgari standartları düzenler.\n"
)
BROKEN_PAGE = CLEAN_PAGE.replace("i", "￾")


class TestInvalidUnicodePages(unittest.TestCase):
    def test_single_invalid_char_blocks_high_confidence(self):
        clean = _pdf_page_quality(CLEAN_PAGE)
        one_bad = _pdf_page_quality(CLEAN_PAGE.replace("Kapsam", "Kaps￾m", 1))
        self.assertEqual(clean["status"], "HIGH_CONFIDENCE")
        self.assertEqual(one_bad["invalid_unicode_count"], 1)
        self.assertNotEqual(one_bad["status"], "HIGH_CONFIDENCE")

    def test_broken_page_is_replaced_from_clean_candidate(self):
        glued = CLEAN_PAGE.replace(" ", "")
        decision = _select_pdf_engine_b3([
            ("pypdfium2", [CLEAN_PAGE, CLEAN_PAGE.replace("Kapsam", "Kaps￾m", 1)]),
            ("pypdf", [glued, CLEAN_PAGE]),
        ])
        self.assertEqual(decision["selected_engine"], "pypdfium2")
        self.assertEqual(sum(invalid_count(p) for p in decision["selected_pages"]), 0)
        self.assertEqual(decision["page_substitutions"], [{"page": 2, "engine": "pypdf", "invalid_unicode_count": 1}])

    def test_no_clean_alternative_keeps_page_visible_for_gate(self):
        decision = _select_pdf_engine_b3([("pypdfium2", [CLEAN_PAGE, BROKEN_PAGE])])
        self.assertGreater(sum(invalid_count(p) for p in decision["selected_pages"]), 0)
        self.assertEqual(decision["page_substitutions"], [])


class TestDocumentIdentity(unittest.TestCase):
    BDDK_TEXT = (
        "Resmî Gazete Tarihi: 01.11.2006 Sayı: 6827\n"
        "BANKALARIN KREDİ İŞLEMLERİNE İLİŞKİN YÖNETMELİK\n"
        "MADDE 1 – (1) Bu Yönetmeliğin amacı, 19/10/2005 tarihli ve 5411 sayılı Bankacılık Kanunu "
        "uyarınca bankaların kredi işlemlerine ilişkin usul ve esasları düzenlemektir.\n"
    )

    def _bddk(self, doc_id: str, **extra):
        base = {"source_id": "bddk_official", "url": f"https://www.bddk.gov.tr/Mevzuat/DokumanGetir/{doc_id}", **extra}
        return extract_legal_metadata(doc_id, self.BDDK_TEXT, base)

    def test_regulation_citing_a_law_is_not_that_law(self):
        meta = self._bddk("956")
        self.assertEqual(meta["law_number"], "")
        self.assertEqual(meta["document_key"], "956")

    def test_two_regulations_with_same_gazette_issue_get_distinct_keys(self):
        self.assertNotEqual(self._bddk("956")["document_key"], self._bddk("955")["document_key"])

    def test_law_keeps_its_own_number(self):
        text = "BANKACILIK KANUNU\nKanun Numarası : 5411\nKabul Tarihi : 19/10/2005\nMADDE 1 – Amaç\n"
        meta = extract_legal_metadata("BANKACILIK KANUNU", text, {"source_id": "local"})
        self.assertEqual((meta["law_number"], meta["document_key"]), ("5411", "5411"))

    def test_law_number_from_title(self):
        meta = extract_legal_metadata("6361 sayılı Kanun", "MADDE 1 – Amaç\n", {"source_id": "local"})
        self.assertEqual(meta["law_number"], "6361")

    def test_superseded_status_survives_rechunk_metadata(self):
        self.assertEqual(self._bddk("956", status="superseded")["status"], "superseded")
        self.assertEqual(self._bddk("956", status="active")["status"], "active")


class TestVersionChainPlan(unittest.TestCase):
    def test_newest_download_wins_and_bogus_links_are_cleared(self):
        items = {
            "a1": {"source_id": "bddk", "title": "1210", "timestamp": "2026-09-01", "status": "active"},
            "a2": {"source_id": "bddk", "title": "1210", "timestamp": "2026-09-03", "status": "active",
                   "superseded_by": "b1"},  # farklı belgeye yanlış bağ
            "b1": {"source_id": "bddk", "title": "943", "timestamp": "2026-09-02", "status": "active"},
            "q1": {"source_id": "bddk", "title": "1210", "timestamp": "2026-09-09", "status": "quarantined"},
        }
        keys = {"a1": "1210", "a2": "1210", "b1": "943", "q1": "1210"}
        result = plan_version_chains(items, keys)
        self.assertEqual([(x["hash"], x["superseded_by"]) for x in result["supersede"]], [("a1", "a2")])
        self.assertEqual([x["hash"] for x in result["clear_links"]], ["a2"])

    def test_already_consistent_chain_needs_no_change(self):
        items = {
            "a1": {"source_id": "bddk", "timestamp": "1", "status": "superseded", "superseded_by": "a2"},
            "a2": {"source_id": "bddk", "timestamp": "2", "status": "active"},
        }
        result = plan_version_chains(items, {"a1": "k", "a2": "k"})
        self.assertEqual((result["supersede"], result["clear_links"]), ([], []))


class TestGluedMetric(unittest.TestCase):
    def test_glued_words_are_counted(self):
        self.assertEqual(glued_count(["kişisel verilerin işlenmesi"]), 0)
        self.assertEqual(glued_count(["kişiselverilerinişlenmesi"]), 1)


if __name__ == "__main__":
    unittest.main()


class _LimitedCollection:
    """Chroma gibi tek çağrıda en fazla `limit` kayıt kabul eden sahte koleksiyon."""

    def __init__(self, n: int, limit: int):
        self.limit = limit
        self.records = {f"c{i}": {"file_hash": "h"} for i in range(n)}

    def _check(self, ids):
        if len(ids) > self.limit:
            raise ValueError(f"Batch size of {len(ids)} is greater than max batch size of {self.limit}")

    def get(self, where=None, include=None, ids=None):
        rows = [cid for cid, m in self.records.items() if all(m.get(k) == v for k, v in (where or {}).items())]
        return {"ids": rows, "metadatas": [dict(self.records[c]) for c in rows],
                "documents": ["x"] * len(rows), "embeddings": [[0.0]] * len(rows)}

    def update(self, ids, metadatas):
        self._check(ids)
        for cid, meta in zip(ids, metadatas):
            self.records[cid] = meta

    def upsert(self, ids, documents, metadatas=None, embeddings=None):
        self._check(ids)
        for cid, meta in zip(ids, metadatas):
            self.records[cid] = dict(meta)


class TestChromaBatchLimits(unittest.TestCase):
    """1043/1334 hesap planları ~7000 parça; Chroma 5461 üstü tek çağrıyı reddediyordu."""

    def test_update_hash_metadata_is_batched(self):
        from unittest.mock import patch

        from mufettis.rag import engine

        rag = engine.LocalRAG.__new__(engine.LocalRAG)
        rag.collection = _LimitedCollection(n=25, limit=10)
        with patch.object(engine, "CHROMA_UPSERT_BATCH_SIZE", 10):
            self.assertEqual(rag.mark_hash_superseded("h", "yeni"), 25)
        self.assertTrue(all(m["status"] == "superseded" for m in rag.collection.records.values()))

    def test_quarantine_restore_is_batched(self):
        import shutil
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from mufettis.rag.index import IngestIndex
        from mufettis.repair import ingestion_gate
        from mufettis.repair.audit import RepairAuditLogger

        tmp = Path(tempfile.mkdtemp(prefix="gate_batch_"))
        try:
            collection = _LimitedCollection(n=25, limit=10)
            rag = type("R", (), {"collection": collection,
                                 "delete_hash": lambda self, h: collection.records.clear()})()
            gate = ingestion_gate.AutoHealingIngestionGate(audit_logger=RepairAuditLogger(log_path=tmp / "a.jsonl"))
            index = IngestIndex(path=tmp / "i.json")
            state = gate._capture_previous_state(rag, index, "h")
            with patch.object(ingestion_gate, "CHROMA_UPSERT_BATCH_SIZE", 10):
                gate.quarantine_document(rag, index, "h", {"file_hash": "h"}, "TEST", ["TEST"], previous_state=state)
            self.assertEqual(len(collection.records), 25)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestInlineArticleHeaders(unittest.TestCase):
    """PDF çıkarımının önceki cümleye yapıştırdığı madde başlıkları ayrı madde olur."""

    def test_inline_headers_start_new_articles_but_references_do_not(self):
        from mufettis.legal.parser import split_legal_chunks

        text = ("GEÇİCİ MADDE 3 –Kuruluşlar uygun hale getirmek zorundadırlar. Ancak 24 üncü MADDE hükmü "
                "saklıdır; 5 inci Madde uyarınca işlem yapılır. GEÇİCİ MADDE 4 –Borçlular taksitle öder. "
                "Madde 15 — (Değişik: 1/1/2020) Bağımsız denetim yapılır.")
        chunks = [(c["article_prefix"], c["article_no"]) for c in split_legal_chunks(text)]
        self.assertEqual(chunks, [("GEÇİCİ", "3"), ("GEÇİCİ", "4"), ("", "15")])


class TestAccountingPlanDetectionIsDensityBased(unittest.TestCase):
    """Hesap planı kipi kod yoğunluğuna veya belgenin kendi başlığına dayanır; birkaç sayı
    veya gövdede geçen "Tekdüzen Hesap Planı" atfı bir belgeyi hesap planı yapmaz."""

    BOARD_DECISION = (
        "Bankacılık Düzenleme ve Denetleme Kurumundan:\nKURUL KARARI\nKarar Sayısı:\n"
        "11237 Karar Tarihi: 07.07.2025\n(10.07.2025 tarih ve 32952 sayılı Resmî Gazete'de yayımlanmıştır.)\n"
        "Kurulun 07.07.2025 tarihli toplantısında 159540 sayılı yazı ile 11036 sayılı Kararı "
        "ve 20145 sayılı yazısı incelenmiş, 30011 sayılı başvuru uygun bulunmuştur.\n"
    )

    def test_board_decision_numbers_are_not_account_codes(self):
        from mufettis.legal.parser import classify_legal_structure, split_legal_chunks

        chunks = split_legal_chunks(self.BOARD_DECISION, title="11237")
        self.assertFalse([c for c in chunks if c["chunk_type"] == "accounting_code"])
        self.assertNotEqual(classify_legal_structure("11237", self.BOARD_DECISION, "BDDK Belge")["structure_type"],
                            "accounting_plan")

    def test_body_reference_to_chart_does_not_make_a_law_a_chart(self):
        from mufettis.legal.parser import is_accounting_plan_text

        law = ("VERGİ USUL KANUNU\n" + "Mükellefler defterlerini tutar. " * 40
               + "Tekdüzen Hesap Planı uygulanır.\n100 KASA HESABI\n120 ALICILAR\n" + "Hüküm devam eder. " * 40)
        self.assertFalse(is_accounting_plan_text(law, "Vergi Usul Kanunu"))

    def test_dense_chart_is_detected_with_or_without_title(self):
        from mufettis.legal.parser import is_accounting_plan_text

        chart = "\n".join(f"{100 + i} HESAP ADI {i}" for i in range(60))
        self.assertTrue(is_accounting_plan_text(chart))
        self.assertTrue(is_accounting_plan_text("010 KASA\n011 EFEKTİF DEPOSU", "Tekdüzen Hesap Planı"))


class TestControlCharactersAreRemoved(unittest.TestCase):
    def test_unmapped_glyph_codes_do_not_survive_normalization(self):
        from mufettis.extraction.text_repair import normalize_extracted_text

        out = normalize_extracted_text("Tablo 1: Şok Oranı\n±\x02\x03\x04\x05\x07\x08  400 baz puan\x0c\n\tDevam\x1f.")
        self.assertFalse([c for c in out if ord(c) < 32 and c not in "\n\t\r"])
        self.assertIn("400 baz puan", out)
        self.assertIn("\n", out)
