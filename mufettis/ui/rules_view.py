# -*- coding: utf-8 -*-
"""Teminat uygunluk kontrolü bölümü (M9): kefalet sözleşmesi kuralları, belirsiz alanlar için müfettişe soru.

Mantık arayüzden bağımsız modüllerde (mufettis.rules.*); burada yalnızca çizim ve oturum durumu.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Optional

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

from mufettis.rules.catalog import load_catalog
from mufettis.rules.engine import EXEMPT, MANUAL, MET, NOT_APPLICABLE, NOT_MET, UNKNOWN, evaluate
from mufettis.rules.fields_kefalet import extract_kefalet_fields
from mufettis.rules.review import UNKNOWN_ANSWER, apply_answers, pending_questions

ICON = {MET: "✅", NOT_MET: "❌", UNKNOWN: "❔", MANUAL: "📝", EXEMPT: "➖", NOT_APPLICABLE: "➖"}


def render_collateral_check(text: str, event_date: Optional[dt.date] = None) -> None:
    if st is None:
        return
    with st.expander("Teminat uygunluk kontrolü — kefalet sözleşmesi (TBK 583–584, 5070, TKS md. 28)"):
        if not (text or "").strip():
            st.caption("Kontrol için önce belge metnini girin.")
            return
        rules = load_catalog()
        fields = extract_kefalet_fields(text)
        # Cevaplar belgeye bağlı: metin değişince eski beyanlar yeni belgeye taşınmaz.
        doc_key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        answers = st.session_state.setdefault(f"rules_answers_{doc_key}", {})
        fields = apply_answers(fields, answers)
        results = evaluate(rules, "kefalet_sozlesmesi", fields, text=text, event_date=event_date or dt.date.today())
        questions = pending_questions(rules, results, fields)
        if questions:
            st.markdown("**Belgeden anlaşılamayanlar** — cevaplarınız *müfettiş beyanı* olarak kanıta yazılır:")
            for q in questions:
                labels = [UNKNOWN_ANSWER] + [shown for shown, _ in q.options]
                choice = st.radio(q.text, labels, horizontal=True, key=f"rules_q_{doc_key}_{q.field}")
                value = dict(q.options).get(choice) if choice != UNKNOWN_ANSWER else None
                if answers.get(q.field) != value:
                    answers[q.field] = value
                    st.rerun()
        st.markdown("**Sonuçlar**")
        for res in results:
            line = f"{ICON.get(res.status, '•')} **{res.status}** — `{res.rule_id}` ({res.basis})"
            if res.finding:
                line += f": {res.finding}"
            st.markdown(line)
            shown = [e for e in res.evidence if e[1] != "bulunamadı"]
            if shown:
                st.caption(" · ".join(f"{name}={value if value is not None else '(boş)'} [{source}; {where}]"
                                      for name, _state, value, where, source in shown))


__all__ = ["render_collateral_check"]
