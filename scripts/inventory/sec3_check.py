"""K1(e): domain_scope.md Bölüm 3'teki her eksik listede (al) mı? Başlık parçalarıyla aranır."""
import json
from pathlib import Path
d = json.loads(Path(Path(__file__).resolve().parents[2] / "mufettis" / "corpus" / "legislation_sources.json").read_text(encoding="utf-8"))
L = d["entries"]
def tl(s): return (s or "").replace("I", "ı").replace("İ", "i").lower()
ITEMS = [
 ("5549", ["suç gelirlerinin aklanmasının önlenmesi hakkında kanun"]),
 ("Tedbirler Yönetmeliği", ["terörün finansmanının önlenmesine dair tedbirler hakkında yönetmelik"]),
 ("Uyum Programı Yönetmeliği", ["uyum programı"]),
 ("MASAK genel tebliğleri", ["mali suçları araştırma kurulu genel tebliği", "masak genel tebliği"]),
 ("6415", ["terörün finansmanının önlenmesi hakkında kanun"]),
 ("6098 TBK", ["türk borçlar kanunu"]),
 ("4721 TMK", ["türk medeni kanunu"]),
 ("İç Sistemler / İSEDES", ["iç sistemleri ve içsel sermaye"]),
 ("Kredilerin Sınıflandırılması", ["kredilerin sınıflandırılması", "karşılıklar hakkında yönetmelik"]),
 ("Bilgi Sistemleri ve Elektronik Bankacılık", ["bilgi sistemleri ve elektronik bankacılık"]),
 ("Kurumsal Yönetim", ["kurumsal yönetim ilkelerine"]),
 ("Destek Hizmeti", ["destek hizmeti"]),
 ("6502", ["tüketicinin korunması hakkında kanun"]),
 ("Tüketici Kredisi Sözleşmeleri Yön.", ["tüketici kredisi sözleşmeleri"]),
 ("Konut Finansmanı Sözleşmeleri Yön.", ["konut finansmanı sözleşmeleri"]),
 ("TCMB Finansal Tüketici Ücretleri", ["finansal tüketicilerden alınacak ücret"]),
 ("Kart Yönetmeliği", ["banka kartları ve kredi kartları hakkında yönetmelik"]),
 ("Finansal tüketici şikâyetleri", ["şikayet", "şikâyet"]),
 ("5941 Çek", ["çek kanunu"]),
 ("6102 TTK", ["türk ticaret kanunu"]),
 ("2004 İİK", ["icra ve iflas kanunu"]),
 ("32 Sayılı Karar", ["türk parası kıymetini koruma hakkında 32"]),
 ("Sermaye Hareketleri Genelgesi", ["sermaye hareketleri genelgesi"]),
 ("6493", ["ödeme ve menkul kıymet mutabakat sistemleri"]),
 ("TCMB ödeme hizmetleri yön.", ["ödeme hizmetleri ve elektronik para ihracı ile ödeme hizmeti sağlayıcıları hakkında yönetmelik"]),
 ("Sır niteliğindeki bilgilerin paylaşılması", ["sır niteliğindeki bilgilerin paylaşılması"]),
 ("6698 KVKK", ["kişisel verilerin korunması kanunu"]),
 ("KVKK ikincil düzenlemeler", ["kişisel verilerin silinmesi", "veri sorumluları siciline", "aydınlatma yükümlülüğünün"]),
 ("6802 Gider Vergileri", ["gider vergileri kanunu"]),
 ("KKDF kararı", ["kaynak kullanımını destekleme fonu"]),
 ("492 Harçlar", ["harçlar kanunu"]),
 ("VUK tahsilatların banka aracılığıyla", ["tahsilat ve ödemelerin banka", "aracı kurumlar veya postane kanalıyla"]),
]
for label, pats in ITEMS:
    hits = [x for x in L if any(p in tl(x["title"]) for p in pats)]
    best = sorted(hits, key=lambda x: (x["priority"], len(x["title"])))[:3]
    print(("OK  " if hits else "YOK ") + label, "|", len(hits), "|", " ;; ".join(f"p{x['priority']} {x['title'][:70]}" for x in best))
