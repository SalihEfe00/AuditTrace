# -*- coding: utf-8 -*-
"""
Müfettiş AI - Denetim, Mevzuat ve Karar Destek Sistemi
"""
from __future__ import annotations

from .config import APP_VERSION
from .rag.engine import LocalRAG
from .rag.index import IngestIndex
from .harvesting.resmi_gazete import ResmiGazeteScraper
from .harvesting.web_harvester import WebSourceHarvester, BrowserWebSourceHarvester
from .importers.bulk_importer import BulkRegulationImporter
from .importers.bootstrapper import RegulationBootstrapper
from .scheduler.scheduler import BackgroundUpdateScheduler
from .analysis.risk_engine import run_analysis
from .tests.runner import run_self_tests

__version__ = APP_VERSION
__all__ = [
    "APP_VERSION",
    "LocalRAG",
    "IngestIndex",
    "ResmiGazeteScraper",
    "WebSourceHarvester",
    "BrowserWebSourceHarvester",
    "BulkRegulationImporter",
    "RegulationBootstrapper",
    "BackgroundUpdateScheduler",
    "run_analysis",
    "run_self_tests",
]
