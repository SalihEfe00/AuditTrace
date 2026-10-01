"""mevzuat.gov.tr başlık araması sonuçlarının kapsam kararı (başlığa göre)."""
import re

TUR = {"1": "kanun", "2": "tüzük", "3": "yönetmelik (BKK)", "4": "khk", "7": "yönetmelik", "8": "üniversite yönetmeliği",
       "9": "tebliğ", "10": "yönetmelik (BKK)", "19": "cbk", "20": "cb / bk kararı", "21": "yönetmelik (CB)",
       "22": "cb genelgesi"}

# Açıkça şube / banka işlemine değen başlıklar
STRONG = re.compile(
    r"\bbanka|bankacılık|mevduat|katılım fon|kredi|kart(ı|lar|lı)?|ödeme hizmet|elektronik para|kambiyo|döviz|türk parası|"
    r"sermaye hareket|aklanma|suç gelir|terörizmin finansman|kitle imha|mali suç|şüpheli işlem|tüketici|finansal hizmet|"
    r"finansal tüketici|faiz|\bçek\b|çek kanun|çekle|kambiyo senedi|haciz|icra ve iflas|amme alacak|tebligat|"
    r"nüfus hizmet|kimlik kart|noter|mirasçı|veraset|vesayet|gider vergi|damga vergi|kaynak kullanımını destekleme|"
    r"kişisel veri|sır niteliğ|elektronik imza|kıymetli maden|altın(?!d)|sermaye piyasası|menkul kıymet|yatırım hizmet|"
    r"yatırım fon|finansal kiralama|faktoring|finansman şirket|konut finansman|ipotek|rehin|kefalet|teminat|"
    r"tasarruf finansman|varlık yönetim|risk merkezi|kayıt dışı|mesafeli|haksız ticari|ticari reklam|hakem heyet|"
    r"arabulucu|bireysel emeklilik|hayat sigorta|sigorta acente|kredilerle bağlantılı|zorunlu deprem|afet sigorta|"
    r"mutabakat sistem|kripto|kitle fonlama|gelir vergisi genel tebliğ|vergi usul kanunu genel tebliğ|"
    r"gelir vergisi kanunu|vergi usul kanunu|katma değer vergisi kanunu|kurumlar vergisi kanunu|harçlar kanunu|"
    r"ceza kanunu|ceza muhakemesi|kabahatler|hukuk muhakemeleri|borçlar kanunu|medeni kanun|ticaret kanunu|"
    r"ticaret sicil|kooperatifler kanunu|dernekler|vakıflar|sosyal sigortalar|emekli|maaş promosyon|zorunlu karşılık|"
    r"efektif|banknot|emisyon (müdürlüğü|işlem)|kamu haznedarlığı|borçlanma araç|kira sertifika|repo|taşınır rehni|tapu|kat mülkiyeti|"
    r"yabancılar ve uluslararası|vatandaşlık|kayıtlı elektronik posta|elektronik ticaret|erişilebilir|kimlik bildir|"
    r"adres kayıt|mernis|kuyum|ihracat bedel|ihracat genelge|ihracat rejim|ithalat rejim|sübvansiyon|faiz destek|"
    r"hazine destekli|kredi garanti|kobi|küçük ve orta büyüklükteki|esnaf ve sanatkâr|tarımsal kredi|zirai kredi",
    re.I)

# Açıkça başka sektör / kurum içi
OFF = re.compile(
    r"görevde yükselme|unvan değişikliği|disiplin amir|sicil amir|personel(inin|i)? (atama|yetiştir|seçme|sınav)|"
    r"kıyafet|teşkilat ve görev|çalışma usul ve esasları hakkında yönetmelik$|sınav yönetmeliği|ödül yönetmeliği|"
    r"öğrenci|lisansüstü|önlisans|yükseköğretim|üniversite|okul|milli eğitim|yurt(ları)? yönetmeliği|"
    r"kamulaştır|acele kamulaştırma|imar|orman|av |avcılık|balık|su ürünleri|veteriner|hayvan|tohum|gübre|ilaç|tıbbi|"
    r"hastane|eczacı|kozmetik|gıda|yem |tarım ürünleri|maden işleri|petrol|doğal gaz|elektrik piyasası|enerji|"
    r"denizcilik|gemi|liman|havacılık|hava araç|demiryolu|karayolu taşıma|telsiz|frekans|radyo|televizyon|"
    r"spor|futbol|kültür|müze|turizm tesis|arkeoloj|vakıf üniversite|askeri|silahlı kuvvet|jandarma|emniyet teşkilat|"
    r"sahil güvenlik|milli savunma|güvenlik soruşturması|nükleer|radyasyon|çevre|atık|emisyon ticaret|iklim|"
    r"sağlık bakanlığı|aile hekim|tabip|diş hek|hemşire|ebe|laboratuvar|biyosidal|kimyasal|patlayıcı|"
    r"yangın güvenlik|asansör|iş ekipman|makine emniyet|oyuncak|ölçü ve ayar|gümrük tarife|dampinge|anti-damping|"
    r"korunma önlem|menşe|tercihli ticaret|serbest ticaret anlaşma|ek gümrük|ithalatta haksız rekabet",
    re.I)


MOU = re.compile(r"mutabakat zaptı|mutabakat muhtırası|işbirliği anlaşma|anlaşmasının onaylanması|arasında .* anlaşma", re.I)
BANK_TREATY = re.compile(r"finansal hesap bilgi|vergi amaçlı bilgi|otomatik (bilgi )?değişim|FATCA|vergi konularında karşılıklı idari yardım|uluslararası vergi uyumunun", re.I)
TAX_TEBLIG = re.compile(r"(vergi usul kanunu|gelir vergisi|kurumlar vergisi|katma değer vergisi|harçlar kanunu|"
                        r"emlak vergisi|özel tüketim|özel iletişim|tahsilat) genel tebliğ", re.I)


def decide(code, title):
    tur = code.split(".")[0]
    if re.search(r"tasarısı|gerekçesi|teklif ettiği metin|tutanağı", title, re.I):
        return "alma", "yasama süreci belgesi; bağlayıcı değil"
    if BANK_TREATY.search(title):
        return "al", ("vergi bilgisi paylaşımı anlaşması (FATCA / CRS): bankaya hesap sahibinden mukimlik beyanı alma ve "
                      "raporlama yükümlülüğü getirir (K1.6)")
    if MOU.search(title):
        return "alma", "uluslararası mutabakat / anlaşma onayı; bankaya doğrudan yükümlülük getirmez"
    if TAX_TEBLIG.search(title) and not re.search(r"banka|mevduat|faiz|menkul|tevkifat|stopaj", title, re.I):
        return "incele", "vergi genel tebliği; bankayı ilgilendiren hüküm içerip içermediği içerik aramasıyla belirlenecek"
    if tur == "8":
        return "alma", "üniversite iç yönetmeliği; banka / şube işlemine değmez"
    if OFF.search(title) and not STRONG.search(title):
        return "alma", "başka sektör / kurum içi düzenleme (başlığa göre)"
    if STRONG.search(title):
        return "al", "başlık şube / banka işlemine doğrudan değiyor"
    return "incele", "başlık kapsamı belirsiz; tek tek bakılacak"


AMENDING = re.compile(r"değişiklik yapılmasına (dair|ilişkin)|değişiklik yapılması hakkında|değiştirilmesine (dair|ilişkin)|"
                      r"değişiklik yapan|yürürlükten kaldırılmasına (dair|ilişkin)", re.I)
STANDALONE = re.compile(r"yeniden yapılandır|af|affı|varlık barışı|matrah artırım|uzlaşma|tahsilat", re.I)


def amending(title):
    """Değiştirici metin: hükümleri değiştirdiği metnin güncel (birleşik) hâlinde ve önceki metinlerinde (M7.3)."""
    return bool(AMENDING.search(title)) and not STANDALONE.search(title)
