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
| **K1** | M6.15.1, .2, .6 | **Envanterin kapanışı (önce her şey toplanır).** (a) Atıf kapanışının ikinci turu: M6.15'te indirilen belgelerin atıfları da taranır, yeni aday kalmayana kadar. (b) `resolve_cites` KHK / CB kararnamesi yedeğiyle yeniden çalıştırılır. (c) Eksik arama terimi "ödünç". (d) Resmî Gazete yargı bölümü taraması (2000–bugün) bitirilir; bankacılık / icra / kefalet / faiz / çek / miras / vergi konulu içtihadı birleştirme kararları seçilir (M6.15.6). (e) `domain_scope.md` Bölüm 3'teki her eksiğin listede olduğu tek tek kontrol edilir. (f) Şube faaliyet haritasının denetimi (kullanıcı sorusu 2026-10-01: "bankayla alakalı olması için banka kelimesi geçmek zorunda mı?" — hayır): metninde "banka" geçmeyen ve hiçbir metnin atıf yapmadığı mevzuatı yalnızca harita yakalar; her şube işi (müşteri kabulü, vekalet, vesayet, miras, haciz, ipotek, kefalet, tebligat, yabancı müşteri, personel, iş güvenliği, fiziksel güvenlik, arşiv …) için onu düzenleyen kanun / yönetmelik / tebliğ banka kelimesine bakılmadan yeniden sorulur, eksikler listeye gerekçesiyle eklenir | Son atıf turunda yeni aday sıfır; Bölüm 3 ve faaliyet denetimi tabloları bu dosyada; İBK listesi gerekçeli | — | sürüyor (d arka planda) |
| K1.1 | M6.15.1 | (a) Atıfların ikinci turu: öncelik ≤ 2'de indirilmiş belgelerin atıfları (`cite_round.py` → `cites2.json`); yeni atıflar çözülür, çözülen aday listeye; tur yeni aday çıkmayana kadar tekrarlanır. Öncelik 3'te "al" kalan belgelerin atıfları K3 sonrasında aynı yolla (K3.6). **Bulunan hata:** betik listeyi yalnızca `code` ile eşliyordu, kurum sitesi kayıtları (kodu boş, adresi `url`) eşleşmiyordu; anahtar `code` yoksa `url` (`LegislationSource.key` ile aynı) | Son turda yeni aday sıfır | — | sürüyor |
| K1.2 | M6.15.1 | (b) İki turun numaralı atıflarının hepsi resmî koda çözülür (`resolve_cites2.py`). **Bulunanlar:** ilk tur eski çıkarıcıyla üretilmiş (tür adları "kararı", "bakanlar" …) → normalleştirilip birleştirilir (`cites_all.json`); BKK / CB kararı / kurul kararı atıfları hiç çözülmüyordu → BKK ve CB kararları mevzuat.gov.tr CB kararları türünde numarayla ("2009/15481" → 200915481), BDDK / KVKK kurul kararları kataloglarda kurumun kendi sayı biçimiyle; `merge.py` atıf notunu yalnızca başlık aramasında çıkan kodlara bağlıyordu → numarayla çözülen kodlar da | Her numaralı atıf ya çözüldü ya gerekçesiyle "çözülemedi" | K1.1 | bekliyor |
| K1.3 | M6.15.2 | (c) Arama terimi eksikleri: "ödünç"; **şapkalı yazımlar** ("şikâyet", "hâkim", "kâr" … — mevzuat.gov.tr araması harf duyarlı: "şikayet" araması "şikâyet"i bulmuyor); 1 000 sonuç sınırına takılan `teblig|ithalat` sorgusu yıl aralıklarıyla | Şapkalı / şapkasız her terim çifti aranmış; kesilmiş sorgu yok | — | bekliyor |
| K1.4 | M6.15.6 | (d) Resmî Gazete yargı bölümü taraması (2000–bugün) bitirilir. Başlıklar konuyu vermiyor ("Yargıtay 13. Hukuk Dairesine Ait Karar"): 2005'e kadar 1 089 kayıt, ~50'si içtihadı birleştirme (bağlayıcı), çoğu kanun yararına bozma (yönlendirici). Tarama bitince PDF'ler RG'den indirilir (aynı sunucuya tek akış), metin banka / haciz / kefalet / çek / faiz / ipotek / mevduat / kredi / icra / miras / vergi terimleriyle taranır, sınırda kalanı Sonnet alt ajanı okur; içtihadı birleştirme kararlarının hepsi konusuna bakılarak tek tek | Tarama 2000'e ulaştı; her kaydın al / alma kararı gerekçeli | — | arka planda (2007'de) |
| K1.5 | — | (e) Bölüm 3 kontrolü: 32 maddeden 30'u listede. Eksik: VUK Genel Tebliği Sıra No 320 (tahsilat ve ödemelerin banka aracılığıyla yapılması) — mevzuat.gov.tr VUK tebliğlerini 327'den başlatıyor; KKDF'nin dayanak kararı (88/12944) metin olarak yok (K1.7); finansal tüketici şikâyet düzenlemesi şapkalı yazım yüzünden aranmamış olabilir (K1.3) | Bölüm 3'teki her madde listede ya da gerekçeli | — | ✓ kontrol yapıldı; eksikler K1.3 / K1.7'de |
| K1.6 | M6.15.3 | (f) Şube faaliyet haritası denetimi: metninde "banka" geçmeyen ve atıf almayan mevzuatı yalnızca harita yakalar; her şube işi için düzenleyen kanun / yönetmelik / tebliğ banka kelimesine bakılmadan yeniden sorulur | Faaliyet × mevzuat tablosu bu dosyada; eksikler gerekçesiyle listede | — | bekliyor |
| K1.7 | M6.15.2 | (g) **mevzuat.gov.tr'de bulunmayan yürürlükteki eski metinler** (ör. VUK 320, KKDF kararı 88/12944): kaynak Resmî Gazete arşivi (resmî, gerekirse OCR); aday listesi envanterdeki çözülemeyen atıflar + Bölüm 3 eksikleri. **Değişiklik metni kuralının sınırı** (K1.6'da KGF örneğiyle bulundu): "değiştirici metin alınmaz" kuralı temel metnin birleşik güncel hâli resmî kaynakta varsayar; mevzuat.gov.tr'de yalnızca değişiklikleri olan kararlarda (ör. Kredi Garanti Kurumlarına Sağlanan Hazine Desteğine İlişkin Karar — 11 değişiklik kararı var, temel karar yok) temel metin Resmî Gazete'den alınır, değişiklikler tarih sırasıyla birlikte tutulur | Çözülemeyen her atıf ya RG'den indirildi ya gerekçesiyle (mülga / değiştirici / kapsam dışı) kapandı | K1.1, K1.2 | bekliyor |
| **K2** | M6.15.5 | **İndirme ve hesap.** K1'in yeni adayları indirilir. İndirmede reddedilen (29 + 33), başarısız (6) ve öncelik 2 yüklemesinde karantinaya düşen (44) her belge için sebep; sınıf hatasıysa kod düzeltilip yeniden denenir, gerçekten alınamıyorsa gerekçesiyle "alma" | Listedeki her "al" kaydı indirildi ya da gerekçesi var; açıklanamayan kayıt sıfır | K1 | bekliyor |
| **K3** | M6.15.5e | **Geniş katman eleği (toplama bittikten sonra, hepsine bir kerede).** Öncelik 3'teki her indirilmiş belge için al / alma kararı ve gerekçesi | Kural ve örnek isabeti bu dosyada; öncelik 3'te gerekçesiz kayıt yok | K2 | bekliyor |
| K3.1 | M6.15.5e | Tarama ölçüleri (`screen.py`): banka cümlesi, bankanın işi yapan taraf olduğu cümle (actor), banka ürünü cümlesi (product). **Bulunan hata (2026-10-01):** banka kalıbı `bankalar?` yalnızca "bankal…" ile eşleşiyordu; "banka", "Bankası", "Bankacılık" kaçıyordu (örneklemde 8 "banka geçmiyor" belgenin 7'sinde banka geçiyordu). Kalıp düzeltilir, örnek cümlelerle test edilir, tarama baştan yapılır | Kalıp testleri geçer; tüm öncelik 3 belgelerinin sayıları `screen.json`'da | K2 | bekliyor |
| K3.2 | M6.15.5e | Kesin grup: metinde hiç banka ve banka ürünü geçmeyen belge → "alma — indirildi; metinde banka ya da banka ürünü geçmiyor". 40 belgelik rastgele örnek okunur; metin çıkarma hatası (OCR, boş metin) bu gruba düşmemeli | Örnekte şube işine değen ya da metni bozuk belge yok | K3.1 | bekliyor |
| K3.3 | M6.15.5e | Sınırda grup (banka ya da ürün geçen her belge): başlık + en çok 8 kanıt cümlesi paketlenir (`build_evidence.py`); Sonnet alt ajanları "al / alma + kısa gerekçe + konu" diye etiketler (kullanıcı kuralı: toplu okuma alt ajana, Sonnet önce) | Her sınırdaki belgenin etiketi ve gerekçesi var | K3.1 | bekliyor |
| K3.4 | M6.15.5e | Etiket denetimi: alt ajan etiketlerinden rastgele 40 belge tarafımdan yeniden değerlendirilir | Uyum ≥ %90; altındaysa yönerge düzeltilip yeniden etiketlenir | K3.3 | bekliyor |
| K3.5 | M6.15.5e | Kararlar `merge` / `gen_sources`'a işlenir, `legislation_sources.json` yeniden üretilir | Öncelik 3'te gerekçesiz kayıt yok | K3.2–K3.4 | bekliyor |
| **K4** | M6.15.7 | **Dönemsel yenilenen karar serileri.** Aynı adla tekrar çıkan ve bir öncekini kaldıran kararlar (ör. Gelir Vergisi Kanunu geçici 67 tevkifat oranları — 25+ karar). Seri tanınır; en sonuncusu güncel, öncekiler bir sonrakinin yayımına kadar geçerli tarihî sürüm (olay tarihli arama doğru oranı bulur); aynı yapıdaki diğer seriler de | Seri tanıma testi; tevkifat serisinde tek güncel kayıt; olay tarihine göre doğru karar seçilir | K3 | bekliyor |
| **K5** | M6.15.5d | **Öncelik 3'ün yüklenmesi ve ölçüm** (`datacopy_m615`). Doğruluk (taban 30 / 3 / 0), dayanak bulunabilirliği (`check_legislation_retrieval`), belge benchmark'ı (`eval_documents`), bellek ve süre (M6.9 değerleri güncellenir) | Ölçümler bu dosyada; kötüleşme varsa K5.n alt adımı açılır ve sınıf düzeyinde çözülür | K1–K4 | bekliyor |
| **K6** | M6.1, M6.15.5d | **Kalan sapmalar ve sıralayıcı yeniden ölçümü.** Tam korpusta: 5464 md. 32 aday havuzuna girmiyor; dev-ui-internal kaçıyor; dev-ui-complaint ticari ücret tebliğini seçiyor; ho-dormant-deposit TMSF md. 8'i seçiyor. Ardından eşik ve havuz derinliği tam korpusta yeniden seçilir | Her sapmanın sebebi yazılı; düzeltilebilenler düzeltildi, sıralayıcı kaynaklı olanlar gerekçeyle M14'e girdi | K5 | bekliyor |
| **K7** | M7.3 | **Önceki metinler eksikleri.** Yeni katmanlarla gelen kanunların önceki metinleri (şu an yalnızca öncelik ≤ 2'deki eski 19 kanun); TTK'nin 4 eksik önceki metninin yeniden indirilmesi | Listedeki her kanunun önceki metin listesi indirildi ya da gerekçesi var | K5 | bekliyor |
| **K8** | M6.8 | **Güncelleme yolu yeni kaynakları kapsar.** `update_legislation.py` kurum sayfası kayıtlarını (KVKK, TBB, TKBB, TMSF, SEDDK, BDDK bağlantıları) ve K4 serilerini de denetler; ağsız testler | Yeni kaynak türlerinin her biri için test | K4 | bekliyor |
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
bakılmadan yeniden soruldu. Sonuç 26 faaliyet / 191 gereksinim (`inv/branch_map.py`). Eklenen faaliyetler: teminat
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
