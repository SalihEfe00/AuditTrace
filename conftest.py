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

import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parent
_PRODUCTION_DATA_DIR = Path(
    os.environ.get("MUFETTIS_TEST_SOURCE_DATA_DIR") or (_REPO_ROOT / "data")
).resolve()


def _snapshot(root: Path) -> Dict[str, Tuple[int, int]]:
    """Dosya yolu -> (boyut, mtime_ns). Dizin yoksa boş sözlük."""
    snap: Dict[str, Tuple[int, int]] = {}
    if not root.exists():
        return snap
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            snap[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return snap


def _diff(before: Dict[str, Tuple[int, int]], after: Dict[str, Tuple[int, int]]) -> List[str]:
    changes = [f"+ {p}" for p in sorted(after.keys() - before.keys())]
    changes += [f"- {p}" for p in sorted(before.keys() - after.keys())]
    changes += [f"~ {p}" for p in sorted(before.keys() & after.keys()) if before[p] != after[p]]
    return changes


_TEST_ROOT = Path(tempfile.mkdtemp(prefix="mufettis_test_data_"))
_TEST_DATA_DIR = _TEST_ROOT / "data"
if _PRODUCTION_DATA_DIR.is_dir():
    shutil.copytree(_PRODUCTION_DATA_DIR, _TEST_DATA_DIR)
else:
    _TEST_DATA_DIR.mkdir(parents=True)

os.environ["MUFETTIS_DATA_DIR"] = str(_TEST_DATA_DIR)
_PRODUCTION_SNAPSHOT = _snapshot(_PRODUCTION_DATA_DIR)


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
    changes = _diff(_PRODUCTION_SNAPSHOT, _snapshot(_PRODUCTION_DATA_DIR))
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)
    if changes:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        lines = [f"PRODUCTION DATA MUTATED DURING TESTS ({len(changes)} dosya):"] + changes[:50]
        if reporter is not None:
            reporter.write_line("")
            for line in lines:
                reporter.write_line(line, red=True)
        session.exitstatus = 1
