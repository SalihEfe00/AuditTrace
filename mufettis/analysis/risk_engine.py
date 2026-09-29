# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from collections import Counter, defaultdict
import inspect
import math
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from mufettis.config import MAX_FINDINGS_FOR_MATCH
from mufettis.common.utils import mask_sensitive, now_iso, safe_json
from mufettis.audit.service import append_audit, apply_reviews
from mufettis.extraction.text_repair import clean_evidence_text
from mufettis.rag.engine import LocalRAG

DATE_COL_PATTERNS = ["tarih", "date", "yıl", "ay"]
def looks_like_date_column(series: pd.Series, name: str) -> bool:
    lname = str(name).lower()
    if any(p in lname for p in DATE_COL_PATTERNS):
        return True
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    sample = series.dropna().astype(str).head(20)
    if sample.empty:
        return False
    dateish = sample.str.match(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$|^\d{4}[./-]\d{1,2}[./-]\d{1,2}$").mean()
    return dateish >= 0.7
def parse_turkish_number(value: Any) -> float:
    """Türkçe sayı/tutar ayrıştırıcı.

    Kurallar:
    - 12.500 -> 12500
    - 1.234 -> 1234
    - 100.000 -> 100000
    - 1.234.567 -> 1234567
    - 12,50 -> 12.50
    - 1.234,56 -> 1234.56
    - 1,234.56 -> 1234.56
    - (2.000,00) -> -2000
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan

    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        try:
            return float(value)
        except Exception:
            return np.nan

    s = str(value).strip()
    if not s:
        return np.nan

    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()

    s = (
        s.replace("₺", "")
        .replace("TL", "")
        .replace("TRY", "")
        .replace(" ", "")
        .replace("\u00a0", "")
    )
    s = re.sub(r"[^0-9,.\-]", "", s)
    if not s:
        return np.nan

    if "-" in s:
        if s.startswith("-"):
            neg = True
            s = s[1:]
        else:
            s = s.replace("-", "")

    dot_count = s.count(".")
    comma_count = s.count(",")

    if dot_count and comma_count:
        # Son görülen ayraç ondalıktır.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif comma_count:
        # Virgül: 1-2 hane = ondalık; 3+ hane = binlik kabul edilir.
        parts = s.split(",")
        if len(parts) == 2 and len(parts[-1]) in (1, 2):
            s = parts[0] + "." + parts[1]
        else:
            s = "".join(parts)
    elif dot_count:
        # Tek nokta + son grup tam 3 hane -> Türkçe binlik ayıracı.
        # Örn. 12.500, 1.234, 100.000 -> integer.
        parts = s.split(".")
        if len(parts) >= 2 and all(part.isdigit() and len(part) == 3 for part in parts[1:]):
            s = "".join(parts)
        elif len(parts) == 2 and len(parts[1]) in (1, 2):
            s = parts[0] + "." + parts[1]
        else:
            # Çok noktalı sayıda güvenli varsayım: binlik grup.
            s = "".join(parts)

    try:
        number = float(s)
        return -number if neg else number
    except (ValueError, TypeError):
        return np.nan
def normalize_amount_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    out = df.copy()
    amount_cols: List[str] = []
    for col in out.columns:
        if looks_like_date_column(out[col], col):
            continue
        converted = out[col].map(parse_turkish_number)
        success_rate = converted.notna().mean()
        if pd.api.types.is_numeric_dtype(out[col]) or success_rate >= 0.85:
            out[col] = converted
            amount_cols.append(col)
    return out, amount_cols
def format_turkish_number(value: Any, decimals: int = 2) -> str:
    """Rapor/UI için 1.000.000,00 biçiminde Türkçe sayı."""
    try:
        num=float(value)
    except (ValueError, TypeError):
        return clean_evidence_text(value, 300)

    if math.isnan(num) or math.isinf(num):
        return ""

    raw=f"{abs(num):,.{decimals}f}"
    raw=raw.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-{raw}" if num < 0 else raw
def data_quality_report(df: pd.DataFrame) -> Dict[str, Any]:
    report: Dict[str, Any] = {"rows": len(df), "columns": len(df.columns), "columns_detail": {}}
    for col in df.columns:
        s = df[col]
        blank = s.astype(str).str.strip().eq("").sum()
        missing = int(s.isna().sum())
        detail = {
            "missing": missing,
            "blank": int(blank),
            "unique": int(s.nunique(dropna=True)),
        }
        if pd.api.types.is_object_dtype(s):
            numeric = s.map(parse_turkish_number)
            detail["parse_errors_example"] = s[(s.astype(str).str.strip() != "") & numeric.isna()].astype(str).head(5).tolist()
        report["columns_detail"][str(col)] = detail
    return report
def _numeric_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        raise KeyError(col)
    return df[col].map(parse_turkish_number)
def _severity(score: float) -> str:
    if score >= 80:
        return "Yüksek"
    if score >= 50:
        return "Orta"
    return "Düşük"
def _base_finding(run_id: str, source_file: str, row_index: Any, finding_type: str, severity: str, reason: str, value: Any = None, columns: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "bulgu_id": uuid.uuid4().hex[:16],
        "audit_run_id": run_id,
        "created_at": now_iso(),
        "source_file": source_file or "",
        "satir": int(row_index) if str(row_index).isdigit() else str(row_index),
        "bulgu_tipi": finding_type,
        "onem": severity,
        "gerekce": reason,
        "deger": safe_json(value),
        "kolonlar": columns or [],
    }
def run_analysis(
    df: pd.DataFrame,
    amount_column: Optional[str] = None,
    methods: Optional[List[str]] = None,
    z_threshold: float = 3.0,
    iqr_multiplier: float = 1.5,
    mad_threshold: float = 3.5,
    high_amount_threshold: Optional[float] = None,
    keywords: Optional[List[str]] = None,
    duplicate_columns: Optional[List[str]] = None,
    group_columns: Optional[List[str]] = None,
    trend_date_column: Optional[str] = None,
    trend_pct_threshold: float = 100.0,
    ratio_numerator: Optional[str] = None,
    ratio_denominator: Optional[str] = None,
    ratio_threshold: float = 10.0,
    benchmark_column: Optional[str] = None,
    benchmark_ratio_threshold: float = 3.0,
    source_file: str = "",
) -> Tuple[List[Dict[str, Any]], pd.DataFrame, Dict[str, Any]]:
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df pandas DataFrame olmalı")
    if df.empty:
        raise ValueError("Analiz için veri boş")

    methods = methods or ["zscore", "mad", "iqr", "duplicates", "keywords"]
    group_columns = [c for c in (group_columns or []) if c in df.columns]
    duplicate_columns = [c for c in (duplicate_columns or []) if c in df.columns]
    keywords = [k.strip().lower() for k in (keywords or []) if k.strip()]

    run_id = uuid.uuid4().hex
    created = now_iso()
    working, numeric_cols = normalize_amount_columns(df)
    findings: List[Dict[str, Any]] = []

    if amount_column and amount_column in working.columns:
        amount = _numeric_series(working, amount_column)
    else:
        amount_column = next((c for c in numeric_cols if "tutar" in str(c).lower() or "amount" in str(c).lower()), None)
        amount = _numeric_series(working, amount_column) if amount_column else pd.Series(np.nan, index=working.index)

    def add(f: Dict[str, Any], base_score: float, evidence_weight: float = 0.0):
        raw = min(100.0, max(0.0, base_score + evidence_weight))
        f["risk_skoru"] = round(raw, 2)
        f["risk_seviyesi"] = _severity(raw)
        findings.append(f)

    if amount_column and amount.notna().sum() >= 3:
        clean = amount.dropna()
        mean = clean.mean()
        std = clean.std(ddof=1)
        if "zscore" in methods and std and std > 0:
            z = (amount - mean) / std
            for idx in z[abs(z) >= z_threshold].dropna().index:
                add(_base_finding(run_id, source_file, idx, "Z-score", "", f"Tutar, örneklem standart sapmasına göre {z.loc[idx]:.2f} z-score seviyesinde.", amount.loc[idx], [amount_column]), min(100, 45 + abs(float(z.loc[idx])) * 8))

        if "mad" in methods:
            med = clean.median()
            mad = np.median(np.abs(clean - med))
            if mad and mad > 0:
                modified_z = 0.6745 * (amount - med) / mad
                for idx in modified_z[abs(modified_z) >= mad_threshold].dropna().index:
                    add(_base_finding(run_id, source_file, idx, "MAD", "", f"Robust modified z-score {modified_z.loc[idx]:.2f}; medyana göre aykırılık.", amount.loc[idx], [amount_column]), min(100, 50 + abs(float(modified_z.loc[idx])) * 7))

        if "iqr" in methods:
            q1, q3 = clean.quantile([0.25, 0.75])
            iqr = q3 - q1
            low, high = q1 - iqr_multiplier * iqr, q3 + iqr_multiplier * iqr
            mask = (amount < low) | (amount > high)
            for idx in amount[mask].dropna().index:
                add(_base_finding(run_id, source_file, idx, "IQR", "", f"Tutar IQR sınırlarının dışında ({low:.2f}, {high:.2f}).", amount.loc[idx], [amount_column]), 55)

        if high_amount_threshold is not None and "high_amount" in methods:
            mask = amount >= float(high_amount_threshold)
            for idx in amount[mask].dropna().index:
                add(_base_finding(run_id, source_file, idx, "Yüksek tutar analitik sinyali", "", f"Tutar {high_amount_threshold:,.2f} eşiğini aşıyor. Bu değer hukuki bildirim eşiği değildir; yalnızca analitik filtre olarak kullanılmıştır.", amount.loc[idx], [amount_column]), 35)

        if "group" in methods and group_columns:
            grouped = working.assign(__amount__=amount).groupby(group_columns, dropna=False, sort=False)
            for _, part in grouped:
                vals = part["__amount__"].dropna()
                if len(vals) < 4:
                    continue
                gmean, gstd = vals.mean(), vals.std(ddof=1)
                if not gstd or gstd <= 0:
                    continue
                gz = (part["__amount__"] - gmean) / gstd
                for idx in gz[abs(gz) >= z_threshold].dropna().index:
                    group_txt = ", ".join(f"{c}={working.loc[idx, c]}" for c in group_columns)
                    add(_base_finding(run_id, source_file, idx, "Grup içi Z-score", "", f"{group_txt}; grup içinde z-score={gz.loc[idx]:.2f}.", amount.loc[idx], [amount_column] + group_columns), 60 + min(30, abs(float(gz.loc[idx])) * 6))

    if "duplicates" in methods:
        dup_cols = duplicate_columns or list(working.columns)
        dup = working.duplicated(subset=dup_cols, keep=False)
        for idx in working.index[dup]:
            add(_base_finding(run_id, source_file, idx, "Potansiyel duplicate", "", f"Seçilen kolonlarda tekrar eden kayıt tespit edildi: {dup_cols}.", None, dup_cols), 60)

    if "keywords" in methods and keywords:
        text_cols = [c for c in working.columns if not looks_like_date_column(working[c], c)]
        for idx, row in working.iterrows():
            haystack = " | ".join(str(row.get(c, "")) for c in text_cols).lower()
            matched = [kw for kw in keywords if kw in haystack]
            if matched:
                add(_base_finding(run_id, source_file, idx, "Anahtar kelime sinyali", "", f"Anahtar kelime(ler) bulundu: {', '.join(matched)}. Bu bir ihlal kanıtı değildir.", None, text_cols), 40 + min(25, len(matched) * 5))

    if "trend" in methods and amount_column and trend_date_column in working.columns:
        dates = pd.to_datetime(working[trend_date_column], errors="coerce", dayfirst=True)
        temp = pd.DataFrame({"date": dates, "amount": amount}).dropna().sort_values("date")
        if len(temp) >= 4:
            pct = temp["amount"].pct_change() * 100
            for idx in pct[pct >= trend_pct_threshold].index:
                original_idx = temp.loc[idx].name
                add(_base_finding(run_id, source_file, original_idx, "Trend sıçraması", "", f"Önceki dönemle karşılaştırıldığında tutarda %{pct.loc[idx]:.1f} artış gözlendi.", amount.loc[original_idx], [amount_column, trend_date_column]), 58)

    if "ratio" in methods and ratio_numerator in working.columns and ratio_denominator in working.columns:
        num = _numeric_series(working, ratio_numerator)
        den = _numeric_series(working, ratio_denominator).replace(0, np.nan)
        ratio = num / den
        for idx in ratio[ratio >= ratio_threshold].dropna().index:
            add(_base_finding(run_id, source_file, idx, "Oran analitik sinyali", "", f"{ratio_numerator}/{ratio_denominator} oranı {ratio.loc[idx]:.2f} ile eşik üzerinde.", ratio.loc[idx], [ratio_numerator, ratio_denominator]), 55)

    if "benchmark" in methods and benchmark_column and amount_column and benchmark_column in working.columns:
        bench = _numeric_series(working, benchmark_column).replace(0, np.nan)
        ratio = amount / bench
        for idx in ratio[ratio >= benchmark_ratio_threshold].dropna().index:
            add(_base_finding(run_id, source_file, idx, "Benchmark sapması", "", f"Tutar / benchmark oranı {ratio.loc[idx]:.2f}; eşik {benchmark_ratio_threshold:.2f}.", ratio.loc[idx], [amount_column, benchmark_column]), 58)

    # Aynı satırda birden fazla sinyali ağırlıklandır
    by_row = Counter(str(f["satir"]) for f in findings)
    for f in findings:
        bonus = min(20, max(0, by_row[str(f["satir"])] - 1) * 5)
        f["risk_skoru"] = round(min(100, f["risk_skoru"] + bonus), 2)
        f["risk_seviyesi"] = _severity(f["risk_skoru"])

    # Working'de satır bazlı özet
    working = working.copy()
    row_scores = defaultdict(float)
    row_counts = Counter()
    for f in findings:
        row_scores[str(f["satir"])] = max(row_scores[str(f["satir"])], float(f["risk_skoru"]))
        row_counts[str(f["satir"])] += 1
    working["risk_skoru"] = [round(row_scores.get(str(idx), 0.0), 2) for idx in working.index]
    working["risk_seviyesi"] = [_severity(x) if x else "Normal" for x in working["risk_skoru"]]
    working["bulgu_sayisi"] = [row_counts.get(str(idx), 0) for idx in working.index]

    summary = {
        "row_count": len(working),
        "column_count": len(working.columns),
        "finding_count": len(findings),
        "affected_rows": len(set(str(f["satir"]) for f in findings)),
        "high_count": sum(f["risk_seviyesi"] == "Yüksek" for f in findings),
        "medium_count": sum(f["risk_seviyesi"] == "Orta" for f in findings),
        "low_count": sum(f["risk_seviyesi"] == "Düşük" for f in findings),
        "audit_run_id": run_id,
        "source_file": source_file,
        "timestamp": created,
        "amount_column": amount_column,
        "methods": methods,
        "z_threshold": float(z_threshold),
        "mad_threshold": float(mad_threshold),
        "iqr_multiplier": float(iqr_multiplier),
    }
    append_audit("analysis_completed", summary)
    return findings, working, summary
def validate_run_analysis_source() -> bool:
    src = inspect.getsource(run_analysis)
    tree = ast.parse(src)
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
    explicit_tuple = any(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 3 for r in returns)
    return bool(returns and explicit_tuple)
def build_finding_query(finding: Dict[str, Any], row_context: Optional[pd.Series] = None) -> str:
    parts = [finding.get("bulgu_tipi", ""), finding.get("gerekce", "")]
    if row_context is not None:
        for col, value in row_context.items():
            if isinstance(value, (int, float, np.integer, np.floating)):
                parts.append(f"{col}: {value}")
            else:
                parts.append(f"{col}: {mask_sensitive(str(value))[:250]}")
    return " | ".join(p for p in parts if p)
def match_anomaly_to_mevzuat(
    finding: Dict[str, Any],
    rag: LocalRAG,
    row_context: Optional[pd.Series] = None,
    k: int = 3,
) -> List[Dict[str, Any]]:
    """Bulgu için izlenebilir aday mevzuat evidence chain üret."""
    query = build_finding_query(finding, row_context)
    results = rag.query(query, k=k)
    candidates: List[Dict[str, Any]] = []

    for rank, r in enumerate(results, 1):
        meta = r.get("metadata", {}) or {}
        candidates.append({
            "rank": rank,
            "etiket": "Aday mevzuat eşleşmesi",
            "source_id": meta.get("source_id", ""),
            "source": clean_evidence_text(meta.get("source", ""), 500),
            "title": clean_evidence_text(meta.get("title", ""), 1000),
            "url": meta.get("url", ""),
            "page": meta.get("page", meta.get("page_no", "")),
            "document_type": meta.get("document_type", ""),
            "document_number": meta.get("document_number", ""),
            "law_number": meta.get("law_number", ""),
            "document_key": meta.get("document_key", ""),
            "article_prefix": meta.get("article_prefix", ""),
            "article_no": meta.get("article_no", ""),
            "paragraph_no": meta.get("paragraph_no", ""),
            "item_no": meta.get("item_no", ""),
            "subitem_no": meta.get("subitem_no", ""),
            "effective_date": meta.get("effective_date", ""),
            "status": meta.get("status", ""),
            "version_number": meta.get("version_number", ""),
            "file_hash": meta.get("file_hash", ""),
            "chunk_schema_version": meta.get("chunk_schema_version", ""),
            "chunk_type": meta.get("chunk_type", ""),
            "chunk_no": meta.get("chunk_no", ""),
            "subchunk_no": meta.get("subchunk_no", ""),
            "distance": r.get("distance"),
            "rerank_score": r.get("_rerank_score"),
            "ingested_at": meta.get("timestamp", ""),
            "snippet": clean_evidence_text(
                mask_sensitive(r.get("document", "")),
                max_chars=1600,
            ),
            "evidence_note": (
                "Semantik/kurallı eşleşme aday dayanak üretir; "
                "hukuki uygulanabilirlik ve güncellik uzman tarafından doğrulanmalıdır."
            ),
        })
    return candidates
def attach_regulation_matches(findings: List[Dict[str, Any]], working: pd.DataFrame, rag: Optional[LocalRAG], max_findings: int = MAX_FINDINGS_FOR_MATCH, k: int = 3) -> List[Dict[str, Any]]:
    if rag is None or not findings:
        return findings
    ordered = sorted(findings, key=lambda f: float(f.get("risk_skoru", 0)), reverse=True)
    target_ids = {f["bulgu_id"] for f in ordered[:max_findings]}
    for f in findings:
        f["mevzuat_eslesmeleri"] = []
        if f["bulgu_id"] not in target_ids:
            f["eslesme_notu"] = "Düşük öncelikli bulgu: mevzuat eşleşmesi atlandı."
            continue
        try:
            row_context = None
            satir = f.get("satir")
            if isinstance(satir, int) and satir in working.index:
                row_context = working.loc[satir]
            f["mevzuat_eslesmeleri"] = match_anomaly_to_mevzuat(f, rag, row_context=row_context, k=k)
            f["eslesme_notu"] = "Eşleşmeler aday kaynaklardır; yürürlük/tatbik ve madde uygulanabilirliği uzman tarafından doğrulanmalıdır."
        except Exception as exc:
            f["eslesme_notu"] = f"Eşleşme oluşturulamadı: {exc}"
    return findings
def findings_to_dataframe(findings: List[Dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "bulgu_id", "audit_run_id", "created_at", "source_file", "satir",
        "bulgu_tipi", "onem", "gerekce", "deger", "kolonlar",
        "risk_skoru", "risk_seviyesi", "candidate_count", "candidate_sources",
        "candidate_titles", "candidate_articles", "candidate_urls",
        "candidate_distances", "evidence_note", "inceleme_durumu",
        "inceleyen", "inceleme_notu",
    ]
    if not findings:
        return pd.DataFrame(columns=columns)
    rows = []
    for f in findings:
        candidates = f.get("mevzuat_eslesmeleri", []) or []
        rows.append({
            "bulgu_id": f.get("bulgu_id"),
            "audit_run_id": f.get("audit_run_id"),
            "created_at": f.get("created_at"),
            "source_file": f.get("source_file"),
            "satir": f.get("satir"),
            "bulgu_tipi": f.get("bulgu_tipi"),
            "onem": f.get("onem"),
            "gerekce": f.get("gerekce"),
            "deger": f.get("deger"),
            "kolonlar": ", ".join(map(str, f.get("kolonlar", []))),
            "risk_skoru": f.get("risk_skoru", 0),
            "risk_seviyesi": f.get("risk_seviyesi", ""),
            "candidate_count": len(candidates),
            "candidate_sources": " || ".join(clean_evidence_text(c.get("source", ""), 300) for c in candidates),
            "candidate_titles": " || ".join(clean_evidence_text(c.get("title", ""), 600) for c in candidates),
            "candidate_articles": " || ".join(
                str(c.get("article_no", "")) for c in candidates
            ),
            "candidate_paragraphs": " || ".join(
                str(c.get("paragraph_no", "")) for c in candidates
            ),
            "candidate_items": " || ".join(
                str(c.get("item_no", "")) for c in candidates
            ),
            "candidate_subitems": " || ".join(
                str(c.get("subitem_no", "")) for c in candidates
            ),
            "candidate_urls": " || ".join(c.get("url", "") for c in candidates),
            "candidate_distances": " || ".join(str(c.get("distance", "")) for c in candidates),
            "evidence_note": f.get("eslesme_notu", ""),
        })
    return apply_reviews(pd.DataFrame(rows))


__all__ = [
    "DATE_COL_PATTERNS",
    "looks_like_date_column",
    "parse_turkish_number",
    "normalize_amount_columns",
    "format_turkish_number",
    "data_quality_report",
    "run_analysis",
    "validate_run_analysis_source",
    "build_finding_query",
    "match_anomaly_to_mevzuat",
    "attach_regulation_matches",
    "findings_to_dataframe",
]
