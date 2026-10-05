# -*- coding: utf-8 -*-
"""Streamlit sunucusu yalnızca yerel adresi dinler ve kullanım istatistiği göndermez (kaynak kurulum ve paket)."""
from __future__ import annotations

import ast
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestStreamlitConfig(unittest.TestCase):
    def test_source_config_is_local_only_without_telemetry(self):
        cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["server"]["address"], "127.0.0.1")
        self.assertIs(cfg["browser"]["gatherUsageStats"], False)
        self.assertEqual(cfg["client"]["toolbarMode"], "minimal")  # M15.9: Deploy gizli

    def test_package_launcher_passes_the_same_settings(self):
        tree = ast.parse((ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8"))
        flags = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "flags" for t in node.targets):
                flags = {k.value: v for k, v in zip(node.value.keys, node.value.values)}
        self.assertEqual(ast.literal_eval(flags["server_address"]), "127.0.0.1")
        self.assertIs(ast.literal_eval(flags["browser_gatherUsageStats"]), False)
        self.assertEqual(ast.literal_eval(flags["client_toolbarMode"]), "minimal")


if __name__ == "__main__":
    unittest.main()
