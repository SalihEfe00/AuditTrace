import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
assert os.environ.get("MUFETTIS_DATA_DIR")
from mufettis.corpus.curation import exclude, find_delisted
from mufettis.corpus.legislation_sources import load_sources
from mufettis.rag.engine import LocalRAG
from mufettis.rag.index import IngestIndex
index = IngestIndex()
rag = LocalRAG(allow_download=False)
c = find_delisted(index.iter_records(), {s.key for s in load_sources()})
n = sum(exclude(rag, index, x["file_hash"], x["reason"]) for x in c)
print(len(c), "kayıt,", n, "parça ayıklandı")
for x in c[:40]: print(" ", x["title"][:80])
