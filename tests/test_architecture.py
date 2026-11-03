# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class TestArchitectureBoundaries(unittest.TestCase):
    """
    Müfettiş AI Katmanlı Mimari Sınır Testleri (FAZ 8)

    Doğrulanan Katman Hiyerarşisi:
    UI
    ↓
    ctx / orchestration
    ↓
    audit / reporting / rag / legal / extraction
    ↓
    core (config, utils)
    """

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent / "mufettis"
        cls.modules = {
            "core": [cls.root / "config.py", cls.root / "common" / "utils.py"],
            "extraction": list((cls.root / "extraction").glob("*.py")),
            "legal": list((cls.root / "legal").glob("*.py")),
            "rag": list((cls.root / "rag").glob("*.py")),
            "audit": list((cls.root / "audit").glob("*.py")),
            "reporting": list((cls.root / "exports").glob("*.py")),
            "ui": list((cls.root / "ui").glob("*.py")),
            "repair": list((Path(__file__).resolve().parent.parent / "mufettis" / "repair").glob("*.py")),
        }

    def _get_imports(self, file_path: Path):
        with open(file_path, "r", encoding="utf-8") as fp:
            tree = ast.parse(fp.read(), filename=str(file_path))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.append(node.module)
        return imported_modules

    def test_core_does_not_import_ui(self):
        """Kural: core -> ui ihlali olmamalı."""
        for path in self.modules["core"]:
            imports = self._get_imports(path)
            for imp in imports:
                self.assertFalse(
                    "ui" in imp or "streamlit" in imp,
                    f"Architecture Violation: core ({path.name}) UI/streamlit import edemez: {imp}",
                )

    def test_legal_does_not_import_ui(self):
        """Kural: legal -> ui ihlali olmamalı."""
        for path in self.modules["legal"]:
            imports = self._get_imports(path)
            for imp in imports:
                self.assertFalse(
                    "ui" in imp or "streamlit" in imp,
                    f"Architecture Violation: legal ({path.name}) UI/streamlit import edemez: {imp}",
                )

    def test_reporting_does_not_import_ui(self):
        """Kural: reporting -> ui ihlali olmamalı."""
        for path in self.modules["reporting"]:
            imports = self._get_imports(path)
            for imp in imports:
                self.assertFalse(
                    "ui" in imp or "streamlit" in imp,
                    f"Architecture Violation: reporting ({path.name}) UI import edemez: {imp}",
                )

    def test_audit_does_not_import_ui(self):
        """Kural: audit -> ui ihlali olmamalı."""
        for path in self.modules["audit"]:
            imports = self._get_imports(path)
            for imp in imports:
                self.assertFalse(
                    "ui" in imp or "streamlit" in imp,
                    f"Architecture Violation: audit ({path.name}) UI import edemez: {imp}",
                )

    def test_audit_does_not_import_rag_repair_implementation(self):
        """Kural: audit -> rag repair implementation ihlali olmamalı."""
        for path in self.modules["audit"]:
            imports = self._get_imports(path)
            for imp in imports:
                self.assertFalse(
                    "repair" in imp or "maintenance" in imp,
                    f"Architecture Violation: audit ({path.name}) RAG repair/maintenance import edemez: {imp}",
                )

    def test_extraction_does_not_import_streamlit(self):
        """Kural: extraction -> streamlit ihlali olmamalı."""
        for path in self.modules["extraction"]:
            imports = self._get_imports(path)
            for imp in imports:
                self.assertFalse(
                    "streamlit" in imp,
                    f"Architecture Violation: extraction ({path.name}) streamlit import edemez: {imp}",
                )

    def test_core_does_not_import_streamlit(self):
        """Kural: core -> streamlit ihlali olmamalı."""
        for path in self.modules["core"]:
            imports = self._get_imports(path)
            for imp in imports:
                self.assertFalse(
                    "streamlit" in imp,
                    f"Architecture Violation: core ({path.name}) streamlit import edemez: {imp}",
                )

    def test_repair_does_not_import_ui_or_streamlit(self):
        """Kural: repair -> ui / streamlit ihlali olmamalı."""
        for path in self.modules["repair"]:
            imports = self._get_imports(path)
            for imp in imports:
                self.assertFalse(
                    "ui" in imp or "streamlit" in imp,
                    f"Architecture Violation: repair ({path.name}) UI/streamlit import edemez: {imp}",
                )

    def test_repair_decision_modules_do_not_import_llm(self):
        """Kural: repair scanner, classifier, comparator karar süreçlerinde LLM import edemez (deterministik kalmalı)."""
        deterministic_modules = [
            p for p in self.modules["repair"]
            if p.name in ("scanner.py", "classifier.py", "comparator.py")
        ]
        for path in deterministic_modules:
            imports = self._get_imports(path)
            for imp in imports:
                self.assertFalse(
                    any(x in imp for x in ("llm", "google.genai", "openai", "anthropic")),
                    f"Architecture Violation: repair ({path.name}) LLM import edemez: {imp}",
                )

    def test_repair_scanner_does_not_import_write_functions(self):
        """Kural: repair scanner üretim yazma fonksiyonlarını doğrudan import edemez."""
        scanner_path = next(p for p in self.modules["repair"] if p.name == "scanner.py")
        with open(scanner_path, "r", encoding="utf-8") as fp:
            tree = ast.parse(fp.read(), filename=str(scanner_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.assertNotIn(
                        alias.name,
                        ["save_source_cache", "reset_chroma_collection", "rebuild_rag_from_source_cache"],
                        f"Architecture Violation: repair scanner cannot import write function: {alias.name}",
                    )


if __name__ == "__main__":
    unittest.main()
