"""mevzuat.gov.tr başlık araması: terim × tür → resmî kod, başlık, RG tarihi (yürürlükteki mevzuat)."""
import json
import re
import sys
from pathlib import Path

import web

TERMS = [
    "banka", "bankacılık", "kredi", "mevduat", "katılım fonu", "kart", "ödeme", "elektronik para", "kambiyo", "döviz",
    "türk parası", "sermaye hareket", "aklanma", "suç gelir", "terör", "kitle imha", "mali suç", "şüpheli işlem",
    "tüketici", "finansal hizmet", "sigorta", "bireysel emeklilik", "faiz", "çek", "kambiyo senedi", "senet", "haciz",
    "icra", "iflas", "konkordato", "tebligat", "kimlik", "nüfus", "vekalet", "noter", "miras", "veraset", "vesayet",
    "vergi", "damga", "harç", "gider vergi", "kaynak kullanımını", "kişisel veri", "sır", "elektronik imza",
    "elektronik ticaret", "güvenlik", "kıymetli maden", "altın", "sermaye piyasası", "menkul kıymet", "finansal kiralama",
    "faktoring", "finansman şirket", "konut finansman", "ipotek", "rehin", "teminat", "kefalet", "hesap", "kasa",
    "tasarruf", "fon", "varlık yönetim", "risk merkezi", "kayıt dışı", "amme alacak", "gümrük", "ihracat", "yatırım",
    "yabancı", "ticaret sicil", "şirket", "kooperatif", "vakıf", "dernek", "sosyal güvenlik", "maaş", "emekli",
    "kripto", "kitle fonlama", "mesafeli", "reklam", "haksız ticari", "hakem heyet", "arabulucu", "zamanaşımı",
    "belge saklama", "arşiv", "muhasebe", "denetim", "bağımsız denetim", "iç denetim", "kurumsal yönetim",
    "tapu", "kat mülkiyeti", "taşınır rehni", "trafik", "vatandaşlık", "yabancılar", "siyasi parti", "sendika",
    "hukuk muhakeme", "ceza muhakeme", "ceza kanunu", "kabahat", "iş kanunu", "iş sağlığı", "özel güvenlik",
    "kayıtlı elektronik posta", "elektronik tebligat", "promosyon", "küçük ve orta", "esnaf", "tarım", "çiftçi",
    "zirai", "kefil", "garanti fonu", "nafaka", "engelli", "erişilebilir", "emisyon", "banknot", "efektif",
    "kripto varlık", "hazine", "borçlanma", "repo", "dijital", "açık bankacılık", "veri", "bilgi sistem",
    "siber", "dolandırıcılık", "ihtiyati", "kamu haznedarlığı", "belediye", "kıymetli evrak", "protesto",
    "zorunlu karşılık", "munzam", "mutabakat", "takas", "saklama", "emanet", "kiralık kasa", "ipotekli",
    "gayrimenkul değerleme", "değerleme", "sözleşme", "bilgilendirme", "şikayet", "hakem", "ücret", "komisyon",
    "idari para ceza", "uzaktan", "kimlik tespit", "risk", "karşılık", "sermaye yeterli", "likidite",
    "kredi kartı", "banka kartı", "üye işyeri", "katılım bank", "faizsiz", "sukuk", "kira sertifika",
    "altın", "gümüş", "ithalat", "ihracat bedel", "dış ticaret", "yatırım teşvik", "destek", "hibe",
    "sosyal yardım", "emekli maaş", "maaş promosyon", "hazine destekli", "konut", "taşıt", "otomotiv",
    "turizm", "sağlık", "eğitim kredi", "burs", "vergi kimlik", "kimlik bildir", "adres", "mernis",
    "malvarlığı", "dondurul", "kamu ihale", "teminat mektub", "ihale uygulama", "akreditif", "vesaik",
    "sosyal yardım", "aile destek", "yersiz ödeme", "ölüm aylığı", "kiralık", "yaşlı", "hükümlü", "tutuklu",
    "gaip", "kayyım", "tasfiye", "iflas idare", "karşılıksız", "sahte", "dolandır", "bilişim", "internet",
    "elektronik haberleşme", "abonelik", "fatura", "otomatik ödeme", "talimat", "posta", "ptt",
    "kredi garanti", "garanti kurum", "promosyon", "personel maaş",
]
FORMS = {
    "kanun": {"MevzuatTur": "Kanun", "YonetmelikMevzuatTur": "OsmanliKanunu"},
    "khk": {"MevzuatTur": "KHK", "YonetmelikMevzuatTur": "OsmanliKanunu"},
    "cbk": {"MevzuatTur": "CumhurbaskaniKararnameleri", "YonetmelikMevzuatTur": "OsmanliKanunu"},
    "cbkarar": {"MevzuatTur": "CumhurbaskaniKararlari", "YonetmelikMevzuatTur": "OsmanliKanunu"},
    "yonetmelik": {"MevzuatTur": "TumYonetmelik", "YonetmelikMevzuatTur": "TumYonetmelik", "UniversiteId": "0",
                   "KurumId": "0"},
    "teblig": {"MevzuatTur": "Teblig", "YonetmelikMevzuatTur": "OsmanliKanunu", "KurumId": "0"},
    "tuzuk": {"MevzuatTur": "Tuzuk", "YonetmelikMevzuatTur": "OsmanliKanunu"},
    "genelge": {"MevzuatTur": "CumhurbaskanligiGenelgeleri", "YonetmelikMevzuatTur": "OsmanliKanunu"},
}
OUT = Path("mevzuat_search.json")
results = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
home = web.get("https://www.mevzuat.gov.tr/", cache=False).decode("utf-8", "replace")
token = re.search(r'name="antiforgerytoken" type="hidden" value="([^"]+)"', home).group(1)


def search(form, term, start=0):
    p = dict(FORMS[form])
    p.update({"AranacakIfade": term, "AranacakYer": "2", "BaslangicTarihi": "", "BitisTarihi": "", "MevzuatNo": "",
              "antiforgerytoken": token})
    body = {"draw": 1, "columns": [], "order": [], "start": start, "length": 100,
            "search": {"value": "", "regex": False}, "parameters": p}
    raw = web.get("https://www.mevzuat.gov.tr/Anasayfa/MevzuatDatatable", method="POST", data=json.dumps(body),
                  headers={"Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest"})
    return json.loads(raw)


for term in TERMS:
    for form in FORMS:
        key = f"{form}|{term}"
        if key in results and not any("error" in r for r in results[key]):
            continue
        rows, start = [], 0
        try:
            while True:
                d = search(form, term, start)
                data = d.get("data") or []
                for it in data:
                    rows.append({"code": f"{it.get('mevzuatTur')}.{it.get('mevzuatTertip')}.{it.get('mevzuatNo')}",
                                 "title": re.sub("<[^>]+>", "", it.get("mevAdi") or ""),
                                 "rg": it.get("resmiGazeteTarihi"), "no": it.get("mevzuatNo")})
                total = int(d.get("recordsTotal") or d.get("recordsFiltered") or 0)
                start += len(data)
                if not data or start >= total or start >= 1000:
                    break
        except Exception as exc:
            rows = [{"error": f"{type(exc).__name__}: {exc}"}]
        results[key] = rows
        OUT.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        print(key, len(rows), flush=True)
print("bitti", len(results), file=sys.stderr)
