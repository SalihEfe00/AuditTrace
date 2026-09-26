# -*- coding: utf-8 -*-
"""Belgenin atıf yaptığı mevzuatın çıkarılması ve veritabanı kataloğuyla eşleştirilmesi."""
from __future__ import annotations

import io
import unittest

from mufettis.analysis.cited_references import (
    IN_CORPUS,
    INTERNAL,
    NOT_CHECKED,
    NOT_IN_CORPUS,
    CatalogEntry,
    catalog_from_index,
    cited_references,
    describe,
    extract_citations,
    missing_note,
)

CATALOG = [
    CatalogEntry(title="5411 sayılı Bankacılık Kanunu", law_number="5411", is_law=True,
                 names="BANKACILIK KANUNU 5411 sayılı Bankacılık Kanunu"),
    CatalogEntry(title="5464 sayılı Banka Kartları ve Kredi Kartları Kanunu", law_number="5464", is_law=True,
                 names="BANKA KARTLARI VE KREDİ KARTLARI KANUNU"),
    CatalogEntry(title="Kişisel Verilerin İşlenmesi ve Korunması", names="Kişisel Verilerin İşlenmesi ve Korunması Tebliğ"),
    CatalogEntry(title="Bankaların Kredi İşlemlerine İlişkin Yönetmelik",
                 names="Bankaların Kredi İşlemlerine İlişkin Yönetmelik"),
]


def _by_label(refs):
    return {r.label: r for r in refs}


class TestExtraction(unittest.TestCase):
    def test_named_numbered_and_article_citations(self):
        text = ("Ayrıca Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği uyarınca iade talep ediyorum. "
                "5464 sayılı Banka Kartları ve Kredi Kartları Kanunu Madde 26 hükmü de açıktır. "
                "Bankacılık Kanununun 54 üncü maddesi gereğince bilgi verilmelidir.")
        cites = {c["label"]: c for c in extract_citations(text)}
        self.assertIn("Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği", cites)  # "Ayrıca" atıldı
        self.assertEqual(cites["5464 sayılı Banka Kartları ve Kredi Kartları Kanunu"]["article"], "26")
        self.assertEqual(cites["Bankacılık Kanununun"]["article"], "54")

    def test_bare_instrument_word_is_not_a_citation(self):
        self.assertEqual(extract_citations("Yönetmelik ve Kanun hükümlerine aykırıdır."), [])

    def test_internal_rule_is_marked(self):
        refs = cited_references("Bankamız Kredi Tahsis Yönetmeliği md 12 ihlal edilmiştir.", CATALOG)
        self.assertEqual([(r.label, r.status, r.article) for r in refs],
                         [("Kredi Tahsis Yönetmeliği", INTERNAL, "12")])

    def test_repeated_citation_listed_once(self):
        text = "5411 sayılı Bankacılık Kanunu çerçevesinde... yine 5411 sayılı Bankacılık Kanunu kapsamında."
        self.assertEqual(len(extract_citations(text)), 1)


class TestResolution(unittest.TestCase):
    def test_law_number_and_inflected_name_match_law(self):
        refs = _by_label(cited_references(
            "5464 sayılı Kanun md 26 ve Bankacılık Kanununun 54 üncü maddesi", CATALOG))
        self.assertEqual(refs["5464 sayılı Kanun"].status, IN_CORPUS)
        self.assertEqual(refs["Bankacılık Kanununun"].matched_title, "5411 sayılı Bankacılık Kanunu")

    def test_kind_must_match(self):
        # Kanun kataloğda var, aynı adlı yönetmelik yok: yönetmelik atfı kanuna bağlanmamalı.
        refs = cited_references("Banka Kartları ve Kredi Kartları Hakkında Yönetmelik gereği", CATALOG)
        self.assertEqual(refs[0].status, NOT_IN_CORPUS)
        # Kanun atfı adı benzeyen tebliğe bağlanmaz.
        refs = cited_references("Kişisel Verilerin Korunması Kanunu uyarınca", CATALOG)
        self.assertEqual(refs[0].status, NOT_IN_CORPUS)

    def test_unknown_law_number_is_not_in_corpus(self):
        self.assertEqual(cited_references("6698 sayılı Kanun", CATALOG)[0].status, NOT_IN_CORPUS)

    def test_without_catalog_nothing_is_claimed_missing(self):
        refs = cited_references("Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği", None)
        self.assertEqual(refs[0].status, NOT_CHECKED)
        self.assertEqual(missing_note(refs, has_findings=False), "")

    def test_catalog_skips_superseded_and_quarantined(self):
        items = {
            "a": {"title": "BANKACILIK KANUNU", "law_number": "5411", "document_type": "Kanun", "status": "active"},
            "b": {"title": "ESKİ KANUN", "law_number": "9999", "document_type": "Kanun", "status": "superseded"},
            "c": {"title": "KARANTİNA", "law_number": "8888", "document_type": "Kanun", "status": "quarantined"},
        }
        catalog = catalog_from_index(items, lambda rec: rec.get("title", ""))
        self.assertEqual([(e.law_number, e.is_law) for e in catalog], [("5411", True)])


class TestPresentation(unittest.TestCase):
    def test_describe_and_missing_note(self):
        refs = cited_references("Bankacılık Kanununun 54 üncü maddesi ve Ücretler Yönetmeliği", CATALOG)
        lines = [describe(r) for r in refs]
        self.assertIn("Bankacılık Kanununun Md. 54 — veritabanında var (eşleşen belge: 5411 sayılı Bankacılık Kanunu)",
                      lines)
        self.assertIn("Ücretler Yönetmeliği — veritabanında yok", lines)
        self.assertIn("Bulgu üretilmemesinin olası nedeni", missing_note(refs, has_findings=False))
        self.assertIn("Ücretler Yönetmeliği", missing_note(refs, has_findings=True))


class TestPresenterAndReport(unittest.TestCase):
    TEXT = ("Kredi kartımdan sözleşmede olmayan yıllık aidat kesilmiştir. Finansal Tüketicilerden Alınacak "
            "Ücretler Yönetmeliği uyarınca iadesini talep ediyorum.")

    def test_result_and_docx_carry_cited_regulations(self):
        from docx import Document
        from mufettis.analysis.analysis_presenter import AnalysisPresenter
        from mufettis.exports.analysis_report import create_analysis_docx

        presenter = AnalysisPresenter(catalog_fn=lambda: CATALOG)
        result = presenter.execute_analysis(text=self.TEXT, title="Aidat", sample_evidence=[]).result
        self.assertEqual([(c.label, c.status) for c in result.cited_regulations],
                         [("Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği", NOT_IN_CORPUS)])
        text = "\n".join(p.text for p in Document(io.BytesIO(create_analysis_docx(result))).paragraphs)
        self.assertIn("Belgenin atıf yaptığı mevzuat", text)
        self.assertIn("Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği — veritabanında yok", text)

    def test_sample_evidence_analysis_does_not_claim_missing(self):
        from mufettis.analysis.analysis_presenter import AnalysisPresenter

        result = AnalysisPresenter().execute_analysis(text=self.TEXT, title="Aidat", sample_evidence=[]).result
        self.assertEqual({c.status for c in result.cited_regulations}, {NOT_CHECKED})


class TestCatalogCache(unittest.TestCase):
    def test_catalog_rebuilt_only_when_index_file_changes(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from mufettis.analysis import analysis_presenter
        from mufettis.rag.index import IngestIndex

        def write(path, items):
            path.write_text(json.dumps({"version": "2.0", "indexed_items": items}), encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ingest_index.json"
            write(path, {"a": {"title": "BANKACILIK KANUNU", "law_number": "5411", "document_type": "Kanun"}})
            with patch.object(IngestIndex, "default_path", staticmethod(lambda: path)):
                first = analysis_presenter._index_catalog()
                with patch.object(analysis_presenter, "catalog_from_index", side_effect=AssertionError("yeniden")):
                    self.assertIs(analysis_presenter._index_catalog(), first)
                write(path, {"a": {"title": "BANKACILIK KANUNU", "law_number": "5411", "document_type": "Kanun"},
                             "b": {"title": "ÖRNEK KARTLAR KANUNU", "law_number": "99001", "document_type": "Kanun"}})
                self.assertEqual(len(analysis_presenter._index_catalog()), 2)


if __name__ == "__main__":
    unittest.main()
