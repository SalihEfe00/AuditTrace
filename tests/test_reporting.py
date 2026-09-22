# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:
    import chromadb
except Exception:
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

try:
    import streamlit as st
except Exception:
    st = None

try:
    import pdfplumber
except Exception:
    pdfplumber = None

from mufettis.config import *
from mufettis.common.utils import *
from mufettis.audit.service import *
from mufettis.extraction.text_repair import *
from mufettis.extraction.pdf import *
from mufettis.legal.parser import *
from mufettis.harvesting.web_harvester import *
from mufettis.harvesting.resmi_gazete import *
from mufettis.rag.index import *
from mufettis.rag.engine import *
from mufettis.rag.maintenance import *
from mufettis.analysis.risk_engine import *
from mufettis.importers.bulk_importer import *
from mufettis.importers.bootstrapper import *
from mufettis.scheduler.scheduler import *
from mufettis.llm.client import *
from mufettis.exports.report_generator import *
from mufettis.ui.admin import *
from mufettis.ui.app import *

class TestV1318ReportAndAdmin(unittest.TestCase):
    def test_report_preflight_counts(self):
        findings = [
            {"bulgu_id": "1", "mevzuat_eslesmeleri": [{"title": "5549 sayılı Kanun"}]},
            {"bulgu_id": "2", "mevzuat_eslesmeleri": []},
        ]
        p = report_mevzuat_preflight(findings)
        self.assertEqual(p["total_findings"], 2)
        self.assertEqual(p["matched_findings"], 1)
        self.assertEqual(p["unmatched_findings"], 1)

    def test_report_prepare_without_rag_is_explicit(self):
        original = globals().get("_get_rag")
        try:
            globals()["_get_rag"] = lambda **kwargs: None
            prepared, status = prepare_report_findings(
                [{"bulgu_id": "x", "mevzuat_eslesmeleri": []}],
                pd.DataFrame(),
                allow_embedding_download=False,
            )
            self.assertEqual(len(prepared), 1)
            self.assertEqual(status["unmatched_findings"], 1)
            self.assertTrue(status["errors"])
        finally:
            if original is not None:
                globals()["_get_rag"] = original

    def test_docx_no_match_is_explicit(self):
        summary = {
            "row_count": 1,
            "finding_count": 1,
            "affected_rows": 1,
            "high_count": 1,
            "medium_count": 0,
            "low_count": 0,
        }
        findings = [{
            "bulgu_id": "no-ref",
            "source_file": "test.xlsx",
            "satir": 1,
            "bulgu_tipi": "Z-score",
            "risk_skoru": 80,
            "risk_seviyesi": "Yüksek",
            "deger": 1000,
            "kolonlar": ["Tutar"],
            "gerekce": "test",
            "mevzuat_eslesmeleri": [],
        }]
        data = create_docx_report(summary, findings)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("Mevzuat atfı:", xml)

    def test_docx_real_attribution(self):
        summary = {
            "row_count": 1,
            "finding_count": 1,
            "affected_rows": 1,
            "high_count": 1,
            "medium_count": 0,
            "low_count": 0,
        }
        findings = [{
            "bulgu_id": "ref",
            "source_file": "test.xlsx",
            "satir": 1,
            "bulgu_tipi": "Z-score",
            "risk_skoru": 90,
            "risk_seviyesi": "Yüksek",
            "deger": 12500,
            "kolonlar": ["Tutar"],
            "gerekce": "test",
            "mevzuat_eslesmeleri": [{
                "title": "5549 sayılı Kanun",
                "law_number": "5549",
                "document_number": "5549",
                "source": "Resmî Gazete",
                "article_no": "5",
                "paragraph_no": "1",
                "item_no": "a",
                "subitem_no": "2",
                "effective_date": "05.09.2026",
                "status": "active",
                "page": "12",
                "snippet": "5549 sayılı Kanun kapsamında yükümlülük.",
                "url": "https://www.resmigazete.gov.tr/test",
            }],
        }]
        data = create_docx_report(summary, findings)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("Mevzuat atfı:", xml)
        self.assertIn("5549 sayılı Kanun", xml)
        self.assertIn("Madde 5 / Fıkra 1 / Bent a / Alt bent 2", xml)
        self.assertIn("Kanun No: 5549", xml)

