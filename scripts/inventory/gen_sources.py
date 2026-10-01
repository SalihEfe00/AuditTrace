"""inventory.json ("al", korpusta yok) → legislation_sources.json kayıtları (M6.15.4).

Mevcut 63 kayıt korunur; yeni kayıtlar eklenir. Kaynaksız (resmî adres / kod yok) kalanlar ayrıca raporlanır.

Seçenek A (K1.8a, 2026-10-02): öncelikler commit'li listedeki gibi kalır (07a3a6b6 dondurması); yalnızca K1.8 çekirdeği
(records/k18_core.json) öncelik 2 olur ya da eklenir; envanterin yeni adayları v2'ye kalır. --all: eski davranış (her
şey envanterden yeniden).
Kullanım: python gen_sources.py [--write] [--all]   (--write: depodaki dosyayı günceller)
"""
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mufettis.corpus.legislation_sources import (  # noqa: E402
    INSTRUMENTS, OFFICIAL_HOSTS, SOURCES_FILE, LegislationSource, validate_sources)

HERE = Path(__file__).parent / "records"
TUR_INSTRUMENT = {"1": "kanun", "2": "tüzük", "3": "yönetmelik", "4": "kararname", "7": "yönetmelik",
                  "10": "yönetmelik", "19": "kararname", "20": "karar", "21": "yönetmelik", "9": "tebliğ",
                  "22": "genelge"}
SOURCE_ISSUER = {"bddk": "BDDK", "tcmb": "TCMB", "masak": "MASAK", "kvkk": "KVKK", "tmsf": "TMSF", "seddk": "SEDDK",
                 "tbb": "TBB", "tkbb": "TKBB"}
DIRECT = {"bddk", "tcmb", "masak", "kvkk", "tmsf", "seddk", "tbb", "tkbb", "korpus", "atıf", "elle"}
CB_START = dt.date(2018, 7, 9)

GROUP_RULES = [
    ("masak", r"aklanma|suç gelir|terör|kitle imha|mali suç|şüpheli işlem|malvarlı|mal varlı|güvenlik konseyi|masak"),
    ("kvkk_sir", r"kişisel veri|sır niteliğ|kvkk|veri sorumlu|aydınlatma yükümlülüğ|açık rıza|veri ihlal"),
    ("odeme", r"ödeme|elektronik para|mutabakat|karekod|\bfast\b|\beft\b|\biban\b|uluslararası banka hesap|kart"),
    ("cek_ticaret_icra", r"\bçek|kambiyo senedi|icra|iflas|haciz|konkordato|ticaret kanunu|ticaret sicil|tebligat|"
                         r"amme alacak|protesto"),
    ("kambiyo", r"kambiyo|döviz|türk parası|sermaye hareket|görünmeyen işlem|ihracat|ithalat|efektif|kıymetli maden|"
                r"altın|yuvam|işçi döviz"),
    ("vergi", r"vergi|damga|harç|gider vergi|kaynak kullanımını|tevkifat|stopaj|kdv|veraset"),
    ("tuketici", r"tüketici|mesafeli|reklam|haksız ticari|hakem heyet|arabulucu|ücret|finansal hizmet"),
    ("sigorta_emeklilik", r"sigorta|emeklilik|hayat|deprem|acente|genel şart"),
    ("sermaye_piyasasi", r"sermaye piyasası|menkul kıymet|yatırım (hizmet|fon|kuruluş)|borçlanma araç|repo|pay senedi|"
                         r"\(i+-|\bseri\b"),
    ("katilim", r"katılım|faizsiz|murabaha|mudarebe|müşareke|müşâreke|teverruk|sukuk|kira sertifika"),
    ("mevduat_sigortası", r"tmsf|tasarruf mevduatı sigorta|sigortaya tabi mevduat"),
    ("meslek_birligi", r"bankalar birliği|katılım bankaları birliği|\btbb\b|\btkbb\b|etik ilke|mesleki tanzim"),
    ("sosyal_guvenlik", r"sosyal sigorta|sosyal güvenlik|\bsgk\b|emekli aylığı|genel sağlık"),
    ("borclar_medeni", r"borçlar kanunu|medeni kanun|noter|miras|vesayet|kefalet"),
    ("bddk", r"bankacılık|banka|kredi|mevduat|karşılık|sermaye yeterli|likidite|destek hizmet|iç sistem|"
             r"finansal kiralama|faktoring|finansman şirket|varlık yönetim"),
]


def turkish_lower(s):
    return (s or "").replace("I", "ı").replace("İ", "i").lower()


def group_for(title, source):
    t = turkish_lower(title)
    for g, rx in GROUP_RULES:
        g = "mevduat_sigortasi" if g == "mevduat_sigortası" else g
        if re.search(rx, t):
            return g
    return {"kvkk": "kvkk_sir", "tmsf": "mevduat_sigortasi", "seddk": "sigorta_emeklilik", "tbb": "meslek_birligi",
            "tkbb": "katilim", "masak": "masak", "tcmb": "odeme", "bddk": "bddk"}.get(source, "genel")


def instrument_for(item):
    code = item.get("code") or ""
    if code:
        return TUR_INSTRUMENT.get(code.split(".")[0], "")
    t = turkish_lower(item["title"])
    if "kvkk" in item["sources"] and (item.get("kind") == "kurul kararı" or "karar özeti" in t):
        return "emsal karar"  # tek olaya ilişkin Kurul kararı: yol gösterici
    for rx, inst in [(r"kurul kararı|karar özeti|ilke kararı|sayılı karar", "kurul kararı"), (r"yönetmeli", "yönetmelik"),
                     (r"tebliğ", "tebliğ"), (r"genelge", "genelge"), (r"talimat", "talimat"),
                     (r"rehber|kılavuz|yönerge|tavsiye", "rehber"), (r"standard", "standart"),
                     (r"sözleşme", "tip sözleşme"), (r"genel şart", "rehber"), (r"duyuru|açıklama", "duyuru"),
                     (r"kanun", "kanun"), (r"esaslar|usul", "talimat"), (r"karar", "kurul kararı")]:
        if re.search(rx, t):
            return inst
    return {"bddk": "kurul kararı", "kvkk": "rehber", "tcmb": "talimat", "seddk": "genelge", "tbb": "tebliğ",
            "tkbb": "rehber", "tmsf": "yönetmelik", "masak": "rehber"}.get(item["sources"][0], "duyuru")


def number_for(item, instrument):
    code, t = item.get("code") or "", item["title"]
    if instrument == "kanun":
        if code:
            return code.split(".")[2]
        m = re.search(r"(\d{3,4}) [Ss]ayılı|Kanun No:? ?(\d{3,4})", t)
        return (m.group(1) or m.group(2)) if m else ""
    for rx in (r"^\(\s*\d{1,2}[./]\d{1,2}[./]\d{4}\s*[-–]\s*(\d+)\s*\)",  # BDDK: "(15.08.2024 - 10953) ..."
               r"S[İI]RA NO ?:? ?(\d+)", r"SER[İI] NO ?:? ?(\d+)", r"Sıra No ?:? ?(\d+)", r"Seri No ?:? ?(\d+)",
               r"\((I+-\d+(?:\.\d+)?)\)", r"(?:Sayı|SAYI|Sayılı|No) ?:? ?(\d{4}/\d+)", r"KARAR SAYISI ?:? ?(\d+)",
               r"(\d{4}/\d+) [Ss]ayılı", r"(\d{4}-\d+)$"):
        m = re.search(rx, t)
        if m:
            return m.group(1)
    return ""


def rg_date(item):
    raw = str(item.get("rg") or "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", raw)
    return m.group(0) if m else ""


def issuer_for(item, instrument):
    src = item["sources"][0]
    if src in SOURCE_ISSUER:
        return SOURCE_ISSUER[src]
    code, t = item.get("code") or "", turkish_lower(item["title"])
    tur = code.split(".")[0] if code else ""
    if tur == "1":
        return "TBMM"
    if tur in ("4", "20", "3", "10") or instrument == "kararname":
        d = rg_date(item)
        if d:
            day, mon, yr = map(int, d.split("."))
            if dt.date(yr, mon, day) >= CB_START:
                return "Cumhurbaşkanı"
        return "Cumhurbaşkanı" if tur == "19" else "Bakanlar Kurulu"
    if tur in ("19", "21", "22"):
        return "Cumhurbaşkanı"
    for rx, who in [(r"bankacılık düzenleme|bddk", "BDDK"), (r"merkez bankası|tcmb", "TCMB"),
                    (r"mali suçları araştırma|masak", "MASAK"), (r"sermaye piyasası|\(i+-|seri ?:|spk", "SPK"),
                    (r"kişisel veri", "KVKK"), (r"tüketici|reklam|ticaret sicil|haksız ticari", "Ticaret Bakanlığı"),
                    (r"vergi|damga|harç|tahsilat genel|gider vergi|gümrük", "Hazine ve Maliye Bakanlığı"),
                    (r"sigorta|emeklilik", "SEDDK"), (r"sosyal güvenlik|sosyal sigorta", "SGK"),
                    (r"tebligat|noter|icra|adalet", "Adalet Bakanlığı"), (r"nüfus|kimlik", "İçişleri Bakanlığı")]:
        if re.search(rx, t):
            return who
    return ""


def official_url(item):
    from urllib.parse import quote

    for u in item.get("urls") or []:
        if u and urlparse(u).scheme == "https" and urlparse(u).hostname in OFFICIAL_HOSTS:
            return quote(u, safe=":/?&=%#+,;@!$'()*~")  # boşluk / Türkçe harf içeren dosya adları
    return ""


def clean_title(title):
    """Katalog etiketini resmî ada yaklaştır: BDDK tarih-sayı öneki, "Hk.", tırnaklar."""
    m = re.match(r"^\(\s*(\d{1,2}[./]\d{1,2}[./]\d{4})\s*[-–]\s*(\d+)\s*\)\s*", title)
    t = title[m.end():] if m else title
    t = re.sub(r"\bHk\.?(?=\s|$)", "Hakkında", t)
    t = re.sub(r"\s+", " ", t.strip(" \"“”'’")).strip()
    if m:  # BDDK Kurul kararı: tarih ve sayı bulgu gerekçesinde görünsün (metinde yalnızca "Karar Sayısı" var)
        t += f" (BDDK, {m.group(1).replace(chr(47), chr(46))}, sayı {m.group(2)})"
    return t


GENERATED = ("M6.15", "K1.8")  # bu önekle başlayan gerekçeler her çalıştırmada yeniden üretilir


def main(write=False, keep_all=False):
    inv = json.loads((HERE / "inventory.json").read_text(encoding="utf-8"))["items"]
    data = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    previous = {e.get("code") or e.get("url"): e for e in data["entries"] if e["reason"].startswith(GENERATED)}
    core = json.loads((HERE / "k18_core.json").read_text(encoding="utf-8"))
    data["entries"] = [e for e in data["entries"] if not e["reason"].startswith(GENERATED)]
    existing = {e.get("code") or e.get("url") for e in data["entries"]}
    existing_titles = {turkish_lower(re.sub(r"\s+", " ", e["title"])) for e in data["entries"]}
    new, sourceless, skipped = [], [], collections.Counter()
    # Kurum sayfasındaki kanun kopyası yerine resmî metin: numarayla mevzuat.gov.tr kodu
    law_code = {c.split(".")[2]: c for c in (m.get("code") or "" for m in inv) if c.startswith("1.")}
    for item in inv:
        if item["decision"] != "al" or item.get("in_corpus") or item["sources"] == ["elle"]:
            continue
        code = item.get("code") or ""
        if code and not re.fullmatch(r"\d{1,2}\.\d\.\d+", code):
            code = ""
        url = "" if code else official_url(item)
        if not code and not url:
            sourceless.append({"title": item["title"], "sources": item["sources"], "urls": item.get("urls"),
                               "reason": item["reason"]})
            continue
        key = code or url
        if key in existing or turkish_lower(re.sub(r"\s+", " ", item["title"])) in existing_titles:
            skipped["zaten listede"] += 1
            continue
        instrument = instrument_for(item)
        number = number_for(item, instrument)
        if instrument == "kanun" and not code and law_code.get(number):
            code, url = law_code[number], ""
            key = code
            if key in existing:
                skipped["zaten listede"] += 1
                continue
        if instrument == "kanun" and not number:
            instrument = "duyuru" if not code else instrument
        if instrument not in INSTRUMENTS:
            skipped[f"tür yok: {instrument}"] += 1
            continue
        direct = bool(DIRECT & set(item["sources"])) or bool(item.get("citations"))
        reasons = "; ".join(dict.fromkeys(r for r in item.get("reasons", [item["reason"]])))[:600]
        title = re.sub(r"\s+", " ", item["title"]).strip() if code else clean_title(item["title"])
        entry = {"code": code, "title": title, "instrument": instrument,
                 "number": number, "issuer": issuer_for(item, instrument), "priority": 2 if direct else 3,
                 "group": group_for(item["title"], item["sources"][0]),
                 "reason": f"M6.15 ({', '.join(item['sources'])}): {reasons}", "in_corpus": False,
                 "rg_date": rg_date(item), "url": url}
        if not keep_all:
            if key in core["core"]:
                entry["priority"] = 2
                entry["reason"] = f"K1.8 (Seçenek A): {core['core'][key]}; {entry['reason']}"[:900]
            elif key in previous:
                entry["priority"] = previous[key]["priority"]
            else:
                skipped["Seçenek A: yeni aday v2'de"] += 1
                continue
        problems = LegislationSource(**entry).problems()
        if problems:
            skipped["geçersiz: " + problems[0][:40]] += 1
            continue
        existing.add(key)
        new.append(entry)
    if not keep_all:
        for entry in core["entries"]:  # envanterde olmayan çekirdek kayıtlar
            if (entry.get("code") or entry.get("url")) not in existing:
                existing.add(entry.get("code") or entry.get("url"))
                new.append(entry)
        for key, entry in previous.items():  # dondurulmuş: envanterden artık çıkmasa da listede kalır
            if key not in existing:
                existing.add(key)
                new.append(entry)
                skipped["Seçenek A: dondurulmuş kayıt korundu"] += 1
    entries = data["entries"] + new
    problems = validate_sources([LegislationSource(**e) for e in entries])
    print("yeni kayıt", len(new), collections.Counter(e["priority"] for e in new),
          collections.Counter(e["instrument"] for e in new))
    print("kaynaksız", len(sourceless), "| atlanan", dict(skipped), "| doğrulama sorunu", len(problems))
    (HERE / "sources_new.json").write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "sourceless.json").write_text(json.dumps(sourceless, ensure_ascii=False, indent=1), encoding="utf-8")
    if write and not problems:
        data["entries"] = entries
        data["description"] = (data["description"].split(" M6.15:")[0] + " M6.15: kapsamlı envanterden eklenen "
                               "kayıtlar (öncelik 2 = kurum katalogları, atıf, şube haritası; 3 = mevzuat.gov.tr "
                               "başlık / içerik aramasının geniş katmanı); gerekçe her kayıtta.")
        SOURCES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("yazıldı:", SOURCES_FILE)
    return problems


if __name__ == "__main__":
    main("--write" in sys.argv, keep_all="--all" in sys.argv)
