# Müfettiş AI — Alan, Kapsam ve Eksikler

Yazıldı: 2026-09-28. Amaç: ürünün hedef kullanıcılarının ne iş yaptığını, hangi belgeleri incelediğini ve hangi
kararları verdiğini yazmak; bugünkü korpus ve analiz motorunun buna göre nerede eksik kaldığını çıkarmak.
Kapsam: **bir banka şubesiyle ilgili olabilecek tüm belgeler** — yalnızca müşteri şikâyetleri değil.

---

## 1. Kullanıcılar ve işleri

| Rol | Kimin adına | Ne yapar | Verdiği / önerdiği kararlar |
|---|---|---|---|
| **Banka müfettişi** (Teftiş Kurulu) | Yönetim kurulu (iç denetim, 5411 md. 29–31, İç Sistemler Yönetmeliği) | Şube ve genel müdürlük birimlerinin periyodik ve risk odaklı teftişi; inceleme ve soruşturma (suistimal, zimmet, yetki aşımı) | Bulgu (tespit + mevzuat/iç düzenleme aykırılığı), risk derecesi, sorumlu personel, düzeltici aksiyon ve süresi, zarar tespiti / tazmin, disiplin veya soruşturma önerisi, suç duyurusu önerisi, MASAK'a şüpheli işlem bildirimi önerisi |
| **İç kontrol elemanı** | İç kontrol birimi | Şube işlemlerinin günlük/sürekli kontrolü: kasa ve kıymetli evrak sayımı, hesap açılış ve kimlik tespiti, kredi kullandırım belgeleri, havale/EFT yetkileri, gün sonu mutabakatı | Kontrol bulgusu, düzeltme talebi ve takibi |
| **Uyum görevlisi** (MASAK) | Uyum birimi (5549, Tedbirler Yönetmeliği, Uyum Programı Yönetmeliği) | Müşteriyi tanıma, işlem izleme, şüpheli işlem analizi | Şüpheli işlem bildirimi, müşteri risk sınıfı, işlemin reddi / ilişkinin sonlandırılması |
| **BDDK denetçisi** (bankacılık uzmanı / başuzman) | Kurum (BDDK Denetim Yönetmeliği) | Yerinde denetim + uzaktan gözetim: mali bünye, risk yönetimi, iç sistemler, muhasebe, 5411 ve 5464 uyumu, bilgi sistemleri, kurumsal yönetim, destek hizmetleri | Denetim raporu / mütalaa / bilgi notu, derecelendirme notu; Kurum sonrasında idari para cezası (5411 md. 148 vd.), tedbir (md. 68–71), faaliyet kısıtı, suç duyurusu |
| **Mali müşavir / YMM** (3568) | Mükellef işletme | Defter tutma, beyanname, mali tablo; YMM: tam tasdik, KDV iade raporu | Beyanın doğruluğu, vergi riski (vergi ziyaı, usulsüzlük cezaları), tasdik görüşü. Bankaya kredi başvurusunda mali tabloyu hazırlayan / tasdik eden taraftır |
| **Bağımsız denetçi** | Banka / şirket (BDDK bağımsız denetim düzenlemesi, KGK) | Finansal tablo denetimi | Denetim görüşü, yönetim mektubu |

Ortak nokta: hepsi **bir belge (ya da belge dosyası) + bir ölçüt** ile çalışır. Ölçüt kanun, yönetmelik, tebliğ,
genelge ya da bankanın iç düzenlemesidir (talimat, yetki tablosu, ücret tarifesi). Çıktı ise **"ölçüte uygun mu,
değilse ne kadar önemli, kim sorumlu, ne yapılmalı"** sorusunun cevabıdır.

## 2. Şubede incelenen belge türleri

| Grup | Belgeler | Denetçinin tipik sorusu |
|---|---|---|
| Kredi dosyası | Başvuru, mali tablolar ve mizan, bağımsız denetim / YMM raporu, KKB / Findeks raporu, istihbarat, ekspertiz raporu, teminat belgeleri (ipotek, rehin, kefalet), kredi teklifi ve komite / yetkili kararı, sözleşme, kullandırım dekontu, izleme notları | Yetki içinde mi verilmiş? Teminat kullandırımdan önce tesis edilmiş mi? Kefalette eş rızası var mı? Mali tablo ile KKB tutarlı mı? Kredi sınırları (5411 md. 54) aşılmış mı? Sınıflandırma ve karşılık doğru mu? |
| Hesap / müşteri | Müşteri bilgi formu, kimlik belgesi, imza sirküleri, vekaletname, ticaret sicil gazetesi, vergi levhası, sözleşmeler | Kimlik tespiti ve teyidi yapılmış mı? Yetkili imza doğru kişiye mi ait? Eksik belge var mı? |
| Nakit ve kıymetli | Kasa sayım tutanağı, ATM dolum tutanakları, kıymetli evrak / emanet kayıtları, gün sonu raporları | Sayım ile kayıt tutuyor mu? Çift anahtar / çift imza kuralı uygulanmış mı? |
| İşlem kayıtları | Dekontlar, havale / EFT talimatları, döviz alım-satım fişleri, çek-senet kayıtları | Talimat yetkili kişiden mi? Eşik üstü işlemde kimlik alınmış mı? Olağandışı işlem kalıbı var mı? |
| Tüketici | Şikâyet dilekçesi, cevap yazısı, ücret-komisyon tahsilatı, kart itirazları, sözleşme öncesi bilgi formu | Ücret mevzuata ve sözleşmeye uygun mu? Şikâyet süresinde ve usulüne göre cevaplanmış mı? |
| Kurum içi | İç yazışma, talimat, yetki tablosu, toplantı tutanakları, önceki teftiş / iç kontrol raporları, aksiyon takip listeleri | İç düzenlemeye uyulmuş mu? Önceki bulgu kapatılmış mı? |
| Resmî yazışma | BDDK / MASAK / TCMB / mahkeme / icra yazıları, haciz ve bilgi talepleri | Süresinde ve doğru cevaplanmış mı? Sır saklama (5411 md. 73) ihlali var mı? |
| Personel | Görevlendirme, yetki devri, izin / rotasyon, disiplin | Görevler ayrılığı ve yetki sınırı korunmuş mu? |

## 3. Korpus eksikleri

Bugünkü korpus 78 belge. Ağırlıklı olarak BDDK rehberleri, Kredi İşlemleri Yönetmeliği, 5411, 5464, 6361 ve
vergi kanunları (VUK, GVK, KVK, KDV, Damga, 6183) var. Rollere göre eksik olan **temel** mevzuat:

| Öncelik | Eksik | Neden gerekli |
|---|---|---|
| **1** | 5549 Suç Gelirlerinin Aklanmasının Önlenmesi Kanunu; Tedbirler Yönetmeliği; Uyum Programı Yönetmeliği; MASAK genel tebliğleri; 6415 | Kimlik tespiti, eşik tutarlar, şüpheli işlem — şube denetiminin çekirdeği; korpusta hiç yok |
| **1** | 6098 Türk Borçlar Kanunu (kefalet md. 581–603, özellikle eş rızası), 4721 Türk Medeni Kanunu (aile konutu md. 194, ipotek) | Kredi / teminat dosyasındaki en sık hukuki eksik |
| **1** | İç Sistemler ve İSEDES Yönetmeliği; Kredilerin Sınıflandırılması ve Karşılıklar Yönetmeliği; Bilgi Sistemleri ve Elektronik Bankacılık Hizmetleri Yönetmeliği; Kurumsal Yönetim Yönetmeliği; Destek Hizmeti Yönetmeliği | Müfettişin ve BDDK denetçisinin doğrudan ölçütleri |
| **1** | 6502 Tüketicinin Korunması Kanunu; Tüketici Kredisi Sözleşmeleri Yönetmeliği; Konut Finansmanı Sözleşmeleri Yönetmeliği; TCMB Finansal Tüketicilerden Alınacak Ücretler Yönetmeliği; TCMB Banka Kartları ve Kredi Kartları Yönetmeliği; finansal tüketici şikâyetleri düzenlemesi | Ücret, sözleşme ve şikâyet belgelerinin ölçütü |
| 2 | 5941 Çek Kanunu; 6102 Türk Ticaret Kanunu (kambiyo senetleri, ticari defterler, şirket temsili); 2004 İcra ve İflas Kanunu (haciz yazıları) | Çek-senet, tüzel kişi yetkisi, icra yazıları |
| 2 | 32 Sayılı Karar ve Sermaye Hareketleri Genelgesi (kambiyo); 6493 (ödeme hizmetleri) ve TCMB ödeme hizmetleri yönetmeliği | Döviz ve ödeme işlemleri |
| 2 | Sır niteliğindeki bilgilerin paylaşılması yönetmeliği (genelgesi var, yönetmeliği yok); 6698 KVKK ikincil düzenlemeleri | Bilgi paylaşımı, sır saklama |
| 2 | 6802 Gider Vergileri Kanunu (BSMV), KKDF kararı, 492 Harçlar Kanunu, VUK tahsilatların banka aracılığıyla yapılması hükmü ve tebliğleri | Mali müşavir tarafı ve banka işlemlerinin vergisel boyutu |
| 3 | Bankanın **iç düzenlemeleri** (talimat, yetki tablosu, ücret tarifesi) | Teftişin önemli kısmı iç düzenlemeye göre yapılır; bunlar bankaya özeldir, kullanıcının yükleyebilmesi gerekir |

Not: Korpustaki "mevzuat.org", "Mükellef Girişi — e-Beyan Panel", "Mevzuat Hesaplama Kütüphanesi" ve
"mufettis_ai_test_temiz" gibi başlıklar mevzuat değil; yükleme sırasında ayıklanmalı.

## 4. Analiz motoru eksikleri

Bugünkü motor şunu iyi yapıyor: bir metindeki olgu cümlelerini bulup her biri için **ilgili mevzuat hükmünü**
göstermek. Denetçinin asıl sorusu ise **uygunluk**. Aradaki fark eksiklerin çoğunu açıklıyor.

| # | Eksik | Bugün | Gereken |
|---|---|---|---|
| 1 | **İlgi → uygunluk** | "Bu cümle şu maddeyle ilgili" | "Madde X şunu şart koşar; belgede şart karşılanmış / karşılanmamış / belirsiz." Her şartın kanıtı belgeden gösterilmeli |
| 2 | **Belge türleri** | 3 tür: şikâyet, iç yazışma, bilinmeyen | Bölüm 2'deki türler (kredi dosyası, hesap açılış, kasa tutanağı, sözleşme, dekont, resmî yazı, teftiş raporu…). Her tür için hangi mevzuatın hangi şartlarının aranacağı |
| 3 | **Yapılandırılmış belge okuma** | Düz metin ve düzyazı cümleleri; formlar, tablolar ve onay kutuları olgu olarak algılanmıyor | Form alanlarını (tarih, tutar, imzalayan, onay var/yok) okuyan alan çıkarımı. Varlık çıkarımı bugün kurum, tutar, IBAN ve birimle sınırlı; TCKN/VKN, hesap no, kişi-rol, teminat, limit, faiz/ücret oranı yok |
| 4 | **Dosya düzeyi (çoklu belge)** | Tek metin analizi | Aynı dosyadaki belgeleri çapraz kontrol: teminat tarihi kullandırımdan önce mi, imzalayan imza sirkülerinde var mı, mali tablo ile KKB tutarlı mı |
| 5 | **Sayısal kurallar** | Yok (risk_engine yalnızca tablo anomalisi) | Eşik ve oran kontrolleri: kredi sınırları (özkaynağa oran), MASAK eşik tutarı, ücret üst sınırları, süreler (cevap süresi, bildirim süresi) |
| 6 | **Olay tarihindeki mevzuat** | Geçerlilik her zaman bugüne göre | Olay tarihinde yürürlükte olan metin; eski işlem yeni mevzuatla değerlendirilmemeli |
| 7 | **Bulgu formatı** | Bulgu + dayanak + öneri; risk düzeyi | Teftiş bulgusu yapısı: tespit, ölçüt, neden, etki / risk, öneri, sorumlu, aksiyon tarihi. Önem derecesinin gerekçeli ve tutarlı olması |
| 8 | **Yaptırım eşlemesi** | Yok | İhlal edilen hükümden yaptırım hükmüne bağlantı (5411 idari para cezaları, 5549 cezaları, KVKK, VUK cezaları) |
| 9 | **İç düzenlemeler** | Belgedeki iç düzenleme atıfları "internal" olarak işaretleniyor ama içerik yok | Bankanın kendi talimatlarını ayrı, önceliği tanımlı bir kaynak olarak yükleme |
| 10 | **Şüpheli işlem tipolojileri** | Yok | MASAK tipolojilerine dayalı kural tabanlı uyarılar (kısa sürede çok sayıda eşik altı işlem vb.) |
| 11 | **Ölçüm** | Benchmark şikâyet ve iç not ağırlıklı | Her belge türünden vaka; "ilgili hüküm" değil "doğru uygunluk kararı" ölçülmeli |

## 5. v1 kapsamı ve sırası (kullanıcı kararı, 2026-09-28)

v1 = Bölüm 3'teki korpus ve ardından tam mevzuat + Bölüm 4'teki 11 eksiğin tamamı + gündelik dil genişletmesi,
ikinci eğitim turu, kod ve veri temizliği, arayüz, marka ve .exe paketi. Hedef: kusursuza en yakın, masaüstünde
kurulup çalışan bir ürün. Kapsam burada sabittir; v1 sırasında çıkan yeni fikirler "v2" listesine yazılır. Her
kilometre taşı ölçütü sağlanınca kapanır. Numara = yapılış sırası.

Sıralama ilkeleri:
- **Önce temizlik ve karar gerektiren altyapı.** Ölü kod yeni özelliklerden önce gider (üzerine inşa edilmez);
  paketleme / arayüz teknolojisi kararı erkenden verilir, çünkü sonraki her ekran o teknolojiyle yazılacak.
- **Önce ölçüm, sonra özellik.** Belge türü bazlı benchmark altyapısı özelliklerden önce kurulur; sentetik örnekler
  geldikçe dolar.
- **Doğruluk işleri gerçek ölçekte ayarlanır.** 78 belgelik korpusta ayarlanan eşikler binlerce belgede bozulabilir;
  bu yüzden tam mevzuat, uygunluk kataloğu / genişletme / eğitimden önce yüklenir.
- **Kriter kaynakları kataloğdan önce.** Olay tarihi (hangi sürüm) ve iç düzenlemeler (banka talimatı) şart
  kataloğunun ölçüt kaynaklarıdır; katalog bunlarla birlikte tasarlanır.
- **Görsel tasarım en son**, özellikler sabitlendiğinde; o zamana kadar yeni özellikler sade işlevsel ekranlarla gelir.

| # | Kilometre taşı | Bitti sayılma ölçütü | Bağımlılık |
|---|---|---|---|
| M0 | Sıralayıcı ince ayarının kapanışı ✓ | Pilot 2/3'ten biri mühürlü ho6'da önceden yazılan kurala göre kabul ya da ret; ret ise mevcut model kalır — **bitti 2026-09-28: pilot 3 kabul, `mufettis-reranker-ft1` varsayılan model** | — |
| M0.1 | Pilot 2 (benchmark dayanakları eğitimden tamamen dışlanmış) | Tüm bölümlerde ve ho5'te eşiksiz ölçüm; eşik yalnızca dev'den (✓ 2026-09-28: −3,1'de 33 doğru / 3 yanlış dayanak / 0 YP; taban 34/6/0) | — |
| M0.2 | Pilot 3 (incelemede zayıf/yanlış işaretli 115 iddia atılmış) | Aynı ölçüm (✓ −4,48'de 32 doğru / 1 yanlış dayanak / 0 YP) | — |
| M0.3 | Seçim | Kural: YP 0 → daha az yanlış dayanak → daha çok doğru; eşitlikte pilot 2 (✓ pilot 3: 1 yanlış dayanak, pilot 2: 3) | M0.1, M0.2 |
| M0.4 | Mühürlü ho6 doğrulaması (bir kez) | Kabul: YP 0, yanlış dayanak ≤ taban, doğru ≥ taban − 1 (✓ pilot 3: 4 doğru / 2 yanlış dayanak / 0 YP; taban 3 / 4 / 0) | M0.3 |
| M0.5 | Ürüne alma ya da rafa kaldırma | Kabulse: model sürüm adıyla kurulur, eşik ve özet reranker.py'de, baseline.json yeniden yazılır; retse mevcut model kalır ve sonuç kaydedilir (✓ `reranker.MODELS` kaydı, çok modelli kurulum betiği, taban 32 / 1 / 0; testler geçiyor) | M0.4 |
| M0.6 | Ürüne giren modelin tekrar üretilebilirliği (M0.5'te çıkan alt adım) | Eğitim verisi, grup kurma, eğitim ve eşik seçme betikleri repoda (`scripts/reranker_finetune/`, `benchmarks/reranker_finetune/`); tarif `claim_matching.md`'de; eşik aracı iki pilotun eşiğini aynen üretiyor (✓) | M0.5 |
| M1 | **Ölü kod temizliği** | Kullanılmayan fonksiyon, içe aktarım, ayar ve dizinler kaldırılmış; statik denetim (vulture/ruff) temiz; testler geçiyor — **bitti 2026-09-30** | — |
| M1.1 | Denetim araçları | ruff ve vulture geliştirme bağımlılığı olarak sabit sürümle (`requirements-dev.txt`); kullanıcı indirme onayı (✓ 70d6650c) | kullanıcı onayı |
| M1.2 | Kullanılmayan içe aktarım ve değişkenler | ruff F401/F811/F841 temiz; yıldızlı / yeniden dışa aktarım (`__all__`) korunuyor (✓ uygulama 169 + testler 206 içe aktarım, 16 değişken; alt paket `__init__` dosyalarındaki kimsenin kullanmadığı 141 yeniden dışa aktarım kaldırıldı, kök `mufettis/__init__.py` (`__all__`) korundu; 84 modül temiz süreçte içe aktarılıyor — 70d6650c, 5a52596a, e041445c, 38c81811) | M1.1 |
| M1.3 | Kullanılmayan fonksiyon, sınıf, sabit | vulture adaylarının her biri depo genelinde aranarak teyit edilmiş; Streamlit geri çağrıları, betikler, testler ve dinamik kullanım ayrı listede (izin listesi) (✓ silinen: `pdf._select_best_pdf_raw_candidate` (yerini `_select_pdf_engine_b3` aldı), `pdf._physical_line_tokens` (hiç çağrılmamış), arayüzde `run_analysis`'in zaten yaptığı tutar normalleştirmesinin boşa tekrarı, hiçbir yerde ayarlanmayan ve etkisiz `max_hit_rate_drop_tolerance`, web_harvester'da ölü satır; izin listesi `vulture_allowlist.py` gerekçeli) | M1.1 |
| M1.4 | Kullanılmayan ayarlar ve dizinler | config'te hiçbir yerde okunmayan ayar kalmamış; kullanılmayan dizin oluşturulmuyor (✓ f74fadd2: `LOG_DIR`, `SCRAPE_STATE_FILE`, `BOOTSTRAP_SECONDARY_DEFAULT`) | — |
| M1.5 | Ölü dosyalar ve betikler | Depodaki kullanılmayan betik, arşiv ve artık dosyaların envanteri; silinenler git geçmişinde, gerekçe commit mesajında (✓ ölü modül yok; tek seferlik onarım betikleri kullanıcı kararıyla kalıyor) | — |
| M1.6 | Kalıcı koruma | ruff ayarı ve test: ruff + vulture (izin listesiyle) temiz değilse test başarısız; yeni ölü kod birikmez (✓ `ruff.toml`, `tests/test_dead_code.py`; bilerek eklenen ölü fonksiyon ve içe aktarımı yakaladığı doğrulandı) | M1.2, M1.3 |
| M1.7 | Doğrulama | Tüm testler; benchmark tabanı değişmemiş (`--check-baseline`); uygulama veri kopyasında açılıp bir analiz yapıyor (✓ 2026-09-30: 1179 geçti / 41 atlandı; taban 32 / 1 / 0 korunuyor; uygulama `datacopy2`'de açılıp örnek şikâyeti 5,5 sn'de analiz etti) | M1.1–M1.6 |
| M1.8 | Üretim izolasyon testleri etkin depoyu denetliyor (M1.7'de çıkan alt adım) | SQLite'a geçişten sonra 4 test dosyası hâlâ `chroma.sqlite3`'ü izliyordu (ikisi başarısız, ikisi sessizce hiçbir şeyi denetlemiyordu); `store.active_store_file` ile etkin depo izleniyor (✓ f74fadd2) | — |
| M1.9 | Statik denetimin bulduğu gizli hatalar (M1.2'de çıkan alt adım) | ruff F821 (tanımsız ad) uygulama kodunda temiz: `app.py` otomasyonunun üretim yolunda `SOURCE_CACHE_DIR` tanımsızdı (çalışınca çökerdi); tür ipuçlarında `LocalRAG` (✓ 5a52596a; testlerdeki eski tanımsız adlar e041445c) | M1.2 |

**M1 güvenlik kuralı (kullanıcı, 2026-09-30: "ihtiyacımız olan bir şeyi asla silme").** Bir şey ancak (1) tüm depoda — kod, test, betik, doküman, ayar — hem adıyla hem metin olarak (getattr, Streamlit anahtarı, ortam değişkeni, dosya adı) aranıp kullanımı bulunmadıysa, (2) şüphe yoksa silinir; şüpheli olan "kullanıcı kararı" listesine gider. Silmeler küçük, gerekçeli ve ayrı commit'lerle yapılır; her gruptan sonra testler + benchmark tabanı + uygulama açılışı doğrulanır. `data/` altına dokunulmaz.

| M2 | **Eski yedeklerin temizliği** | Kullanıcı onayıyla data/backups eski kopyaları ve bayat data/mevzuat_db silinmiş; saklama politikası (kaç yedek, nerede) kodda. Tam mevzuat için disk açar | kullanıcı onayı |
| M3 | **Paketleme ve arayüz teknolojisi kararı** | Bugünkü uygulama (Streamlit) ile ve bir masaüstü alternatifiyle deneme paketi; temiz Windows'ta boyut, açılış süresi, RAM, internetsiz çalışma ölçülmüş; karar yazılı | — |
| M4 | **Ölçüm altyapısı** (eksik 11) | Belge türü bazlı vaka biçimi; "doğru uygunluk kararı", alan çıkarımı ve belge türü metrikleri; sentetik örnekleri (benchmarks/synthetic) içeri alan dönüştürücü; mühürlü model çıktıları ayrı; tabana yazılıp testle korunuyor | — |
| M5 | **Belge türleri + alan çıkarımı** (eksik 2, 3) | Bölüm 2'deki türler sınıflandırılıyor; tarih, tutar, TCKN/VKN, IBAN, hesap no, kişi-rol, imza/onay, teminat, limit, oran çıkarılıyor; M4 ile ölçülüyor | M4 |
| M6 | **Korpus: öncelikli set → tam mevzuat** | Yükleme hattı büyük ölçek için sağlamlaştırılmış; önce Bölüm 3'teki öncelikli set, ardından şube işlemlerine değen tüm mevzuat (bankacılık, tüketici, MASAK, ödeme, vergi, ticaret, borçlar) resmî kaynaklardan; mevzuat olmayan kayıtlar ayıklanmış; Resmî Gazete'den güncelleme yolu; 8 GB RAM'li dizüstünde performans ölçülmüş; mevcut benchmark kötüleşmiyor (kötüleşirse eşikler yeniden ayarlanır) | M2, kullanıcı onayı |
| M6.1 | Sıralayıcının tam korpusta yeniden ölçümü | M0'da seçilen model ve eşik tam korpusta tüm benchmark'larla yeniden ölçülür; eşik dev'den yeniden seçilir; kötüleşme varsa neden analizi M14'ün girdisi olur | M6 |
| M7 | **Olay tarihi** (eksik 6) | Olay tarihi belgeden çıkarılıyor; dayanak o tarihte yürürlükteki metinden; yürürlük değişikliği olan vakalarla test | M5, M6 |
| M8 | **İç düzenlemeler** (eksik 9) | Kullanıcı bankanın talimat / yetki tablosu / ücret tarifesini yükleyebiliyor; ayrı kaynak türü, mevzuattan ayırt ediliyor | M6 |
| M9 | **Şart kataloğu = uygunluk kararı** (eksik 1, 5) | Veri olarak tutulan katalog (şart → mevzuat veya iç düzenleme hükmü → kontrol türü: alan var mı / tarih sırası / eşik / süre / metin); her şart için karşılandı / karşılanmadı / belirsiz + belgeden kanıt; kredi, teminat, hesap açılış/KYC, kasa, işlem, şikâyet, ücret, resmî yazı türleri; kullanıcı hukuki gözden geçirmesi | M5–M8 |
| M10 | **Dosya düzeyi çapraz kontrol** (eksik 4) | Birden çok belge tek dosya; belgeler arası şartlar (teminat ↔ kullandırım tarihi, imzalayan ↔ sirküler, mali tablo ↔ KKB) | M9 |
| M11 | **Bulgu formatı + yaptırım** (eksik 7, 8) | Bulgu: tespit, ölçüt, neden, etki/önem, öneri, sorumlu, aksiyon tarihi; ihlal edilen hükümden yaptırım hükmüne bağlantı; UI ve DOCX'te | M9 |
| M12 | **Tipoloji uyarıları** (eksik 10) | MASAK tipolojilerine dayalı kural tabanlı uyarılar, işlem listeleri / dekont kümeleri üzerinde | M5, M6 |
| M13 | **Gündelik dil genişletmesi** | Tam korpusta madde başına gündelik / denetçi dili açıklamaları (üretim yapım aşamasında, çalışma anında dil modeli yok); ürüne alınmış; yanlış pozitif artmıyor | M6 |
| M14 | **Sıralayıcı eğitimi — gerektiği kadar tur** | Tam korpus + çok modelli sentetik belgeler + M9'un gerçek aday listeleriyle eğitim. Tur açma koşulu: M6.1 veya sonraki taşlarda sıralayıcı kaynaklı kötüleşme ya da yeni eğitim verisi. Her tur M14.n alt adımı; bir tur ancak mühürlü doğrulamada önceki modeli geçerse kabul edilir. Durma kuralı: üst üste iki tur önceki modeli geçemezse eğitim durur, en iyi model kalır. Eğitim düzeneği repoda, tekrarlanabilir | M4, M6, M9, M13 |
| M15 | **Arayüz tasarımı** | M3 kararındaki teknolojiyle; denetçi iş akışı (dosya yükleme → kontrol listesi → bulgular → rapor); tutarlı görsel dil; düşük çözünürlük kontrolü | M3, M9–M12 |
| M16 | **Logo ve marka** | Logo, uygulama simgesi, renk ve yazı tipi; arayüze ve DOCX raporuna uygulanmış (tasarım çalışması M15 ile paralel yürüyebilir) | M15 |
| M17 | **.exe paketleme** | Tek kurulum dosyası; Python / model / korpus dahil; internetsiz ilk açılış; 8 GB RAM, GPU'suz i5 dizüstünde temiz Windows'ta kurulum ve uçtan uca deneme; güncelleme ve kaldırma yolu; Streamlit dosya izleyicisi kapalı (M1.7'de görüldü: izleyici transformers'ın tembel modüllerini yoklarken günlüğe `torchvision` hatası yazıyor) | M3, M15, M16 |
| M18 | Son kontrol ve v1 etiketi | Tüm testler, tüm benchmark'lar, mühürlü doğrulama, uçtan uca deneme, doküman ↔ kod uyumu | hepsi |

**Gerileme kapısı.** Her kilometre taşının bitti ölçütüne örtük olarak şu da dahildir: tüm benchmark tabanları (`eval_accuracy --check-baseline` ve belge türü benchmark'ları) kötüleşmiyor. Önceki bir taşta kabul edilen bir bileşen (ör. M0'daki model) sonraki bir taşta bozulursa bu, bozulduğu taşın alt adımı olarak ele alınır (ör. M9.3 "sıralayıcı gerilemesi"); gerekirse M14'te yeni eğitim turu açılır. Bir taş ancak tabanlar yeşilken kapanır.

**Plan disiplini.** Bir kilometre taşında farklı iş gerektiren bir sorun çıkarsa çözümler sınıflandırılır ve o taşın
alt adımı olarak bu tabloya eklenir (ör. M9.1, M9.2; her biri kendi bitti ölçütüyle) — önce yazılır, sonra yapılır.
Hiçbir kilometre taşına ait olmayan iş v1'de yapılmaz; aşağıdaki v2 listesine gider. İlerleme alt adım düzeyinde
raporlanır.

Paralel yürüyebilenler: kullanıcının sentetik örnek üretimi (M4'ten itibaren), logo çalışması (her an),
şart kataloğunun hukuki gözden geçirmesi (M9 taslakları çıktıkça).

İlkeler: çekirdek deterministik ve GPU'suz; dil modeli isteğe bağlı açıklama katmanı. Şartların hukuki doğruluğu
bir alan uzmanının gözden geçirmesini gerektirir (katalog veri olduğu için uzman kod bilmeden düzeltebilir).

### v2 listesi

v1'de yapılmayacak, sonraya kalan fikirler buraya yazılır.

- (boş)

## Kaynaklar

- [BDDK Tarafından Yapılacak Denetime İlişkin Usul ve Esaslar Hakkında Yönetmelik (Lexpera)](https://www.lexpera.com.tr/mevzuat/yonetmelikler/bankacilik-duzenleme-ve-denetleme-kurumu-tarafindan-yapilacak-denetime-iliskin-usul-ve-esaslar)
- [BDDK — Denetim ve Gözetim Faaliyetleri SSS](https://www.bddk.org.tr/Sss/Liste/109)
- [Bankaların İç Sistemleri ve İSEDES Hakkında Yönetmelik (mevzuat.gov.tr)](https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=19864&MevzuatTur=7&MevzuatTertip=5)
- [TBB — Bankacılığımızda İç Kontrol (G. Yurtsever)](https://www.tbb.org.tr/sites/default/files/kitaplar/2ickontrol_2.pdf)
- [Türkiye Kalkınma Bankası Teftiş Kurulu Yönetmeliği (Lexpera)](https://www.lexpera.com.tr/mevzuat/yonetmelikler/turkiye-kalkinma-bankasi-anonim-sirketi-teftis-kurulu-yonetmeligi)
- [İş Bankası — Müfettiş Yardımcısı pozisyonu](https://ik.isbank.com.tr/pozisyon-detayi/mufettis-yardimcisi-20)
- [Kurumsal Kredilerde Kredi Tahsis Süreci (muhasebetr)](https://www.muhasebetr.com/yazarlarimiz/yilmazvelioglu/006/)
- [3568 sayılı Kanun (mevzuat.gov.tr)](https://www.mevzuat.gov.tr/MevzuatMetin/1.5.3568.pdf)
- [MASAK Genel Tebliği (Sıra No: 4)](https://masak.hmb.gov.tr/masak-genel-tebligi-sira-no-4/)
