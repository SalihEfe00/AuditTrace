"""K1.4: indirilmiş tüm mevzuat metinlerinde ve RG yargı metinlerinde içtihadı birleştirme kararı atıflarını topla."""
import collections, json, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mufettis.importers.legislation_importer import document_text  # noqa: E402

dl = Path(os.environ["MUFETTIS_DATA_DIR"]) / "legislation_downloads"
records = json.loads((dl / "downloads.json").read_text(encoding="utf-8"))
PAT = re.compile(r"[^.;\n]{0,160}(?:içtihad[ıi]|içtihat(?:ları)?)\s*(?:n[ıi]|lar[ıi]n[ıi])?\s*birleştir\w*[^.;\n]{0,160}", re.I)
YEAR = re.compile(r"\b(19[2-9]\d|20[0-2]\d)\b")
hits = collections.defaultdict(list)
n = 0
for key, rec in records.items():
    if rec.get("status") not in ("downloaded", "cached"):
        continue
    path = dl / Path(rec["path"]).name
    try:
        text, _, _ = document_text(path, path.read_bytes(), main_content=not re.fullmatch(r"\d+\.\d\.\d+", key))
    except Exception:
        continue
    n += 1
    for m in PAT.finditer(text):
        hits[key].append(re.sub(r"\s+", " ", m.group(0)).strip())
for f in Path("rg_docs").glob("*.txt"):
    for m in PAT.finditer(f.read_text(encoding="utf-8")):
        hits["RG:" + f.stem].append(re.sub(r"\s+", " ", m.group(0)).strip())
Path("ibk_cites.json").write_text(json.dumps(hits, ensure_ascii=False, indent=1), encoding="utf-8")
allc = [c for v in hits.values() for c in v]
pre = [c for c in allc if any(int(y) < 2000 for y in YEAR.findall(c))]
print(n, "belge;", len(hits), "belgede", len(allc), "İBK geçişi;", len(pre), "tanesinde 2000 öncesi yıl")
