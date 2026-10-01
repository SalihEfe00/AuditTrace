"""BDDK katalog kayıtlarına karar + gerekçe ver."""
import json, re
SINGLE = re.compile(r"A\.\s?Ş\.|T\.A\.Ş|T\.A\.O|Bank\b|Bankası|N\.V\.|Şubesi|Ltd|unvanlı|ünvanlı|faaliyet izni|kurulmasına izin|"
                    r"izninin iptal|devrine|birleşme|hisse|tescil|yetkisinin iptal|değerleme hizmeti|Dairesi|daire başkanlığı|kuruluş izni|adet (değerleme )?şirket|verme yetkisi|listesinden çıkarıl|Teşkilat|intikal eden bankalar|TMSF.ye devredilen|birleştiril", re.I)
GENERAL = re.compile(r"\bHk\.?|Hakkında|uygulamasına ilişkin|ilişkin Kurul Kararı$|Sektör|bankaların|bankalarca|kredi|kart|"
                     r"mevduat|katılım fon|karşılık|risk|pandemi|döviz|YP|TP|faiz|ücret|taksit|vade", re.I)
rows = json.load(open("bddk_catalog.json", encoding="utf-8"))
out = []
for x in rows:
    t, l = x["title"], x["list"]
    if l == 58:
        d, why = "alma", "taslak; yürürlükte değil (yürürlüğe girerse ilgili listeden alınır)"
    elif l == 63:
        d, why = "mülga", "yürürlükten kalkmış; olay tarihi eski belgeler için önceki metin (M7)"
    elif l in (55, 56):
        if SINGLE.search(t) and not re.search(r"bankaların|bankalarca|Sektör", t, re.I):
            d, why = "alma", "tek kuruluşa özgü izin / iptal / devir / teşkilat kararı; şube işlemine genel kural koymaz"
        else:
            d, why = "al", "sektöre genel uygulama kuralı içeren Kurul kararı"
    elif l == 54:
        d, why = "incele", "BDDK'nın kurum içi düzenlemesi; denetim usulü olanlar alınır"
    else:
        d, why = "al", "BDDK mevzuat listesi (" + x["list_name"] + ")"
    out.append({**x, "source": "bddk", "decision": d, "reason": why})
json.dump(out, open("bddk_classified.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
import collections
print(collections.Counter((o["list"], o["decision"]) for o in out))
for o in out:
    if o["list"] in (55, 56) and o["decision"] == "al": print("AL", o["title"][:150])
