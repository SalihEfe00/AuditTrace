"""K1(a) atıf kapanışı, sonraki tur: indirilmiş "al" belgelerinin atıfları (cites1.json biçiminde).

Kullanım: MUFETTIS_DATA_DIR=<kopya> python cite_round.py <çıktı.json> [--max-priority 2]
Yalnızca listede önceliği <= max olan ve indirilmiş belgeler taranır (öncelik 3 elemeden sonra, K3.6).
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mufettis.corpus.citations import tally  # noqa: E402
from mufettis.importers.legislation_importer import document_text  # noqa: E402

out_path = Path(sys.argv[1])
max_pri = int(sys.argv[sys.argv.index("--max-priority") + 1]) if "--max-priority" in sys.argv else 2
dl = Path(os.environ["MUFETTIS_DATA_DIR"]) / "legislation_downloads"
records = json.loads((dl / "downloads.json").read_text(encoding="utf-8"))
src = json.loads(Path(Path(__file__).resolve().parents[2] / "mufettis" / "corpus" / "legislation_sources.json")
                 .read_text(encoding="utf-8"))
pri = {(x["code"] or x.get("url", "")): x["priority"] for x in next(v for v in src.values() if isinstance(v, list))}


def docs():
    n = 0
    for key, rec in records.items():
        if rec.get("status") not in ("downloaded", "cached") or pri.get(key, 9) > max_pri:
            continue
        path = dl / Path(rec["path"]).name
        try:
            text, _, _ = document_text(path, path.read_bytes(), main_content=not re.fullmatch(r"\d+\.\d\.\d+", key))
        except Exception as exc:  # okunamayan belge K2'de hesaplanır
            print("okunamadı", key, type(exc).__name__, file=sys.stderr)
            continue
        n += 1
        yield key, text
    print(n, "belge tarandı")


table = tally(docs())
numbered, named = [], []
NUMBERED_KINDS = {"kanun", "khk", "cbk", "cb_karari", "bkk", "kurul_karari", "karar"}
for (kind, ident), info in table.items():
    names = info["names"].most_common()
    base = {"count": info["count"], "docs": len(info["documents"]), "from": sorted(info["documents"])[:5]}
    if kind in NUMBERED_KINDS and info["sample"].number == ident:
        numbered.append({"kind": kind, "number": ident, **base, "names": [list(n) for n in names[:3]]})
    else:
        named.append({"name": names[0][0] if names else ident, "kind": kind, **base})
out_path.write_text(json.dumps({"numbered": numbered, "named": named}, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(numbered), "numaralı,", len(named), "adlı atıf")
