# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path

from mufettis.config import DATA_DIR, EMBEDDING_MODEL
from repair.models import (
    ComparisonResult,
    ComparisonStatus,
    FinalGateDecision,
    RetrievalBenchmarkResult,
    RetrievalComparisonResult,
)
from repair.retrieval_comparator import (
    RetrievalGatePolicy,
    compare_retrieval,
    evaluate_final_gate,
    evaluate_single_benchmark,
)

RAG_LAB_DIR = DATA_DIR / "rag_lab"
RAG_LAB_BASELINE_FILE = RAG_LAB_DIR / "retrieval_baseline.json"


class TestFaz11BRetrievalQualityGate(unittest.TestCase):
    def setUp(self):
        self.policy = RetrievalGatePolicy()
        self.dummy_base = {
            "version": "1.0.0",
            "model_name": EMBEDDING_MODEL,
            "benchmarks": [
                {
                    "id": "BM-01",
                    "type": "exact_law",
                    "query": "5411 sayılı Kanun",
                    "hit": True,
                    "rank": 1,
                    "reference_match": True,
                    "retrievable": True,
                },
                {
                    "id": "BM-02",
                    "type": "document_lookup",
                    "query": "Bankaların Kredi İşlemleri",
                    "hit": True,
                    "rank": 2,
                    "document_match": True,
                    "retrievable": True,
                },
                {
                    "id": "BM-06",
                    "type": "accounting_lookup",
                    "query": "010 KASA",
                    "hit": True,
                    "rank": 1,
                    "account_match": True,
                    "retrievable": True,
                },
                {
                    "id": "BM-08",
                    "type": "semantic",
                    "query": "kredi komitesi esasları",
                    "hit": True,
                    "rank": 3,
                    "retrievable": True,
                },
            ],
        }

    # 1. CURRENT hit / CANDIDATE hit same rank -> REVIEW / no improvement
    def test_01_current_hit_candidate_hit_same_rank(self):
        cand = [
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "retrievable": True},
            {"id": "BM-02", "hit": True, "rank": 2, "document_match": True, "retrievable": True},
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": True, "retrievable": True},
            {"id": "BM-08", "hit": True, "rank": 3, "retrievable": True},
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)
        self.assertEqual(res.aggregate_delta["improved_benchmarks"], 0)
        self.assertEqual(res.aggregate_delta["regressed_benchmarks"], 0)

    # 2. CURRENT miss / CANDIDATE hit -> improvement
    def test_02_current_miss_candidate_hit(self):
        base = {
            "benchmarks": [
                {"id": "BM-01", "type": "exact_law", "hit": False, "rank": None, "retrievable": True}
            ]
        }
        cand = [
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "retrievable": True}
        ]
        res = compare_retrieval(base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)
        self.assertEqual(res.benchmark_results[0].status, "IMPROVED")

    # 3. CURRENT hit / CANDIDATE miss -> regression (REJECT)
    def test_03_current_hit_candidate_miss(self):
        cand = [
            {"id": "BM-01", "hit": False, "rank": None, "reference_match": False, "retrievable": True},
            {"id": "BM-02", "hit": True, "rank": 2, "document_match": True, "retrievable": True},
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": True, "retrievable": True},
            {"id": "BM-08", "hit": True, "rank": 3, "retrievable": True},
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertGreater(len(res.hard_failures), 0)

    # 4. CURRENT rank 5 / CANDIDATE rank 2 -> improvement
    def test_04_current_rank_5_candidate_rank_2(self):
        base = {
            "benchmarks": [
                {"id": "BM-08", "type": "semantic", "hit": True, "rank": 5, "retrievable": True}
            ]
        }
        cand = [
            {"id": "BM-08", "hit": True, "rank": 2, "retrievable": True}
        ]
        res = compare_retrieval(base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)
        self.assertEqual(res.benchmark_results[0].status, "IMPROVED")

    # 5. CURRENT rank 2 / CANDIDATE rank 7 -> regression signal
    def test_05_current_rank_2_candidate_rank_7(self):
        base = {
            "benchmarks": [
                {"id": "BM-08", "type": "semantic", "hit": True, "rank": 2, "retrievable": True}
            ]
        }
        cand = [
            {"id": "BM-08", "hit": True, "rank": 7, "retrievable": True}
        ]
        res = compare_retrieval(base, cand, self.policy)
        self.assertEqual(res.benchmark_results[0].status, "REGRESSED")
        self.assertIn(res.status, [ComparisonStatus.REVIEW.value, ComparisonStatus.REJECT.value])

    # 6. document retrieval lost -> REJECT
    def test_06_document_retrieval_lost(self):
        cand = [
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "retrievable": True},
            {"id": "BM-02", "hit": True, "rank": 2, "document_match": False, "retrievable": True},
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": True, "retrievable": True},
            {"id": "BM-08", "hit": True, "rank": 3, "retrievable": True},
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(any("doküman" in f.lower() for f in res.hard_failures))

    # 7. account retrieval lost -> REJECT
    def test_07_account_retrieval_lost(self):
        cand = [
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "retrievable": True},
            {"id": "BM-02", "hit": True, "rank": 2, "document_match": True, "retrievable": True},
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": False, "retrievable": True},
            {"id": "BM-08", "hit": True, "rank": 3, "retrievable": True},
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(any("muhasebe" in f.lower() for f in res.hard_failures))

    # 8. reference retrieval lost -> REJECT
    def test_08_reference_retrieval_lost(self):
        cand = [
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": False, "retrievable": True},
            {"id": "BM-02", "hit": True, "rank": 2, "document_match": True, "retrievable": True},
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": True, "retrievable": True},
            {"id": "BM-08", "hit": True, "rank": 3, "retrievable": True},
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(any("yasal" in f.lower() for f in res.hard_failures))

    # 9. semantic hit improved -> positive signal
    def test_09_semantic_hit_improved(self):
        base = {
            "benchmarks": [
                {"id": "BM-08", "type": "semantic", "hit": False, "rank": None, "retrievable": True}
            ]
        }
        cand = [
            {"id": "BM-08", "hit": True, "rank": 1, "retrievable": True}
        ]
        res = compare_retrieval(base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)
        self.assertEqual(res.benchmark_results[0].status, "IMPROVED")

    # 10. semantic hit lost -> negative signal
    def test_10_semantic_hit_lost(self):
        base = {
            "benchmarks": [
                {"id": "BM-08", "type": "semantic", "hit": True, "rank": 1, "retrievable": True}
            ]
        }
        cand = [
            {"id": "BM-08", "hit": False, "rank": None, "retrievable": True}
        ]
        res = compare_retrieval(base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)

    # 11. metadata filtering regression -> negative / REVIEW
    def test_11_metadata_filtering_regression(self):
        cand = [
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "metadata_match": False, "retrievable": True},
            {"id": "BM-02", "hit": True, "rank": 2, "document_match": True, "retrievable": True},
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": True, "retrievable": True},
            {"id": "BM-08", "hit": True, "rank": 3, "retrievable": True},
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)
        self.assertGreater(len(res.warnings), 0)

    # 12. retrievability lost -> hard regression (REJECT)
    def test_12_retrievability_lost(self):
        cand = [
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "retrievable": False},
            {"id": "BM-02", "hit": True, "rank": 2, "document_match": True, "retrievable": True},
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": True, "retrievable": True},
            {"id": "BM-08", "hit": True, "rank": 3, "retrievable": True},
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(any("retrievability" in f.lower() for f in res.hard_failures))

    # 13. embedding configuration mismatch -> invalid / REJECT
    def test_13_embedding_configuration_mismatch(self):
        cand = {
            "model_name": "different-embedding-model-v1",
            "benchmarks": [
                {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "retrievable": True}
            ],
        }
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REJECT.value)
        self.assertTrue(any("embedding model" in f.lower() for f in res.hard_failures))

    # 14. mixed signals -> REVIEW
    def test_14_mixed_signals(self):
        cand = [
            # BM-01 rank improved 1 -> 1 (neutral)
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "retrievable": True},
            # BM-02 improved rank 2 -> 1
            {"id": "BM-02", "hit": True, "rank": 1, "document_match": True, "retrievable": True},
            # BM-06 neutral
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": True, "retrievable": True},
            # BM-08 regressed rank 3 -> 9
            {"id": "BM-08", "hit": True, "rank": 9, "retrievable": True},
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)
        self.assertIn("Mixed signals", res.confidence_explanation)

    # 15. clear improvement -> READY_TO_COMMIT
    def test_15_clear_improvement(self):
        cand = [
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "retrievable": True},
            {"id": "BM-02", "hit": True, "rank": 1, "document_match": True, "retrievable": True},  # rank 2 -> 1
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": True, "retrievable": True},
            {"id": "BM-08", "hit": True, "rank": 1, "retrievable": True},  # rank 3 -> 1
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)
        self.assertEqual(res.aggregate_delta["regressed_benchmarks"], 0)
        self.assertGreaterEqual(res.aggregate_delta["improved_benchmarks"], 1)

    # 16. all unchanged -> REVIEW
    def test_16_all_unchanged(self):
        cand = [
            {"id": "BM-01", "hit": True, "rank": 1, "reference_match": True, "retrievable": True},
            {"id": "BM-02", "hit": True, "rank": 2, "document_match": True, "retrievable": True},
            {"id": "BM-06", "hit": True, "rank": 1, "account_match": True, "retrievable": True},
            {"id": "BM-08", "hit": True, "rank": 3, "retrievable": True},
        ]
        res = compare_retrieval(self.dummy_base, cand, self.policy)
        self.assertEqual(res.status, ComparisonStatus.REVIEW.value)

    # 17. FAZ 11A READY + 11B REJECT -> final REJECT
    def test_17_faz11a_ready_11b_reject(self):
        res_a = ComparisonResult(document_id="doc1", status=ComparisonStatus.READY_TO_COMMIT.value)
        res_b = RetrievalComparisonResult(status=ComparisonStatus.REJECT.value, hard_failures=["Lost BM-01"])
        final = evaluate_final_gate(res_a, res_b)
        self.assertEqual(final.final_status, ComparisonStatus.REJECT.value)

    # 18. FAZ 11A REJECT + 11B READY -> final REJECT
    def test_18_faz11a_reject_11b_ready(self):
        res_a = ComparisonResult(document_id="doc1", status=ComparisonStatus.REJECT.value, hard_failures=["Mojibake introduced"])
        res_b = RetrievalComparisonResult(status=ComparisonStatus.READY_TO_COMMIT.value)
        final = evaluate_final_gate(res_a, res_b)
        self.assertEqual(final.final_status, ComparisonStatus.REJECT.value)

    # 19. FAZ 11A READY + 11B REVIEW -> final REVIEW
    def test_19_faz11a_ready_11b_review(self):
        res_a = ComparisonResult(document_id="doc1", status=ComparisonStatus.READY_TO_COMMIT.value)
        res_b = RetrievalComparisonResult(status=ComparisonStatus.REVIEW.value, warnings=["Mixed signals"])
        final = evaluate_final_gate(res_a, res_b)
        self.assertEqual(final.final_status, ComparisonStatus.REVIEW.value)

    # 20. FAZ 11A READY + 11B READY -> final READY_TO_COMMIT
    def test_20_faz11a_ready_11b_ready(self):
        res_a = ComparisonResult(document_id="doc1", status=ComparisonStatus.READY_TO_COMMIT.value)
        res_b = RetrievalComparisonResult(status=ComparisonStatus.READY_TO_COMMIT.value)
        final = evaluate_final_gate(res_a, res_b)
        self.assertEqual(final.final_status, ComparisonStatus.READY_TO_COMMIT.value)

    # 21. Real mini-RAG benchmark validation against data/rag_lab/retrieval_baseline.json
    def test_21_real_mini_rag_baseline_comparison(self):
        self.assertTrue(RAG_LAB_BASELINE_FILE.exists())
        base_data = json.loads(RAG_LAB_BASELINE_FILE.read_text(encoding="utf-8"))

        # Candidate that reproduces current baseline + improves BM-08 rank from 3 to 1
        base_benchmarks = []
        for b in base_data["benchmarks"]:
            base_b = dict(b)
            if b["id"] == "BM-08":
                base_b["rank"] = 3
            base_benchmarks.append(base_b)

        base_copy = {**base_data, "benchmarks": base_benchmarks}

        cand_benchmarks = []
        for b in base_benchmarks:
            cand_b = dict(b)
            if b["id"] == "BM-08":
                cand_b["rank"] = 1  # improved from rank 3 to rank 1
            cand_benchmarks.append(cand_b)

        res = compare_retrieval(base_copy, cand_benchmarks, self.policy)
        self.assertEqual(res.status, ComparisonStatus.READY_TO_COMMIT.value)
        self.assertEqual(len(res.hard_failures), 0)
        self.assertGreaterEqual(res.aggregate_delta["improved_benchmarks"], 1)


if __name__ == "__main__":
    unittest.main()
