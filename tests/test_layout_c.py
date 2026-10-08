# -*- coding: utf-8 -*-
"""M16.5 düzen C: simge şeridi + panel, sayfa kapları, sonuç başlığı ve çizelge.

- Seçili olmayan sayfa ve panel kapları CSS ile gizlenir; seçili olan gizlenmez.
- Kabuk varken kredi dosyasının belgeleri, olay tarihi ve adımları kenar çubuğuna (panele) düşer; kabuk yokken eskisi
  gibi ana alanda kalır (öteki UI testleri bu yolu kullanır).
- Başlık "N kontrolden K'si bulgu" ve çizelge sayıları eski gruplamayla aynı.
"""
from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest

from mufettis.ui import layout, rules_view


class ShellTests(unittest.TestCase):
    def test_only_selected_page_is_visible(self):
        for page in layout.PAGE_KEYS:
            css = layout.shell_css(page)
            hidden = css.split("{display:none !important;}")[0]
            self.assertNotIn(f".st-key-at-page-{page},", hidden + ",")
            for other in set(layout.PAGE_KEYS) - {page}:
                self.assertIn(f".st-key-at-page-{other}", hidden)
                self.assertIn(f".st-key-at-panel-{other}", hidden)

    def test_steps_html(self):
        html = layout.steps_html([("done", "Belgeler", "4 belge"), ("current", "Sorular", "1 soru"),
                                  ("todo", "Çalışma kâğıdı", "Word")])
        self.assertIn('class="at-step done"', html)
        self.assertIn('<span class="n">✓</span>', html)
        self.assertIn('<span class="n">3</span>', html)


class ResultTests(unittest.TestCase):
    def test_number_suffix(self):
        cases = {0: "0'ı", 1: "1'i", 2: "2'si", 3: "3'ü", 4: "4'ü", 5: "5'i", 6: "6'sı", 7: "7'si", 8: "8'i",
                 9: "9'u", 10: "10'u", 15: "15'i", 20: "20'si", 40: "40'ı", 60: "60'ı", 100: "100'ü", 1000: "1000'i"}
        for n, text in cases.items():
            self.assertEqual(rules_view._of(n), text)

    def test_headline(self):
        head = rules_view._headline_html({"finding": 4, "undecided": 1, "passed": 3, "missing": 7})
        self.assertIn("15 kontrolden 4'ü bulgu", head)
        self.assertIn("7 belge yok", head)
        self.assertIn("bulgu yok", rules_view._headline_html({"finding": 0, "undecided": 0, "passed": 2,
                                                              "missing": 0}))


def _shell_script():
    import streamlit as st

    from mufettis.ui import analysis_view, layout
    from mufettis.ui.rules_view import render_credit_file_check
    with st.sidebar:
        slots = {name: st.container() for name in ("steps", "upload", "docs", "date", "paste")}
    layout.set_slots(slots)
    render_credit_file_check([(n, t) for n, t, _ in analysis_view.sample_items()])


def _plain_script():
    from mufettis.ui import analysis_view, layout
    from mufettis.ui.rules_view import render_credit_file_check
    layout.set_slots(None)
    render_credit_file_check([(n, t) for n, t, _ in analysis_view.sample_items()])


class PanelTests(unittest.TestCase):
    def test_documents_and_steps_go_to_the_panel(self):
        at = AppTest.from_function(_shell_script, default_timeout=60).run()
        self.assertFalse(at.exception)
        side = "\n".join(m.value for m in at.sidebar.markdown)
        main = "\n".join(m.value for m in at.main.markdown)
        self.assertIn("a_kredi_onay.docx", side)
        self.assertIn("at-step", side)
        self.assertIn("4 belge tanındı", side)
        self.assertIn("1 soru cevabınızı bekliyor", side)
        self.assertEqual(len(at.sidebar.date_input), 1)
        self.assertIn("15 kontrolden 4'ü bulgu", main)
        self.assertIn('<td class="ref">K-15</td>', main)
        self.assertNotIn('<td class="ref">K-16</td>', main)
        self.assertIn("#### Bulgular (4)", main)

    def test_without_shell_everything_stays_in_main(self):
        at = AppTest.from_function(_plain_script, default_timeout=60).run()
        self.assertFalse(at.exception)
        self.assertFalse(at.sidebar.markdown)
        self.assertIn("a_kredi_onay.docx", "\n".join(m.value for m in at.main.markdown))
        self.assertEqual(len(at.main.date_input), 1)


if __name__ == "__main__":
    unittest.main()
