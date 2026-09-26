# -*- coding: utf-8 -*-
"""Kanonik corpus modeli (mufettis.corpus) regression testleri.

Testler yalnızca sentetik belgeler kullanır; hiçbir test bugünkü corpus'taki dosya adlarına,
klasörlere veya belge kimliklerine dayanmaz. Doğrulanan ilkeler:

* Kimlik (Work / Expression / Manifestation) kaynaktan ve formattan bağımsızdır.
* Yayıncı niteliği ve kopya doğrulaması kaynak kaydından ve kanıttan gelir; dosya adı, klasör
  veya yerel yol güven üretmez. Kullanıcının yüklediği kopya "ikincil yayıncı" sayılmaz.
* Alaka, otorite önceliği, geçerlilik ve kimlik retrieval'da ayrı eksenlerdir: otorite yalnızca
  sınırlı bir önceliktir, geçerlilik ayrı katmandır, aynı sürümün kopyaları tekilleşir.
* Kanıt uygunluğu normatif role / yayıncı niteliğine göredir; kanonik alanı olmayan eski
  parçalarda da aynı karar verilir.
"""
from __future__ import annotations

import datetime as dt
import unittest
from typing import Any, Dict, List

from mufettis.corpus import (
    AUTHORITY_PRIOR_MAX,
    EligibilityPolicy,
    InstrumentType,
    NormativeRole,
    PublisherKind,
    Validity,
    Verification,
    canonical_view,
    derive,
    validity,
    with_canonical,
)
from mufettis.corpus.identity import content_fingerprint, instrument_from_title
from mufettis.corpus.provenance import reconcile_fingerprints
from mufettis.legal.parser import extract_legal_metadata, prefer_official_matches

OFFICIAL_URL = "https://www.bddk.gov.tr/Mevzuat/DokumanGetir/900001"
LAW_TEXT = "KANUN\nKanun Numarası : 99001\nMADDE 1 – Bu Kanunun amacı kart hamillerini korumaktır.\n"


def _law(**over: Any) -> Dict[str, Any]:
    base = {"title": "ÖRNEK KARTLAR KANUNU", "law_number": "99001", "document_type": "Kanun",
            "structure_type": "article", "source_id": "local_file", "file_hash": "a" * 64,
            "status": "active"}
    base.update(over)
    return base


def _regulation(**over: Any) -> Dict[str, Any]:
    base = {"title": "900001", "document_type": "Yönetmelik", "structure_type": "article",
            "source_id": "bddk_official", "source_document_id": "900001", "url": OFFICIAL_URL,
            "file_hash": "b" * 64, "status": "active"}
    base.update(over)
    return base


class TestIdentity(unittest.TestCase):
    def test_title_head_noun_decides_instrument(self):
        cases = {
            "Örnek Kanununun 42 nci Maddesine İlişkin Yönetmelik": InstrumentType.REGULATION,
            "ÖRNEK KARTLAR KANUNU": InstrumentType.LAW,
            "Örnek Hakkında Kanun Hükmünde Kararname": InstrumentType.PRESIDENTIAL_DECREE,
            "Örnek Uygulamalarına İlişkin Rehber": InstrumentType.GUIDELINE,
            "Örnek Usul ve Esaslar": InstrumentType.PROCEDURE,
            "Tekdüzen Hesap Planı Hakkında Tebliğ": InstrumentType.COMMUNIQUE,
        }
        for title, expected in cases.items():
            self.assertEqual(instrument_from_title(title), expected, title)

    def test_same_law_from_any_channel_is_one_work(self):
        uploaded = derive(_law(), LAW_TEXT)
        official = derive(_law(source_id="bddk_official", url=OFFICIAL_URL, source_document_id="900001",
                               file_hash="c" * 64), LAW_TEXT)
        self.assertEqual(uploaded.work_id, official.work_id)
        self.assertEqual(uploaded.identity_basis, "official_number")
        self.assertEqual(uploaded.identity_confidence, "high")

    def test_expression_is_content_identity_not_format(self):
        pdf = derive(_law(file_hash="1" * 64), LAW_TEXT)
        html = derive(_law(file_hash="2" * 64), "  KANUN Kanun  Numarası: 99001\n\nMADDE 1 –  Bu Kanunun amacı "
                                                  "kart hamillerini korumaktır.")
        amended = derive(_law(file_hash="3" * 64), LAW_TEXT + "MADDE 2 – Ek hüküm.")
        self.assertEqual(pdf.expression_id, html.expression_id)
        self.assertNotEqual(pdf.manifestation_id, html.manifestation_id)
        self.assertNotEqual(pdf.expression_id, amended.expression_id)
        self.assertEqual(pdf.work_id, amended.work_id)

    def test_regulation_citing_a_law_does_not_take_its_identity(self):
        reg = derive(_regulation(title="Örnek Kanununun Uygulanmasına İlişkin Yönetmelik"),
                     "MADDE 1 – 99001 sayılı Kanuna dayanılarak hazırlanmıştır.")
        self.assertEqual(reg.instrument_type, InstrumentType.REGULATION)
        self.assertNotIn("99001", reg.work_id)

    def test_source_scoped_and_title_identities_are_marked_weaker(self):
        self.assertEqual(derive(_regulation()).identity_confidence, "medium")
        weak = derive({"title": "Örnek Genelge", "source_id": "local_file", "file_hash": "d" * 64})
        self.assertEqual((weak.identity_basis, weak.identity_confidence), ("title", "low"))

    def test_chunk_structure_changes_role_but_never_identity(self):
        for meta in (_regulation(), {"title": "başlıksız yükleme", "source_id": "local_file", "file_hash": "z" * 64}):
            doc = derive(meta, LAW_TEXT)
            rows = derive({**meta, "structure_type": "accounting_plan"}, LAW_TEXT)
            self.assertEqual((doc.work_id, doc.expression_id, doc.instrument_type),
                             (rows.work_id, rows.expression_id, rows.instrument_type))
            self.assertEqual(rows.normative_role, NormativeRole.CLASSIFICATION)

    def test_pipeline_declared_identity_wins(self):
        rec = derive(_law(declared_work_id="eli/tr/kanun/99001", declared_version="2030-01-01"))
        self.assertEqual(rec.work_id, "eli/tr/kanun/99001")
        self.assertEqual(rec.expression_id, "eli/tr/kanun/99001@2030-01-01")
        self.assertEqual(rec.identity_basis, "declared")

    def test_body_guess_does_not_claim_an_issuer(self):
        rec = derive({"title": "Portal Sayfası", "document_type": "Kanun", "source_id": "local_file",
                      "file_hash": "e" * 64})
        self.assertEqual(rec.instrument_basis, "document_type")
        self.assertEqual(rec.issuer, "")
        self.assertEqual(derive(_law()).issuer, "TBMM")


class TestProvenance(unittest.TestCase):
    def test_user_supplied_copy_is_not_a_secondary_publisher(self):
        rec = derive(_law())
        self.assertEqual(rec.publisher_kind, PublisherKind.USER_SUPPLIED)
        self.assertEqual(rec.verification, Verification.SELF_DECLARED)
        anonymous = derive({"title": "not", "source_id": "local_file", "file_hash": "f" * 64})
        self.assertEqual(anonymous.verification, Verification.UNVERIFIED)

    def test_official_channel_requires_registered_host(self):
        self.assertEqual(derive(_regulation()).verification, Verification.OFFICIAL_CHANNEL)
        spoofed = derive(_regulation(url="https://bddk.example.com/Mevzuat/DokumanGetir/900001"))
        self.assertNotEqual(spoofed.verification, Verification.OFFICIAL_CHANNEL)

    def test_unregistered_source_is_unknown_not_trusted(self):
        rec = derive(_law(source_id="some_new_portal", url="https://portal.example/doc/1"))
        self.assertEqual(rec.publisher_kind, PublisherKind.UNKNOWN)
        self.assertEqual(rec.verification, Verification.SELF_DECLARED)

    def test_file_location_and_name_never_affect_trust(self):
        plain = derive(_law())
        for extra in ({"local_path": "C:/x/mevzuat_deposu/99001.pdf"}, {"relative_path": "resmi/kanun.pdf"},
                      {"file_name": "RESMI_GAZETE_ONAYLI.pdf"}, {"source_path": "/official/bddk/99001.pdf"}):
            self.assertEqual(derive(_law(**extra)), plain, extra)

    def test_derivation_inputs_exclude_file_location_fields(self):
        # Yapısal güvence: türetme yalnızca beyaz listedeki alanları görür.
        from mufettis.corpus.canonical import DERIVATION_INPUTS

        location = {"local_path", "relative_path", "source_path", "file_name", "legacy_source_file",
                    "file_extension", "collector"}
        self.assertFalse(set(DERIVATION_INPUTS) & location)

    def test_fingerprint_match_verifies_an_uploaded_copy(self):
        uploaded = with_canonical(_law(), LAW_TEXT)
        official = with_canonical(_law(source_id="bddk_official", url=OFFICIAL_URL, file_hash="c" * 64), LAW_TEXT)
        other = with_canonical(_law(file_hash="9" * 64), LAW_TEXT + "MADDE 2 – Farklı.")
        records = {r["manifestation_id"]: r for r in (uploaded, official, other)}
        updates = reconcile_fingerprints(records)
        self.assertEqual(updates, [{"manifestation_id": "a" * 64, "verified_against": "c" * 64}])
        verified = derive({**uploaded, "verified_against": "c" * 64})
        self.assertEqual(verified.verification, Verification.FINGERPRINT_MATCHED)


class TestValidity(unittest.TestCase):
    TODAY = dt.date(2030, 6, 1)

    def test_status_and_dates(self):
        self.assertEqual(validity({"status": "active"}, self.TODAY), Validity.IN_FORCE)
        self.assertEqual(validity({"status": "superseded"}, self.TODAY), Validity.NOT_IN_FORCE)
        self.assertEqual(validity({"status": "active", "effective_date": "01.01.2031"}, self.TODAY),
                         Validity.NOT_YET_IN_FORCE)
        self.assertEqual(validity({"status": "active", "valid_to": "2030-01-01 00:00:00"}, self.TODAY),
                         Validity.NOT_IN_FORCE)
        self.assertEqual(validity({}, self.TODAY), Validity.UNKNOWN)

    def test_validity_is_not_stored_in_canonical_record(self):
        self.assertFalse(any("valid" in k or "status" in k for k in derive(_law()).to_metadata()))


def _hit(meta: Dict[str, Any], text: str, distance: float, chunk_no: int = 0) -> Dict[str, Any]:
    return {"document": text, "metadata": {**meta, "chunk_no": str(chunk_no)}, "distance": distance}


class TestRetrievalAxesAreSeparate(unittest.TestCase):
    QUERY = "kart hamili aidat bildirimi"

    def test_more_relevant_unverified_law_beats_less_relevant_official_regulation(self):
        law = _hit(with_canonical(_law(), LAW_TEXT), "Kart hamili aidat bildirimi yapılır.", 0.50)
        reg = _hit(with_canonical(_regulation()), "Kart hamili aidat bildirimi yapılır.", 0.60)
        ranked = prefer_official_matches([reg, law], limit=2, query=self.QUERY)
        self.assertEqual(ranked[0]["metadata"]["file_hash"], "a" * 64)

    def test_authority_only_breaks_ties_and_is_bounded(self):
        guide = _hit(with_canonical({"title": "Örnek Rehber", "source_id": "local_file", "file_hash": "g" * 64}),
                     "Kart hamili aidat bildirimi yapılır.", 0.55)
        reg = _hit(with_canonical(_regulation()), "Kart hamili aidat bildirimi yapılır.", 0.55)
        ranked = prefer_official_matches([guide, reg], limit=2, query=self.QUERY)
        self.assertEqual(ranked[0]["metadata"]["file_hash"], "b" * 64)
        for item in ranked:
            gap = item["_rerank_score"] - item["_relevance_score"]
            self.assertGreaterEqual(gap, 0.0)
            self.assertLessEqual(gap, AUTHORITY_PRIOR_MAX + 1e-9)

    def test_validity_is_a_separate_tier(self):
        old = _hit(with_canonical(_law(status="superseded", file_hash="5" * 64), LAW_TEXT),
                   "Kart hamili aidat bildirimi yapılır.", 0.30)
        new = _hit(with_canonical(_law(file_hash="6" * 64), LAW_TEXT + " "), "Aidat.", 0.90)
        ranked = prefer_official_matches([old, new], limit=2, query=self.QUERY)
        self.assertEqual([r["metadata"]["status"] for r in ranked], ["active", "superseded"])
        self.assertGreater(ranked[1]["_relevance_score"], ranked[0]["_relevance_score"])

    def test_copies_of_one_expression_are_returned_once(self):
        a = _hit(with_canonical(_law(file_hash="1" * 64), LAW_TEXT), "Kart hamili aidat.", 0.40)
        b = _hit(with_canonical(_law(file_hash="2" * 64, source_id="bddk_official", url=OFFICIAL_URL), LAW_TEXT),
                 "Kart hamili aidat.", 0.40)
        c = _hit(with_canonical(_law(file_hash="2" * 64, source_id="bddk_official", url=OFFICIAL_URL), LAW_TEXT),
                 "Kart hamili aidat, ikinci madde.", 0.90, chunk_no=1)
        ranked = prefer_official_matches([a, b, c], limit=2, query=self.QUERY)
        # a ve b aynı sürümün aynı parçası: c daha az ilgili olsa da ikinci sırayı ikiz değil c alır.
        self.assertEqual([(r["metadata"]["file_hash"][0], r["metadata"]["chunk_no"]) for r in ranked],
                         [("2", "0"), ("2", "1")])

    def test_legacy_chunks_without_canonical_fields_rank_like_backfilled_ones(self):
        legacy = [_hit(_law(), "Kart hamili aidat bildirimi.", 0.5), _hit(_regulation(), "Aidat.", 0.7)]
        filled = [_hit(with_canonical(h["metadata"]), h["document"], h["distance"]) for h in legacy]
        rank = lambda hits: [(r["metadata"]["file_hash"], r["_rerank_score"]) for r in
                             prefer_official_matches(hits, limit=2, query=self.QUERY)]
        self.assertEqual(rank(legacy), rank(filled))


def _chroma_match(meta: Dict[str, Any], where: Dict[str, Any]) -> bool:
    """Chroma 1.5 filtre semantiği: `$nin` alanı olmayan kaydı geçirir."""
    if "$and" in where:
        return all(_chroma_match(meta, w) for w in where["$and"])
    for key, cond in where.items():
        value = meta.get(key)
        if isinstance(cond, dict) and "$nin" in cond:
            if value is not None and value in cond["$nin"]:
                return False
        elif value != cond:
            return False
    return True


class TestEligibility(unittest.TestCase):
    POLICY = EligibilityPolicy(exclude_roles=frozenset({NormativeRole.CLASSIFICATION}),
                               exclude_publisher_kinds=frozenset({PublisherKind.SECONDARY}))

    def _metas(self) -> Dict[str, Dict[str, Any]]:
        accounting = _regulation(structure_type="accounting_plan", file_hash="h" * 64)
        secondary = _law(source_id="mevzuat_org", url="https://www.mevzuat.org/k/99001", file_hash="s" * 64)
        return {
            "law_legacy": _law(),
            "law_canonical": with_canonical(_law(file_hash="l" * 64)),
            "accounting_legacy": accounting,
            "accounting_canonical": with_canonical(accounting),
            "secondary_canonical": with_canonical(secondary),
            "secondary_legacy": secondary,
        }

    def test_policy_decision_uses_the_canonical_model_for_legacy_and_new_chunks(self):
        admitted = {name for name, meta in self._metas().items() if self.POLICY.admits(meta)}
        self.assertEqual(admitted, {"law_legacy", "law_canonical"})

    def test_chroma_prefilter_removes_non_rule_chunks_even_without_canonical_fields(self):
        where = {"$and": self.POLICY.chroma_clauses()}
        passed = {name for name, meta in self._metas().items() if _chroma_match(meta, where)}
        self.assertNotIn("accounting_legacy", passed)
        self.assertNotIn("accounting_canonical", passed)
        # Yayıncı niteliği eski parçalarda yok: ön filtre geçirir, admits() eler.
        self.assertIn("secondary_legacy", passed)

    def test_structure_role_overrides_instrument_role(self):
        self.assertEqual(canonical_view(_regulation(structure_type="accounting_plan")).normative_role,
                         NormativeRole.CLASSIFICATION)
        self.assertEqual(canonical_view(_regulation()).normative_role, NormativeRole.RULE)
        self.assertEqual(canonical_view({"title": "Örnek Kılavuz", "file_hash": "k" * 64}).normative_role,
                         NormativeRole.GUIDANCE)


class _Collection:
    def __init__(self):
        self.rows: Dict[str, Dict[str, Any]] = {}

    def get(self, where=None, include=None, ids=None):
        hit = [(i, r) for i, r in self.rows.items() if _chroma_match(r["metadata"], where or {})]
        return {"ids": [i for i, _ in hit], "documents": [r["document"] for _, r in hit],
                "metadatas": [r["metadata"] for _, r in hit]}

    def delete(self, where=None, ids=None):
        for i in ids or self.get(where=where)["ids"]:
            self.rows.pop(i, None)

    def upsert(self, ids, documents, embeddings, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = {"document": d, "metadata": m}

    def update(self, ids, metadatas):
        for i, m in zip(ids, metadatas):
            self.rows[i]["metadata"] = m


class TestIngestionWritesCanonicalFields(unittest.TestCase):
    def _rag(self):
        from mufettis.rag.engine import LocalRAG

        rag = LocalRAG.__new__(LocalRAG)
        rag.collection = _Collection()
        rag._model = None
        rag.embed = lambda texts, **kw: [[0.0, 1.0] for _ in texts]
        return rag

    def test_every_chunk_carries_the_same_identity_as_the_index_record(self):
        rag = self._rag()
        meta = extract_legal_metadata("ÖRNEK KARTLAR KANUNU", LAW_TEXT * 3,
                                      {"source_id": "local_file", "file_hash": "a" * 64})
        result = rag.ingest_text(LAW_TEXT * 3, meta, force=True)
        chunk_metas = [r["metadata"] for r in rag.collection.rows.values()]
        self.assertTrue(chunk_metas)
        for m in chunk_metas:
            self.assertEqual(m["work_id"], meta["work_id"])
            self.assertEqual(m["expression_id"], meta["expression_id"])
            self.assertEqual(m["canonical_schema"], "1")
        self.assertEqual(result["canonical"]["work_id"], "tr/kanun/99001")

    def test_identity_correction_refreshes_canonical_fields(self):
        rag = self._rag()
        meta = extract_legal_metadata("ÖRNEK KARTLAR KANUNU", LAW_TEXT,
                                      {"source_id": "local_file", "file_hash": "a" * 64})
        rag.ingest_text(LAW_TEXT, meta, force=True)
        rag.update_hash_metadata("a" * 64, {"law_number": "99002"})
        self.assertEqual({r["metadata"]["work_id"] for r in rag.collection.rows.values()}, {"tr/kanun/99002"})
        rag.update_hash_metadata("a" * 64, {"status": "superseded"})
        self.assertEqual({r["metadata"]["work_id"] for r in rag.collection.rows.values()}, {"tr/kanun/99002"})


class TestVersionChainUsesWorkIdentity(unittest.TestCase):
    def _index(self, items: Dict[str, Dict[str, Any]]):
        class _Index:
            data = {"indexed_items": items}
        return _Index()

    def test_versions_follow_work_id_and_fall_back_for_legacy_records(self):
        from mufettis.rag.maintenance import find_previous_version

        old = with_canonical(_law(file_hash="o" * 64, document_key="eski-anahtar"), LAW_TEXT)
        other = with_canonical(_law(file_hash="x" * 64, law_number="99009", document_key="99001"), LAW_TEXT)
        new = with_canonical(_law(file_hash="n" * 64, document_key="99001"), LAW_TEXT + "MADDE 2 – Ek.")
        found = find_previous_version(self._index({"o" * 64: old, "x" * 64: other}), new, exclude_hash="n" * 64)
        self.assertEqual(found["file_hash"], "o" * 64)

        legacy = _law(file_hash="l" * 64, document_key="99001")
        found = find_previous_version(self._index({"l" * 64: legacy}), new, exclude_hash="n" * 64)
        self.assertEqual(found["file_hash"], "l" * 64)


class TestFingerprint(unittest.TestCase):
    def test_spacing_and_case_do_not_change_fingerprint(self):
        self.assertEqual(content_fingerprint("MADDE 1 – İşlem"), content_fingerprint("madde1-  işlem\n"))
        self.assertEqual(content_fingerprint(""), "")


if __name__ == "__main__":
    unittest.main()


class TestEvidenceDisplayTitle(unittest.TestCase):
    def test_meaningless_file_name_shows_the_declared_law(self):
        from mufettis.analysis.engine import evidence_display_title

        self.assertEqual(evidence_display_title(_law(title="55555")), "99001 sayılı Kanun")
        self.assertEqual(evidence_display_title(_law()), "ÖRNEK KARTLAR KANUNU")
        # Kanun numarası beyan etmeyen sayısal başlık (yayıncı kimliği) olduğu gibi kalır.
        self.assertEqual(evidence_display_title(_regulation()), "900001")

    def test_declared_title_beats_file_name_and_browser_page_title(self):
        from mufettis.analysis.engine import evidence_display_title

        meta = _law(title="Mevzuat Bilgi Sistemi", declared_title="ÖRNEK VE İLGİLİ KARTLAR KANUNU")
        self.assertEqual(evidence_display_title(meta), "99001 sayılı Örnek ve İlgili Kartlar Kanunu")


class TestDeclaredTitle(unittest.TestCase):
    def test_title_block_above_law_number_declaration(self):
        from mufettis.legal.parser import extract_declared_title

        text = ("7.09.2026 19:59 Mevzuat Bilgi Sistemi\n\nÖRNEK ALACAKLARIN YAPILANDIRILMASI İLE BAZI\n"
                "KANUNLARDA DEĞİŞİKLİK YAPILMASINA DAİR KANUN\nKanun Numarası : 99002\nKabul Tarihi : 1.1.2020\n")
        self.assertEqual(extract_declared_title(text),
                         "ÖRNEK ALACAKLARIN YAPILANDIRILMASI İLE BAZI KANUNLARDA DEĞİŞİKLİK YAPILMASINA DAİR KANUN")
        self.assertEqual(extract_declared_title("ÖRNEK KARTLAR KANUNU\n\nKanun Numarası : 99001\n"),
                         "ÖRNEK KARTLAR KANUNU")

    def test_incomplete_or_missing_titles_are_rejected(self):
        from mufettis.legal.parser import extract_declared_title

        # Adın ilk satırı sayfa başlığına karışmış: kalan kısım yalnızca genel sözcükler.
        self.assertEqual(extract_declared_title("4.09.2026 18:58 Mevzuat Bilgi Sistemi\nHAKKINDA KANUN\n"
                                                "Kanun Numarası : 99003\n"), "")
        self.assertEqual(extract_declared_title("Bu metin 5411 sayılı Kanuna atıf yapar.\nMADDE 1 –"), "")
        self.assertEqual(extract_declared_title("Karışık Harfli Başlık\nKanun Numarası : 99004\n"), "")

    def test_correspondence_subject_and_circular_number(self):
        from mufettis.analysis.engine import evidence_display_title
        from mufettis.legal.parser import extract_declared_subject

        text = ("T. C\nÖRNEK DÜZENLEME KURUMU\nSayı: 1-2\nKonu: Örnek Hesaplardan Kaynaklanan Faiz\n"
                "Oranı Riskinin Ölçülmesi Hk.\nGENELGE\n(2025/1)\nBilindiği üzere ...")
        subject = extract_declared_subject(text)
        self.assertEqual(subject, "Genelge 2025/1: Örnek Hesaplardan Kaynaklanan Faiz Oranı Riskinin Ölçülmesi Hk")
        self.assertEqual(extract_declared_subject("Konu yok.\nMADDE 1 –"), "")
        # Yalnızca başlık bir numaraysa kullanılır; tanımlayıcı başlık korunur.
        self.assertEqual(evidence_display_title(_regulation(declared_subject=subject)), subject)
        self.assertEqual(evidence_display_title(_regulation(title="Örnek Genelge", declared_subject=subject)),
                         "Örnek Genelge")

    def test_metadata_carries_declared_title(self):
        meta = extract_legal_metadata("55555", "ÖRNEK KARTLAR KANUNU\n\nKanun Numarası : 99001\nMADDE 1 – Amaç.",
                                      {"source_id": "local_file"})
        self.assertEqual(meta["declared_title"], "ÖRNEK KARTLAR KANUNU")
