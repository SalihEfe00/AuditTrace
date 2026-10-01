"""Envanter araştırması için nazik istemci: dürüst UA, sistem sertifika deposu, robots.txt, 3 sn, disk önbelleği."""
import hashlib
import sys
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import requests  # noqa: E402

from mufettis.common.http import SystemTrustAdapter  # noqa: E402

UA = "MufettisAI/0.13 (+local-decision-support)"
CACHE = Path(__file__).parent / "records" / "cache"
CACHE.mkdir(exist_ok=True)
_s = requests.Session()
_s.mount("https://", SystemTrustAdapter())
_s.headers["User-Agent"] = UA
_last = [0.0]
_robots = {}


def _wait():
    d = 3.0 - (time.monotonic() - _last[0])
    if d > 0:
        time.sleep(d)
    _last[0] = time.monotonic()


def allowed(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = None
        try:
            _wait()
            r = _s.get(base + "/robots.txt", timeout=40)
            if r.status_code == 200:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(r.text.splitlines())
        except Exception:
            rp = None
        _robots[base] = rp
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(UA, url)


def get(url, method="GET", data=None, headers=None, cache=True):
    key = hashlib.sha1((method + url + str(data)).encode()).hexdigest()
    f = CACHE / key
    if cache and f.exists():
        return f.read_bytes()
    if not allowed(url):
        raise PermissionError(f"robots.txt: {url}")
    _wait()
    r = _s.request(method, url, data=data, headers=headers or {}, timeout=60)
    r.raise_for_status()
    if cache:
        f.write_bytes(r.content)
    return r.content
