# -*- coding: utf-8 -*-
"""M16.2–M16.3: AuditTrace teması — kaynak (.streamlit/config.toml) ve paket (başlatıcı bayrakları) aynı temayı
kullanır; durum renkleri okunur karşıtlıkta; amblem dosyaları pakette."""
import tomllib
import unittest
from pathlib import Path

from streamlit import config

from mufettis.ui import theme

ROOT = Path(__file__).resolve().parents[1]


class ThemeTests(unittest.TestCase):
    def test_config_toml_matches_launcher_flags(self):
        toml = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["theme"]
        self.assertEqual({f"theme_{k}": v for k, v in toml.items()}, theme.theme_flags())

    def test_every_flag_is_a_streamlit_option(self):
        for key in theme.theme_flags():
            self.assertIn(key.replace("theme_", "theme.", 1), config._config_options_template, key)

    def test_launcher_passes_theme(self):
        self.assertIn("flags.update(theme_flags())", (ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8"))

    def test_font_files_are_served_and_packaged(self):
        # M19.1: yazı tipleri app.py'nin yanındaki static/ klasöründe, statik sunum açık, paket aynı klasörü taşır
        for face in theme.FONT_FACES:
            self.assertTrue((ROOT / face["url"].removeprefix("app/")).is_file(), face["url"])
        self.assertTrue(tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
                        ["server"]["enableStaticServing"])
        self.assertIn('"server_enableStaticServing": True', (ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8"))
        self.assertIn('"static/fonts"', (ROOT / "packaging" / "audittrace.spec").read_text(encoding="utf-8"))

    def test_text_contrast(self):
        self.assertGreaterEqual(theme.contrast(theme.INK, theme.PAPER), 7)
        self.assertGreaterEqual(theme.contrast(theme.INK_MUTED, theme.PAPER), 4.5)
        self.assertGreaterEqual(theme.contrast("#FFFFFF", theme.GREEN_INK), 4.5)
        for kind, (text, bg, edge) in theme.STATUS.items():
            self.assertGreaterEqual(theme.contrast(text, bg), 4.5, kind)
            self.assertGreaterEqual(theme.contrast(edge, theme.PAPER), 3, kind)

    def test_brand_files(self):
        for name in ("mark.svg", "mark_small.svg", "logo.svg", "icon.ico", "favicon.png"):
            self.assertTrue((theme.BRAND_DIR / name).is_file(), name)
        self.assertIn("<svg", theme.mark_svg(38))
        self.assertIn(theme.RED_PENCIL, theme.mark_svg())


class WordIdentityTests(unittest.TestCase):
    def test_rules_report_carries_identity(self):
        import io

        from docx import Document

        from mufettis.exports.rules_report import create_rules_docx

        doc = Document(io.BytesIO(create_rules_docx("Kredi dosyası kontrolü", [("a.docx", "ipotek")], [], {}, None)))
        head = doc.paragraphs[0]
        self.assertEqual([r.text for r in head.runs if r.text.strip()], ["Audit", "Trace"])
        self.assertTrue(head.runs[0]._r.xpath(".//pic:pic"))  # amblem resmi
        self.assertEqual(doc.paragraphs[1].text, "Kredi dosyası kontrolü")
        self.assertEqual(doc.styles["Heading 2"].font.name, "Georgia")
        self.assertIn("AuditTrace", doc.sections[0].footer.paragraphs[0].text)


if __name__ == "__main__":
    unittest.main()
