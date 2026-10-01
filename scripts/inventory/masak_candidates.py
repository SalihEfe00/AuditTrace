"""MASAK katalog adayları (masak.hmb.gov.tr/mevzuat-tamami ve sektorel-sib-rehberleri, 2026-10-01 tarayıcıyla okundu)."""
import json

MG = "https://www.mevzuat.gov.tr/mevzuat?MevzuatNo={no}&MevzuatTur={tur}&MevzuatTertip=5"
C = []


def add(title, url, kind, decision, reason, code=""):
    C.append({"source": "masak", "title": title, "url": url, "kind": kind, "code": code, "decision": decision,
              "reason": reason})


for no, t in [("5549", "Suç Gelirlerinin Aklanmasının Önlenmesi Hakkında Kanun"),
              ("6415", "Terörizmin Finansmanının Önlenmesi Hakkında Kanun"),
              ("7262", "Kitle İmha Silahlarının Yayılmasının Finansmanının Önlenmesine İlişkin Kanun"),
              ("6493", "Ödeme ve Menkul Kıymet Mutabakat Sistemleri, Ödeme Hizmetleri ve Elektronik Para Kuruluşları Hakkında Kanun"),
              ("5237", "Türk Ceza Kanunu"), ("5271", "Ceza Muhakemesi Kanunu"), ("3713", "Terörle Mücadele Kanunu"),
              ("5411", "Bankacılık Kanunu"), ("5326", "Kabahatler Kanunu")]:
    add(t, MG.format(no=no, tur=1), "kanun", "al", "MASAK mevzuat listesi (bankaya / şube işlemine doğrudan dayanak ya da yaptırım)", f"1.5.{no}")
add("Adli Yargı İlk Derece Mahkemeleri ile Bölge Adliye Mahkemelerinin Kuruluş, Görev ve Yetkileri Hakkında Kanun",
    MG.format(no=5235, tur=1), "kanun", "alma", "mahkeme teşkilatı; banka / şube işlemine yükümlülük getirmez", "1.5.5235")
add("1 sayılı Cumhurbaşkanlığı Teşkilatı Hakkında Cumhurbaşkanlığı Kararnamesi (MASAK, BDDK ile ilgili maddeler)",
    MG.format(no=1, tur=19), "cbk", "al", "MASAK'ın görev ve yetkileri (tebligat, bilgi isteme) bu kararnamededir", "19.5.1")
for no, tur, t, why in [
    ("200712454", 3, "Aklama Suçu İncelemesi Hakkında Yönetmelik", "MASAK incelemesinde bankadan bilgi / belge isteme usulü"),
    ("38568", 7, "Bankalarca Kullanılacak Uzaktan Kimlik Tespiti Yöntemlerine ve Elektronik Ortamda Sözleşme İlişkisinin Kurulmasına İlişkin Yönetmelik", "listede var"),
    ("39368", 7, "Finansal Kiralama, Faktoring, Finansman ve Tasarruf Finansman Şirketlerince Kullanılacak Uzaktan Kimlik Tespiti Yöntemleri Hakkında Yönetmelik", "banka dışı kuruluş; bankanın iştirak / aracılık işlemlerinde karşılaştırma"),
    ("38353", 7, "Kitle İmha Silahlarının Yayılmasının Finansmanının Önlenmesine İlişkin Kanunun Uygulanmasına İlişkin Yönetmelik", "malvarlığı dondurma kararlarının bankada uygulanması"),
    ("12064", 7, "Kontrollü Teslimat Uygulaması Esas ve Usulleri Hakkında Yönetmelik", "kolluk / savcılık usulü; banka yükümlülüğü içermez"),
    ("20631", 7, "Mali Suçları Araştırma Kurulu Başkanlığı Elektronik Tebligat Sistemine İlişkin Usul ve Esaslar Hakkında Yönetmelik", "MASAK'ın bankalara elektronik tebligatı"),
    ("45068", 7, "Mali Suçlarla Mücadele Koordinasyon Kurulunun Çalışma Usul ve Esasları Hakkında Yönetmelik", "kurul iç işleyişi; banka yükümlülüğü içermez"),
    ("39080", 7, "Ödeme Hizmetleri ve Elektronik Para İhracı ile Ödeme Hizmeti Sağlayıcıları Hakkında Yönetmelik", "listede var"),
    ("19825", 7, "Ödeme ve Menkul Kıymet Mutabakat Sistemlerinin Faaliyetleri Hakkında Yönetmelik", "EFT / FAST vb. sistem kuralları; şube ödeme işlemleri"),
    ("38569", 7, "Ödemelerde Kripto Varlıkların Kullanılmamasına Dair Yönetmelik", "bankaların kripto varlık aracılığı yasağı"),
    ("22694", 7, "Suç Gelirlerinin Aklanmasının ve Terörizmin Finansmanının Önlenmesi Kapsamında İşlemlerin Ertelenmesine Dair Yönetmelik", "şubede işlem erteleme"),
    ("200713012", 21, "Suç Gelirlerinin Aklanmasının ve Terörün Finansmanının Önlenmesine Dair Tedbirler Hakkında Yönetmelik", "listede var"),
    ("12426", 7, "Suç Gelirlerinin Aklanmasının ve Terörün Finansmanının Önlenmesine İlişkin Yükümlülüklere Uyum Programı Hakkında Yönetmelik", "bankanın uyum programı, eğitim, iç denetim"),
    ("18420", 7, "Terörizmin Finansmanının Önlenmesi Hakkında Kanunun Uygulamasına İlişkin Usul ve Esaslar Hakkında Yönetmelik", "malvarlığı dondurma kararlarının bankada uygulanması"),
]:
    dec = "alma" if "yükümlülüğü içermez" in why else "al"
    add(t, MG.format(no=no, tur=tur), "yonetmelik", dec, why, f"{tur}.5.{no}")
for sira, url, note in [
    (5, MG.format(no=12073, tur=9), "basitleştirilmiş tedbirler (listede var)"),
    (7, "https://masak.hmb.gov.tr/masak-genel-tebligi-sira-no-7-2/", "müşterinin tanınması; mevzuat.gov.tr'de yok, MASAK sayfası"),
    (8, "https://masak.hmb.gov.tr/masak-genel-tebligi-sira-no-8-2/", "listede var"),
    (12, MG.format(no=19810, tur=9), "listede var"), (13, MG.format(no=19995, tur=9), "listede var"),
    (19, MG.format(no=38566, tur=9), "listede var"), (21, MG.format(no=39833, tur=9), "listede var"),
    (29, MG.format(no=42465, tur=9), "listede var"), (30, MG.format(no=42596, tur=9), "listede var"),
    (34, MG.format(no=46302, tur=9), "listede var")]:
    add(f"Mali Suçları Araştırma Kurulu Genel Tebliği (Sıra No: {sira})", url, "teblig", "al", note)
for sira in (1, 2, 3, 4, 6):
    add(f"Mali Suçları Araştırma Kurulu Genel Tebliği (Sıra No: {sira})",
        f"https://masak.hmb.gov.tr/masak-genel-tebligi-sira-no-{sira}/", "teblig", "mülga",
        "yürürlükten kaldırılmış; olay tarihi eski belgeler için önceki metin olarak değerlendirilecek (M7)")
for sira in (9, 10, 11, 14, 15, 16, 17, 18):
    add(f"Mali Suçları Araştırma Kurulu Genel Tebliği (Sıra No: {sira})", "", "teblig", "alma",
        "değiştirici tebliğ; değişiklik 5 / 13 sayılı tebliğlerin derlenmiş metnine işlenmiş")
add("Terörün Finansmanına Yönelik Şüpheli İşlemlerin Bildirimi Genel Tebliği", MG.format(no=11703, tur=9), "teblig",
    "al", "TF şüpheli işlem bildirimi")
add("Aracı Kurumlar, Portföy Yönetim Şirketleri ve Kripto Varlık Hizmet Sağlayıcılar Tarafından ... Genel Tebliğ",
    MG.format(no=39408, tur=9), "teblig", "incele", "banka dışı yükümlü; bankanın aracılık faaliyeti kapsamında gerekebilir")
add("Terörizmin Finansmanının Önlenmesine Dair Uluslararası Sözleşme", "http://ua.mfa.gov.tr/detay.aspx?5887",
    "uluslararasi", "incele", "onaylanmış uluslararası sözleşme; yükümlülükler iç mevzuata aktarılmış")
add("Suçtan Kaynaklanan Gelirlerin Aklanması, Araştırılması, Ele Geçirilmesi ve El Konulmasına Dair Sözleşme",
    "http://ua.mfa.gov.tr/detay.aspx?7432", "uluslararasi", "incele", "onaylanmış uluslararası sözleşme")
U = "https://ms.hmb.gov.tr/uploads/sites/12/"
for t, u, dec, why in [
    ("Şüphe Kategorileri Referans Tablosu", U + "2026/08/SK-KODLAR-21082026-3dcdc4482c3cd056.pdf", "al", "listede var (güncel sürüm adresi)"),
    ("Hawala ve Benzeri Alternatif Değer Transfer Sistemlerinin Tespiti ve ŞİB Rehberi", U + "2026/09/HAWALA-VE-BENZERI-ALTERNATIF-DEGER-TRANSFER-SISTEMLERININ-TESPITI-VE-SUPHELI-ISLEM-BILDIRIMI-REHBERI-2e2ecb667a992fd9.pdf", "al", "listede var (karantinada; bu adres yeniden denenecek)"),
    ("Bankalar ve PTT Sektörel ŞİB Rehberi (Sürüm 2.0)", U + "2024/05/MSK-RHB-SIB-001-2.pdf", "al", "listede var"),
    ("Bankalar ve PTT ŞİB Formu / Kılavuz", U + "2021/02/MSK-FRM-THT-001-1.0_1s4s.pdf", "al", "şubenin ŞİB formu doldurma kuralları"),
    ("Ödeme Kuruluşları ile Elektronik Para Kuruluşları ŞİB Rehberi (2.0)", U + "2024/06/MSK-RHB-SIB-006-2.pdf", "al", "bankanın ödeme hizmetleri ve aracılık ettiği ödeme kuruluşu işlemleri"),
    ("Bankalar Dışında Banka Kartı veya Kredi Kartı Çıkaran Kuruluşlar ŞİB Rehberi", U + "2025/09/SIB-REHBER-BANKA-DISI-KK-1.0-4d8b688f9c5e7560.pdf", "incele", "banka dışı kart çıkaranlar; kart tipolojileri bankada da geçerli"),
    ("Kripto Varlık Hizmet Sağlayıcılar ŞİB Rehberi (2.0)", U + "2024/07/KVHS-REHBER-2-0.pdf", "incele", "bankaya kripto kaynaklı transferler için şüphe göstergeleri"),
]:
    add(t, u, "rehber", dec, why)
for t, why in [("Diğer yükümlü grupları ŞİB rehberleri (talih oyunları, e-ticaret, kıymetli maden, taşınmaz, yatırım ortaklığı, sigorta brokeri, nakil vasıtası, antika, spor kulübü, noter, avukat, SMMM ...)",
                "yükümlü banka değil; ancak müşteri bu sektördense şube için şüphe göstergesi — tek tek incelenecek")]:
    add(t, "https://masak.hmb.gov.tr/sektorel-sib-rehberleri", "rehber", "incele", why)
for t, u in [("Tipolojiler", "https://masak.hmb.gov.tr/tipolojiler"), ("FATF Rehberleri", "https://masak.hmb.gov.tr/fatf-rehberleri"),
             ("Ulusal Risk Değerlendirmesi", "https://masak.hmb.gov.tr/ulusal-risk-degerlendirmesi"),
             ("MASAK Rehberler (yayınlar)", "https://masak.hmb.gov.tr/rehberler"),
             ("KİSYF URD Raporu Özeti", "https://masak.hmb.gov.tr/kisyf-urd-raporu-ozeti")]:
    add(t, u, "sayfa", "incele", "alt belgeleri tek tek listelenecek")
json.dump(C, open("masak_candidates.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(C))
