# -*- coding: utf-8 -*-
"""M15.8 mevzuat yardımcısı: kısa sonuç cümlesi, şablonlar geri çağrıyla, teknik ayrıntı anahtar kapalıyken çizilmez
(kanıtlar kaybolmaz, anahtar açılınca görünür)."""
import types
import unittest
from unittest.mock import MagicMock, patch

from mufettis.analysis.analysis_presenter import AnalysisPresenter
from mufettis.analysis.models import Evidence
from mufettis.ui import analysis_view
from mufettis.ui.analysis_view import SAMPLE_COMPLAINT_TEXT, _assistant_sentence, render_analysis_page


def _evidence():
    return Evidence(source_document="Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği", document_identity="5411",
                    chunk_reference="Madde 13 Fıkra 1",
                    text_span="Vadesiz mevduat hesaplarından hesap işletim ücreti alınamaz.",
                    metadata={"source_id": "bddk_official"})


class TestAssistantView(unittest.TestCase):
    def setUp(self):
        self.presenter = AnalysisPresenter(audit_sink=lambda *a, **k: None)
        self.res = self.presenter.execute_analysis(text=SAMPLE_COMPLAINT_TEXT, sample_evidence=[_evidence()])
        self.assertTrue(self.res.success)

    def test_sentence(self):
        result = self.res.result
        sentence = _assistant_sentence(result)
        self.assertTrue(sentence.endswith("."))
        if result.findings:
            self.assertIn(f"{len(result.findings)} olası bulgu", sentence)
        empty = types.SimpleNamespace(findings=[], status="completed_insufficient_evidence", unsupported_claims=[1])
        self.assertIn("uygunluk teyidi değildir", _assistant_sentence(empty))
        self.assertIn("1 iddia için mevzuat dayanağı bulunamadı", _assistant_sentence(empty))

    def test_template_callback_fills_text(self):
        state = {}
        with patch.object(analysis_view, "st", types.SimpleNamespace(session_state=state)):
            analysis_view._load_template(analysis_view.SAMPLE_TEMPLATES["📑 Örnek Tüketici Şikayeti"])
        self.assertEqual(state["analysis_ui_text"], SAMPLE_COMPLAINT_TEXT)
        self.assertIsNone(state["analysis_exec_result"])

    def test_technical_details_only_when_toggled(self):
        for toggled in (False, True):
            mock_st = MagicMock()
            mock_st.session_state = {"analysis_exec_result": self.res}
            mock_st.columns.side_effect = lambda n, **k: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
            mock_st.button.return_value = False
            mock_st.text_input.return_value = ""
            mock_st.text_area.return_value = ""
            mock_st.selectbox.return_value = "Bekliyor"
            mock_st.toggle.return_value = toggled
            with patch("mufettis.ui.analysis_view.st", mock_st):
                render_analysis_page(presenter=self.presenter)
            codes = [c.args[0] for c in mock_st.code.call_args_list if c.args]
            self.assertEqual(_evidence().text_span in codes, toggled)
            headings = [c.args[0] for c in mock_st.markdown.call_args_list if c.args]
            self.assertTrue(any(h.startswith("#### Olası bulgular") for h in headings))
            self.assertEqual(any("Getirilen bütün mevzuat kanıtları" in h for h in headings), toggled)


if __name__ == "__main__":
    unittest.main()
