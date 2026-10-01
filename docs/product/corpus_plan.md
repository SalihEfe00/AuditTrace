# Korpus yan planı (K)

Kullanıcı isteği (2026-10-01): korpusu oturtmak için ana plandan ayrı, yalnızca korpusa ait bir plan. Ana plandaki
ileriki maddelere (M8 ve sonrası) bağımlılığı olmayan, korpusla ilgili açık her iş burada. Her K adımı ana plandaki
bir M satırına bağlıdır; K adımı bitince o M satırı `docs/product/domain_scope.md`'de işaretlenir.

## Çalışma kuralları

1. **Sırayla, tek tek.** Bir K adımı bitmeden sonrakine geçilmez. Adım içinde çıkan sorun yeni bir alt adım olur
   (K3.1, K3.2 …) ve önce bu dosyaya yazılır, sonra yapılır. Plan dışı iş yapılmaz.
2. **Her adımın sonu aynıdır:** kabul ölçütü sağlandı → testler geçti → bu dosyada durum ✓ ve sonuç sayıları →
   commit → kullanıcıya kısa rapor.
3. **Gerçek `data/`'ya yalnızca K10'da dokunulur** (yedekten sonra). O zamana kadar her şey `datacopy_m615`'te.
4. Resmî kaynak, dürüst kullanıcı aracısı, robots.txt, istekler arası 3 sn; giriş gerektiren hiçbir kaynak yok.
5. Kötüleşme olursa tek belge değil sınıf düzeyinde sebep bulunur ve kod düzeltilir.

## Seçenek A sonrası sıra (kullanıcı kararı 2026-10-02)

v1 artık "kredi dosyası: teminat ve kullandırım öncesi uygunluk" (`domain_scope.md` 5.0). Korpus işi buna göre kısalır.
Envanter 3 399 kayıtla ara kayıt olarak commit'lendi (07a3a6b6); hattı `scripts/inventory/`'de (899ad35e).
Betikler `scripts/inventory/records/` içinden çalıştırılır.

Sıra:

1. **K1 kısa kapanış:** K1.2 sonucunun `merge.py`'ye işlenmesi, K1.4 daraltılmış, K1.7 daraltılmış, K1.8 (yeni).
2. **K2:** yalnızca K1.8 listesi ve kredi ile ilgili yeni adaylar.
3. **K5:** yalnızca öncelik ≤ 2 ve K1.8.
4. **K6.**
5. **K7:** yalnızca K1.8'deki kanunlar.
6. **K9:** K1.8 listesi kullanıcıya.
7. **K10** (onayla).
8. **K11.**
9. Ana plana dönüş: M7.6 → M7.7 → M9.

v2'ye gidenler: K3 (öncelik 3 geniş katmanın elemesi; öncelik 3 v1'de yüklenmez), K4 (dönemsel seriler), K8.

## Bugüne kadar biten (özet)

| M | İş | Durum |
|---|---|---|
| M6.1–M6.9, M6.11–M6.14 | Yükleme hattı, öncelik 1 + 2 listesi, ayıklama, güncelleme yolu, 8 GB ölçümü, seri belgeler, madde erişimi | ✓ |
| M6.12 | Cevap anahtarı ekleri | ✓ kullanıcı onayladı (2026-10-01) |
| M6.15.1–.3 | Envanter: atıf, kurum katalogları, şube faaliyet haritası | ✓ ilk tur (yaklaşık 3 350 aday, gerekçeli) |
| M6.15.5a–d | OCR, Word, kurum sayfaları, katmanlı yükleme, kural önce seçim, listeden çıkanların ayıklanması | ✓ |
| M6.15.5d | Öncelik 2 katmanı `datacopy_m615`'te | ✓ 29 / 3 / 0 (taban 30 / 3 / 0) |
| M7.3c | Mülga kanunlar (6762, 2499) | ✓ kod; veri kopyasında yüklü |

## Adımlar

| Adım | M | İş | Kabul ölçütü | Bağımlı | Durum |
|---|---|---|---|---|---|
| **K1** | M6.15.1, .2, .6 | **Envanterin kapanışı (önce her şey toplanır; 2026-10-02'den beri kısa kapanış, yukarıdaki sıra).** (a) Atıf kapanışının ikinci turu: M6.15'te indirilen belgelerin atıfları da taranır, yeni aday kalmayana kadar. (b) `resolve_cites` KHK / CB kararnamesi yedeğiyle yeniden çalıştırılır. (c) Eksik arama terimi "ödünç". (d) Resmî Gazete yargı bölümü taraması (2000–bugün) bitirilir; bankacılık / icra / kefalet / faiz / çek / miras / vergi konulu içtihadı birleştirme kararları seçilir (M6.15.6). (e) `domain_scope.md` Bölüm 3'teki her eksiğin listede olduğu tek tek kontrol edilir. (f) Şube faaliyet haritasının denetimi (kullanıcı sorusu 2026-10-01: "bankayla alakalı olması için banka kelimesi geçmek zorunda mı?" — hayır): metninde "banka" geçmeyen ve hiçbir metnin atıf yapmadığı mevzuatı yalnızca harita yakalar; her şube işi (müşteri kabulü, vekalet, vesayet, miras, haciz, ipotek, kefalet, tebligat, yabancı müşteri, personel, iş güvenliği, fiziksel güvenlik, arşiv …) için onu düzenleyen kanun / yönetmelik / tebliğ banka kelimesine bakılmadan yeniden sorulur, eksikler listeye gerekçesiyle eklenir | Son atıf turunda yeni aday sıfır; Bölüm 3 ve faaliyet denetimi tabloları bu dosyada; İBK listesi gerekçeli | — | sürüyor (d arka planda) |
| K1.1 | M6.15.1 | (a) Atıfların ikinci turu: öncelik ≤ 2'de indirilmiş belgelerin atıfları (`cite_round.py` → `cites2.json`); yeni atıflar çözülür, çözülen aday listeye; tur yeni aday çıkmayana kadar tekrarlanır. Öncelik 3'te "al" kalan belgelerin atıfları K3 sonrasında aynı yolla (K3.6). **Bulunan hata:** betik listeyi yalnızca `code` ile eşliyordu, kurum sitesi kayıtları (kodu boş, adresi `url`) eşleşmiyordu; anahtar `code` yoksa `url` (`LegislationSource.key` ile aynı) | Son turda yeni aday sıfır | — | ikinci tur ✓ (`cites2.json`); sonraki turlar v2 (2026-10-02) |
| K1.2 | M6.15.1 | (b) İki turun numaralı atıflarının hepsi resmî koda çözülür (`resolve_cites2.py`). **Bulunanlar:** ilk tur eski çıkarıcıyla üretilmiş (tür adları "kararı", "bakanlar" …) → normalleştirilip birleştirilir (`cites_all.json`); BKK / CB kararı / kurul kararı atıfları hiç çözülmüyordu → BKK ve CB kararları mevzuat.gov.tr CB kararları türünde numarayla ("2009/15481" → 200915481), BDDK / KVKK kurul kararları kataloglarda kurumun kendi sayı biçimiyle; `merge.py` atıf notunu yalnızca başlık aramasında çıkan kodlara bağlıyordu → numarayla çözülen kodlar da | Her numaralı atıf ya çözüldü ya gerekçesiyle "çözülemedi" | K1.1 | ✓ 2026-10-02 (aşağıda kayıt) |
| K1.3 | M6.15.2 | (c) Arama terimi eksikleri: "ödünç"; **şapkalı yazımlar** ("şikâyet", "hâkim", "kâr" … — mevzuat.gov.tr araması harf duyarlı: "şikayet" araması "şikâyet"i bulmuyor); 1 000 sonuç sınırına takılan `teblig|ithalat` sorgusu yıl aralıklarıyla | Şapkalı / şapkasız her terim çifti aranmış; kesilmiş sorgu yok | — | ✓ aramalar bitti (`search_k13.py`), 2026-10-02 birleştirmesine işlendi |
| K1.4 | M6.15.6 | (d) Resmî Gazete yargı bölümü taraması (2000–bugün) bitirilir. Başlıklar konuyu vermiyor ("Yargıtay 13. Hukuk Dairesine Ait Karar"): 2005'e kadar 1 089 kayıt, ~50'si içtihadı birleştirme (bağlayıcı), çoğu kanun yararına bozma (yönlendirici). Tarama bitince PDF'ler RG'den indirilir (aynı sunucuya tek akış), metin banka / haciz / kefalet / çek / faiz / ipotek / mevduat / kredi / icra / miras / vergi terimleriyle taranır, sınırda kalanı Sonnet alt ajanı okur; içtihadı birleştirme kararlarının hepsi konusuna bakılarak tek tek. **2000 öncesi** (kullanıcı sorusu 2026-10-01: "neden hedef 2000?"): 2000 sınırı hukuki değil teknik bir varsayımdır (günlük HTML fihristin başlangıcı, doğrulanmadı); kaldırılmamış İBK yaşından bağımsız bağlayıcıdır. Tarama bitince 2000 öncesi fihrist yoklanır — varsa tarama geriye uzatılır; yoksa 2000 öncesi İBK resmî kaynaktan tamamlanır (Yargıtay / Danıştay'ın yayımladığı İBK listeleri, RG tam sayı PDF'leri, gerekirse OCR; e-Devlet girişi gerektiren kaynak kullanılmaz) ve elimizdeki kararların atıf yaptığı eski İBK'lar listeye eklenir. **Tarama sonucu (2026-10-01 18:00):** 1 290 kayıt (Yargıtay 945, Danıştay 345), 68'i İBK; günlük fihrist 2000-06-29'da başlıyor (1990 / 1995 / 1999 yoklamaları 404) → 2000-06 öncesi İBK için ayrı resmî kaynak kesin gerekli. Metin biçimi: 2000–~2009 HTML (metin gün sayfasında, çapa ile ayrılır); ~2009 sonrası 663 PDF **taranmış görüntü, metin katmanı yok** → OCR gerekir; önce indirilir ve sayfa sayısı ölçülür, OCR süresi / makine yükü kullanıcıya sunulup onayla başlatılır | Tarama 2000'e ulaştı; 2000 öncesi İBK kaynağı belirlendi ve liste tamamlandı; her kaydın al / alma kararı gerekçeli | — | sürüyor — **v1'de daraltıldı (2026-10-02):** yalnızca 68 İBK + kefalet / teminat / kredi terimi geçen kararların sayfaları OCR'lanır (`rg_ocr.py` 446 / 8 877 sayfada durdu; `rg_screen.py` seçer; `rg_fallback.py ocr` 18 indirilmiş gün). Tam OCR ve 2000 öncesi İBK v2 |
| K1.4a | M6.15.6 | **Daraltılmış OCR (Seçenek A).** Başlık konuyu vermediği ve konu ancak OCR'la görüldüğü için iki aşama: (1) 68 İBK'nın PDF'leri (50 PDF, 1 080 sayfa) tam; (2) kalan her kararın ilk 2 sayfası (`rg_heads/`), `rg_screen` kalıplarından kredi / teminat terimleri (kefalet, ipotek / rehin, kredi, banka, senet / kambiyo, çek) ilk sayfalarda geçen karar tam OCR'a. Bilerek geniş: kaçırmaktansa fazla okumak. `rg_ocr.py --ibk`, `--head 2`, `--selected`; 2 iş parçacığı, tek süreç | İBK'ların hepsi ve terim geçen her karar `rg_docs/`'ta; seçilmeyenlerin ilk sayfa metni `rg_heads/`'ta (gerekçe: ilk 2 sayfada kredi / teminat terimi yok) | K1.4 | bekliyor |
| K1.5 | — | (e) Bölüm 3 kontrolü: 32 maddeden 30'u listede. Eksik: VUK Genel Tebliği Sıra No 320 (tahsilat ve ödemelerin banka aracılığıyla yapılması) — mevzuat.gov.tr VUK tebliğlerini 327'den başlatıyor; KKDF'nin dayanak kararı (88/12944) metin olarak yok (K1.7); finansal tüketici şikâyet düzenlemesi şapkalı yazım yüzünden aranmamış olabilir (K1.3) | Bölüm 3'teki her madde listede ya da gerekçeli | — | ✓ kontrol yapıldı; eksikler K1.3 / K1.7'de |
| K1.6 | M6.15.3 | (f) Şube faaliyet haritası denetimi: metninde "banka" geçmeyen ve atıf almayan mevzuatı yalnızca harita yakalar; her şube işi için düzenleyen kanun / yönetmelik / tebliğ banka kelimesine bakılmadan yeniden sorulur | Faaliyet × mevzuat tablosu bu dosyada; eksikler gerekçesiyle listede | — | bekliyor |
| K1.7 | M6.15.2 | (g) **mevzuat.gov.tr'de bulunmayan yürürlükteki eski metinler** (ör. VUK 320, KKDF kararı 88/12944): kaynak Resmî Gazete arşivi (resmî, gerekirse OCR); aday listesi envanterdeki çözülemeyen atıflar + Bölüm 3 eksikleri. **Değişiklik metni kuralının sınırı** (K1.6'da KGF örneğiyle bulundu): "değiştirici metin alınmaz" kuralı temel metnin birleşik güncel hâli resmî kaynakta varsayar; mevzuat.gov.tr'de yalnızca değişiklikleri olan kararlarda (ör. Kredi Garanti Kurumlarına Sağlanan Hazine Desteğine İlişkin Karar — 11 değişiklik kararı var, temel karar yok) temel metin Resmî Gazete'den alınır, değişiklikler tarih sırasıyla birlikte tutulur | Çözülemeyen her atıf ya RG'den indirildi ya gerekçesiyle (mülga / değiştirici / kapsam dışı) kapandı | K1.1, K1.2 | ✓ v1 kapsamı 2026-10-02 (aşağıda kayıt); BSMV / KKDF temel metinleri v1.1 (kredi maliyeti, teminat değil) |
| K1.8 | M6.15 | **Kefalet / teminat mevzuat kontrol listesi (Seçenek A).** M9 kataloğunun dayanaklarının listede ve korpusta olduğu tek tek kontrol edilir: TBK (kefalet 581–603, genel hükümler), TMK (ipotek, rehin, aile konutu md. 194), 6750 Ticari İşlemlerde Taşınır Rehni Kanunu ve yönetmeliği, 6305 Afet Sigortaları Kanunu (DASK), TTK kambiyo senetleri, 5070 Elektronik İmza Kanunu, Kredi İşlemleri Yönetmeliği, Tüketici Kredisi Sözleşmeleri Yönetmeliği md. 28, değerleme / ekspertiz düzenlemeleri (SPK, BDDK), Tapu Kanunu ve ipotek tescili, ilgili İBK'lar (K1.4) | Her kalem "listede + korpusta" ya da gerekçesiyle eksik ve indirme adayı; tablo bu dosyada | — | ✓ 2026-10-02 (aşağıda tablo) |
| K1.8a | M6.15.4 | **Liste dondurma (Seçenek A).** `gen_sources.py` bugünkü kurallarla öncelik 2'ye 372 kayıt daha eklerdi (168 yeni, 204'ü 3 → 2; K1.2 atıfları yüzünden; gelir vergisi geçici maddeleri, halkoylaması kanunu …). v1'de öncelikler commit'li listedeki (07a3a6b6) gibi kalır; değişiklik yalnızca K1.8 çekirdeğiyle; yeni adaylar v2'ye; eski davranış `--all` | Kuru çalıştırmada öncelik 2'ye giren her kayıt ya önceden 2 ya K1.8 çekirdeğinde; test | K1.8 | ✓ 2026-10-02: kuru çalıştırmada 174 yeni aday v2'ye, öncelik 2'ye giren yalnızca çekirdek; test `test_option_a_core_is_loaded_in_v1` |
| K1.8b | M6.15.4 | **K1.8 çekirdeği** (`scripts/inventory/records/k18_core.json`): kontrol listesinde öncelik 3'te ya da listede olmayan, kural kataloğunun dayanağı olan metinler gerekçesiyle öncelik 2 — 6750, Rehinli Taşınır Sicili Yönetmeliği, ticari işlemlerde rehin hakkı yönetmeliği, Tapu Kanunu, Tapu Sicili Tüzüğü, tapu resmî senet yönetmeliği, SPK değerleme standartları; K1.7'den KGF Hazine desteği kararı (2016/9538, RG 22/11/2016, `20161122-3.pdf`; 2009/15197 eski usul ve esaslar kararı). Alınmayanlar gerekçesiyle: 6103, 3402, TBB Bireysel Sözleşmeler Tebliği | Kontrol listesinde her kalem öncelik ≤ 2 ya da gerekçeli | K1.8a | ✓ 2026-10-02: 7 kayıt 3 → 2, KGF kararı eklendi (liste 3 400) |
| **K2** | M6.15.5 | **İndirme ve hesap.** K1'in yeni adayları indirilir. İndirmede reddedilen (29 + 33), başarısız (6) ve öncelik 2 yüklemesinde karantinaya düşen (44) her belge için sebep; sınıf hatasıysa kod düzeltilip yeniden denenir, gerçekten alınamıyorsa gerekçesiyle "alma" | Listedeki her "al" kaydı indirildi ya da gerekçesi var; açıklanamayan kayıt sıfır | K1 | bekliyor |
| **K3** | M6.15.5e | **Geniş katman eleği (toplama bittikten sonra, hepsine bir kerede).** Öncelik 3'teki her indirilmiş belge için al / alma kararı ve gerekçesi | Kural ve örnek isabeti bu dosyada; öncelik 3'te gerekçesiz kayıt yok | K2 | v2 (2026-10-02) |
| K3.1 | M6.15.5e | Tarama ölçüleri (`screen.py`): banka cümlesi, bankanın işi yapan taraf olduğu cümle (actor), banka ürünü cümlesi (product). **Bulunan hata (2026-10-01):** banka kalıbı `bankalar?` yalnızca "bankal…" ile eşleşiyordu; "banka", "Bankası", "Bankacılık" kaçıyordu (örneklemde 8 "banka geçmiyor" belgenin 7'sinde banka geçiyordu). Kalıp düzeltilir, örnek cümlelerle test edilir, tarama baştan yapılır | Kalıp testleri geçer; tüm öncelik 3 belgelerinin sayıları `screen.json`'da | K2 | v2 (2026-10-02) |
| K3.2 | M6.15.5e | Kesin grup: metinde hiç banka ve banka ürünü geçmeyen belge → "alma — indirildi; metinde banka ya da banka ürünü geçmiyor". 40 belgelik rastgele örnek okunur; metin çıkarma hatası (OCR, boş metin) bu gruba düşmemeli | Örnekte şube işine değen ya da metni bozuk belge yok | K3.1 | v2 (2026-10-02) |
| K3.3 | M6.15.5e | Sınırda grup (banka ya da ürün geçen her belge): başlık + en çok 8 kanıt cümlesi paketlenir (`build_evidence.py`); Sonnet alt ajanları "al / alma + kısa gerekçe + konu" diye etiketler (kullanıcı kuralı: toplu okuma alt ajana, Sonnet önce) | Her sınırdaki belgenin etiketi ve gerekçesi var | K3.1 | v2 (2026-10-02) |
| K3.4 | M6.15.5e | Etiket denetimi: alt ajan etiketlerinden rastgele 40 belge tarafımdan yeniden değerlendirilir | Uyum ≥ %90; altındaysa yönerge düzeltilip yeniden etiketlenir | K3.3 | v2 (2026-10-02) |
| K3.5 | M6.15.5e | Kararlar `merge` / `gen_sources`'a işlenir, `legislation_sources.json` yeniden üretilir | Öncelik 3'te gerekçesiz kayıt yok | K3.2–K3.4 | v2 (2026-10-02) |
| **K4** | M6.15.7 | **Dönemsel yenilenen karar serileri.** Aynı adla tekrar çıkan ve bir öncekini kaldıran kararlar (ör. Gelir Vergisi Kanunu geçici 67 tevkifat oranları — 25+ karar). Seri tanınır; en sonuncusu güncel, öncekiler bir sonrakinin yayımına kadar geçerli tarihî sürüm (olay tarihli arama doğru oranı bulur); aynı yapıdaki diğer seriler de | Seri tanıma testi; tevkifat serisinde tek güncel kayıt; olay tarihine göre doğru karar seçilir | K3 | v2 (2026-10-02) |
| **K5** | M6.15.5d | **Öncelik 3'ün yüklenmesi ve ölçüm** (`datacopy_m615`). Doğruluk (taban 30 / 3 / 0), dayanak bulunabilirliği (`check_legislation_retrieval`), belge benchmark'ı (`eval_documents`), bellek ve süre (M6.9 değerleri güncellenir) | Ölçümler bu dosyada; kötüleşme varsa K5.n alt adımı açılır ve sınıf düzeyinde çözülür | K1–K4 | bekliyor |
| **K6** | M6.1, M6.15.5d | **Kalan sapmalar ve sıralayıcı yeniden ölçümü.** Tam korpusta: 5464 md. 32 aday havuzuna girmiyor; dev-ui-internal kaçıyor; dev-ui-complaint ticari ücret tebliğini seçiyor; ho-dormant-deposit TMSF md. 8'i seçiyor. Ardından eşik ve havuz derinliği tam korpusta yeniden seçilir | Her sapmanın sebebi yazılı; düzeltilebilenler düzeltildi, sıralayıcı kaynaklı olanlar gerekçeyle M14'e girdi | K5 | bekliyor |
| **K7** | M7.3 | **Önceki metinler eksikleri.** Yeni katmanlarla gelen kanunların önceki metinleri (şu an yalnızca öncelik ≤ 2'deki eski 19 kanun); TTK'nin 4 eksik önceki metninin yeniden indirilmesi | Listedeki her kanunun önceki metin listesi indirildi ya da gerekçesi var | K5 | bekliyor |
| **K8** | M6.8 | **Güncelleme yolu yeni kaynakları kapsar.** `update_legislation.py` kurum sayfası kayıtlarını (KVKK, TBB, TKBB, TMSF, SEDDK, BDDK bağlantıları) ve K4 serilerini de denetler; ağsız testler | Yeni kaynak türlerinin her biri için test | K4 | v2 (2026-10-02) |
| **K9** | M6.15.4 | **Kullanıcıya sunum.** Tam liste (al / alma, kurum, tür, öncelik, gerekçe, korpus durumu) gezilebilir bir sayfa olarak; kullanıcının itirazları işlenir; `legislation_sources.json` commit | Kullanıcı listeyi gördü; itirazlar işlendi; liste commit'li | K1–K8 | bekliyor |
| **K10** | M6.10 | **Üretime uygulama.** `data/` yedeği → `apply_legislation_corpus.py --production --downloads-from datacopy_m615/legislation_downloads` → tüm ölçümler gerçek `data/`'da → tabanlar yeniden yazılır. İndirme klasörünün akıbeti kullanıcıya sorulur | Üretim ölçümleri veri kopyasıyla aynı; yedek yeri yazılı | K9 | bekliyor |
| **K11** | M6, M6.15 | **Kapanış.** `domain_scope.md`'de M6 ve M6.15 satırları ✓; hafıza notları; ana plana dönüş (M7.6 yürürlük değişikliği olan vakalar → M7.7) | Plan ve hafıza güncel | K10 | bekliyor |

## Kapsam dışı (bu planda değil)

- M7.6 / M7.7 (olay tarihli vakalar ve doğrulama): korpusu kullanır ama korpus işi değildir; K11'den sonra ana planda.
- M8 iç düzenlemeler (bankanın kendi talimatları): kullanıcı yüklemesi, ayrı kaynak türü — ana plan.
- M14 sıralayıcı eğitimi: K6'da sıralayıcıya bağlanan sapmalar oraya girdi olur.

## Adım kayıtları

(Her adım bitince sonuç buraya yazılır.)

### K1.5 — Bölüm 3 kontrolü (2026-10-01)

32 maddeden 30'u listede "al". 6415 korpusta (resmî adı "Terörizmin …"). Eksikler: VUK Genel Tebliği Sıra No 320
(mevzuat.gov.tr VUK tebliğlerini 327'den başlatıyor → K1.7, Resmî Gazete arşivi); KKDF dayanak kararı 88/12944
(→ K1.7; karar serisi K4); finansal tüketici şikâyet düzenlemesi (→ K1.3, şapkalı yazım araması).

### K1.6 — Şube faaliyet haritası denetimi (2026-10-01)

Haritada 19 faaliyet / 165 gereksinim vardı; her faaliyet için "bu işi hangi metin düzenler" sorusu banka kelimesine
bakılmadan yeniden soruldu. Sonuç 26 faaliyet / 191 gereksinim (`scripts/inventory/branch_map.py`). Eklenen faaliyetler: teminat
mektubu ve gayrinakdi krediler (4734, KİK genel tebliği, ihale uygulama yönetmelikleri, 4735, gümrük teminatı,
e-teminat); bağış / yardım / devlet katkılı hesaplar (2860 ve yönetmeliği, konut hesabı, çeyiz hesabı, BES otomatik
katılım); FATCA / CRS; pazarlama ve müşteri iletişimi (İYS yönetmeliği, 6563, reklam yönetmeliği); teminat
değerlemesi ve DASK; avukat / noter / tebligat; şube binası (yangın yönetmeliği, 5188, işyeri ruhsatı).

Bulunan sınıf hataları:
- "Uluslararası anlaşma onayı → alma" kuralı bankaya doğrudan yükümlülük getiren vergi bilgisi paylaşımı
  anlaşmalarını da (CRS Paris anlaşması, FATCA) dışarıda bırakıyordu → istisna (`search_rules.BANK_TREATY`).
- İYS yönetmeliği ve FATCA anlaşması başlık aramasında yoktu → arama terimleri eklendi (K1.3 betiği).
- TCMB reeskont kredisi uygulama talimatları gece doğruluk düşüşü yüzünden listeden çıkarılmıştı; ihracatçıya
  reeskont kredisi kullandıran şube için gerekli. Belgeyi atmak yanlış çözümdü → K6'da geri alınır, sorun sıralamada
  çözülür.

### K1.2 / K1.3 — birleştirme (2026-10-02)

`merge.py` K1.2 atıf çözümü (1 974 numaralı atıf, `resolve_cites2.py`) ve K1.3 aramalarıyla çalıştırıldı:
14 398 tekil aday (al 3 638, incele 25, mülga 68, alma 10 667). Bir önceki envantere göre 149 yeni "al" kaydı var;
hepsi atıf kaynaklı ve neredeyse tamamı şube işi dışında (teknopark, seferberlik, radyoloji …). Çözülemeyen atıf
2 124 (numaralı + adlı).

**Seçenek A kararı.** `gen_sources.py` atıf alan her kaydı öncelik 2 yapıyor. Öncelik 2 v1'de yükleneceği için
`legislation_sources.json` bu turda yeniden üretilmez; envanter kayıt olarak commit'lenir. Yeni kayıtlardan
kredi / teminatla ilgisi olabilecek üçü (3402 Kadastro Kanunu, 6103 TTK yürürlük kanunu, TBB Bireysel Sözleşmeler
Tebliği) K1.8'de tek tek değerlendirilir. "Atıf → öncelik 2" kuralının yeniden düşünülmesi v2'ye kalır (geniş korpus).

### K1.7 — eski temel metinler, v1 kapsamı (2026-10-02)

`k17_basedates.py` hiç sonuç vermiyordu: değişiklik kodu kalıbı `r"d+.d+.d+"` ters bölüsüz yazılmıştı (heredoc ile
yazılırken düşmüş), hiçbir kod eşleşmedi. Düzeltildi (`\d+\.\d+\.\d+`), yeniden çalıştırıldı. KGF Hazine desteği
kararının temeli **2016/9538** (RG 22/11/2016, `20161122-3.pdf`, taranmış; RG fihristinden, önbellekten bulundu);
2009/15197 (RG 15/7/2009) eski usul ve esaslar kararı. BSMV temeli 98/11591 (28/8/1998), KKDF 88/12944 ve
2004/7668: kredinin maliyetine ilişkin, teminat geçerliliğine değil → v1.1.

### K1.8 — kefalet / teminat kontrol listesi (2026-10-02)

`scripts/inventory/k18_checklist.py` (veri kopyası `datacopy_m615`). "skipped" = veri kopyasında zaten yüklüydü
(önceki katmandan). Öncelik 3'te kalanlar: DASK tarifesi (poliçe kontrolü için öncelik 2'deki genel şartlar yeterli),
SPK'nın eski Seri VIII No 45 tebliği (yerini III-62.1 aldı, o öncelik 2). Alınmayanların gerekçesi `k18_core.json`'da.

| Kalem | Neden | Listede (öncelik) | İndirildi | Yüklendi / korpusta |
|---|---|---|---|---|
| 6098 Türk Borçlar Kanunu | kefalet md. 581–603; blok 1 | 1: TÜRK BORÇLAR KANUNU | downloaded | skipped |
| 4721 Türk Medeni Kanunu | ipotek, rehin, aile konutu md. 194; blok 2 | 1: TÜRK MEDENİ KANUNU | downloaded | skipped |
| 6102 Türk Ticaret Kanunu | kambiyo senetleri, şirket temsili; blok 2–3 | 2: TÜRK TİCARET KANUNU | downloaded | skipped |
| 6103 TTK yürürlük ve uygulama kanunu | TTK geçiş hükümleri (K1.2'den) | **yok** | — | — |
| 6750 Ticari İşlemlerde Taşınır Rehni Kanunu | taşınır rehni; blok 2 | 2: TİCARİ İŞLEMLERDE TAŞINIR REHNİ KANUNU | downloaded | — |
| Taşınır rehin sicili yönetmeliği | rehin sicili kaydı; blok 2 | 2: REHİNLİ TAŞINIR SİCİLİ YÖNETMELİĞİ | downloaded | — |
| Taşınır rehin sicili yönetmeliği | rehin sicili kaydı; blok 2 | 2: TİCARİ İŞLEMLERDE REHİN HAKKININ KURULMASI VE TEMERRÜT SONRASI HAKLARI | downloaded | — |
| 6305 Afet Sigortaları Kanunu | DASK; blok 2 | 2: 6305 sayılı Afet Sigortaları Kanunu (18.05.2012 - 28296) | downloaded | indexed |
| Zorunlu deprem sigortası tarife / genel şartlar | DASK poliçesi; blok 2 | 3: DOĞAL AFET SİGORTALARI KURUMU TARAFINDAN ÜSTLENİLEN ZORUNLU DEPREM SİG | downloaded | — |
| Zorunlu deprem sigortası tarife / genel şartlar | DASK poliçesi; blok 2 | 2: Kredi İle Bağlantılı Zorunlu Deprem Sigortası Poliçelerinin Kredi Sözl | downloaded | skipped |
| Zorunlu deprem sigortası tarife / genel şartlar | DASK poliçesi; blok 2 | 2: Zorunlu Deprem Sigortası Genel Şartları | downloaded | indexed |
| Zorunlu deprem sigortası tarife / genel şartlar | DASK poliçesi; blok 2 | 2: Zorunlu Deprem Sigortası Kapsamında Doğal Afet Sigortaları Kurumu Tara | downloaded | skipped |
| Zorunlu deprem sigortası tarife / genel şartlar | | +2 kayıt daha | | |
| 5070 Elektronik İmza Kanunu | md. 5/2 kefalette güvenli e-imza yok; blok 1 | 2: ELEKTRONİK İMZA KANUNU | downloaded | skipped |
| 2644 Tapu Kanunu | ipotek tescili; blok 2 | 2: TAPU KANUNU | downloaded | — |
| Tapu sicili tüzüğü / yönetmeliği | tescil usulü; blok 2 | 2: TAPU SİCİL MÜDÜRLÜKLERİNCE DÜZENLENEN RESMÎ SENETLERE İLİŞKİN USUL VE  | downloaded | — |
| Tapu sicili tüzüğü / yönetmeliği | tescil usulü; blok 2 | 2: TAPU SİCİLİ TÜZÜĞÜ | downloaded | — |
| 3402 Kadastro Kanunu | taşınmaz kaydı (K1.2'den); blok 2 | **yok** | — | — |
| 634 Kat Mülkiyeti Kanunu | kat irtifakı / mülkiyeti üzerinde ipotek; blok 2 | 2: KAT MÜLKİYETİ KANUNU | downloaded | indexed |
| 2004 İcra ve İflas Kanunu | rehnin paraya çevrilmesi; blok 2 | 2: İCRA VE İFLAS KANUNU | downloaded | skipped |
| 5411 Bankacılık Kanunu | kredi tanımı, teminat, kredi sınırları; blok 3 | korpusta: BANKACILIK KANUNU | — | korpusta (öncelik 1) |
| Kredi işlemleri yönetmeliği (BDDK) | kredi tahsisi, teminat, belge; blok 3 | 1: BANKALARIN KREDİ İŞLEMLERİNE İLİŞKİN YÖNETMELİK | downloaded | skipped |
| Kredilerin sınıflandırılması ve karşılıklar yönetmeliği | teminat türleri ve değerleme; blok 2 | 1: KREDİLERİN SINIFLANDIRILMASI VE BUNLAR İÇİN AYRILACAK KARŞILIKLARA İLİ | downloaded | skipped |
| Değerleme hizmeti veren kuruluşlar yönetmeliği (BDDK) | ekspertiz yetkisi; blok 2 | 2: Bankaların Değerleme Hizmeti Almaları ve Bankalara Değerleme Hizmeti V | downloaded | skipped |
| Değerleme hizmeti veren kuruluşlar yönetmeliği (BDDK) | ekspertiz yetkisi; blok 2 | 2: Bankaların Değerleme Hizmeti Almaları ve Bankalara Değerleme Hizmeti V | downloaded | indexed |
| SPK değerleme standartları | ekspertiz raporu; blok 2 | 2: SERMAYE PİYASASINDA DEĞERLEME STANDARTLARI HAKKINDA TEBLİĞ (III-62.1) | downloaded | — |
| SPK değerleme standartları | ekspertiz raporu; blok 2 | 3: SERMAYE PİYASASINDA ULUSLARARASI DEĞERLEME STANDARTLARI HAKKINDA TEBLİ | downloaded | — |
| 6502 Tüketicinin Korunması Hakkında Kanun | TKS Yön. dayanağı; kefalet K12 | 1: TÜKETİCİNİN KORUNMASI HAKKINDA KANUN | downloaded | skipped |
| Tüketici kredisi sözleşmeleri yönetmeliği | md. 28 şahsi teminat; kefalet K12 | 1: TÜKETİCİ KREDİSİ SÖZLEŞMELERİ YÖNETMELİĞİ | downloaded | skipped |
| 5570 Faiz destekli kredi kanunu | TBK 584/3 eş rızası istisnası; blok 1 | 2: KAMU SERMAYELİ BANKALAR TARAFINDAN YÜRÜTÜLEN FAİZ DESTEKLİ KREDİ VE KA | downloaded | indexed |
| KGF Hazine desteği kararı | KGF kefaleti teminat olarak; K1.7 | 2: Kredi Garanti Kurumlarına Sağlanan Hazine Desteğine İlişkin Karar (201 | — | — |
| TBB Bireysel Sözleşmeler Tebliği | kredi sözleşmesi şekli (K1.2'den) | 2: Bireysel Sözleşmeler Tebliği | Türkiye Bankalar Birliği | rejected | not_downloaded |
| 1512 Noterlik Kanunu | noter onaylı belgeler, imza sirküleri; blok 3 | 2: NOTERLİK KANUNU | downloaded | indexed |
