# -*- coding: utf-8 -*-
"""
Statik tanımsız isim denetimi (harici linter gerektirmez).

Her proje dosyasında okunan ama tanımlanmamış/import edilmemiş isimleri symtable
ile bulur. Bu tür hatalar (ör. risk_engine'de import edilmeden kullanılan
clean_evidence_text) yalnızca ilgili kod yolu çalıştığında NameError verir;
bu test onları derleme zamanında yakalar. Proje genelinde `import *` de yasaktır.
"""
from __future__ import annotations

import ast
import builtins
import symtable
import unittest
from pathlib import Path
from typing import List, Set

ROOT = Path(__file__).resolve().parent.parent
_ALWAYS_DEFINED = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__spec__", "__builtins__", "__package__",
    "__loader__", "__path__", "__annotations__", "__dict__", "__class__",
}


def _project_files() -> List[Path]:
    files = list((ROOT / "mufettis").rglob("*.py")) + list((ROOT / "repair").rglob("*.py"))
    files += list((ROOT / "tests").glob("*.py")) + [ROOT / "app.py", ROOT / "conftest.py"]
    return sorted(p for p in files if "__pycache__" not in p.parts)


def _module_bound_names(tree: ast.Module) -> Set[str]:
    """Modül kapsamında bağlanan isimler (if/try içindekiler ve satır içi üreteç değişkenleri dahil)."""
    bound: Set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            bound.add(node.name)
        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            bound.add(node.name)

        def visit_Import(self, node):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])

        def visit_ImportFrom(self, node):
            for alias in node.names:
                if alias.name != "*":
                    bound.add(alias.asname or alias.name)

        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)

        def visit_ExceptHandler(self, node):
            if node.name:
                bound.add(node.name)
            self.generic_visit(node)

        def visit_Lambda(self, node):
            pass

        def visit_ListComp(self, node):
            # PEP 709: üreteç değişkenleri symtable'da modül kapsamında görünür.
            for gen in node.generators:
                for sub in ast.walk(gen.target):
                    if isinstance(sub, ast.Name):
                        bound.add(sub.id)
        visit_SetComp = visit_DictComp = visit_GeneratorExp = visit_ListComp

    Visitor().visit(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            bound.update(node.names)
    return bound


def _referenced_globals(source: str, filename: str) -> Set[str]:
    names: Set[str] = set()

    def walk(table, is_module):
        for sym in table.get_symbols():
            if sym.is_referenced() and (is_module or sym.is_global()):
                names.add(sym.get_name())
        for child in table.get_children():
            walk(child, False)

    walk(symtable.symtable(source, filename, "exec"), True)
    return names


def undefined_names(path: Path) -> List[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, str(path))
    bound = _module_bound_names(tree)
    return sorted(n for n in _referenced_globals(source, str(path)) if n not in bound and n not in _ALWAYS_DEFINED)


def star_imports(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    return [
        f"L{node.lineno}: from {node.module} import *"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
    ]


def duplicate_top_level_definitions(path: Path) -> List[str]:
    """Modül düzeyinde aynı adla iki kez tanımlanan fonksiyon/sınıflar: ikincisi ilkini sessizce
    gölgeler (E8: pdf.py'de iki farklı _extract_pdf_with_pdfplumber vardı)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    seen: dict = {}
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in seen:
                out.append(f"{node.name} (L{seen[node.name]}, L{node.lineno})")
            seen.setdefault(node.name, node.lineno)
    return out


class TestStaticNames(unittest.TestCase):
    def test_no_undefined_names_in_project(self):
        problems = {}
        for path in _project_files():
            missing = undefined_names(path)
            if missing:
                problems[str(path.relative_to(ROOT))] = missing
        self.assertEqual(problems, {}, "Tanımsız isimler (eksik import?)")

    def test_no_star_imports(self):
        problems = {str(p.relative_to(ROOT)): star_imports(p) for p in _project_files() if star_imports(p)}
        self.assertEqual(problems, {}, "`import *` yerine açık import kullanın")

    def test_no_shadowed_top_level_definitions(self):
        problems = {str(p.relative_to(ROOT)): d for p in _project_files() if (d := duplicate_top_level_definitions(p))}
        self.assertEqual(problems, {}, "Aynı adla ikinci tanım ilkini gölgeliyor")

    def test_detector_finds_a_missing_import(self):
        """Denetleyicinin kendisi: import edilmemiş isim yakalanmalı, yerel/builtin isimler yakalanmamalı."""
        import tempfile

        code = (
            "import os\n"
            "def f(x):\n"
            "    y = [i for i in range(x)]\n"
            "    return os.sep, len(y), clean_evidence_text(x)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ornek.py"
            path.write_text(code, encoding="utf-8")
            self.assertEqual(undefined_names(path), ["clean_evidence_text"])


if __name__ == "__main__":
    unittest.main()
