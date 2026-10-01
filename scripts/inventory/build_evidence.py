"""K1: sınırda kalan öncelik 3 belgeleri için etiketleme kanıt paketleri.

Banka öznesi ya da banka ürünü geçen (screen.json'da actor >= 1 veya product >= 1) her belge için başlık ve en çok
8 kanıt cümlesi (bankanın iş yapan taraf olduğu cümleler önce, sonra ürün cümleleri, sonra diğer banka cümleleri).
Çıktı: k1_batches/batch_NN.json (her biri 100 belge). Kullanım: MUFETTIS_DATA_DIR=<kopya> python build_evidence.py
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mufettis.importers.legislation_importer import document_text  # noqa: E402
from screen import ACTOR, BANK, PRODUCT, SENT  # noqa: E402

BATCH = 100
MAX_SENTS = 8
SENT_CHARS = 300


def evidence(text):
    actor, product, bank = [], [], []
    for sent in SENT.split(text or ""):
        flat = re.sub(r"\s+", " ", sent).strip()
        if len(flat) < 25:
            continue
        flat = flat[:SENT_CHARS]
        if BANK.search(flat) and ACTOR.search(flat):
            actor.append(flat)
        elif PRODUCT.search(flat):
            product.append(flat)
        elif BANK.search(flat):
            bank.append(flat)
    picked = actor[:5] + product[:3]
    picked += bank[: MAX_SENTS - len(picked)]
    return picked[:MAX_SENTS]


if __name__ == "__main__":
    dl = Path(os.environ["MUFETTIS_DATA_DIR"]) / "legislation_downloads"
    records = json.loads((dl / "downloads.json").read_text(encoding="utf-8"))
    screen = json.loads(Path("screen.json").read_text(encoding="utf-8"))
    src = json.loads(Path(Path(__file__).resolve().parents[2] / "mufettis" / "corpus" / "legislation_sources.json")
                     .read_text(encoding="utf-8"))
    sources = {(x["code"] or x.get("url", "")): x for x in next(v for v in src.values() if isinstance(v, list))}
    keys = sorted(k for k, v in screen.items() if "actor" in v and (v["actor"] or v["product"]))
    out_dir = Path("k1_batches")
    out_dir.mkdir(exist_ok=True)
    items = []
    for key in keys:
        path = dl / Path(records[key]["path"]).name
        text, _, _ = document_text(path, path.read_bytes(), main_content=not re.fullmatch(r"\d+\.\d\.\d+", key))
        s = sources.get(key, {})
        items.append({"key": key, "title": s.get("title", ""), "instrument": s.get("instrument", ""),
                      "rg_date": s.get("rg_date", ""), "sentences": evidence(text)})
    for i in range(0, len(items), BATCH):
        (out_dir / f"batch_{i // BATCH:02d}.json").write_text(
            json.dumps(items[i:i + BATCH], ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(items), "belge,", (len(items) + BATCH - 1) // BATCH, "paket")
