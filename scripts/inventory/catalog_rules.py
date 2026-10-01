"""Kurum katalogları için karar kuralları: her kayıt → (decision, reason). Sıra önemli: ilk eşleşen kural."""
import json
import re

LICENCE = re.compile(r"faaliyet izn|faaliyet izni kapsam|faaliyet göstermek üzere|izninin (iptal|sona|geçici)|"
                     r"kuruluş izni|unvanlı|ünvanlı|Banka Meclisi Kararının", re.I)


def rule(rules, title, page=""):
    for pattern, decision, reason in rules:
        if re.search(pattern, title + " ¦ " + page, re.I):
            return decision, reason
    return None


TCMB = [
    (LICENCE.pattern, "alma", "tek kuruluşa faaliyet izni / iptal kararı; genel kural koymaz (yetkili kuruluş listesi ayrıca alınır)"),
    (r"Faaliyet İzni Başvurusunda|Rapor Formatı|Rapor Şablonu|Form Bildirimleri|Özkaynak Hesaplaması|Topluluk Bulutu|"
     r"Etiket Kullanımı|Logo ve Marka", "alma", "ödeme / e-para kuruluşunun TCMB'ye raporlama ve iç işleyişi; banka şubesine değmez"),
    (r"Reeskont|Avans Kredisi|Swap|Likidite Desteği|Eski Talimat", "alma",
     "TCMB'nin bankalara kullandırdığı kredi / likidite imkânı (para politikası aracı); şube işlemine yükümlülük getirmez"),
    (r"¦ Piya", "alma", "TCMB ile banka hazinesi arasındaki piyasa (APİ, repo, likidite) işlemi; şube işlemine uygulanmaz"),
    (r"İhbarlı Döviz", "al", "ihbarlı döviz mevduatı; şube mevduat işlemi"),
    (r"¦ Ista", "alma", "TCMB'ye istatistik raporlaması; şube işlemine yükümlülük getirmez"),
    (r"Web Servis|NYS|Nakit Yönetim|MONY", "al", "şube nakit / banknot teslimatı (NYS) kuralları"),
    (r"Tedavülden Kaldırıl|Banknot|Madenî Ufaklık|Resim ve Görüntü", "al", "banknot değişimi / tedavülden kalkan para; şube veznesi"),
    (r"Teknik İlke|Karekod Yönlendirme|Logo Kullanım|API İlke|İşyeri Kayıt", "al", "TR Karekod / ödeme teknik kuralları; şube ve dijital kanal ödeme işlemleri"),
    (r"Mutabakat Sistemlerinde Kullanılabilecek Teminat|Bilgi Sistemleri Hakkında Tebliğ", "al", "ödeme sistemi katılımcısı olarak bankanın yükümlülüğü"),
    (r".", "al", "TCMB mevzuat listesi; banka / şube işlemine doğrudan kural"),
]

TBB = [
    (r"Gerekçe|Teklif Ettiği Metin|Kabul Ettiği Metin|Tutana|Komisyon(u|unun)? (Rapor|Kabul)|Cumhuriyet Dönemi|"
     r"¦ .*bankacilik-kanunu", "alma",
     "yasama süreci belgesi (tasarı, gerekçe, komisyon metni, tutanak); bağlayıcı değil, kanunun kendisi alınıyor"),
    (r"^(Law No|Principles|A Guideline)", "alma", "İngilizce çeviri; Türkçe asıl metni alınıyor"),
    (r"Tasarı|Taslağı|Görüş|Değerlendirme|Rapor|Karşılaştırma|Yanıtı|Değişiklik Öneri|Komisyon Raporu", "alma",
     "TBB'nin kanun tasarısı / taslak görüşü; bağlayıcı kural değil"),
    (r"Konsey Yönergesi|EEC|/EC", "alma", "AB direktifi; Türk hukukunda doğrudan uygulanmaz"),
    (r"Tavsiye Kararı|Kamuoyuna Duyuru", "al", "TBB tavsiye kararı / duyurusu; bankaların uygulamasına yön verir"),
    (r"^\(Mülga\)|\(İlk Hali\)|4389", "mülga", "yürürlükten kalkmış / ilk hâl; olay tarihi eski belgeler için (M7.3c)"),
    (r"Promosyon.*Genelge", "al", "maaş promosyonu Başbakanlık genelgesi (RG); başka resmî çevrimiçi metni yok, TBB kopyası"),
    (r"¦ .*diger-duzenlemeler", "alma", "başka kurumun düzenlemesinin TBB kopyası (çoğu eski sürüm); asıl metni resmî kaynaktan alınıyor"),
    (r".", "al", "TBB'nin kendi düzenlemesi (tebliğ, mesleki tanzim kararı, etik ilke, hakem heyeti)"),
]

TMSF = [
    (r"Teşkilat|Disiplin|İnsan Kaynakları|İç Denetçi|Kurulunun Çalışma|Sicil Amirleri|Bilgi Edinme|Mevzuat Hazırlama|"
     r"Resmi Yazışma|Çözümleme Gelirleri", "alma", "TMSF / kamu kurumunun iç işleyişi; banka şubesine yükümlülük getirmez"),
    (r"AB Direktifi", "alma", "AB direktifi; Türk hukukunda doğrudan uygulanmaz"),
    (r"Mülga", "mülga", "yürürlükten kalkmış kanun; olay tarihi eski belgeler için"),
    (r"5411 Sayılı Bankacılık Kanunu|5271-|6415-", "alma", "aynı metin resmî kaynaktan (mevzuat.gov.tr) alınıyor"),
    (r"OHAL", "incele", "olağanüstü hal kanunu; kayyım atanan şirket hesapları için gerekebilir"),
    (r".", "al", "TMSF mevzuatı: sigortalı mevduat, zamanaşımı, kayyım / tasfiye işlemleri şubeye yansır"),
]

KVKK_GUIDE_OFF = (r"Reality Show|Seçim Faaliyet|Avukatların|Kamu Kurum ve Kuruluşları|Genetik|Unutulma|Çerez|"
                  r"Duyulan İhtiyaç|Uluslararası ve Ulusal|Kurulu'nun Yapısı|Anayasal Bir Hak")
KVKK = [
    (r"Disiplin Amirleri|Görevde Yükselme|Teşkilat Yönetmeliği|Uzmanlığı Yönetmeliği|Kurulu Çalışma Usul|"
     r"Personel Sertifikasyon", "alma", "KVKK'nın kurum içi / personel düzenlemesi; veri sorumlusu bankaya yükümlülük getirmez"),
    (r"Kongre|Tezleri|Yılında Kişisel|Etken Yapay|Üretken Yapay Zekâ Araçlarının|Podcast|Diğer Dokümanlar",
     "alma", "tanıtım / genel bilgilendirme yayını; bağlayıcı kural içermez"),
    (KVKK_GUIDE_OFF,"alma", "banka şubesi dışındaki sektöre / genel tanıtıma yönelik; şube işlemine kural getirmez"),
    (r"Veri İhlali Bildirimi|Kamuoyu Duyurusu|İHLAL|ŞİKAYET MODÜLÜ|STANDART SÖZLEŞME|KURUL KARARLARI$|Etkinlik|Video|Sınav|"
     r"Gelişmeler|Sempozyum|video-js", "alma", "duyuru / uygulama bağlantısı; mevzuat değil"),
    (r"karar-ozet|Kurul Karar Özet", "incele", "KVKK Kurul karar özeti; bankaya ilişkin olanlar alınacak (tek tek)"),
    (r".", "al", "KVKK mevzuatı / ilke kararı / uygulama rehberi; şubede kişisel veri işleme"),
]

SEDDK_ON = (r"Acente|Bireysel Emeklilik|Emeklilik Sistem|Otomatik Katılım|Hayat|Ferdi Kaza|Kredilerle Bağlantılı|"
            r"Bilgilendirme|Zorunlu Deprem|Afet Sigorta|Yangın|Kefalet|Gelir Koruma|Tahkim|Tüketicinin|Borçlar Kanunu|"
            r"Ticaret Kanunu|Sigortacılık Kanunu|Devlet Katkısı|Kısmen Ödeme|Aktarım|Aracıları|Sağlık Sigortası|"
            r"Katılım Sigortacılığı|Aranmayan Paralar|Bina Tamamlama")
SEDDK = [
    (r"Sigortacılık ve Özel Emeklilik Düzenleme ve Denetleme Kurumu \(SEDDK\)$|Atama Kararı|İnsan Kaynakları|Görevde Yükselme|"
     r"Kurulu Çalışma", "alma", "sayfa başlığı / kurum iç işleyişi"),
    (r"Maden Çalışanları|Koltuk|Aktüerya|Erasmus|Meslek Standardı|Trafik|Bilgi Merkezine Gönderim|Hakemlerinde Aranacak|"
     r"Ayrılma Değerinin Hesaplanması|Nükleer|Tarım", "alma",
     "sigorta şirketinin teknik / başka branş düzenlemesi; banka acenteliğine değmez"),
    (SEDDK_ON, "al", "bankanın sigorta acenteliği / BES aracılığı (bankasürans) ve kredi bağlantılı sigortalar"),
    (r"¦ .*genelge-teblig", "alma", "sigorta şirketlerine yönelik genelge / duyuru; banka acenteliği içermiyor (başlıkça)"),
    (r".", "alma", "sigorta şirketinin iç / teknik düzenlemesi ya da başka sigorta branşı; şube işlemine değmez"),
]


def classify(source, title, page=""):
    rules = {"tcmb": TCMB, "tbb": TBB, "tmsf": TMSF, "kvkk": KVKK, "seddk": SEDDK}[source]
    return rule(rules, title, page)


if __name__ == "__main__":
    import collections
    for src in ("tcmb", "tbb", "tmsf", "kvkk", "seddk"):
        rows = json.load(open(f"catalog_{src}.json", encoding="utf-8"))
        c = collections.Counter(classify(src, r["title"], r.get("page", ""))[0] for r in rows)
        print(src, len(rows), dict(c))
