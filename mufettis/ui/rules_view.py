# -*- coding: utf-8 -*-
"""Teminat uygunluk kontrolü bölümü (M9): kefalet, ipotek, rehin, ekspertiz, DASK kuralları; belirsiz alanlar için
müfettişe soru.

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
from mufettis.rules.doctype import detect_collateral_type
from mufettis.rules.event_date import document_event_date
from mufettis.rules.engine import EXEMPT, MANUAL, MET, NOT_APPLICABLE, NOT_MET, UNKNOWN, evaluate
from mufettis.rules.fields_kefalet import extract_kefalet_fields
from mufettis.rules.fields_teminat import extract_teminat_fields
from mufettis.rules.review import UNKNOWN_ANSWER, apply_answers, pending_questions

TYPE_NAMES = {"kefalet_sozlesmesi": "kefalet sözleşmesi", "ipotek": "ipotek", "rehin": "taşınır rehni",
              "ekspertiz": "ekspertiz / değerleme raporu", "dask": "DASK poliçesi", "bono": "bono"}
ICON = {MET: "✅", NOT_MET: "❌", UNKNOWN: "❔", MANUAL: "📝", EXEMPT: "➖", NOT_APPLICABLE: "➖"}


def render_collateral_check(text: str, event_date: Optional[dt.date] = None) -> None:
    if st is None:
        return
    with st.expander("Teminat uygunluk kontrolü — kefalet, ipotek, rehin, ekspertiz, DASK, bono"):
        if not (text or "").strip():
            st.caption("Kontrol için önce belge metnini girin.")
            return
        doc_type = detect_collateral_type(text)
        kind = doc_type.document_type
        if kind not in TYPE_NAMES:
            st.caption(f"Belge kurallı bir teminat belgesi olarak tanınmadı ({doc_type.reason}).")
            return
        st.caption(f"Belge türü: {TYPE_NAMES[kind]} ({doc_type.reason})")
        if doc_type.via_603:
            st.info(f"Kişisel güvence belgesi: {doc_type.reason}")
        rules = load_catalog()
        fields = extract_kefalet_fields(text) if kind == "kefalet_sozlesmesi" else extract_teminat_fields(kind, text)
        # Cevaplar belgeye bağlı: metin değişince eski beyanlar yeni belgeye taşınmaz.
        doc_key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        answers = st.session_state.setdefault(f"rules_answers_{doc_key}", {})
        fields = apply_answers(fields, answers)
        if event_date is None:
            event_date, source = document_event_date(kind, fields)
            st.caption(f"Olay tarihi: {event_date:%d.%m.%Y} ("
                       + ("belgeden okunamadı, bugün varsayıldı; kurallar güncel metne göre" if source == "bugün"
                          else f"belgedeki {source}; kurallar o tarihteki kanun metnine göre") + ")")
        results = evaluate(rules, kind, fields, text=text, event_date=event_date)
        questions = pending_questions(rules, results, fields)
        if questions:
            st.markdown("**Belgeden anlaşılamayanlar** — cevaplarınız *müfettiş beyanı* olarak kanıta yazılır:")
            for q in questions:
                labels = [UNKNOWN_ANSWER] + [shown for shown, _ in q.options]
                if q.kind != "choice":
                    labels.append("yazacağım")
                choice = st.radio(q.text, labels, horizontal=True, key=f"rules_q_{doc_key}_{q.field}")
                if choice == "yazacağım":
                    hint = {"amount": "Tutar (TL)", "money": "Tutar ve para birimi (ör. 250.000 USD)",
                            "text": "Ad soyad (birden çok ise virgülle)"}.get(q.kind, "Tarih (GG.AA.YYYY)")
                    typed = st.text_input(hint, key=f"rules_qa_{doc_key}_{q.field}").strip()
                    value = typed or None
                else:
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
