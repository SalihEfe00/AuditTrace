# -*- coding: utf-8 -*-
"""Teminat uygunluk kontrolü bölümü (M9): kefalet, ipotek, rehin, ekspertiz, DASK kuralları; belirsiz alanlar için
müfettişe soru.

Mantık arayüzden bağımsız modüllerde (mufettis.rules.*); burada yalnızca çizim ve oturum durumu.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
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


def _plain_source(source: str) -> str:
    """Olay tarihinin kaynağı sade yazılır (M15.6): "kullandirim.kullandirim_tarihi" → "kullandırım talimatı —
    kullandirim tarihi"."""
    kind, _, field = source.rpartition(".")
    field = field.replace("_", " ")
    return f"{FILE_TYPE_NAMES.get(kind, kind)} — {field}" if kind else field


def _event_date_input(detected: dt.date, source_text: str, key: str) -> dt.date:
    """Olay tarihi (M15.6): belgeden okunan tarih önerilir, müfettiş değiştirebilir; kaynağı yazılır."""
    col1, col2 = st.columns([1, 3])
    chosen = col1.date_input("Olay tarihi", value=detected, min_value=dt.date(1950, 1, 1),
                             max_value=dt.date(2100, 12, 31), format="DD.MM.YYYY", key=key)
    chosen = chosen if isinstance(chosen, dt.date) else detected
    why = "sizin girdiğiniz tarih" if chosen != detected else source_text
    col2.caption(f"Olay tarihi: {chosen:%d.%m.%Y} ({why})")
    return chosen


def _type_choice(label: str, options, current, key: str, ask: bool):
    """Belge türü seçimi (M15.6): tanınmayan belgede doğrudan sorulur, tanınanda "türü değiştir" altında."""
    names = {None: "(seçilmedi)", **FILE_TYPE_NAMES}
    opts = [None] + [o for o in options if o is not None]
    index = opts.index(current) if current in opts else 0
    box = st.container() if ask else st.popover("türü değiştir")
    with box:
        return box.selectbox(label, opts, index=index, format_func=lambda k: names.get(k, k), key=key)


def render_collateral_check(text: str, event_date: Optional[dt.date] = None) -> None:
    if st is None or not (text or "").strip():
        return
    doc_type = detect_collateral_type(text)
    # Cevaplar ve tür seçimi belgeye bağlı: metin değişince eski beyanlar yeni belgeye taşınmaz.
    doc_key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    detected = doc_type.document_type if doc_type.document_type in TYPE_NAMES else None
    chosen = st.session_state.get(f"rules_kind_{doc_key}")
    kind = chosen or detected
    col1, col2 = st.columns([3, 2])
    if kind is None:
        col1.caption(f"Belge kurallı bir teminat belgesi olarak tanınmadı ({doc_type.reason}). Türünü biliyorsanız "
                     "seçin.")
    else:
        col1.caption(f"Belge türü: {TYPE_NAMES[kind]}" + (" (sizin seçtiğiniz)" if chosen else
                     ("" if doc_type.reason.startswith("puan") else f" ({doc_type.reason})")))
    with col2:
        picked = _type_choice("Belge türü", list(TYPE_NAMES), chosen, f"rules_kind_pick_{doc_key}", ask=kind is None)
    if picked != chosen:
        st.session_state[f"rules_kind_{doc_key}"] = picked
        st.rerun()
    if kind is None:
        return
    if doc_type.via_603 and not chosen:
        st.info(f"Kişisel güvence belgesi: {doc_type.reason}")
    rules = load_catalog()
    fields = extract_kefalet_fields(text) if kind == "kefalet_sozlesmesi" else extract_teminat_fields(kind, text)
    answers = st.session_state.setdefault(f"rules_answers_{doc_key}", {})
    fields = apply_answers(fields, answers)
    if event_date is None:
        detected_day, source = document_event_date(kind, fields)
        event_date = _event_date_input(
            detected_day,
            "belgeden okunamadı, bugün varsayıldı; kurallar güncel metne göre" if source == "bugün"
            else f"belgedeki {_plain_source(source)}; kurallar o tarihteki kanun metnine göre",
            f"rules_event_{doc_key}")
    results = evaluate(rules, kind, fields, text=text, event_date=event_date)
    questions = pending_questions(rules, results, fields)

    def ask():
        if not questions:
            return
        with st.container(border=True):
            _questions_header("Belgeden anlaşılamayanlar")
            changed = _questions(questions, answers, doc_key, "rules")
        if changed:
            st.rerun()

    def full_list():
        for res in results:
            _result_line(res)

    _findings_section(rules, results, "Teminat Belgesi Kontrolü", [("belge", TYPE_NAMES[kind])], event_date,
                      f"rules_{doc_key}", questions=ask, full_list=full_list)


FILE_TYPE_NAMES = {**TYPE_NAMES, "senet_gks": "senet / genel kredi sözleşmesi", "kredi_onay": "kredi teklif / onay",
                   "kullandirim": "kullandırım talimatı", "imza_sirkuleri": "imza sirküleri"}
_AUTO = "(otomatik tanı)"


def _questions_header(title: str, extra: str = "") -> None:
    st.markdown(f"#### ✍️ Sizden cevap bekleyen — {title.lower()}")
    st.caption("Cevaplarınız *müfettiş beyanı* olarak kanıta yazılır ve ilgili kontrol yeniden yapılır"
               + (f" ({extra})" if extra else "") + ".")


_QUESTION_TAG = re.compile(r"^\[([a-z_]+)\]\s*")


def _questions(questions, answers, key, prefix, names=None):
    """Soruları çizer; cevap değişirse True (çağıran yeniden çizer). `names`: belge türü → ekranda gösterilecek ad
    (M15.6: soruların başındaki `[dask]` gibi teknik etiket yerine belge adı)."""
    changed = False
    for q in questions:
        labels = [UNKNOWN_ANSWER] + [shown for shown, _ in q.options]
        if q.kind != "choice":
            labels.append("yazacağım")
        text = q.text
        if names:
            text = _QUESTION_TAG.sub(lambda m: f"[{names.get(m.group(1), m.group(1))}] ", text)
        choice = st.radio(text, labels, horizontal=True, key=f"{prefix}_q_{key}_{q.field}")
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


def _group(results):
    """Sonuçları ekrandaki yerine ayırır (M15.1): bulgu (karşılanmadı / manuel), karar verilemeyen (belirsiz),
    uygun (karşılandı / istisna), yapılamayan (gerektirdiği belge dosyada yok). Uygulanmayanlar yalnız tam listede."""
    groups = {"finding": [], "undecided": [], "passed": [], "missing": []}
    for item in results:
        res = getattr(item, "result", item)
        if getattr(item, "missing", ()) and res.status in (UNKNOWN, NOT_MET):
            groups["missing"].append(item)
        elif res.status in (NOT_MET, MANUAL):
            groups["finding"].append(item)
        elif res.status == UNKNOWN:
            groups["undecided"].append(item)
        elif res.status in (MET, EXEMPT):
            groups["passed"].append(item)
    return groups


def _headline(text: str) -> str:
    """Bulgunun ilk cümleciği (ayrıntı açılır bölümde): "X sonra tescil edilmiş: …" → "X sonra tescil edilmiş"."""
    head = re.split(r": | \(| — ", text or "", maxsplit=1)[0].strip().rstrip(".")
    return head[:1].upper() + head[1:]


def _short_ref(criterion: str) -> str:
    """Ölçütün kısa adı: "Türk Borçlar Kanunu (TBK md. 584/1)" → "TBK md. 584/1"; iç kontrolde iki noktadan öncesi."""
    m = re.search(r"\(([^()]*md\.[^()]*)\)$", criterion)
    return m.group(1) if m else criterion.split(":", 1)[0]


_TILES = (("finding", "Bulgu", "255,75,75"), ("undecided", "Karar verilemeyen", "255,170,40"),
          ("passed", "Uygun", "60,180,110"), ("missing", "Yapılamayan (belge yok)", "140,140,150"))


def _summary_tiles(groups) -> None:
    cells = "".join(
        f'<div style="flex:1;min-width:130px;padding:10px 14px;border-radius:10px;background:rgba({rgb},.12);'
        f'border:1px solid rgba({rgb},.45)"><div style="font-size:13px;opacity:.85">{label}</div>'
        f'<div style="font-size:26px;font-weight:600;line-height:1.2">{len(groups[name])}</div></div>'
        for name, label, rgb in _TILES)
    st.markdown(f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin:4px 0 12px">{cells}</div>',
                unsafe_allow_html=True)


def _finding_card(f) -> None:
    """Bulgu kartı: tek satır başlık + kısa dayanak; tespit, ölçüt, kanıt ve öneri açılır bölümde."""
    esc = html.escape
    with st.container(border=True):
        st.markdown(f"{ICON.get(f.status, '•')} **{_headline(f.detection)}**"
                    + (" — *elle kontrol edilmeli*" if f.status == MANUAL else ""))
        st.caption(" · ".join(x for x in (_short_ref(f.criterion), ", ".join(f.documents)) if x))
        evidence = "".join(f"<li>{esc(e)}</li>" for e in f.evidence)
        st.markdown(
            '<details><summary style="cursor:pointer;font-size:14px;opacity:.8">Ölçüt, kanıt ve öneri</summary>'
            '<div style="font-size:14px;line-height:1.55;margin-top:6px">'
            f"<b>Tespit:</b> {esc(f.detection)}<br><b>Ölçüt:</b> {esc(f.criterion)} — “{esc(f.quote)}”<br>"
            + (f"<b>Kanıt:</b><ul style='margin:2px 0 4px'>{evidence}</ul>" if evidence else "")
            + f"<b>Öneri:</b> {esc(f.recommendation)}<br>"
            f'<span style="opacity:.7">Kural: {esc(f.title)} · {esc(f.rule_id)} · sonuç sınıfı {esc(f.result_class)}'
            "</span></div></details>", unsafe_allow_html=True)


def _brief_line(rules_by_id, item, note: str = "") -> None:
    """Sekmelerdeki kısa satır: simge, kuralın adı, kısa dayanak (ve varsa not)."""
    res = getattr(item, "result", item)
    rule = rules_by_id.get(res.rule_id)
    title = rule.title if rule else res.rule_id
    st.markdown(f"{ICON.get(res.status, '•')} {title} — <span style='opacity:.7'>{html.escape(_short_ref(res.basis))}"
                + (f" · {html.escape(note)}" if note else "") + "</span>", unsafe_allow_html=True)


def _findings_section(rules, results, title, documents, event_date, key, questions=None, full_list=None):
    """M11 + M15.1: özet sayılar → bulgular (tek satır, ayrıntı açılır) → müfettişe sorular → geri kalanlar
    sekmelerde (karar verilemeyen, uygun, yapılamayan, bütün kurallar) → DOCX çalışma kağıdı.
    `questions()` soruları çizer, `full_list()` bütün kuralları çizer (çağıranın kendi biçimiyle)."""
    from mufettis.exports.rules_report import create_rules_docx
    from mufettis.rules.findings import absent_collateral, build_findings, summary
    groups = _group(results)
    findings = build_findings(rules, groups["finding"])
    _summary_tiles(groups)
    st.markdown(f"#### Bulgular ({len(findings)})")
    if not findings:
        st.caption("Belgeden açık bir aykırılık bulunmadı. Karar verilemeyen kontroller aşağıda.")
    for f in findings:
        _finding_card(f)
    if questions is not None:
        questions()
    by_id = {r.id: r for r in rules}
    tabs = st.tabs([f"Karar verilemeyen ({len(groups['undecided'])})", f"Uygun ({len(groups['passed'])})",
                    f"Yapılamayan ({len(groups['missing'])})", "Bütün kurallar"])
    with tabs[0]:
        if not groups["undecided"]:
            st.caption("Yok.")
        for item in groups["undecided"]:
            res = getattr(item, "result", item)
            _brief_line(by_id, item, _headline(res.finding) if res.finding else "")
        if groups["undecided"]:
            st.caption("Belge bu kontroller için yetmedi: sorulan bilgiyi cevaplayın ya da belge aslından kontrol edin.")
    with tabs[1]:
        if not groups["passed"]:
            st.caption("Yok.")
        for item in groups["passed"]:
            _brief_line(by_id, item)
    with tabs[2]:
        if not groups["missing"]:
            st.caption("Yok.")
        for item in groups["missing"]:
            names = ", ".join(FILE_TYPE_NAMES.get(k, k) for k in getattr(item, "missing", ()))
            _brief_line(by_id, item, f"dosyada yok: {names}")
        if groups["missing"]:
            st.caption("Bu belgeler dosyada yok; ilgili teminat ya da belge varsa dosyaya ekleyin.")
    with tabs[3]:
        if full_list is not None:
            full_list()
    counts = summary(results)
    absent = sorted({k for item in results for k in absent_collateral(item)})
    notes = ["Dosyada olmayan teminat türleri (kuralları bulgu sayılmadı; teminat varsa belgesini ekleyin): "
             + ", ".join(FILE_TYPE_NAMES.get(k, k) for k in absent)] if absent else []
    try:
        data = create_rules_docx(title, documents, build_findings(rules, results), counts, event_date, notes=notes)
    except RuntimeError as exc:
        st.caption(f"DOCX oluşturulamadı: {exc}")
        return
    st.download_button("📄 Çalışma kağıdını indir (DOCX)", data, file_name=f"{title.replace(' ', '_')}.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       key=f"{key}_docx")


def current_authority_table():
    """Yetki tablosu (M10.4): kayıtlı tablo oturuma bir kez yüklenir; düzenleyici Admin → Ayarlar'dadır (M15.5)."""
    from mufettis.rules.authority import load_table
    if "credit_file_authority" not in st.session_state:
        try:
            st.session_state["credit_file_authority"] = load_table()
        except Exception as exc:  # bozuk dosya: tablo boş başlar, kullanıcı görür
            st.warning(f"Kayıtlı yetki tablosu okunamadı: {exc}")
            st.session_state["credit_file_authority"] = []
    return st.session_state["credit_file_authority"]


def render_authority_settings() -> None:
    """Admin → Ayarlar: yetki tablosu düzenleyicisi (önceden kredi dosyası bölümünde açılır kutuydu)."""
    from mufettis.rules.authority import save_table, table_path
    table = current_authority_table()
    st.markdown("**Yetki tablosu (isteğe bağlı)**" + (f" — {len(table)} satır" if table else ""))
    st.caption("Bankanın kredi yetki sınırları: onay mercii ve onaylayabileceği azami tutar (ör. 'Şube Kredi "
               "Komitesi' — '5.000.000 TL'). Birimsiz tutar TL sayılır. Kanun değil iç düzenlemedir; sonuç "
               "'iç kontrol' olarak gösterilir.")
    rows = [{"Onay mercii": n, "Azami tutar": m} for n, m in table] or [{"Onay mercii": "", "Azami tutar": ""}]
    edited = st.data_editor(rows, num_rows="dynamic", key="credit_file_authority_editor", use_container_width=True)
    if st.button("Tabloyu kaydet", key="credit_file_authority_save"):
        saved = save_table([(r.get("Onay mercii"), r.get("Azami tutar")) for r in edited])
        st.session_state["credit_file_authority"] = saved
        st.success(f"{len(saved)} satır kaydedildi ({table_path()}).")
        st.rerun()


def render_credit_file_check(items=None, event_date: Optional[dt.date] = None, authority_table=None) -> None:
    """Kredi dosyası kontrolü (M10): birden çok belge tek dosya; belgeler türüne göre yerleşir, her belgenin kendi
    kuralları ve belgeler arası dosya kuralları çalışır, belirsiz kalanlar müfettişe sorulur. `items` (ad, metin)
    belgeleri tek yükleyiciden gelir (M15.6); `authority_table` verilmezse kayıtlı tablo kullanılır."""
    if st is None:
        return
    from mufettis.rules.authority import authority_fields
    from mufettis.rules.catalog import AUTHORITY_KIND, FILE_TYPE
    from mufettis.rules.credit_file import CreditFile, evaluate_file, file_event_date, file_fields, load_document

    items = list(items or [])
    if not items:
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
        # Otomatik tanının puanı teknik ayrıntı; yalnız kullanıcının seçimi gibi açıklamalar gösterilir
        why = "" if doc.reason.startswith("puan") else f" ({doc.reason})"
        col1.markdown(f"📄 **{name}** — " + (f"{FILE_TYPE_NAMES.get(doc.kind, doc.kind)}{why}" if doc.kind
                                            else f"⚠️ tanınmadı ({doc.reason}); türünü seçin"))
        with col2:  # tür seçimi yalnız tanınmayan belgede doğrudan, tanınanda "türü değiştir" altında
            picked = _type_choice("Tür", options[1:], chosen, f"credit_file_kind_{file_key}_{i}",
                                  ask=doc.kind is None)
        if picked != chosen:
            kinds[tid] = picked
            st.rerun()
        documents.append(doc)
    credit_file = CreditFile(documents)
    rules = load_catalog()
    answers = st.session_state.setdefault(f"credit_file_answers_{file_key}", {})
    answered = apply_answers({}, answers)
    if event_date is None:
        detected_day, source = file_event_date(credit_file)
        event_date = _event_date_input(
            detected_day, "dosyadan okunamadı, bugün varsayıldı" if source == "bugün" else f"dosyadaki {_plain_source(source)}",
            f"credit_file_event_{file_key}")
    if authority_table is None:
        authority_table = current_authority_table()
        st.caption(f"Yetki tablosu: {len(authority_table)} satır" if authority_table else
                   "Yetki tablosu girilmedi; onay yetkisi kuralı karar verilemedi kalır (Admin → Ayarlar).")
    results = evaluate_file(rules, credit_file, event_date=event_date, answers=answered,
                            authority_table=authority_table)
    approvals = credit_file.by_kind().get("kredi_onay", [])
    virtual = authority_fields(approvals[0].fields, authority_table) if len(approvals) == 1 else {}
    known = {**file_fields(credit_file), **virtual, **answered}
    questions = pending_questions([r for r in rules if r.document_type == FILE_TYPE], [r.result for r in results],
                                  known)
    # Dosyada olmayan belgenin alanı sorulmaz: cevap belgenin yerini tutmaz, sonuç "dosyada yok" diye kalır
    present = set(credit_file.by_kind())
    if "kredi_onay" in present:  # tabloda olmayan merciin sınırı sorulabilir (onay belgesi varsa)
        present.add(AUTHORITY_KIND)
    questions = [q for q in questions if q.field.split(".", 1)[0] in present]

    question_names = {kind: ", ".join(d.name for d in docs) for kind, docs in credit_file.by_kind().items()}
    question_names[AUTHORITY_KIND] = "yetki tablosu"

    def ask():
        if not questions:
            return
        with st.container(border=True):
            _questions_header("Dosyadan anlaşılamayanlar", "aynı türden birden çok belge varsa cevap hepsine uygulanır")
            changed = _questions(questions, answers, file_key, "credit_file", names=question_names)
        if changed:
            st.rerun()

    own = [(d, evaluate(rules, d.kind, d.fields, text=d.text, event_date=document_event_date(d.kind, d.fields)[0]))
           for d in documents if d.kind]
    own = [(d, res) for d, res in own if res]
    from mufettis.rules.credit_file import FileResult
    everything = list(results) + [FileResult((d.name,), r) for d, res in own for r in res]

    def full_list():
        st.markdown("**Belgeler arası kurallar**")
        for fr in results:
            _result_line(fr.result, fr.documents)
        if own:
            st.markdown("**Belgelerin kendi kuralları** (sorular belgenin tek başına kontrolünde)")
            for doc, res in own:
                st.markdown(f"*{doc.name}*")
                for r in res:
                    _result_line(r)

    _findings_section(rules, everything, "Kredi Dosyası Kontrolü",
                      [(d.name, FILE_TYPE_NAMES.get(d.kind, "tanınmadı")) for d in documents], event_date,
                      f"credit_file_{file_key}", questions=ask, full_list=full_list)


__all__ = ["render_collateral_check", "render_credit_file_check", "FILE_TYPE_NAMES"]
