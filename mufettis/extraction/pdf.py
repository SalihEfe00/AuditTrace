# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import math
import re
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


try:
    import pypdfium2 as pdfium
except Exception:
    pdfium = None

try:
    import pdfplumber
except Exception:
    pdfplumber = None

from mufettis.config import PDFIUM_LOCK
from mufettis.extraction.text_repair import (
    repair_extracted_spacing,
)

def classify_legal_structure(*args, **kwargs):
    from mufettis.legal.parser import classify_legal_structure as _cls
    return _cls(*args, **kwargs)

def _pdf_text_quality(value: str) -> Tuple[float, Dict[str, float]]:
    """PDF extraction adayının okunabilirlik ve kelime bütünlüğü skorunu hesaplar."""
    text = str(value or "")
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return -1.0, {"chars": 0.0, "spaces": 0.0, "long_upper": 0.0, "long_glued": 0.0}

    upper_runs = re.findall(r"[A-ZÇĞİÖŞÜ]{14,}", text)
    long_upper = sum(max(0, len(x) - 13) for x in upper_runs)
    glued_runs = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]{28,}", text)
    long_glued = sum(max(0, len(x) - 20) for x in glued_runs)
    spaces = len(re.findall(r"\s", text))
    lines = max(1, len(text.splitlines()))
    space_ratio = spaces / max(1, len(compact))

    score = -float(long_upper) / max(1.0, len(compact)) * 20.0
    score -= float(long_glued) / max(1.0, len(compact)) * 35.0
    score += min(space_ratio, 0.30) * 3.0
    score += min(lines / 100.0, 1.0) * 0.25
    return score, {
        "chars": float(len(compact)),
        "spaces": float(spaces),
        "long_upper": float(long_upper),
        "long_glued": float(long_glued),
    }


def _extract_pdfium_pages(pdf_bytes: bytes) -> List[str]:
    """pypdfium2 Fast Pass: PDF'yi sayfa sayfa ham metin olarak çıkarır."""
    if pdfium is None:
        return []
    pages: List[str] = []
    with PDFIUM_LOCK:
        pdf = None
        try:
            pdf = pdfium.PdfDocument(pdf_bytes)
            for index in range(len(pdf)):
                page = None
                textpage = None
                try:
                    page = pdf.get_page(index)
                    textpage = page.get_textpage()
                    text = textpage.get_text_range() or ""
                    pages.append(str(text).replace("\r\n", "\n").replace("\r", "\n").strip())
                finally:
                    if textpage is not None:
                        try:
                            textpage.close()
                        except Exception:
                            pass
                    if page is not None:
                        try:
                            page.close()
                        except Exception:
                            pass
        except Exception:
            return []
        finally:
            if pdf is not None:
                try:
                    pdf.close()
                except Exception:
                    pass
    return pages


def _build_pdf_physical_page_model(
    textpage: Any,
    raw_text: str,
    page_width: float = 0.0,
    page_height: float = 0.0,
) -> Dict[str, Any]:
    """A1: Şemadan bağımsız fiziksel PDF belge modelini oluşturur.

    Extraction'ın anlamını değiştirmez. Her karakter/satır için koordinatları,
    sayfa ölçüsünü ve güvenli layout sinyallerini taşır. Hukuki, muhasebe veya
    tablo şeması varsayımı yapmaz.
    """
    text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: List[Dict[str, Any]] = []
    current_chars: List[Dict[str, Any]] = []
    char_index = 0

    def flush_line() -> None:
        nonlocal current_chars
        if not current_chars:
            return
        boxes = [c["box"] for c in current_chars if c.get("box") is not None]
        if boxes:
            x0 = min(b[0] for b in boxes)
            y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y1 = max(b[3] for b in boxes)
        else:
            x0 = y0 = x1 = y1 = 0.0
        heights = [max(0.1, b[3] - b[1]) for b in boxes]
        baseline = statistics.median([(b[1] + b[3]) / 2.0 for b in boxes]) if boxes else 0.0
        lines.append({
            "index": len(lines),
            "text": "".join(c["char"] for c in current_chars),
            "chars": current_chars,
            "bbox": (x0, y0, x1, y1),
            "baseline": baseline,
            "height": statistics.median(heights) if heights else 0.0,
        })
        current_chars = []

    for ch in text:
        if ch == "\n":
            flush_line()
            continue
        box = None
        try:
            candidate = tuple(float(v) for v in textpage.get_charbox(char_index))
            if len(candidate) == 4 and all(math.isfinite(v) for v in candidate):
                box = candidate
        except Exception:
            pass
        current_chars.append({"char": ch, "box": box, "index": char_index})
        char_index += 1
    flush_line()

    # Boş satırları layout modeline almıyoruz; ham metnin anlamlı satırları aynen korunur.
    lines = [line for line in lines if line["text"].strip()]

    # Satırları kaba fiziksel bloklara ayır. Bu yalnızca generic layout bilgisidir;
    # hukuki/muhasebe/table şeması çıkarmaya çalışmaz.
    blocks: List[Dict[str, Any]] = []
    for line in sorted(lines, key=lambda item: (-float(item["bbox"][3]), float(item["bbox"][0]), item["index"])):
        if not blocks:
            blocks.append({"index": 0, "line_indices": [line["index"]], "bbox": line["bbox"]})
            continue
        prev = blocks[-1]
        pb = prev["bbox"]
        lb = line["bbox"]
        gap_y = float(pb[1] - lb[3])
        overlap_x = max(0.0, min(pb[2], lb[2]) - max(pb[0], lb[0]))
        min_width = max(1.0, min(pb[2] - pb[0], lb[2] - lb[0]))
        close_vertically = gap_y <= max(18.0, float(line.get("height", 0.0)) * 1.8)
        horizontally_related = overlap_x / min_width >= 0.20
        if close_vertically and horizontally_related:
            prev["line_indices"].append(line["index"])
            prev["bbox"] = (
                min(pb[0], lb[0]), min(pb[1], lb[1]),
                max(pb[2], lb[2]), max(pb[3], lb[3]),
            )
        else:
            blocks.append({"index": len(blocks), "line_indices": [line["index"]], "bbox": line["bbox"]})

    return {
        "schema_version": "physical-document-1",
        "page_index": 0,
        "width": float(page_width or 0.0),
        "height": float(page_height or 0.0),
        "lines": lines,
        "blocks": blocks,
        "columns": [],
    }


def _order_pdf_physical_lines(
    lines: List[Dict[str, Any]],
    page_width: float = 0.0,
) -> List[Dict[str, Any]]:
    """A2: Genel layout için satırların okuma sırasını belirler.

    Varsayılan güvenli davranış tek sütunda yukarıdan aşağıdır. İki sütun sinyali
    ancak satır kutuları sayfa genişliğinde iki belirgin kümeye ayrıldığında
    uygulanır. Bu katman belge şemasından bağımsızdır; tablo/hukuk ayrıştırması
    daha sonraki katmanlara bırakılır.
    """
    if not lines:
        return []
    usable = [line for line in lines if line.get("text", "").strip()]
    if len(usable) <= 1:
        return usable

    if not page_width:
        return usable

    # Kolon adaylarını x0 kümelerinden çıkar. Geniş/ortalanmış başlık satırlarının
    # tek başına ikinci kolon sanılmaması için dar metin satırlarını tercih ederiz.
    narrow_lines = [
        line for line in usable
        if (float(line["bbox"][2]) - float(line["bbox"][0])) <= float(page_width) * 0.72
    ]
    if len(narrow_lines) < max(6, int(math.ceil(len(usable) * 0.45))):
        return usable

    x0_items = sorted(
        ((float(line["bbox"][0]), line) for line in narrow_lines),
        key=lambda pair: (pair[0], pair[1].get("index", 0)),
    )
    xs = [x for x, _ in x0_items]
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    if not gaps:
        return usable
    max_gap, split_idx = max(gaps, key=lambda pair: pair[0])
    left = [line for _, line in x0_items[:split_idx + 1]]
    right = [line for _, line in x0_items[split_idx + 1:]]
    min_col_count = max(3, int(math.ceil(len(narrow_lines) * 0.22)))
    left_median = statistics.median([float(line["bbox"][0]) for line in left]) if left else 0.0
    right_median = statistics.median([float(line["bbox"][0]) for line in right]) if right else 0.0
    relative_gap = max_gap / max(float(page_width), 1.0)
    stable_left = all(abs(float(line["bbox"][0]) - left_median) <= float(page_width) * 0.08 for line in left)
    stable_right = all(abs(float(line["bbox"][0]) - right_median) <= float(page_width) * 0.08 for line in right)
    left_right_gap = (
        min(float(line["bbox"][0]) for line in right) -
        max(float(line["bbox"][2]) for line in left)
    ) if left and right else 0.0
    overlapping_left = 0
    overlapping_right = 0
    for left_line in left:
        ly0, ly1 = float(left_line["bbox"][1]), float(left_line["bbox"][3])
        if any(min(ly1, float(r["bbox"][3])) - max(ly0, float(r["bbox"][1])) > 0.30 * min(ly1 - ly0, float(r["bbox"][3]) - float(r["bbox"][1])) for r in right):
            overlapping_left += 1
    for right_line in right:
        ry0, ry1 = float(right_line["bbox"][1]), float(right_line["bbox"][3])
        if any(min(ry1, float(l["bbox"][3])) - max(ry0, float(l["bbox"][1])) > 0.30 * min(ry1 - ry0, float(l["bbox"][3]) - float(l["bbox"][1])) for l in left):
            overlapping_right += 1
    overlap_ratio = min(
        overlapping_left / max(len(left), 1),
        overlapping_right / max(len(right), 1),
    )
    two_columns = (
        relative_gap >= 0.25
        and left_right_gap >= float(page_width) * 0.06
        and len(left) >= min_col_count
        and len(right) >= min_col_count
        and stable_left
        and stable_right
        and overlap_ratio >= 0.45
    )
    if not two_columns:
        return usable

    left.sort(key=lambda item: (-float(item["bbox"][3]), float(item["bbox"][0]), item["index"]))
    right.sort(key=lambda item: (-float(item["bbox"][3]), float(item["bbox"][0]), item["index"]))
    return left + right



def _build_pdf_visual_rows(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A4: ham newline'lara bağlı kalmadan fiziksel görsel satırlar üretir.

    Tablo tespiti için PDF metin akışındaki satır sonlarını güvenilir kabul etmez.
    Karakter kutularını dikey merkezlerine göre gruplar; böylece aynı fiziksel tablo
    satırındaki hücreler raw extraction tarafından ayrı satırlara bölünse bile aynı
    visual row altında değerlendirilebilir.
    """
    chars = []
    for line in model.get("lines", []):
        for item in line.get("chars", []) or []:
            box = item.get("box")
            ch = str(item.get("char", ""))
            if not ch.strip() or not isinstance(box, (tuple, list)) or len(box) != 4:
                continue
            try:
                x0, y0, x1, y1 = (float(v) for v in box)
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
                continue
            chars.append({"char": ch, "box": (x0, y0, x1, y1), "source_line": int(line.get("index", -1))})
    if not chars:
        return []

    chars.sort(key=lambda c: (-(c["box"][1] + c["box"][3]) / 2.0, c["box"][0]))
    page_height = float(model.get("height", 0.0) or 0.0)
    base_tol = max(2.0, page_height * 0.006)
    rows: List[Dict[str, Any]] = []
    for ch in chars:
        center_y = (ch["box"][1] + ch["box"][3]) / 2.0
        target = None
        best_dist = float("inf")
        for row in rows:
            dist = abs(center_y - row["center_y"])
            row_height = max(row["height"], ch["box"][3] - ch["box"][1], 1.0)
            tol = max(base_tol, row_height * 0.45)
            if dist <= tol and dist < best_dist:
                target = row
                best_dist = dist
        if target is None:
            rows.append({
                "chars": [ch],
                "center_y": center_y,
                "height": ch["box"][3] - ch["box"][1],
            })
        else:
            target["chars"].append(ch)
            n = len(target["chars"])
            target["center_y"] = ((target["center_y"] * (n - 1)) + center_y) / n
            target["height"] = max(target["height"], ch["box"][3] - ch["box"][1])

    visual_rows: List[Dict[str, Any]] = []
    for row in rows:
        row_chars = sorted(row["chars"], key=lambda c: (c["box"][0], c["box"][1]))
        x0 = min(c["box"][0] for c in row_chars)
        y0 = min(c["box"][1] for c in row_chars)
        x1 = max(c["box"][2] for c in row_chars)
        y1 = max(c["box"][3] for c in row_chars)
        # Tokenize by physical gaps, not by raw PDF newlines/whitespace objects.
        words: List[Dict[str, Any]] = []
        current = []
        prev_x1 = None
        widths = [max(0.1, c["box"][2] - c["box"][0]) for c in row_chars]
        median_width = statistics.median(widths) if widths else 1.0
        gap_threshold = max(4.0, median_width * 3.2)
        for c in row_chars:
            gap = None if prev_x1 is None else c["box"][0] - prev_x1
            if current and gap is not None and gap > gap_threshold:
                cb = [x["box"] for x in current]
                words.append({
                    "text": "".join(x["char"] for x in current),
                    "bbox": (min(b[0] for b in cb), min(b[1] for b in cb), max(b[2] for b in cb), max(b[3] for b in cb)),
                })
                current = []
            current.append(c)
            prev_x1 = max(prev_x1 or c["box"][2], c["box"][2])
        if current:
            cb = [x["box"] for x in current]
            words.append({
                "text": "".join(x["char"] for x in current),
                "bbox": (min(b[0] for b in cb), min(b[1] for b in cb), max(b[2] for b in cb), max(b[3] for b in cb)),
            })

        source_lines = sorted(set(c["source_line"] for c in row_chars if c["source_line"] >= 0))
        visual_rows.append({
            "center_y": row["center_y"],
            "height": row["height"],
            "bbox": (x0, y0, x1, y1),
            "cells": words,
            "anchors": [float(w["bbox"][0]) for w in words],
            "source_line_indices": source_lines,
        })

    visual_rows.sort(key=lambda r: (-float(r["bbox"][3]), float(r["bbox"][0])))
    return visual_rows


def _detect_pdf_table_regions(
    lines: List[Dict[str, Any]],
    page_width: float = 0.0,
    *,
    visual_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """A4: içerikten bağımsız, metin-ağırlıklı ve sayısal tabloları geometriden keşfet.

    Tablo sinyali için artık aynı raw-line üzerinde iki sayı bulunması gibi bir şart
    aranmaz. Asıl sinyal, fiziksel satırlarda tekrarlayan kolon başlangıçlarıdır. Böylece
    "Kurum | Bankacılık Düzenleme..." gibi iki metin kolonlu tablolar da yakalanır.
    """
    usable = [line for line in lines if str(line.get("text", "")).strip()]
    if not usable:
        return []

    if visual_rows is None:
        # Satır bazlı çağrılarda da aynı fiziksel hücre gruplama mantığını kullan.
        # Böylece A4 testi ve üretim yolu aynı geometri kurallarını paylaşır.
        max_y = max((float(line.get("bbox", (0, 0, 0, 0))[3]) for line in usable), default=1000.0)
        visual_rows = _build_pdf_visual_rows({"lines": usable, "height": max_y})
    rows = [r for r in visual_rows if len(r.get("cells", [])) >= 2 and len(r.get("anchors", [])) >= 2]
    if len(rows) < 3:
        return []

    # Kolon başlangıçlarını adaptif x kümelerine ayır.
    all_anchors = sorted(float(x) for row in rows for x in row.get("anchors", []))
    if len(all_anchors) < 6:
        return []
    anchor_tolerance = max(float(page_width or 0.0) * 0.02, 3.0)
    clusters: List[List[float]] = []
    for x in all_anchors:
        if not clusters or abs(x - statistics.median(clusters[-1])) > anchor_tolerance:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    strong = [c for c in clusters if len(c) >= 3]
    if len(strong) < 2:
        return []

    def nearest_cluster(x: float) -> Optional[int]:
        if not strong:
            return None
        distance, idx = min((abs(x - statistics.median(c)), i) for i, c in enumerate(strong))
        return idx if distance <= anchor_tolerance else None

    recurring_pairs = []
    for i in range(len(strong)):
        for j in range(i + 1, len(strong)):
            members = []
            for row in rows:
                ids = {cid for x in row["anchors"] if (cid := nearest_cluster(x)) is not None}
                if i in ids and j in ids:
                    members.append(row)
            if len(members) >= 3:
                recurring_pairs.append((i, j, members))
    if not recurring_pairs:
        return []

    i, j, members = max(recurring_pairs, key=lambda item: len(item[2]))
    # Tablo satırlarının dikey aralıkları da tutarlı olmalı; tek bir paragrafın
    # rastgele kelimeleri tekrarlayan x anchor'larına sahip olsa bile kümelenmez.
    ys = sorted(float(r["bbox"][3]) for r in members)
    gaps = [abs(ys[k] - ys[k + 1]) for k in range(len(ys) - 1)]
    median_row_gap = statistics.median(gaps) if gaps else 0.0
    if median_row_gap <= 0:
        return []
    spread = max(gaps) / max(median_row_gap, 1.0) if gaps else 99.0
    if spread > 3.5 and len(members) < 5:
        return []

    source_line_ids: List[int] = sorted({idx for r in members for idx in r.get("source_line_indices", []) if idx >= 0})
    if len(source_line_ids) < 3:
        return []
    boxes = [r["bbox"] for r in members]
    x_gap = max(
        0.0,
        min(float(strong[j] and min(strong[j])), float(page_width or 0.0)) -
        max(float(strong[i] and max(strong[i])), 0.0),
    )
    confidence = min(0.99, 0.70 + 0.035 * min(len(members), 8) + (0.06 if len(strong) >= 3 else 0.0))
    return [{
        "type": "table",
        "line_indices": source_line_ids,
        "bbox": (
            min(float(b[0]) for b in boxes), min(float(b[1]) for b in boxes),
            max(float(b[2]) for b in boxes), max(float(b[3]) for b in boxes),
        ),
        "column_count": max(2, len({i, j})),
        "row_count": len(members),
        "column_anchors": [statistics.median(strong[i]), statistics.median(strong[j])],
        "median_row_gap": round(median_row_gap, 3),
        "column_gap": round(x_gap, 3),
        "confidence": round(confidence, 3),
    }]

def _order_pdf_physical_lines_a4(
    lines: List[Dict[str, Any]],
    page_width: float = 0.0,
    *,
    visual_rows: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """A4: multi-column + table-aware physical reading order.

    A2'nin güvenli sütun düzenini korur; yüksek güvenli tablo bölgelerini önce
    fiziksel satır sırasını koruyan tek bir layout bölgesi olarak ele alır.
    """
    usable = [line for line in lines if str(line.get("text", "")).strip()]
    if not usable:
        return [], {"mode": "empty", "table_regions": 0, "two_column": False}
    table_regions = _detect_pdf_table_regions(usable, page_width, visual_rows=visual_rows)
    table_line_ids = {idx for r in table_regions for idx in r.get("line_indices", [])}

    # A2'nin mevcut güvenli davranışı. Tablo sinyali yoksa birebir onu kullan.
    if not table_regions:
        ordered = _order_pdf_physical_lines(usable, page_width)
        two_col = ordered != sorted(usable, key=lambda item: (-float(item["bbox"][3]), float(item["bbox"][0]), item["index"]))
        return ordered, {"mode": "columns" if two_col else "single_column", "table_regions": 0, "two_column": two_col}

    # Tablo bulunduğunda bile metni henüz yeniden dizme. A4 burada yalnızca
    # fiziksel tablo bölgesini keşfeder; yanlış bir tablo heuristiğinin gerçek
    # belge okuma sırasını bozmasına izin verilmez. Hücreleri yeniden oluşturma
    # ayrı bir tablo-model/render katmanında yapılabilir. Bu yüzden A2'nin güvenli
    # okuma sırası korunur ve table_regions yalnızca layout metadata'sı olarak döner.
    ordered = _order_pdf_physical_lines(usable, page_width)
    two_col = ordered != sorted(usable, key=lambda item: (-float(item["bbox"][3]), float(item["bbox"][0]), item["index"]))
    return ordered, {"mode": "table_aware", "table_regions": len(table_regions), "two_column": two_col, "table_line_count": len(table_line_ids)}


def _reconstruct_pdf_line_word_boundaries(line: Dict[str, Any]) -> str:
    """A3: Tek fiziksel satırda kaybolmuş kelime boşluklarını güvenli biçimde geri kurar.

    Açık whitespace aynen korunur. Yeni boşluk yalnızca ardışık glyph'ler arasında
    çizgi-içi normal kerning aralığından belirgin biçimde büyük fiziksel bir boşluk
    varsa eklenir. Güvenilirlik için iki glyph'in de ölçülebilir genişliğe sahip olması
    gerekir; PDFium'un zero-width karakter kutuları gap hesabına sokulmaz.
    """
    chars = list(line.get("chars", []) or [])
    raw = str(line.get("text", "") or "")
    if not chars or not raw:
        return raw

    widths = []
    pair_gaps = []
    previous_box = None
    previous_width = None
    previous_was_space = True
    for item in chars:
        ch = str(item.get("char", ""))
        box = item.get("box")
        if ch.isspace():
            previous_was_space = True
            continue
        current_box = box if isinstance(box, (tuple, list)) and len(box) == 4 else None
        current_width = None
        if current_box is not None:
            try:
                candidate_width = float(current_box[2]) - float(current_box[0])
                if math.isfinite(candidate_width) and candidate_width > 0.1:
                    current_width = candidate_width
                    widths.append(candidate_width)
            except (TypeError, ValueError):
                current_width = None
        if (
            current_width is not None
            and previous_box is not None
            and previous_width is not None
            and not previous_was_space
        ):
            try:
                gap = float(current_box[0]) - float(previous_box[2])
                if math.isfinite(gap) and gap >= 0:
                    pair_gaps.append(gap)
            except (TypeError, ValueError):
                pass
        # Zero-width glyph'ler (ör. bazı i/y kutuları) bir sonraki gap hesabına
        # referans olmaz; bunları atlayarak fiziksel geometriyi daha güvenli tutarız.
        if current_width is not None:
            previous_box = current_box
            previous_width = current_width
        else:
            previous_box = None
            previous_width = None
        previous_was_space = False

    if not widths:
        return raw

    median_width = statistics.median(widths)
    stable_min_width = max(0.75, median_width * 0.30)
    stable_gaps = [g for g in pair_gaps if g > 0]
    median_gap = statistics.median(stable_gaps) if stable_gaps else 0.0
    gap_threshold = max(2.0, median_gap * 3.0, median_width * 0.55)

    out: List[str] = []
    prev_box = None
    prev_width = None
    explicit_space = True
    for item in chars:
        ch = str(item.get("char", ""))
        box = item.get("box")
        if ch.isspace():
            out.append(ch)
            explicit_space = True
            continue

        current_width = None
        if isinstance(box, (tuple, list)) and len(box) == 4:
            try:
                width = float(box[2]) - float(box[0])
                if math.isfinite(width) and width > 0.1:
                    current_width = width
            except (TypeError, ValueError):
                current_width = None

        if (
            prev_box is not None
            and prev_width is not None
            and current_width is not None
            and not explicit_space
            and prev_width >= stable_min_width
            and current_width >= stable_min_width
        ):
            try:
                gap = float(box[0]) - float(prev_box[2])
            except (TypeError, ValueError):
                gap = 0.0
            gap_ratio = gap / max(prev_width, median_width, 0.1)
            if gap >= gap_threshold and gap_ratio >= 0.75:
                out.append(" ")

        out.append(ch)
        explicit_space = False
        if current_width is not None:
            prev_box = box
            prev_width = current_width
        else:
            prev_box = None
            prev_width = None

    return "".join(out).rstrip()


def _render_pdf_physical_page_model(model: Dict[str, Any]) -> str:
    """Fiziksel modelden okunabilir text üret; layout metadata'yı da korur."""
    visual_rows = _build_pdf_visual_rows(model)
    ordered, layout = _order_pdf_physical_lines_a4(model.get("lines", []), float(model.get("width", 0.0)), visual_rows=visual_rows)
    rendered_lines = []
    for line in ordered:
        if not str(line.get("text", "")).strip():
            continue
        rendered_lines.append(_reconstruct_pdf_line_word_boundaries(line))
    model["layout"] = layout
    return "\n".join(rendered_lines).strip()


def _extract_pdfium_spatial_pages(pdf_bytes: bytes) -> List[str]:
    """A1+A2: Generic physical model + reading-order Fast Pass.

    Karakterleri yeniden yazmaz; öncelikle PDF extractor'ın mevcut metin sınırlarını
    korur, koordinatları okuma sırası için kullanır. Bu nedenle iyi bir RAW metin
    gereksiz yere tek karakterlik satırlara parçalanmaz.
    """
    if pdfium is None:
        return []
    pages: List[str] = []
    with PDFIUM_LOCK:
        pdf = None
        try:
            pdf = pdfium.PdfDocument(pdf_bytes)
            for index in range(len(pdf)):
                page = None
                textpage = None
                try:
                    page = pdf.get_page(index)
                    textpage = page.get_textpage()
                    raw = str(textpage.get_text_range() or "").replace("\r\n", "\n").replace("\r", "\n")
                    try:
                        width, height = page.get_size()
                    except Exception:
                        width, height = 0.0, 0.0
                    model = _build_pdf_physical_page_model(textpage, raw, width, height)
                    model["page_index"] = index
                    pages.append(_render_pdf_physical_page_model(model) or raw.strip())
                finally:
                    if textpage is not None:
                        try:
                            textpage.close()
                        except Exception:
                            pass
                    if page is not None:
                        try:
                            page.close()
                        except Exception:
                            pass
        except Exception:
            return []
        finally:
            if pdf is not None:
                try:
                    pdf.close()
                except Exception:
                    pass
    return pages


def _reconstruct_pdf_spatial_text(pdf_bytes: bytes) -> str:
    """Uyumluluk adı: generic physical document modelden text üretir."""
    return _join_pdf_pages(_extract_pdfium_spatial_pages(pdf_bytes))


def _extract_pdf_with_pdfplumber_pages(pdf_bytes: bytes) -> List[str]:
    """pdfplumber ile sayfa bazlı ikinci extraction adayı üret."""
    if pdfplumber is None:
        return []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages: List[str] = []
            for page in pdf.pages:
                page_text = ""
                try:
                    words = page.extract_words(
                        x_tolerance=1.5,
                        y_tolerance=3,
                        keep_blank_chars=False,
                        use_text_flow=False,
                    ) or []
                except Exception:
                    words = []
                if words:
                    grouped: Dict[int, List[Tuple[float, str]]] = defaultdict(list)
                    for word in words:
                        word_text = str(word.get("text", "") or "").strip()
                        if not word_text:
                            continue
                        try:
                            top = float(word.get("top", 0.0))
                            x0 = float(word.get("x0", 0.0))
                        except (TypeError, ValueError):
                            top, x0 = 0.0, 0.0
                        grouped[int(round(top * 2.0))].append((x0, word_text))
                    lines: List[str] = []
                    for _, line_words in sorted(grouped.items()):
                        line_words.sort(key=lambda item: item[0])
                        line_text = " ".join(word for _, word in line_words).strip()
                        if line_text:
                            lines.append(line_text)
                    page_text = "\n".join(lines).strip()
                if not page_text:
                    try:
                        page_text = page.extract_text(
                            x_tolerance=1.5,
                            y_tolerance=3,
                            keep_blank_chars=False,
                            use_text_flow=False,
                            layout=False,
                        ) or ""
                    except Exception:
                        page_text = ""
                pages.append(str(page_text).replace("\r\n", "\n").replace("\r", "\n").strip())
            return pages
    except Exception:
        return []


def _join_pdf_pages(page_texts: List[str]) -> str:
    return "\n\n".join(page for page in page_texts if str(page).strip()).strip()


def _pdf_encoding_anomaly_metrics(value: str) -> Dict[str, int]:
    """B1: Geçerli Unicode/Türkçe metni false-positive saymadan encoding bozulmasını ölçer."""
    text = str(value or "")

    # U+FFFD gerçek replacement karakteridir. BLACK SQUARE (■) tek başına
    # replacement kabul edilmez; bazı belgeler bunu meşru sembol olarak kullanabilir.
    # Kaynak dosyanın/ortamın karakter kodlamasına güvenmemek için unicode escape kullan.
    replacement_char_count = text.count("\uFFFD")

    # Yaygın UTF-8 -> Windows/Latin-1 mojibake dizilerini tek olay olarak say.
    # Tek bir marker karakterini (ör. her "Ã") ayrı hata saymıyoruz.
    mojibake_sequences = (
        "\u00c3\u00bc", "\u00c3\u0153", "\u00c3\u00a7", "\u00c3\u2021", "\u00c4\u00b1", "\u00c4\u00b0", "\u00c4\u017e", "\u00c4\u0178",
        "\u00c3\u00b6", "\u00c3\u2013", "\u00c5\u0178", "\u00c5\u0160", "\u00c3\u00aa", "\u00c3\u00ae", "\u00c3\u00bb",
        "\u00e2\u20ac\u201c", "\u00e2\u20ac\u201d", "\u00e2\u20ac\u00a6", "\u00ef\u00bf\u00bd",
    )
    mojibake_sequence_count = sum(text.count(seq) for seq in mojibake_sequences)

    # E8: PDFium, ToUnicode eşlemesi olmayan glifleri (ör. bazı fontlarda "i" ve tire)
    # U+FFFE olarak döndürür; U+FFFF ve NUL da metinde geçerli karakter değildir.
    invalid_unicode_count = text.count("￾") + text.count("￿") + text.count("\x00")

    # Tek başına nadir Latin-1 karakterleri otomatik hata sayma; böylece
    # normal Türkçe Unicode karakterleri ve belgeye özgü semboller false-positive olmaz.
    return {
        "replacement_char_count": replacement_char_count,
        "mojibake_sequence_count": mojibake_sequence_count,
        "invalid_unicode_count": invalid_unicode_count,
        "encoding_anomaly_count": replacement_char_count + mojibake_sequence_count + invalid_unicode_count,
    }


_TURKISH_AGGLUTINATIVE_STEMS = {
    "kimlik", "muhasebe", "değerlendir", "karşılaştır", "gerçekleş", "gerçekleştir",
    "gerekçe", "gerekçelendir", "ilişki", "ilişkilendir", "sınıflandır", "öngör", "öngörü",
    "özelleş", "özelleştir", "şeffaf", "şeffaflaş", "şeffaflaştır", "güvenilir", "güvenilirlik",
    "sürdür", "sürdürülebilir", "etkisiz", "etkisizleş", "etkisizleştir", "bağımsız", "bağımsızlaş",
    "bağımsızlaştır", "yetki", "yetkilendir", "standart", "standartlaş", "standartlaştır",
    "somut", "somutlaş", "somutlaştır", "belirgin", "belirginleş", "belirginleştir",
    "sağlam", "sağlamlaş", "sağlamlaştır", "otomatik", "otomatikleştir", "farklı", "farklılaş",
    "farklılaştır", "bütün", "bütünleş", "bütünleştir", "düzenle", "düzenlen", "uygula",
    "uygulan", "uygulanabilir", "tanımla", "tanımlan", "raporla", "raporlan", "kurum",
}

_TURKISH_AGGLUTINATIVE_SUFFIX_RE = re.compile(
    r"^(siz|lik|likler|likleri|liklerin|liklerinde|liklerinden|"
    r"lık|luk|lük|liğe|lığa|liğin|lığın|liğine|lığına|liğinin|lığının|liği|lığı|lüğü|luğu|"
    r"li|sel|leş|laş|tir|dir|tır|dır|tür|dür|tur|dur|t|"
    r"il|ıl|ul|ül|in|ın|un|ün|"
    r"ebil|abil|eme|ama|"
    r"ecek|acak|miş|mış|muş|müş|ir|er|ar|ur|ür|mez|maz|meli|malı|"
    r"dik|dık|duk|dük|tik|tık|tuk|tük|diğ|dığ|duğ|düğ|tiğ|tığ|tuğ|tüğ|"
    r"diğinde|dığında|duğunda|düğünde|tiğinde|tığında|tuğunda|tüğünde|"
    r"cesine|casına|ken|erek|arak|yerek|yarak|ince|ınca|unce|ünce|"
    r"me|ma|mesi|ması|mesine|masına|mesinden|masından|memesi|maması|memesinden|mamasından|"
    r"ce|ca|ten|tan|den|dan|te|ta|de|da|e|a|i|ı|u|ü|in|ın|un|ün|nin|nın|nun|nün|"
    r"ler|lar|lere|lara|lerin|ların|lerinde|larında|lerinden|larında|"
    r"mektedir|maktadır|miştir|mıştır|cektir|caktır|melidir|malıdır|"
    r"dir|dır|dür|dur|tir|tır|tür|tur)+$",
    re.IGNORECASE,
)


def _is_true_glued_token(token: str) -> bool:
    """Belirteç düzeyinde gerçek yapışık kelime bozulması ile doğal uzun Türkçe sözcükleri ayırır."""
    cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", str(token or "").strip())
    if not re.fullmatch(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", cleaned):
        return False
    if len(cleaned) < 24:
        return False
    if len(cleaned) >= 40:
        return True
    # İç büyük harf geçişi (CamelCase / birleşik başlıklar)
    if re.search(r"[a-zçğıöşü][A-ZÇĞİÖŞÜ]", cleaned):
        return True
    # Doğal Türkçe eklemeli türetim kontrolü
    low = cleaned.lower()
    for stem in _TURKISH_AGGLUTINATIVE_STEMS:
        if low.startswith(stem):
            suffix = low[len(stem):]
            if not suffix or _TURKISH_AGGLUTINATIVE_SUFFIX_RE.fullmatch(suffix):
                return False
    return True


is_glued_token = _is_true_glued_token


# Rakam ve noktalama dizileri ("4,5,24,25,26,27,33,…" madde listeleri, hesap kodu aralıkları) bozuk metin değildir.
_NUMERIC_RUN = re.compile(r"^[\d.,;:/()\-–]+$")
# Adres ve e-posta da uzun olur ama bozuk değildir (e-imza doğrulama adresi her sayfada: "Adresi:https://…", K5.3b).
_ADDRESS = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w-]+\.[\w.]+", re.I)


# İçindekiler ve form noktaları ("Kapsamı……….46", "……….……", "____") öğe değil ayraçtır (K5.3d).
_LEADER = re.compile(r"[.…_·]{3,}")


def _is_very_long_token(token: str) -> bool:
    """Bozuk çıkarım belirtisi olan çok uzun öğe (≥ 40 karakter); rakam / noktalama dizileri ve adres / e-posta sayılmaz,
    nokta / alt çizgi dizileri ayraç sayılır ve uzunluğa katılmaz."""
    return any(len(part) >= 40 and not _NUMERIC_RUN.match(part) and not _ADDRESS.search(part)
               for part in _LEADER.split(token))


def _pdf_generic_quality_metrics(value: str) -> Dict[str, Any]:
    """B1: Belge şemasından bağımsız genel metin bütünlüğü metrikleri."""
    text = str(value or "")
    normalized_lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    nonempty_lines = [line for line in normalized_lines if line.strip()]
    compact = re.sub(r"\s+", "", text)
    chars = len(compact)
    tokens = [token for token in re.split(r"\s+", text) if token]
    alpha_tokens = [token for token in tokens if re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]", token)]
    glued_tokens = [token for token in tokens if _is_true_glued_token(token)]
    very_long_tokens = [token for token in tokens if _is_very_long_token(token)]
    fragmented_lines = [line for line in nonempty_lines if len(re.sub(r"\W", "", line, flags=re.UNICODE)) <= 2]
    encoding = _pdf_encoding_anomaly_metrics(text)
    replacement_chars = encoding["replacement_char_count"]
    mojibake_sequence_count = encoding["mojibake_sequence_count"]
    encoding_anomalies = encoding["encoding_anomaly_count"]
    control_chars = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\t\r")
    spaces = sum(1 for ch in text if ch.isspace())
    whitespace_ratio = spaces / max(chars, 1)
    fragmentation_ratio = len(fragmented_lines) / max(len(nonempty_lines), 1)
    whitespace_density_excess = max(0.0, whitespace_ratio - 0.35) / 0.35
    glued_ratio = len(glued_tokens) / max(len(alpha_tokens), 1)

    score = 1.0
    score -= min(glued_ratio * 0.45, 0.45)
    score -= min(len(very_long_tokens) * 0.03, 0.18)
    score -= min(fragmentation_ratio * 0.30, 0.30)
    score -= min(whitespace_density_excess * 0.18, 0.18)
    score -= min(replacement_chars * 0.04, 0.16)
    score -= min(mojibake_sequence_count * 0.04, 0.16)
    score -= min(control_chars * 0.02, 0.10)
    if chars < 80:
        score -= 0.10
    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 4),
        "chars": chars,
        "tokens": len(tokens),
        "nonempty_lines": len(nonempty_lines),
        "whitespace_ratio": round(whitespace_ratio, 6),
        "line_fragmentation_ratio": round(fragmentation_ratio, 6),
        "whitespace_density_excess": round(whitespace_density_excess, 6),
        "glued_token_count": len(glued_tokens),
        "very_long_token_count": len(very_long_tokens),
        "replacement_char_count": replacement_chars,
        "mojibake_sequence_count": mojibake_sequence_count,
        "encoding_anomaly_count": encoding_anomalies,
        "control_char_count": control_chars,
    }


def _pdf_page_quality(value: str) -> Dict[str, Any]:
    """Tek sayfanın generic + mevcut alan sinyallerini hesaplar."""
    text = str(value or "")
    generic = _pdf_generic_quality_metrics(text)
    compact = re.sub(r"\s+", "", text)
    chars = len(compact)
    if chars == 0:
        return {
            "status": "OCR", "score": 0.0, "chars": 0, "spaces": 0,
            "space_ratio": 0.0, "long_glued": 0, "glued_ratio": 0.0,
            "very_long_tokens": 0, "encoding_anomalies": 0,
            "turkish_chars": 0, "structural_signals": 0, "table_signals": 0,
        }
    spaces = len(re.findall(r"\s", text))
    glued_runs = [run for run in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]{28,}", text) if _is_true_glued_token(run)]
    long_glued = sum(max(0, len(run) - 20) for run in glued_runs)
    very_long_tokens = sum(1 for token in re.split(r"\s+", text) if _is_very_long_token(token))
    encoding = _pdf_encoding_anomaly_metrics(text)
    encoding_anomalies = encoding["encoding_anomaly_count"]
    turkish_chars = sum(text.count(ch) for ch in "çğıöşüÇĞİÖŞÜ")
    structural_signals = len(re.findall(
        r"(?im)\b(?:(?:GEÇİCİ|EK)\s+)?MADDE\s*[-:]?\s*\d+[A-Za-z]?\b|"
        r"\b\d{1,3}[.)-]\s+[A-ZÇĞİÖŞÜ]|\b\d{3,6}\s+[A-ZÇĞİÖŞÜ]",
        text,
    ))
    table_signals = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        numeric_cells = len(re.findall(r"(?:\d+[.,]?\d*|\d{3,6})", stripped))
        separators = stripped.count("|") + len(re.findall(r"\s{3,}", stripped))
        if numeric_cells >= 2 and separators >= 1:
            table_signals += 1
    space_ratio = spaces / max(chars, 1)
    glued_ratio = long_glued / max(chars, 1)
    score = 1.0
    score -= min(glued_ratio * 3.5, 0.55)
    score -= min(very_long_tokens * 0.025, 0.20)
    score -= min(encoding_anomalies / max(chars, 1) * 8.0, 0.25)
    if chars < 80:
        score -= 0.20
    elif chars < 180:
        score -= 0.08
    if spaces == 0 and chars >= 120:
        score -= 0.15
    if structural_signals == 0 and chars >= 500:
        score -= 0.05
    score = max(0.0, min(1.0, score))
    # B1 generic score, domain-specific score ve mevcut deterministic score ayrı
    # tutulur. Nihai skor en zayıf sinyalin altında kalmaz; generic metrikler
    # yalnızca aşırı bozuk genel metni de yakalayacak şekilde koruyucu alt sınırdır.
    score = min(score, float(generic.get("score", score)))
    if encoding["invalid_unicode_count"]:
        # Tek bir kayıp harf bile kelimeyi bozar ("Tar￾h￾"); sayfa yedek motorla denenmeli.
        score = min(score, 0.87)
    status = "HIGH_CONFIDENCE" if score >= 0.88 else "RETRY" if score >= 0.45 else "OCR"
    return {
        "status": status,
        "score": round(score, 4),
        "chars": chars,
        "spaces": spaces,
        "space_ratio": round(space_ratio, 6),
        "long_glued": long_glued,
        "glued_ratio": round(glued_ratio, 6),
        "very_long_tokens": very_long_tokens,
        "encoding_anomalies": encoding_anomalies,
        "replacement_char_count": encoding["replacement_char_count"],
        "mojibake_sequence_count": encoding["mojibake_sequence_count"],
        "invalid_unicode_count": encoding["invalid_unicode_count"],
        "turkish_chars": turkish_chars,
        "structural_signals": structural_signals,
        "table_signals": table_signals,
        "generic_quality": generic,
    }



def _schema_aware_quality_metrics(
    title: str,
    text: str,
    document_type: str = "",
    structure: Optional[Dict[str, Any]] = None,
    layout: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """B2: keşfedilen belge şemasına göre bütünlük ve tutarlılık metrikleri."""
    text = str(text or "")
    base_quality = _pdf_generic_quality_metrics(text)
    if structure is None:
        structure = classify_legal_structure(title, text, document_type)

    structure_type = str(structure.get("structure_type", "document") or "document")
    parse_status = str(structure.get("parse_status", "") or "")
    parse_confidence = float(structure.get("parse_confidence", 0.0) or 0.0)
    article_count = int(structure.get("article_count", 0) or 0)
    paragraph_count = int(structure.get("paragraph_marker_count", 0) or 0)
    section_count = int(structure.get("section_count", 0) or 0)
    account_code_count = int(structure.get("account_code_count", 0) or 0)
    text_chars = int(structure.get("text_char_count", 0) or 0)

    layout = layout if isinstance(layout, dict) else {}
    table_regions = int(layout.get("table_region_count", 0) or 0)
    table_columns = int(layout.get("table_column_count", 0) or 0)
    table_rows = int(layout.get("table_row_count", 0) or 0)

    checks: Dict[str, Any] = {}
    if structure_type == "article":
        checks["article_markers"] = article_count >= 1
        checks["article_parse"] = parse_status == "parsed"
        checks["article_body"] = article_count >= 1 and text_chars >= 250
        if paragraph_count > 0:
            checks["paragraph_markers"] = paragraph_count >= 1
        schema_score = 0.0
        weights = []
        for key, weight in (("article_markers", 0.45), ("article_parse", 0.25), ("article_body", 0.20)):
            schema_score += weight * float(checks[key])
            weights.append(weight)
        if "paragraph_markers" in checks:
            schema_score += 0.10 * float(checks["paragraph_markers"])
        else:
            schema_score += 0.10
        schema_label = "article"
    elif structure_type == "accounting_plan":
        checks["account_codes"] = account_code_count >= 3
        checks["accounting_parse"] = parse_status == "parsed"
        checks["accounting_density"] = account_code_count >= 3 and text_chars >= 100
        schema_score = (
            0.55 * float(checks["account_codes"])
            + 0.25 * float(checks["accounting_parse"])
            + 0.20 * float(checks["accounting_density"])
        )
        schema_label = "accounting_plan"
    elif structure_type == "section":
        checks["sections"] = section_count >= 2
        checks["section_parse"] = parse_status == "parsed"
        checks["section_body"] = section_count >= 2 and text_chars >= 150
        schema_score = (
            0.60 * float(checks["sections"])
            + 0.25 * float(checks["section_parse"])
            + 0.15 * float(checks["section_body"])
        )
        schema_label = "section"
    elif structure_type == "title_only":
        checks["sufficient_body"] = False
        schema_score = 0.25
        schema_label = "title_only"
    else:
        checks["generic_fallback"] = text_chars >= 250
        schema_score = 0.80 if checks["generic_fallback"] else 0.40
        schema_label = "document"

    layout_schema = {
        "table_region_count": table_regions,
        "table_row_count": table_rows,
        "table_column_count": table_columns,
        "table_present": table_regions > 0,
        "table_geometry_valid": bool(table_regions > 0 and table_rows >= 2 and table_columns >= 2),
    }
    if layout_schema["table_present"]:
        layout_schema["table_score"] = round(
            min(1.0, (0.4 if table_rows >= 2 else 0.0) + (0.4 if table_columns >= 2 else 0.0) + 0.2),
            4,
        )
    else:
        layout_schema["table_score"] = 1.0

    # B2 ana skor generic kaliteyi ve keşfedilen şema tutarlılığını birlikte raporlar;
    # mevcut B1 score'u değiştirmez. B3'te engine/fallback kararı için tüketilebilir.
    generic_score = float(base_quality.get("score", 0.0) or 0.0)
    schema_score = round(max(0.0, min(1.0, schema_score)), 4)
    combined_score = round((generic_score * 0.55) + (schema_score * 0.45), 4)

    return {
        "schema": schema_label,
        "structure_type": structure_type,
        "parse_status": parse_status,
        "parse_confidence": round(parse_confidence, 4),
        "schema_score": schema_score,
        "combined_score": combined_score,
        "generic_score": round(generic_score, 4),
        "article_count": article_count,
        "paragraph_marker_count": paragraph_count,
        "section_count": section_count,
        "account_code_count": account_code_count,
        "text_char_count": text_chars,
        "checks": checks,
        "layout": layout_schema,
        "indexable": bool(structure.get("document_indexable", text_chars >= 250)),
    }

def _select_pdf_engine_b3(
    candidates: List[Tuple[str, List[str]]],
    *,
    title: str = "",
    document_type: str = "",
) -> Dict[str, Any]:
    """B3: Engine/fallback kararını generic + schema-aware kaliteye göre verir.

    A1-A4 katmanlarından gelen adayların içeriğini değiştirmez; yalnızca hangi
    adayın kullanılacağına, fallback gerekip gerekmediğine ve OCR'ın gerekip
    gerekmediğine karar verir. OCR son çare olarak kalır.
    """
    evaluated: List[Dict[str, Any]] = []
    for name, pages in candidates:
        page_list = [str(p or "") for p in (pages or [])]
        quality = _pdf_quality_gate(page_list)
        joined = _join_pdf_pages(page_list)
        schema_quality = _schema_aware_quality_metrics(title, joined, document_type)
        generic_score = float(quality.get("score", 0.0) or 0.0)
        schema_score = float(schema_quality.get("combined_score", 0.0) or 0.0)
        decision_score = (generic_score * 0.70) + (schema_score * 0.30)
        # Aynı kalite seviyesinde PDFium Fast Pass/spatial adayı kararlı eşitlik bozucu olsun.
        priority = {
            "pypdfium2": 0.003,
            "pypdfium2_spatial": 0.002,
            "pdfplumber": 0.001,
            "pypdf": 0.0,
        }.get(name, 0.0)
        decision_score = min(1.0, decision_score + priority)
        evaluated.append({
            "engine": name,
            "pages": page_list,
            "quality": quality,
            "schema_quality": schema_quality,
            "generic_score": round(generic_score, 4),
            "schema_score": round(schema_score, 4),
            "decision_score": round(decision_score, 4),
            "status": str(quality.get("status", "")),
        })

    if not evaluated:
        return {
            "selected_engine": "OCR",
            "selected_score": 0.0,
            "selected_status": "OCR",
            "fallback_required": True,
            "ocr_required": True,
            "candidates": [],
            "reason": "Hiç extraction adayı üretilemedi.",
        }

    evaluated.sort(key=lambda item: (item["decision_score"], item["engine"] == "pypdfium2"), reverse=True)
    selected = evaluated[0]
    selected_pages, page_substitutions = _substitute_invalid_unicode_pages(selected, evaluated[1:])
    selected_score = float(selected["decision_score"])
    selected_status = selected["status"]
    ocr_required = bool(selected_status == "OCR" or selected_score < 0.45)
    fallback_required = bool(selected_status != "HIGH_CONFIDENCE" or len(evaluated) > 1)

    if ocr_required:
        reason = "Tüm extraction adayları düşük güvenilirlikte; OCR gerekli."
    elif selected_status == "HIGH_CONFIDENCE":
        reason = "En iyi aday HIGH_CONFIDENCE; OCR gerekmiyor."
    else:
        reason = "En iyi extraction adayı kullanılabilir, ancak kalite HIGH_CONFIDENCE değil."

    return {
        "selected_engine": selected["engine"],
        "selected_score": round(selected_score, 4),
        "selected_status": selected_status,
        "fallback_required": fallback_required,
        "ocr_required": ocr_required,
        "candidates": [
            {
                "engine": item["engine"],
                "generic_score": item["generic_score"],
                "schema_score": item["schema_score"],
                "decision_score": item["decision_score"],
                "status": item["status"],
                "retry_pages": item["quality"].get("retry_pages", []),
                "ocr_pages": item["quality"].get("ocr_pages", []),
                "table_pages": item["quality"].get("table_pages", []),
            }
            for item in evaluated
        ],
        "selected_pages": selected_pages,
        "page_substitutions": page_substitutions,
        "reason": reason,
    }


def _substitute_invalid_unicode_pages(
    selected: Dict[str, Any],
    others: List[Dict[str, Any]],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """E8: Seçilen adayın geçersiz Unicode içeren sayfalarını, aynı sayfanın temiz olduğu
    en iyi diğer adayla değiştirir. Belge düzeyindeki seçim (boşluk/yapı kalitesi) korunur;
    yalnızca harf kaybı olan sayfalar değişir. Sayfa sayısı uyuşmayan adaylar kullanılmaz."""
    pages = list(selected["pages"])
    substitutions: List[Dict[str, Any]] = []
    page_results = selected["quality"].get("pages", [])
    for index, result in enumerate(page_results):
        if not result.get("invalid_unicode_count"):
            continue
        best = None
        for other in others:
            other_pages = other["quality"].get("pages", [])
            if len(other["pages"]) != len(pages) or index >= len(other_pages):
                continue
            candidate = other_pages[index]
            if candidate.get("invalid_unicode_count") or candidate.get("status") == "OCR":
                continue
            if best is None or float(candidate.get("score", 0.0)) > float(best[1].get("score", 0.0)):
                best = (other, candidate)
        if best is not None:
            pages[index] = best[0]["pages"][index]
            substitutions.append({
                "page": index + 1,
                "engine": best[0]["engine"],
                "invalid_unicode_count": int(result.get("invalid_unicode_count", 0)),
            })
    return pages, substitutions


def _pdf_quality_gate(page_texts: List[str]) -> Dict[str, Any]:
    """Sayfa kalite sonuçlarını belge seviyesinde karar haline getirir."""
    if not page_texts:
        return {"status": "OCR", "score": 0.0, "pages": [], "retry_pages": [], "ocr_pages": [], "table_pages": []}
    page_results = [_pdf_page_quality(text) for text in page_texts]
    retry_pages = [i + 1 for i, item in enumerate(page_results) if item["status"] == "RETRY"]
    ocr_pages = [i + 1 for i, item in enumerate(page_results) if item["status"] == "OCR"]
    table_pages = [i + 1 for i, item in enumerate(page_results) if item.get("table_signals", 0) > 0]
    avg_score = sum(float(item["score"]) for item in page_results) / len(page_results)
    nonempty = sum(1 for item in page_results if item["chars"] > 0)
    document_text = _join_pdf_pages(page_texts)
    schema_quality = _schema_aware_quality_metrics("", document_text, "")
    if nonempty == 0 or len(ocr_pages) == len(page_results):
        status = "OCR"
    elif retry_pages or ocr_pages:
        status = "RETRY"
    elif avg_score >= 0.88:
        status = "HIGH_CONFIDENCE"
    else:
        status = "RETRY"
    return {
        "status": status,
        "score": round(avg_score, 4),
        "pages": page_results,
        "retry_pages": retry_pages,
        "ocr_pages": ocr_pages,
        "table_pages": table_pages,
        "schema_quality": schema_quality,
        "schema_score": schema_quality["combined_score"],
    }


def _normalize_pdf_candidate_pages(page_texts: List[str]) -> List[str]:
    return [repair_extracted_spacing(page) for page in page_texts]

def _extract_pdf_with_pdfplumber(pdf_bytes: bytes) -> str:
    """pdfplumber ile kelime koordinatlarından ikinci extraction adayı üret."""
    if pdfplumber is None:
        return ""

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages: List[str] = []

            for page in pdf.pages:
                # Önce kelime koordinatları: boşlukları fiziksel x konumundan korur.
                try:
                    words = page.extract_words(
                        x_tolerance=1.5,
                        y_tolerance=3,
                        keep_blank_chars=False,
                        use_text_flow=False,
                    ) or []
                except Exception:
                    words = []

                if words:
                    grouped: Dict[int, List[Tuple[float, float, str]]] = defaultdict(list)
                    for word in words:
                        text_value = str(word.get("text", "") or "").strip()
                        if not text_value:
                            continue
                        try:
                            top = float(word.get("top", 0.0))
                            x0 = float(word.get("x0", 0.0))
                        except (TypeError, ValueError):
                            top, x0 = 0.0, 0.0
                        grouped[int(round(top * 2.0))].append((x0, top, text_value))

                    lines = []
                    for _, line_words in sorted(grouped.items()):
                        line_words.sort(key=lambda item: (item[0], item[1]))
                        line_text = " ".join(word for _, _, word in line_words).strip()
                        if line_text:
                            lines.append(line_text)

                    page_text = "\n".join(lines).strip()
                else:
                    try:
                        page_text = page.extract_text(
                            x_tolerance=1.5,
                            y_tolerance=3,
                            keep_blank_chars=False,
                            use_text_flow=False,
                            layout=False,
                        ) or ""
                    except Exception:
                        page_text = ""

                if page_text.strip():
                    pages.append(page_text.strip())

            return "\n\n".join(pages).strip()
    except Exception:
        return ""



__all__ = [
    "classify_legal_structure",
    "is_glued_token",
]
