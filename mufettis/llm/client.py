# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import requests
except Exception:
    requests = None

from mufettis.config import DEFAULT_LLM, LOG_DIR, OLLAMA_URL, resolve_dynamic
from mufettis.common.utils import mask_sensitive, now_iso, safe_json
from mufettis.audit.service import append_audit, apply_reviews
from mufettis.extraction.text_repair import clean_evidence_text
from mufettis.analysis.risk_engine import attach_regulation_matches

def _get_rag(**kwargs):
    from mufettis.ui.app import _get_rag as _canonical_get_rag
    _dyn = resolve_dynamic("_get_rag", _canonical_get_rag)
    if _dyn is not None and _dyn is not _canonical_get_rag:
        return _dyn(**kwargs)
    return _canonical_get_rag(**kwargs)

OLLAMA_SYSTEM_PROMPT = """
Sen Müfettiş AI için yerel bir rapor taslağı yardımcısısın.
Yalnızca verilen istatistiksel bulguları ve aday mevzuat kaynaklarını yorumlarsın.
Kesin hukuki ihlal, suç, şüpheli işlem, denetim görüşü veya bağlayıcı sonuç ilan etme.
Semantik mevzuat eşleşmelerini 'aday dayanak' olarak belirt.
Yürürlük tarihi/madde/fıkra bilgisi verilmemişse uydurma.
Kişisel verileri gereksiz yere tekrar etme; sayısal değerleri yalnızca karar desteği için kullan.
Çıktı profesyonel Türkçe olsun.
""".strip()
def ollama_health() -> Dict[str, Any]:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return {"ok": r.status_code == 200, "status_code": r.status_code}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
def ollama_generate(prompt: str, model: str = DEFAULT_LLM, temperature: float = 0.1, timeout: int = 300) -> str:
    payload = {
        "model": model,
        # Tek LLM taşıma noktası: çağıran maskelemeyi unutsa bile kişisel veri gönderilmez.
        "prompt": mask_sensitive(prompt),
        "system": OLLAMA_SYSTEM_PROMPT,
        "stream": True,
        "options": {"temperature": temperature, "num_ctx": 4096, "repeat_penalty": 1.15},
    }
    response_parts: List[str] = []
    try:
        with requests.post(f"{OLLAMA_URL}/api/generate", json=payload, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("error"):
                    raise RuntimeError(obj["error"])
                if obj.get("response"):
                    response_parts.append(obj["response"])
                if obj.get("done"):
                    break
        return "".join(response_parts).strip()
    except Exception as exc:
        return f"[LLM HATASI] {exc}"
def build_ai_report_prompt(
    summary: Dict[str, Any],
    findings: List[Dict[str, Any]],
    max_findings: int = 20,
) -> str:
    """Yerel LLM'e gereksiz kişisel/finansal ham veri göndermeyen prompt."""
    selected = sorted(
        findings,
        key=lambda f: float(f.get("risk_skoru", 0) or 0),
        reverse=True,
    )[:max_findings]

    safe_summary = mask_sensitive(json.dumps(summary, ensure_ascii=False))
    payload_findings = []

    for f in selected:
        candidates = []
        for c in f.get("mevzuat_eslesmeleri", [])[:3]:
            candidates.append({
                "title": mask_sensitive(clean_evidence_text(c.get("title", ""), 800)),
                "article": c.get("article_no", ""),
                "paragraph": c.get("paragraph_no", ""),
                "item": c.get("item_no", ""),
                "subitem": c.get("subitem_no", ""),
                "effective_date": c.get("effective_date", ""),
                "status": c.get("status", ""),
                "source": mask_sensitive(clean_evidence_text(c.get("source", ""), 500)),
                "url": c.get("url", ""),
                "snippet": mask_sensitive(
                    clean_evidence_text(c.get("snippet", ""), max_chars=1200)
                ),
            })

        payload_findings.append({
            "id": f.get("bulgu_id"),
            "row": f.get("satir"),
            "type": mask_sensitive(clean_evidence_text(f.get("bulgu_tipi", ""), 300)),
            "risk": f.get("risk_skoru"),
            "level": f.get("risk_seviyesi"),
            # Numeric / text finding values are masked before LLM exposure.
            "reason": mask_sensitive(clean_evidence_text(f.get("gerekce", ""), 1800)),
            "value": mask_sensitive(
                clean_evidence_text(f.get("deger", ""), 500)
            ),
            "columns": [
                mask_sensitive(clean_evidence_text(str(c), 200))
                for c in (f.get("kolonlar", []) or [])
            ],
            "candidates": candidates,
        })

    payload = {
        "summary": safe_summary,
        "findings": payload_findings,
        "privacy_note": (
            "Ham kişisel/finansal tanımlayıcılar LLM prompt'una gönderilmemiştir; "
            "maskeli değerler kullanılmaktadır."
        ),
    }

    return (
        "Aşağıdaki yapılandırılmış, maskelenmiş veriye dayanarak denetim "
        "çalışma kağıdı için taslak metin oluştur.\n"
        "Bölümler: 1) Yönetici özeti, 2) Öne çıkan risk sinyalleri, "
        "3) Bulguların analitik gerekçeleri, 4) Aday mevzuat dayanakları ve kaynakları, "
        "5) Denetçinin doğrulaması gereken hususlar, 6) Sonraki inceleme adımları.\n"
        "Kesin hukuki/denetim sonucu verme; belirsizlikleri açıkça belirt.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
def prepare_report_findings(
    findings: List[Dict[str, Any]],
    working: pd.DataFrame,
    allow_embedding_download: bool = False,
    k: int = 3,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """DOCX öncesinde bütün bulguların mevzuat adaylarını tazeler."""
    prepared = []
    for finding in findings or []:
        item = dict(finding)
        item["mevzuat_eslesmeleri"] = list(
            finding.get("mevzuat_eslesmeleri", []) or []
        )
        prepared.append(item)

    rag = _get_rag(allow_download=allow_embedding_download)
    status = {
        "rag_available": bool(rag),
        "matched_findings": 0,
        "unmatched_findings": 0,
        "errors": [],
    }

    if rag is None:
        status["errors"].append(
            "RAG kullanılamıyor; rapor için yeni mevzuat eşleştirmesi yapılamadı."
        )
        status["unmatched_findings"] = len(prepared)
        return prepared, status

    try:
        # Analiz sırasında sadece öncelikli bulgular eşleştirilmiş olabilir.
        # Rapor için tüm bulguları yeniden eşleştir.
        prepared = attach_regulation_matches(
            prepared,
            working,
            rag,
            max_findings=len(prepared),
            k=k,
        )
    except Exception as exc:
        status["errors"].append(f"Mevzuat eşleştirme hatası: {exc}")

    for finding in prepared:
        if finding.get("mevzuat_eslesmeleri"):
            status["matched_findings"] += 1
        else:
            status["unmatched_findings"] += 1

    return prepared, status
def report_mevzuat_preflight(
    findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """DOCX'e hangi bulguların gerçek aday mevzuat atfı taşıdığını doğrular."""
    total = len(findings or [])
    matched = sum(
        1 for finding in findings or []
        if finding.get("mevzuat_eslesmeleri")
    )
    unmatched_ids = [
        str(finding.get("bulgu_id", ""))
        for finding in findings or []
        if not finding.get("mevzuat_eslesmeleri")
    ]
    return {
        "total_findings": total,
        "matched_findings": matched,
        "unmatched_findings": total - matched,
        "unmatched_ids": unmatched_ids,
        "ready": True,
    }


__all__ = [k for k in dict(globals()).keys() if not k.startswith('__')]
