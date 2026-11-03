# -*- coding: utf-8 -*-
"""M16.1: kullanıcının gördüğü her yerde ad AuditTrace; eski ad (Müfettiş AI) yalnızca kod açıklamalarında kalabilir."""
import ast
import unittest
from pathlib import Path

from mufettis.config import APP_NAME

ROOT = Path(__file__).resolve().parents[1]
# Kullanıcının gördüğü metinleri üreten dosyalar: arayüz, Word raporları, başlatıcı, yerel dil modeli istemi.
USER_FACING = [
    "app.py", "mufettis/cli.py", "packaging/launcher.py", "mufettis/llm/client.py",
    *[str(p.relative_to(ROOT)) for p in (ROOT / "mufettis" / "ui").glob("*.py")],
    *[str(p.relative_to(ROOT)) for p in (ROOT / "mufettis" / "exports").glob("*.py")],
]
OLD_NAMES = ("Müfettiş AI", "MufettisAI", "Mufettis AI")


def string_literals(path: Path):
    """Dosyadaki metin sabitleri; modül, sınıf ve işlev açıklamaları (docstring) hariç."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


class AppNameTests(unittest.TestCase):
    def test_name(self):
        self.assertEqual(APP_NAME, "AuditTrace")

    def test_no_old_name_in_user_facing_strings(self):
        found = [f"{rel}:{line}" for rel in USER_FACING for line, text in string_literals(ROOT / rel)
                 if any(old in text for old in OLD_NAMES)]
        self.assertEqual(found, [])

    def test_streamlit_page_title_uses_app_name(self):
        source = (ROOT / "mufettis" / "ui" / "app.py").read_text(encoding="utf-8")
        self.assertIn("page_title=APP_NAME", source)


if __name__ == "__main__":
    unittest.main()
