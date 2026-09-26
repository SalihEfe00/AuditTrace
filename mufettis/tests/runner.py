# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mufettis.tests.isolation import cleanup_stale_copies, create_isolated_data_copy, diff, snapshot

from mufettis.tests.test_extraction import (
    TestV1345PdfDependencyAndExtraction,
    TestV1369LayoutAwareExtraction,
    TestV1366PdfReconstructionAndGenericQuality,
    TestV1352QualityAndMultiEngine,
    TestV1341TextSpacing,
    TestV1342TextIntegrity,
    TestV1349RawQualityOrdering,
    TestV1344PdfTextReconstruction,
    TestV1343TextIntegrity,
    TestV1362RepairRegression,
    TestV1373EngineSelection,
    TestV1374RealOcrRegression,
    TestV1375ExtractionNormalizationStructure,
    TestV1372SchemaAwareQuality,
)
from mufettis.tests.test_rag import (
    TestV1337FinalPipeline,
    TestV1337IndexPipeline,
    TestRAGReranking,
    TestV1312ChunkIntegrity,
    TestV1313RAGIsolation,
    TestV1316ExactLawRecovery,
    TestV1324RAGInventory,
    TestV1326EvidenceInventoryReasonRecovery,
    TestV1335RetrievalBoundary,
    TestV1334IndexabilityBoundary,
    TestV1338RetrievalIntegrity,
    TestV1339RetrievalFallback,
    TestV1340DeterministicIdentityRetrieval,
    TestV1348Provenance,
    TestV1377QueryIdentityResolution,
    TestV1379SourceCacheRechunkReindex,
    TestV1381ProductionRepairScanner,
    TestV1376RealChromaRetrieval,
    TestV1347RAGDiagnosticHelpers,
)
from mufettis.tests.test_pipeline import (
    TestMufettisAI,
    TestVersioningAndScheduler,
    TestBootstrapSafety,
    TestSourceHierarchy,
    TestV1307Quality,
    TestV1308Quality,
    TestV1309Quality,
    TestV1310Phase1,
    TestV1311Hierarchy,
    TestV1314SourceParsing,
    TestV1315Hardening,
    TestV1317Hardening,
    TestV1318ReportAndAdmin,
    TestV1319Performance,
    TestV1320PerformancePipeline,
    TestV1331NonArticleEvidence,
    TestV1333StructureDiscovery,
    TestV1336ExtractionAndStructure,
    TestV1380FinalE2ERegression,
)

def build_self_test_suite() -> unittest.TestSuite:
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestMufettisAI))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestVersioningAndScheduler))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestSourceHierarchy))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestRAGReranking))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1309Quality))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1311Hierarchy))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1312ChunkIntegrity))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1310Phase1))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1307Quality))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1318ReportAndAdmin))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1319Performance))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1320PerformancePipeline))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1324RAGInventory))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1326EvidenceInventoryReasonRecovery))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1331NonArticleEvidence))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1333StructureDiscovery))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1334IndexabilityBoundary))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1336ExtractionAndStructure))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1337IndexPipeline))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1337FinalPipeline))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1338RetrievalIntegrity))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1339RetrievalFallback))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1342TextIntegrity))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1340DeterministicIdentityRetrieval))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1345PdfDependencyAndExtraction))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1348Provenance))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1344PdfTextReconstruction))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1343TextIntegrity))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1347RAGDiagnosticHelpers))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1349RawQualityOrdering))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1352QualityAndMultiEngine))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1369LayoutAwareExtraction))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1372SchemaAwareQuality))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1373EngineSelection))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1374RealOcrRegression))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1375ExtractionNormalizationStructure))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1377QueryIdentityResolution))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1376RealChromaRetrieval))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1379SourceCacheRechunkReindex))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1381ProductionRepairScanner))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1380FinalE2ERegression))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1366PdfReconstructionAndGenericQuality))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestV1362RepairRegression))
    return suite


SELF_TEST_TIMEOUT_SEC = 1800


class _ReportedTest:
    """Alt süreçten gelen test kimliğini unittest sonuç listelerinde temsil eder."""

    def __init__(self, test_id: str):
        self._test_id = test_id

    def id(self) -> str:
        return self._test_id

    def __str__(self) -> str:
        return self._test_id


class IsolatedSelfTestResult(unittest.TestResult):
    """İzole alt süreçte çalışan self-test sonucunun üst süreçteki karşılığı."""

    def __init__(self):
        super().__init__()
        self.isolated_data_dir: str = ""
        self.production_changes: List[str] = []
        self.output: str = ""

    def add_reported(self, bucket: List[Tuple[Any, str]], test_id: str, detail: str) -> None:
        bucket.append((_ReportedTest(test_id), detail))


def _run_suite_in_process(test_names: Optional[Sequence[str]] = None) -> unittest.TestResult:
    if test_names:
        suite = unittest.defaultTestLoader.loadTestsFromNames(list(test_names))
    else:
        suite = build_self_test_suite()
    return unittest.TextTestRunner(verbosity=1).run(suite)


def _result_to_dict(result: unittest.TestResult) -> Dict[str, Any]:
    return {
        "tests_run": result.testsRun,
        "failures": [[test.id(), tb] for test, tb in result.failures],
        "errors": [[test.id(), tb] for test, tb in result.errors],
        "skipped": [[test.id(), reason] for test, reason in result.skipped],
    }


def run_self_tests(test_names: Optional[Sequence[str]] = None) -> unittest.TestResult:
    """Self-testleri production verisine dokunmadan çalıştırır.

    Veri dizini geçici bir kopyaya alınır ve testler `MUFETTIS_DATA_DIR` bu kopyayı
    gösterecek şekilde ayrı bir Python sürecinde koşar (veri yolları import anında
    sabitlendiği için aynı süreçte güvenli yönlendirme mümkün değildir). Çalışma
    sonunda production veri dizini değiştiyse sonuç hata olarak işaretlenir.
    """
    from mufettis.config import BASE_DIR, DATA_DIR

    result = IsolatedSelfTestResult()
    production_before = snapshot(DATA_DIR)
    cleanup_stale_copies("mufettis_self_test_")
    temp_root, isolated_dir = create_isolated_data_copy(DATA_DIR, prefix="mufettis_self_test_")
    result.isolated_data_dir = str(isolated_dir)
    json_out = temp_root / "self_test_result.json"

    env = dict(os.environ)
    env["MUFETTIS_DATA_DIR"] = str(isolated_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        "-c",
        "import sys; from mufettis.tests.runner import _main; raise SystemExit(_main(sys.argv[1:]))",
        "--json-out",
        str(json_out),
    ]
    if test_names:
        cmd += ["--tests", *test_names]

    try:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(BASE_DIR),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SELF_TEST_TIMEOUT_SEC,
            )
            result.output = (proc.stdout or "") + (proc.stderr or "")
            output_tail = result.output[-4000:]
        except subprocess.TimeoutExpired:
            proc = None
            output_tail = f"Self-test {SELF_TEST_TIMEOUT_SEC} saniyede tamamlanmadı."
            result.output = output_tail

        if json_out.exists():
            data = json.loads(json_out.read_text(encoding="utf-8"))
            result.testsRun = int(data.get("tests_run", 0))
            for test_id, detail in data.get("failures", []):
                result.add_reported(result.failures, test_id, detail)
            for test_id, detail in data.get("errors", []):
                result.add_reported(result.errors, test_id, detail)
            for test_id, reason in data.get("skipped", []):
                result.add_reported(result.skipped, test_id, reason)
        else:
            code = proc.returncode if proc is not None else "timeout"
            result.add_reported(
                result.errors,
                "self_test_subprocess",
                f"Self-test alt süreci sonuç üretmedi (çıkış kodu: {code}).\n\n{output_tail}",
            )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    result.production_changes = diff(production_before, snapshot(DATA_DIR))
    if result.production_changes:
        result.add_reported(
            result.errors,
            "production_data_isolation",
            "Self-test sırasında production veri dizini değişti:\n" + "\n".join(result.production_changes[:50]),
        )
    return result


def _main(argv: Optional[Sequence[str]] = None) -> int:
    """Alt süreç girişi: suite'i bu süreçte çalıştırır ve sonucu JSON'a yazar."""
    parser = argparse.ArgumentParser(description="Müfettiş AI self-test (alt süreç girişi)")
    parser.add_argument("--json-out", required=True, help="Sonucun yazılacağı JSON dosyası")
    parser.add_argument("--tests", nargs="*", default=None, help="Yalnızca bu unittest adlarını çalıştır")
    args = parser.parse_args(argv)

    result = _run_suite_in_process(args.tests)
    Path(args.json_out).write_text(json.dumps(_result_to_dict(result), ensure_ascii=False), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


__all__ = [k for k in dict(globals()).keys() if not k.startswith('__')]
