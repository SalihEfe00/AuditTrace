"""RAG envanteri yalnızca düğmeyle hesaplanır (2026-10-03): bütün parçaları okuyan envanter, Streamlit görünmeyen
sekmeleri de çalıştırdığı için her etkileşimde yeniden hesaplanıyor ve arayüzü dakikalarca kilitliyordu."""
import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402


def _script():
    from unittest.mock import MagicMock, patch

    import streamlit as st

    calls = st.session_state.setdefault("inventory_calls", [])

    def fake_inventory(rag):
        calls.append(1)
        return {"available": True, "total_documents": 2, "total_chunks": 5, "active_documents": 2,
                "sources": [], "documents": []}

    rag = MagicMock()
    rag.count.return_value = 5
    with patch("mufettis.ui.admin.build_rag_inventory", fake_inventory):
        from mufettis.ui.admin import render_mevzuat_admin
        render_mevzuat_admin(rag=rag, local_only=True, use_browser_bddk=False, allow_embedding_download=False)
    st.write(f"envanter çağrısı: {len(calls)}")


def _calls(at):
    return next(m.value for m in at.markdown if m.value.startswith("envanter çağrısı"))


def test_inventory_is_built_only_on_button_and_kept_across_reruns():
    at = AppTest.from_function(_script, default_timeout=60).run()
    assert not at.exception
    assert _calls(at) == "envanter çağrısı: 0"
    assert any("henüz hesaplanmadı" in c.value for c in at.caption)
    at.button(key="rag_inventory_build").click().run()
    assert _calls(at) == "envanter çağrısı: 1"
    assert any("hesaplanma zamanı" in c.value for c in at.caption)
    at.run()  # başka bir etkileşim: envanter yeniden hesaplanmaz, saklanan gösterilir
    assert _calls(at) == "envanter çağrısı: 1"
    assert any(m.label == "Belge" for m in at.metric)
