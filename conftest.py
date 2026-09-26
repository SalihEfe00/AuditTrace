# -*- coding: utf-8 -*-
"""
Pytest oturumu için veri izolasyonu.

Testler production `data/` dizinine asla yazmamalıdır. Bu dosya, herhangi bir
`mufettis` modülü import edilmeden ÖNCE çalışır ve:

1. Production `data/` dizinini geçici bir dizine kopyalar (salt-okunur test
   korpusları — rag_lab, source_cache vb. — testlerde aynen kullanılabilsin diye).
2. `MUFETTIS_DATA_DIR` ortam değişkenini bu kopyaya yönlendirir; `mufettis.config`
   tüm veri yollarını buradan türetir.
3. Oturum sonunda production `data/` dizininin değişmediğini doğrular; değiştiyse
   oturumu başarısız sayar.

Kaynak dizin `MUFETTIS_TEST_SOURCE_DATA_DIR` ile değiştirilebilir. Kaynak yoksa
boş bir veri dizini kullanılır (korpus gerektiren testler kendilerini atlar).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

# `mufettis` paketini import etmek config'i erken yükleyeceği için yardımcı modül
# dosya yolundan doğrudan yüklenir.
_spec = importlib.util.spec_from_file_location(
    "_mufettis_test_isolation", _REPO_ROOT / "mufettis" / "tests" / "isolation.py"
)
_isolation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_isolation)

_PRODUCTION_DATA_DIR = Path(
    os.environ.get("MUFETTIS_TEST_SOURCE_DATA_DIR") or (_REPO_ROOT / "data")
).resolve()

_TEST_DATA_PREFIX = "mufettis_test_data_"
# Önceki oturumlardan kilit nedeniyle silinemeyen kopyalar (her biri ~200 MB) temizlenir.
# Bir oturum birkaç dakika sürdüğünden 15 dakikadan eski kopyalar artık kullanılmıyordur.
_isolation.cleanup_stale_copies(_TEST_DATA_PREFIX, max_age_seconds=900)
_TEST_ROOT, _TEST_DATA_DIR = _isolation.create_isolated_data_copy(_PRODUCTION_DATA_DIR, prefix=_TEST_DATA_PREFIX)
os.environ["MUFETTIS_DATA_DIR"] = str(_TEST_DATA_DIR)
_PRODUCTION_SNAPSHOT = _isolation.snapshot(_PRODUCTION_DATA_DIR)


def pytest_report_header(config):
    return f"mufettis test data dir: {_TEST_DATA_DIR} (production data isolated)"


def pytest_sessionstart(session):
    from mufettis.config import DATA_DIR

    if DATA_DIR.resolve() != _TEST_DATA_DIR.resolve():
        raise RuntimeError(
            f"Test veri izolasyonu kurulamadı: DATA_DIR={DATA_DIR}, beklenen={_TEST_DATA_DIR}. "
            "mufettis.config conftest'ten önce import edilmiş olabilir."
        )


def pytest_sessionfinish(session, exitstatus):
    changes = _isolation.diff(_PRODUCTION_SNAPSHOT, _isolation.snapshot(_PRODUCTION_DATA_DIR))
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)
    if changes:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = [f"PRODUCTION DATA MUTATED DURING TESTS ({len(changes)} dosya):"] + changes[:50]
        if reporter is not None:
            reporter.write_line("")
            for line in lines:
                reporter.write_line(line, red=True)
        session.exitstatus = 1
