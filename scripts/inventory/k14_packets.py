"""K1.4 etiketleme paketleri: 68 İBK + kefalet / ipotek / kredi geçen yargı metinleri → records/k14_packets.json.

Her paket: belge kimliği, tarih, mahkeme, RG başlıkları, metin dosyası, eşleşen terim sayıları, metnin başı (İBK'da
"içtihadı birleştirme"nin ilk geçtiği yer; konu ve taraflar genellikle oradadır) ve terimlerin geçtiği en çok 12 cümle. Etiketleyen paketten karar verir, emin değilse tam
metni (rg_docs/<belge>.txt) açar. Kullanım (records/ içinden): python ../k14_packets.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mufettis.corpus.citations import turkish_lower  # noqa: E402
from rg_screen import CORE, IBK  # noqa: E402

TERMS = ("kefalet", "ipotek", "kredi", "banka", "senet", "çek")
PATTERN = re.compile("|".join(CORE[k] for k in TERMS))
SENT = re.compile(r"[^.!?\n]{30,500}[.!?]")
HEAD_CHARS, MAX_SENTENCES = 1500, 12


def doc_id(row):
    name = row["url"].rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".pdf") else name.split("#")[0].split(".")[0]


def head_at(text, ibk):
    """İBK paketinde baş, "içtihadı birleştirme"nin ilk geçtiği yerin biraz öncesinden (bir günde birden çok karar var)."""
    m = IBK.search(turkish_lower(text)) if ibk else None  # konum değişmez: harf harf küçültme
    return max(0, m.start() - 300) if m else 0


def main():
    rows = json.loads(Path("rg_judiciary.json").read_text(encoding="utf-8"))
    screen = json.loads(Path("rg_screen.json").read_text(encoding="utf-8"))
    titles = {}
    for r in rows:
        titles.setdefault(doc_id(r), []).append({"court": r["court"], "date": r["date"], "title": r["text"]})
    ibk_docs = {doc_id(r) for r in rows if re.search(r"birle[şs]tirme", r["text"], re.I)}
    credit_docs = {k for k, v in screen.items() if any(v["core"].get(t) for t in ("kefalet", "ipotek", "kredi"))}
    packets = []
    for doc in sorted(ibk_docs | credit_docs):
        path = Path("rg_docs") / f"{doc}.txt"
        text = path.read_text(encoding="utf-8").partition("\n")[2] if path.exists() else ""
        hits = [m.group(0).strip() for m in SENT.finditer(text) if PATTERN.search(turkish_lower(m.group(0)))]
        packets.append({
            "doc": doc, "group": "ibk" if doc in ibk_docs else "kredi_terimi",
            "rg_titles": titles.get(doc, []), "text_file": f"scripts/inventory/records/rg_docs/{doc}.txt",
            "chars": len(text), "terms": screen.get(doc, {}).get("core", {}),
            "head": re.sub(r"\s+", " ", text[head_at(text, doc in ibk_docs):][:HEAD_CHARS]),
            "sentences": hits[:MAX_SENTENCES],
        })
    Path("k14_packets.json").write_text(json.dumps(packets, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(packets), "paket;", sum(p["group"] == "ibk" for p in packets), "İBK;",
          sum(len(p["head"]) + sum(map(len, p["sentences"])) for p in packets), "karakter")


if __name__ == "__main__":
    main()
