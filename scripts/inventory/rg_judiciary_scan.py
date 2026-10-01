"""Resmî Gazete günlük fihristlerinde YARGI BÖLÜMÜ: Yargıtay ve Danıştay kararları (içtihadı birleştirme dahil).

Kaynak resmî (resmigazete.gov.tr, robots.txt izinli); nazik istemci (web.py: dürüst UA, 3 sn, önbellek).
Çıktı: rg_judiciary.json [{date, court, text, url}]; kaldığı yerden sürer (rg_judiciary_done.txt).
Kullanım: python rg_judiciary_scan.py [başlangıç YYYY-MM-DD] [bitiş YYYY-MM-DD]
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import web
from bs4 import BeautifulSoup

OUT, DONE = Path("rg_judiciary.json"), Path("rg_judiciary_done.txt")
rows = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
done = set(DONE.read_text(encoding="utf-8").split()) if DONE.exists() else set()
start = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date(2000, 1, 1)
end = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else dt.date.today()
COURT = re.compile(r"YARGITAY|DANIŞTAY|Yargıtay|Danıştay")

day = end
while day >= start:  # yeniden eskiye: önce güncel kararlar
    key = day.isoformat()
    day -= dt.timedelta(days=1)
    if key in done:
        continue
    d = dt.date.fromisoformat(key)
    url = f"https://www.resmigazete.gov.tr/eskiler/{d:%Y/%m}/{d:%Y%m%d}.htm"
    try:
        raw = web.get(url)
    except Exception as exc:  # o gün gazete yok (404) ya da ağ hatası
        if "404" not in str(exc):
            print(key, "HATA", exc, flush=True)
            continue
        raw = b""
    if raw:
        soup = BeautifulSoup(raw, "html.parser")  # meta etiketi windows-1254 bildirir
        text = soup.get_text("\n")
        i = text.find("YARGI")
        if i >= 0:
            for a in soup.find_all("a"):
                t = re.sub(r"\s+", " ", a.get_text(" ")).strip()
                if COURT.search(t) and "Anayasa" not in t:
                    rows.append({"date": key, "court": "Danıştay" if "anıştay" in t or "ANIŞTAY" in t else "Yargıtay",
                                 "text": t, "url": urljoin(url, a.get("href", ""))})
                    print(key, t[:110], flush=True)
    done.add(key)
    if len(done) % 20 == 0 or day < start:
        OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        DONE.write_text("\n".join(sorted(done)), encoding="utf-8")
OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
DONE.write_text("\n".join(sorted(done)), encoding="utf-8")
print("bitti", len(rows), file=sys.stderr)
