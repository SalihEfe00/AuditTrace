# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import requests
except Exception:
    requests = None

from mufettis.config import DEFAULT_LLM, OLLAMA_URL
from mufettis.common.utils import mask_sensitive
from mufettis.extraction.text_repair import clean_evidence_text
from mufettis.analysis.risk_engine import attach_regulation_matches

def _get_rag(**kwargs):
    from mufettis.ui.app import _get_rag as _canonical_get_rag
    return _canonical_get_rag(**kwargs)

OLLAMA_SYSTEM_PROMPT = """
Sen AuditTrace için yerel bir rapor taslağı yardımcısısın.
Yalnızca verilen istatistiksel bulguları ve aday mevzuat kaynaklarını yorumlarsın.
Kesin hukuki ihlal, suç, şüpheli işlem, denetim görüşü veya bağlayıcı sonuç ilan etme.
Semantik mevzuat eşleşmelerini 'aday dayanak' olarak belirt.
Yürürlük tarihi/madde/fıkra bilgisi verilmemişse uydurma.
Kişisel verileri gereksiz yere tekrar etme; sayısal değerleri yalnızca karar desteği için kullan.
Çıktı profesyonel Türkçe olsun.
""".strip()
def _installed(model: str, names: List[str]) -> bool:
    # Etiketsiz ad Ollama'da ":latest" demektir.
    wanted = model if ":" in model else f"{model}:latest"
    return wanted in names or model in names


# Arayüzde seçilen model (Admin → Ayarlar); None ise kendiliğinden seçilir.
_chosen_model: Optional[str] = None
_EMBEDDING_MODEL = re.compile(r"embed|bge|minilm", re.IGNORECASE)  # metin üretmeyen modeller seçilmez


def set_model(name: Optional[str]) -> None:
    global _chosen_model
    _chosen_model = (name or "").strip() or None


def installed_models() -> Optional[List[str]]:
    """Kurulu Ollama modelleri; sunucuya ulaşılamazsa None."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if r.status_code != 200:
            return None
        return [str(m.get("name", "")) for m in (r.json() or {}).get("models", [])]
    except Exception:
        return None


def active_model(names: Optional[List[str]] = None) -> str:
    """Kullanılacak model (M15.2): seçilen ya da varsayılan model kuruluysa o; MUFETTIS_LLM_MODEL açıkça
    verilmişse o (kurulu değilse sağlık denetimi söyler); değilse kurulu ilk metin modeli. Hiç model yoksa
    varsayılan ad döner, sağlık denetimi kurulmasını söyler."""
    names = installed_models() if names is None else names
    names = names or []
    for candidate in (_chosen_model, DEFAULT_LLM):
        if candidate and _installed(candidate, names):
            return candidate
    if _chosen_model or os.environ.get("MUFETTIS_LLM_MODEL", "").strip():
        return _chosen_model or DEFAULT_LLM
    usable = [n for n in names if not _EMBEDDING_MODEL.search(n)]
    return usable[0] if usable else DEFAULT_LLM


AUTO = "auto"


def ollama_health(model: Optional[str] = AUTO) -> Dict[str, Any]:
    """Sunucu yanıt veriyor mu ve istenen model kurulu mu? Yalnızca sunucuya bakmak yetmez: model
    yoksa her üretim çağrısı 404 döner ve çağıranlar (ör. iddia doğrulayıcısı) sessizce devre dışı
    kalırdı. model=None yalnızca sunucuyu denetler; varsayılan (AUTO) kullanılacak modeli denetler."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if r.status_code != 200:
            return {"ok": False, "status_code": r.status_code}
        names = [str(m.get("name", "")) for m in (r.json() or {}).get("models", [])]
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if model == AUTO:
        model = active_model(names)
    if model and not _installed(model, names):
        return {"ok": False, "status_code": 200, "models": names,
                "error": f"'{model}' modeli kurulu değil (kurulu: {', '.join(names) or 'yok'}); "
                         f"'ollama pull {model}' ile kurun ya da MUFETTIS_LLM_MODEL ile kurulu bir model seçin"}
    return {"ok": True, "status_code": 200, "models": names, "model": model}


def ollama_generate(prompt: str, model: Optional[str] = None, temperature: float = 0.1, timeout: int = 300) -> str:
    model = model or active_model()
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


__all__ = [
    "OLLAMA_SYSTEM_PROMPT",
    "ollama_health",
    "active_model",
    "installed_models",
    "set_model",
    "ollama_generate",
    "build_ai_report_prompt",
    "prepare_report_findings",
    "report_mevzuat_preflight",
]
