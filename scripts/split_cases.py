# -*- coding: utf-8 -*-
"""Yapay zekalara yazdırılan vaka dosyalarını tek tek vakalara böler (M9.4).

Girdi: Masaüstü\\VAKALAR\\P<n>_<yapay zeka>.txt — her dosyada "=== VAKA k ===" ayraçlı vakalar ve vakanın son
"Tür: N" satırı (promptlar: Masaüstü\\Mufettis_Vaka_Promptlari.txt). Çıktı:

- <out>/gelistirme/<id>.txt ve <out>/holdout/<id>.txt: "Tür:" satırı çıkarılmış belge metni (sistem türü görmez)
- <out>/manifest.json: id, kaynak dosya, yapay zeka, prompt, tür, holdout mu, uyarılar

Holdout seçimi metne bakılmadan yapılır: her (prompt, tür) grubundan sabit tohumla rastgele ~1/3. Tür satırı
olmayan dosyada tür prompt sırasından çıkarılır (her türden 2 vaka) ve uyarı yazılır. 11 haneli sayılar (TC kimlik no
olabilir; kurallar kullanmaz) çıktıda maskelenir; birebir aynı metinli vakanın ikincisi atılır (manifest'te
"excluded"). Gerçek banka adı taraması yalnızca uyarı üretir; kaynak dosyalara dokunulmaz.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

SEED = 20261004
# Promptlardaki tür sırası (her türden 2 vaka); tür satırı yazılmamış dosyalar için
PROMPT_TYPES = {
    "P1": list(range(1, 11)), "P2": list(range(11, 22)), "P3": list(range(22, 26)), "P4": list(range(26, 33)),
    "P5": list(range(33, 38)), "P6": list(range(38, 42)), "P7": list(range(42, 48)),
    "P8": ["48a", "48b", "48a", "48b", "49a", "49b", "49a", "49b"], "P9": list(range(50, 54)),
}
DOC_TYPE = {"P1": "kefalet_sozlesmesi", "P2": "kefalet_sozlesmesi", "P3": "ipotek", "P4": "rehin", "P5": "ekspertiz",
            "P6": "dask", "P7": "bono", "P8": "kefalet_sozlesmesi", "P9": "kefalet_sozlesmesi"}
REAL_BANKS = r"ziraat|halkbank|halk bankas|vakıfbank|vakıflar bankas|iş bankas|garanti|akbank|yapı kredi|qnb|" \
             r"denizbank|teb\b|türk ekonomi bankas|ing bank|hsbc|şekerbank|fibabanka|kuveyt türk|albaraka|" \
             r"türkiye finans|odeabank|alternatifbank|anadolubank|burgan|icbc|vakıf katılım|emlak katılım|enpara"
SPLIT = re.compile(r"(?m)^[ \t]*\**[ \t]*=+[ \t]*VAKA[ \t]*([0-9]+[a-z]?)[ \t]*=+\**[ \t]*")
TYPE_LINE = re.compile(r"(?im)^[ \t]*\**[ \t]*Tür[ \t]*:[ \t]*\**[ \t]*([0-9]+[a-z]?)[ \t]*\**[ \t]*$")


def _warnings(text: str) -> list:
    low = text.lower()
    out = []
    if re.search(r"\bTR\s?\d{2}(?:\s?\d{4}){4,}", text):
        out.append("IBAN biçimi")
    m = re.search(REAL_BANKS, low)
    if m:
        out.append(f"gerçek banka adı olabilir: {m.group(0)}")
    return out


def split_file(path: Path):
    # "P1_chatgpt", ikinci partide "P1.2_chatgpt" / "p2_2_gemini": prompt numarasından sonraki parti eki atılır
    m = re.match(r"(?i)^(p\d+)(?:[._]\d+)?_(.+)$", path.stem)
    prompt, ai = m.group(1).upper(), m.group(2).lower().replace("copliot", "copilot")
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    parts = SPLIT.split(text)
    cases = []
    for k in range(1, len(parts), 2):
        label, body = parts[k], parts[k + 1]
        types = TYPE_LINE.findall(body)
        warn = []
        if types:
            ctype = types[-1]
            body = TYPE_LINE.sub("", body)
        else:
            order = PROMPT_TYPES[prompt]
            idx = len(cases) if prompt == "P8" else len(cases) // 2  # P8 listesi vaka başına, diğerleri tür başına
            ctype = order[idx] if idx < len(order) else "?"
            warn.append("tür satırı yok: prompt sırasından çıkarıldı")
        body = body.strip("\n") + "\n"
        body, masked = re.subn(r"(?<!\d)\d{11}(?!\d)", "XXXXXXXXXXX", body)
        if masked:
            warn.append(f"{masked} adet 11 haneli sayı maskelendi")
        cases.append({"prompt": prompt, "ai": ai, "source": path.name, "label": label, "type": str(ctype),
                      "document_type": DOC_TYPE[prompt], "text": body, "warnings": warn + _warnings(body)})
    return cases


def main(src: str, out: str, all_holdout: bool = False, tag: str = "") -> int:
    src_dir, out_dir = Path(src), Path(out)
    cases = []
    for path in sorted(src_dir.glob("*.txt"), key=lambda p: p.name.lower()):
        cases.extend(split_file(path))
    seen = {}
    for c in cases:
        digest = hashlib.sha1(re.sub(r"\s+", " ", c["text"]).strip().encode("utf-8")).hexdigest()
        c["id"] = f"{tag}{c['prompt'].lower()}_{c['ai']}_{c['label']}"
        c["excluded"] = digest in seen
        if c["excluded"]:
            c["warnings"].append(f"aynı metin: {seen[digest]} (atıldı)")
        seen.setdefault(digest, c["id"])
    manifest_all = cases
    cases = [c for c in cases if not c["excluded"]]
    rng = random.Random(SEED)
    groups = {}
    for c in cases:
        groups.setdefault((c["prompt"], c["type"].rstrip("ab")), []).append(c)
    for key in sorted(groups):
        group = groups[key]
        pick = set(rng.sample(range(len(group)), max(1, round(len(group) / 3))))
        for i, c in enumerate(group):
            c["holdout"] = all_holdout or i in pick
    for part in ("gelistirme", "holdout"):
        (out_dir / part).mkdir(parents=True, exist_ok=True)
    keys = ("id", "source", "ai", "prompt", "type", "document_type", "holdout", "excluded", "warnings")
    manifest = [{**{k: c.get(k) for k in keys}, "holdout": None} for c in manifest_all if c["excluded"]]
    for c in cases:
        (out_dir / ("holdout" if c["holdout"] else "gelistirme") / f"{c['id']}.txt").write_text(c["text"], encoding="utf-8")
        manifest.append({k: c[k] for k in keys})
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(cases)} vaka ({len(manifest_all) - len(cases)} tekrar atıldı); holdout {sum(c['holdout'] for c in cases)}; uyarılı {sum(bool(c['warnings']) for c in cases)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--all-holdout", action="store_true", help="partinin tamamı gizli sınav seti (ikinci parti)")
    ap.add_argument("--tag", default="", help="vaka kimliği öneki (ör. s2_); önceki partinin vaka düzeltmeleriyle çakışmasın")
    a = ap.parse_args()
    raise SystemExit(main(a.src, a.out, a.all_holdout, a.tag))
