"""K1.8: kefalet / teminat (Seçenek A) mevzuat kontrol listesi.

Her kalem için: kaynak listesinde mi (kod / başlık kalıbı), önceliği (v1'de ≤ 2 yüklenir), veri kopyasında indirildi mi
ve yüklendi mi. Kullanım: MUFETTIS_DATA_DIR=<veri kopyası> python k18_checklist.py [çıktı.md]
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mufettis.corpus.citations import turkish_lower  # noqa: E402

# (kalem, blok / kural, eşleşme: kanun numarası ya da küçük harfli başlık kalıbı)
ITEMS = [
    ("6098 Türk Borçlar Kanunu", "kefalet md. 581–603; blok 1", "6098"),
    ("4721 Türk Medeni Kanunu", "ipotek, rehin, aile konutu md. 194; blok 2", "4721"),
    ("6102 Türk Ticaret Kanunu", "kambiyo senetleri, şirket temsili; blok 2–3", "6102"),
    ("6103 TTK yürürlük ve uygulama kanunu", "TTK geçiş hükümleri (K1.2'den)", "6103"),
    ("6750 Ticari İşlemlerde Taşınır Rehni Kanunu", "taşınır rehni; blok 2", "6750"),
    ("Taşınır rehin sicili yönetmeliği", "rehin sicili kaydı; blok 2", r"rehinli taşınır sicil|rehin hakkının kurulması"),
    ("6305 Afet Sigortaları Kanunu", "DASK; blok 2", "6305"),
    ("Zorunlu deprem sigortası tarife / genel şartlar", "DASK poliçesi; blok 2", r"zorunlu deprem sigortası"),
    ("5070 Elektronik İmza Kanunu", "md. 5/2 kefalette güvenli e-imza yok; blok 1", "5070"),
    ("2644 Tapu Kanunu", "ipotek tescili; blok 2", "2644"),
    ("Tapu sicili tüzüğü / yönetmeliği", "tescil usulü; blok 2", r"tapu sicil"),
    ("3402 Kadastro Kanunu", "taşınmaz kaydı (K1.2'den); blok 2", "3402"),
    ("634 Kat Mülkiyeti Kanunu", "kat irtifakı / mülkiyeti üzerinde ipotek; blok 2", "634"),
    ("2004 İcra ve İflas Kanunu", "rehnin paraya çevrilmesi; blok 2", "2004"),
    ("5411 Bankacılık Kanunu", "kredi tanımı, teminat, kredi sınırları; blok 3", "5411"),
    ("Kredi işlemleri yönetmeliği (BDDK)", "kredi tahsisi, teminat, belge; blok 3", r"bankaların kredi işlemlerine ilişkin yönetmelik"),
    ("Kredilerin sınıflandırılması ve karşılıklar yönetmeliği", "teminat türleri ve değerleme; blok 2", r"sınıflandırılması.*karşılık"),
    ("Değerleme hizmeti veren kuruluşlar yönetmeliği (BDDK)", "ekspertiz yetkisi; blok 2", r"değerleme hizmeti"),
    ("SPK değerleme standartları", "ekspertiz raporu; blok 2", r"değerleme standart"),
    ("6502 Tüketicinin Korunması Hakkında Kanun", "TKS Yön. dayanağı; kefalet K12", "6502"),
    ("Tüketici kredisi sözleşmeleri yönetmeliği", "md. 28 şahsi teminat; kefalet K12", r"tüketici kredisi sözleşmeleri"),
    ("5570 Faiz destekli kredi kanunu", "TBK 584/3 eş rızası istisnası; blok 1", "5570"),
    ("KGF Hazine desteği kararı", "KGF kefaleti teminat olarak; K1.7", r"kredi garanti kurum.*hazine desteği"),
    ("TBB Bireysel Sözleşmeler Tebliği", "kredi sözleşmesi şekli (K1.2'den)", r"bireysel sözleşmeler tebliği"),
    ("1512 Noterlik Kanunu", "noter onaylı belgeler, imza sirküleri; blok 3", "1512"),
]


def main(out_path=None):
    entries = json.loads((ROOT / "mufettis" / "corpus" / "legislation_sources.json").read_text(encoding="utf-8"))["entries"]
    dl_dir = Path(os.environ["MUFETTIS_DATA_DIR"]) / "legislation_downloads"
    downloads = json.loads((dl_dir / "downloads.json").read_text(encoding="utf-8"))
    imported = {}
    for line in (dl_dir / "import_runs.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        imported[rec["key"]] = rec["status"]
    rows = ["| Kalem | Neden | Listede (öncelik) | İndirildi | Yüklendi / korpusta |", "|---|---|---|---|---|"]
    for label, why, match in ITEMS:
        if re.fullmatch(r"\d+", match):  # kanun numarası (eski kanunların kodu 1.3., yenilerin 1.5. ile başlar)
            hits = [e for e in entries if e.get("instrument") == "kanun" and e.get("number") == match]
        else:
            hits = [e for e in entries if re.search(match, turkish_lower(e["title"]))]
        if not hits:
            rows.append(f"| {label} | {why} | **yok** | — | — |")
            continue
        for e in hits[:4]:
            key = e.get("code") or e.get("url")
            pri = "korpusta" if e.get("in_corpus") else str(e["priority"])
            d = downloads.get(key, {}).get("status", "—")
            i = "korpusta (öncelik 1)" if e.get("in_corpus") else imported.get(key, "—")
            rows.append(f"| {label} | {why} | {pri}: {e['title'][:70]} | {d} | {i} |")
        if len(hits) > 4:
            rows.append(f"| {label} | | +{len(hits) - 4} kayıt daha | | |")
    text = "\n".join(rows) + "\n"
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
