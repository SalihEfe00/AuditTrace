# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

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

def run_self_tests() -> unittest.result.TestResult:
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
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return result


__all__ = [k for k in dict(globals()).keys() if not k.startswith('__')]
