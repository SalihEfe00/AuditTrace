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


# Tek prompt partisinde (M9.4i) yapay zekanın ara satırları: "DEVAM İÇİN ...", "=== SON ===", bölüm satırları
_CHATTER = re.compile(r"(?i)\bdevam\b|=+\s*son\s*=+|b[öo]l[üu]m\s*p\d")
SECTION = re.compile(r"(?im)^[ \t]*#+[ \t]*B[ÖO]L[ÜU]M[ \t]*(P\d)")
TYPE_PROMPT = {str(t): p for p, types in PROMPT_TYPES.items() for t in types}


def _title_only(text: str) -> str:
    """Kısa başlık metni (≤3 anlamlı satır) ya da boş; prompt süsleri ("═══", "P6 — ...") ve ara satırlar
    ("DEVAM İÇİN ...") başlık sayılmaz."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) > 3:
        return ""
    lines = [ln for ln in lines if not re.match(r"^\s*(?:[═=\-_*#]{3,}|P\d+\s*[—–-])", ln) and not _CHATTER.search(ln)]
    return "\n".join(lines)


def split_file(path: Path):
    # "P1_chatgpt", ikinci partide "P1.2_chatgpt" / "p2_2_gemini": prompt numarasından sonraki parti eki atılır
    # Tek prompt partisinde (M9.4i) dosya adı yalnızca yapay zeka ("chatgpt.txt"): prompt, vakanın "Tür:" numarasından
    # ya da önceki "##### BÖLÜM P3 #####" satırından çıkar.
    m = re.match(r"(?i)^(p\d+)(?:[._]\d+)?_(.+)$", path.stem)
    combined = m is None
    file_prompt, ai = (None, path.stem) if combined else (m.group(1).upper(), m.group(2))
    ai = ai.lower().replace("copliot", "copilot")
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    parts = SPLIT.split(text)
    sections = [(x.start(), x.group(1).upper()) for x in SECTION.finditer(text)]
    starts = [x.start() for x in SPLIT.finditer(text)]
    seen_in_section: dict = {}
    if combined:  # dosya başındaki açılış cümleleri ("Tabii, başlıyorum") başlık değil: son bölüm satırından sonrası
        head = list(SECTION.finditer(parts[0]))
        parts[0] = parts[0][head[-1].end():].split("\n", 1)[-1] if head else ""
    cases = []
    # Bazı yapay zekalar vakanın başlığını ayraçtan ÖNCE yazar ("BONO\n=== VAKA 1 ===\n..."): son "Tür:" satırından
    # sonra kalan metin bir sonraki vakanın başıdır. Dosya başındaki kısa metin (≤3 satır) de ilk vakanın başlığıdır.
    # Tür satırı vakanın başında olabilir: o zaman ardından gelen uzun gövde taşınmaz (yalnızca kısa başlık taşınır).
    lead = _title_only(parts[0])
    for k in range(1, len(parts), 2):
        label, body = parts[k], lead + "\n" + parts[k + 1] if lead else parts[k + 1]
        lead = ""
        last = list(TYPE_LINE.finditer(body))
        if last and k + 2 < len(parts):
            tail = _title_only(body[last[-1].end():])
            if tail:
                body, lead = body[:last[-1].end()], tail
        cut = SECTION.search(body) if combined else None
        if cut:  # yeni bölüm satırı vakayı bitirir; ardındaki kısa metin bir sonraki vakanın başlığıdır
            body, lead = body[:cut.start()], _title_only(body[cut.end():].split("\n", 1)[-1]) if k + 2 < len(parts) else ""
            last = list(TYPE_LINE.finditer(body))
        if combined and last and len([ln for ln in body[:last[-1].start()].splitlines() if ln.strip()]) > 3:
            body = body[:last[-1].end()]  # tür satırı sonda: ardından gelen ara satırlar vakanın parçası değil
        section = next((p for pos, p in reversed(sections) if pos < starts[k // 2]), None)
        types = TYPE_LINE.findall(body)
        warn = []
        if types:
            ctype = types[-1]
            body = TYPE_LINE.sub("", body)
            prompt = file_prompt or TYPE_PROMPT.get(ctype) or section or "?"
        else:
            prompt = file_prompt or section or "?"
            order = PROMPT_TYPES.get(prompt, [])
            n = seen_in_section.get(prompt, 0) if combined else len(cases)
            # Ayrı prompt partilerinde her türden 2 vaka, tek promptta 1; P8 listesi zaten vaka başına
            idx = n if prompt == "P8" or combined else n // 2
            ctype = order[idx] if idx < len(order) else "?"
            warn.append("tür satırı yok: prompt sırasından çıkarıldı")
        seen_in_section[prompt] = seen_in_section.get(prompt, 0) + 1
        if prompt not in DOC_TYPE:
            continue  # bölümü de türü de belli olmayan metin vaka sayılmaz
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
