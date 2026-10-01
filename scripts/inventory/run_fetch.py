"""M6.15.5 indirme: MUFETTIS_DATA_DIR veri kopyasına; --hosts other|mevzuat, --priority N."""
import collections, os, sys, time
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("MUFETTIS_DATA_DIR"), "veri kopyası gerekli"
from mufettis.config import DATA_DIR
from mufettis.corpus.legislation_sources import load_sources, select_sources
from mufettis.harvesting.official_legislation import OfficialLegislationFetcher
args = dict(a.split("=", 1) for a in sys.argv[1:])
prio, hosts = int(args.get("--priority", 2)), args.get("--hosts", "other")
src = [s for s in select_sources(load_sources(), prio) if s.reason.startswith("M6.15")]
is_mev = lambda s: urlparse(s.download_url).hostname in ("www.mevzuat.gov.tr", "mevzuat.gov.tr")
src = [s for s in src if (is_mev(s) if hosts == "mevzuat" else not is_mev(s))]
f = OfficialLegislationFetcher(DATA_DIR / "legislation_downloads")
c, t0 = collections.Counter(), time.time()
for i, s in enumerate(src, 1):
    r = f.fetch(s)
    c[r.status] += 1
    print(f"[{i}/{len(src)}] {r.status:<14} {s.key[:70]} | {r.reason[:80]}", flush=True)
print("bitti", dict(c), round(time.time() - t0), "sn")
