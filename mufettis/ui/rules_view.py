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
            if _questions(questions, answers, doc_key, "rules"):
                st.rerun()
        st.markdown("**Sonuçlar**")
        for res in results:
            _result_line(res)


FILE_TYPE_NAMES = {**TYPE_NAMES, "senet_gks": "senet / genel kredi sözleşmesi", "kredi_onay": "kredi teklif / onay",
                   "kullandirim": "kullandırım talimatı", "imza_sirkuleri": "imza sirküleri"}
_AUTO = "(otomatik tanı)"


def _questions(questions, answers, key, prefix):
    """Soruları çizer; cevap değişirse True (çağıran yeniden çizer)."""
    changed = False
    for q in questions:
        labels = [UNKNOWN_ANSWER] + [shown for shown, _ in q.options]
        if q.kind != "choice":
            labels.append("yazacağım")
        choice = st.radio(q.text, labels, horizontal=True, key=f"{prefix}_q_{key}_{q.field}")
        if choice == "yazacağım":
            hint = {"amount": "Tutar (TL)", "money": "Tutar ve para birimi (ör. 250.000 USD)",
                    "text": "Ad soyad (birden çok ise virgülle)"}.get(q.kind, "Tarih (GG.AA.YYYY)")
            value = st.text_input(hint, key=f"{prefix}_qa_{key}_{q.field}").strip() or None
        else:
            value = dict(q.options).get(choice) if choice != UNKNOWN_ANSWER else None
        if answers.get(q.field) != value:
            answers[q.field] = value
            changed = True
    return changed


def _result_line(res, documents=()):
    line = f"{ICON.get(res.status, '•')} **{res.status}** — `{res.rule_id}` ({res.basis})"
    if documents:
        line += f" [{', '.join(documents)}]"
    if res.finding:
        line += f": {res.finding}"
    st.markdown(line)
    shown = [e for e in res.evidence if e[1] != "bulunamadı"]
    if shown:
        st.caption(" · ".join(f"{name}={value if value is not None else '(boş)'} [{source}; {where}]"
                              for name, _state, value, where, source in shown))


def _uploaded_texts():
    """Yüklenen dosyaların metni (ad, metin); her dosya bir kez çıkarılır (OCR yavaş olabilir)."""
    files = st.file_uploader("Kredi dosyasının belgeleri (PDF, DOCX, TXT; birden çok seçilebilir)",
                             type=["pdf", "docx", "txt"], accept_multiple_files=True, key="credit_file_upload")
    cache = st.session_state.setdefault("credit_file_texts", {})
    out = []
    for f in files or []:
        data = f.getvalue()
        fid = hashlib.sha256(data).hexdigest()
        if fid not in cache:
            from mufettis.ui.analysis_view import load_uploaded_document
            try:
                cache[fid] = load_uploaded_document(f.name, data).text
            except Exception as exc:
                st.error(f"{f.name} okunamadı: {exc}")
                continue
        out.append((f.name, cache[fid]))
    return out


def render_credit_file_check(items=None, event_date: Optional[dt.date] = None) -> None:
    """Kredi dosyası kontrolü (M10): birden çok belge tek dosya; belgeler türüne göre yerleşir, her belgenin kendi
    kuralları ve belgeler arası dosya kuralları çalışır, belirsiz kalanlar müfettişe sorulur. `items` (ad, metin)
    verilirse yükleyici gösterilmez (test)."""
    if st is None:
        return
    from mufettis.rules.catalog import FILE_TYPE
    from mufettis.rules.credit_file import CreditFile, evaluate_file, file_event_date, file_fields, load_document

    with st.expander("Kredi dosyası kontrolü — onay, teminatlar, imza sirküleri, kullandırım (birden çok belge)"):
        items = list(items) if items is not None else _uploaded_texts()
        if not items:
            st.caption("Kontrol için dosyanın belgelerini yükleyin.")
            return
        file_key = hashlib.sha1("\n".join(sorted(hashlib.sha1(t.encode("utf-8")).hexdigest()
                                                 for _, t in items)).encode()).hexdigest()[:12]
        kinds = st.session_state.setdefault(f"credit_file_kinds_{file_key}", {})
        st.markdown("**Belgeler**")
        documents = []
        options = [_AUTO] + list(FILE_TYPE_NAMES)
        for i, (name, text) in enumerate(items):
            tid = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]  # tür seçimi belgeye bağlı
            chosen = kinds.get(tid)
            doc = load_document(name, text, chosen)
            col1, col2 = st.columns([3, 2])
            col1.markdown(f"📄 **{name}** — " + (f"{FILE_TYPE_NAMES.get(doc.kind, doc.kind)} ({doc.reason})" if doc.kind
                                                else f"⚠️ tanınmadı ({doc.reason}); dosyaya alınmadı"))
            picked = col2.selectbox("Tür", options, index=options.index(chosen) if chosen in options else 0,
                                    format_func=lambda k: FILE_TYPE_NAMES.get(k, k), key=f"credit_file_kind_{file_key}_{i}",
                                    label_visibility="collapsed")
            picked = None if picked == _AUTO else picked
            if picked != chosen:
                kinds[tid] = picked
                st.rerun()
            documents.append(doc)
        credit_file = CreditFile(documents)
        rules = load_catalog()
        answers = st.session_state.setdefault(f"credit_file_answers_{file_key}", {})
        answered = apply_answers({}, answers)
        if event_date is None:
            event_date, source = file_event_date(credit_file)
            st.caption(f"Olay tarihi: {event_date:%d.%m.%Y} ("
                       + ("dosyadan okunamadı, bugün varsayıldı" if source == "bugün" else f"dosyadaki {source}") + ")")
        results = evaluate_file(rules, credit_file, event_date=event_date, answers=answered)
        known = {**file_fields(credit_file), **answered}
        questions = pending_questions([r for r in rules if r.document_type == FILE_TYPE], [r.result for r in results],
                                      known)
        # Dosyada olmayan belgenin alanı sorulmaz: cevap belgenin yerini tutmaz, sonuç "dosyada yok" diye kalır
        present = set(credit_file.by_kind())
        questions = [q for q in questions if q.field.split(".", 1)[0] in present]
        if questions:
            st.markdown("**Dosyadan anlaşılamayanlar** — cevaplarınız *müfettiş beyanı* olarak kanıta yazılır "
                        "(aynı türden birden çok belge varsa cevap hepsine uygulanır):")
            if _questions(questions, answers, file_key, "credit_file"):
                st.rerun()
        st.markdown("**Belgeler arası kurallar**")
        for fr in results:
            _result_line(fr.result, fr.documents)
        own = [(d, evaluate(rules, d.kind, d.fields, text=d.text, event_date=document_event_date(d.kind, d.fields)[0]))
               for d in documents if d.kind]
        own = [(d, res) for d, res in own if res]
        if own:
            st.markdown("**Belgelerin kendi kuralları** (sorular belgenin tek başına kontrolünde)")
            for doc, res in own:
                st.markdown(f"*{doc.name}*")
                for r in res:
                    _result_line(r)


__all__ = ["render_collateral_check", "render_credit_file_check", "FILE_TYPE_NAMES"]
