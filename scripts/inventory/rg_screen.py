"""K1.4 aşama 3: rg_docs/*.txt yargı metinlerinde konu terimi eleği → rg_screen.json.

Her metin için: İBK mı (içtihadı birleştirme — konusuna bakılmaksızın tek tek incelenir), terim başına sayı, örnek
cümleler. Karar: "aday" (bankacılık çekirdek terimi), "sınırda" (yalnızca genel hukuk terimi → Sonnet okur),
"dışarıda" (hiç terim yok). Kanun yararına bozma kararları da kapsanır; seçim gerekçeli yapılır, eleme yalnızca
aday çıkarır.
"""
import json
import re
from pathlib import Path

CORE = {  # şube işine doğrudan değen terimler
    "banka": r"\bbanka\w*", "mevduat": r"\bmevduat\w*", "kredi": r"\bkredi\w*", "çek": r"\bçek(?:in|e|i|ler\w*|le)?\b",
    "senet": r"\bbono\b|\bpoliçe\w*|\bkambiyo\w*|\bsenet\w*", "haciz": r"\bhaciz\w*|\bhacz\w*",
    "ipotek": r"\bipotek\w*|\brehin\w*", "kefalet": r"\bkefalet\w*|\bkefil\w*", "faiz": r"\bfaiz\w*",
    "hesap": r"\bhesab\w*|\bhesap\b|\bhesapta\w*", "kart": r"\bkredi kart\w*|\bbanka kart\w*",
    "havale": r"\bhavale\w*|\beft\b|\bfast\b", "tmsf": r"\btmsf\b|\btasarruf mevduatı sigorta\w*",
    "bddk": r"\bbddk\b|\bbankacılık düzenleme\w*", "masak": r"\bmasak\b|\baklama\w*",
    "tüketici": r"\btüketici\w*", "kiralık kasa": r"\bkiralık kasa\w*", "döviz": r"\bdöviz\w*|\bkambiyo\w*",
}
GENERAL = {  # tek başına bankacılık sayılmaz; Sonnet'e sınırda olarak gider
    "icra": r"\bicra\w*", "miras": r"\bmiras\w*|\bmirasçı\w*|\btereke\w*", "vekalet": r"\bvek[aâ]let\w*",
    "vergi": r"\bvergi\w*", "tebligat": r"\btebli[gğ]\w*", "temlik": r"\btemlik\w*", "zamanaşımı": r"\bzamanaşım\w*",
}
IBK = re.compile(r"içtihad[ıi]\s*birleştir|içtihatları\s*birleştir|birleştirme\s*kurulu|büyük\s*genel\s*kurul", re.I)
SENT = re.compile(r"[^.!?\n]{20,400}[.!?]")


def scan(text):
    low = text.lower()
    core = {k: len(re.findall(p, low)) for k, p in CORE.items()}
    gen = {k: len(re.findall(p, low)) for k, p in GENERAL.items()}
    core = {k: v for k, v in core.items() if v}
    gen = {k: v for k, v in gen.items() if v}
    pats = [re.compile(p) for k, p in CORE.items() if k in core and k not in ("hesap", "faiz", "tüketici")]
    examples = [m.group(0).strip() for m in SENT.finditer(text) if any(p.search(m.group(0).lower()) for p in pats)][:6]
    strong = sum(v for k, v in core.items() if k not in ("hesap", "faiz", "tüketici"))
    verdict = "aday" if strong >= 2 else ("sınırda" if core or gen else "dışarıda")
    return {"ibk": bool(IBK.search(text)), "core": core, "general": gen, "verdict": verdict, "examples": examples,
            "chars": len(text)}


if __name__ == "__main__":
    out = {}
    for f in sorted(Path("rg_docs").glob("*.txt")):
        text = f.read_text(encoding="utf-8")
        url, _, body = text.partition("\n")
        out[f.stem] = {"url": url, **scan(body)}
    Path("rg_screen.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(len(out), "metin;", Counter(v["verdict"] for v in out.values()), "; İBK içeren:",
          sum(v["ibk"] for v in out.values()), "; boş (<300 kr):", sum(v["chars"] < 300 for v in out.values()))
