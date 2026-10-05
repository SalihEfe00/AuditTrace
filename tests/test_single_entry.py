# -*- coding: utf-8 -*-
"""M15.6 tek giriş: tanınmayan belgeye tür seçilir, tanınanın türü değiştirilebilir, olay tarihi değiştirilebilir,
"Yeni kontrol başlat" yüklenenleri ve cevapları siler, ayarları ve yetki tablosunu bırakır."""
import types
from unittest import mock

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402


def _app(text):
    def script(text):
        from mufettis.ui.rules_view import render_collateral_check
        render_collateral_check(text)

    return AppTest.from_function(script, args=(text,), default_timeout=60)


def _markdown(at):
    return "\n".join(m.value for m in at.markdown)


def _status(md, rule_id):
    """Bütün kurallar tablosunda kuralın ekrandaki sonucu (M15.7: sade ad, ör. "karar verilemedi")."""
    for line in md.splitlines():
        if line.startswith("|") and f"`{rule_id}`" in line:
            return line.split("|")[1].strip().split(" ", 1)[1]
    return None


def test_unrecognised_document_gets_type_choice_and_is_checked():
    at = _app("Sözleşme\nTaraf: Kaya Tekstil Ltd. Şti.\nTutar: 800.000,00 TL\n").run()
    assert not at.exception
    assert any("tanınmadı" in c.value for c in at.caption) and not at.radio
    at.selectbox[0].set_value("rehin").run()
    assert not at.exception
    assert any("Belge türü: taşınır rehni (sizin seçtiğiniz)" in c.value for c in at.caption)
    assert "`6750-4-imza-onayi`" in _markdown(at)


def test_recognised_document_type_can_be_changed():
    from mufettis.rules.mutation_cases import BASE
    at = _app(BASE).run()
    assert not at.exception
    assert any(c.value.startswith("Belge türü: kefalet") for c in at.caption)
    at.selectbox[0].set_value("bono").run()
    assert not at.exception
    assert any("(sizin seçtiğiniz)" in c.value for c in at.caption)
    assert "TBK583" not in _markdown(at)


def test_event_date_can_be_changed_and_rules_follow_it():
    from mufettis.rules.mutation_cases import BASE
    import datetime as dt
    at = _app(BASE).run()
    assert _status(_markdown(at), "TBK584-es-rizasi-2013-oncesi") == "bu dosyaya uygulanmaz"
    at.date_input[0].set_value(dt.date(2013, 3, 15)).run()
    assert not at.exception
    assert any("Olay tarihi: 15.03.2013 (sizin girdiğiniz tarih)" in c.value for c in at.caption)
    assert _status(_markdown(at), "TBK584-es-rizasi") == "bu dosyaya uygulanmaz"


def test_new_check_clears_documents_and_answers_but_keeps_settings():
    from mufettis.ui import analysis_view
    state = {"analysis_ui_text": "x", "analysis_ui_upload_id": "a", "check_texts": {"f": 1},
             "rules_answers_abc": {"k": "v"}, "credit_file_answers_def": {}, "credit_file_kinds_def": {},
             "analysis_exec_result": object(), "analysis_ui_llm_verification": True,
             "credit_file_authority": [("Şube", "1")], "sidebar_z_threshold": 3.0, "auditor_name": "A",
             "check_upload_nonce": 2}
    with mock.patch.object(analysis_view, "st", types.SimpleNamespace(session_state=state)):
        analysis_view.reset_check()
    assert set(state) == {"analysis_ui_llm_verification", "credit_file_authority", "sidebar_z_threshold",
                          "auditor_name", "check_upload_nonce"}
    assert state["check_upload_nonce"] == 3
