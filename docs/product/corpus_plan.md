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
| **K1** | M6.15.1, .2, .6 | **Envanterin kapanışı (önce her şey toplanır).** (a) Atıf kapanışının ikinci turu: M6.15'te indirilen belgelerin atıfları da taranır, yeni aday kalmayana kadar. (b) `resolve_cites` KHK / CB kararnamesi yedeğiyle yeniden çalıştırılır. (c) Eksik arama terimi "ödünç". (d) Resmî Gazete yargı bölümü taraması (2000–bugün) bitirilir; bankacılık / icra / kefalet / faiz / çek / miras / vergi konulu içtihadı birleştirme kararları seçilir (M6.15.6). (e) `domain_scope.md` Bölüm 3'teki her eksiğin listede olduğu tek tek kontrol edilir | Son atıf turunda yeni aday sıfır; Bölüm 3 kontrol tablosu bu dosyada; İBK listesi gerekçeli | — | sürüyor (d arka planda) |
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
