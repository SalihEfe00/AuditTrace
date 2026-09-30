# Mevzuat korpusu (M6)

Korpusa girecek mevzuat bir **liste** ile tanımlanır. Tarama yapılmaz, yalnızca listedeki belgeler resmî
kaynaktan indirilir. Belge kimliği de listeden gelir.

## Hat

```
legislation_sources.json ──► fetch_legislation.py ──► legislation_downloads/ ──► import_legislation.py ──► korpus
  (liste, M6.2)               (indirici, M6.3)          (+ downloads.json)          (yükleyici, M6.4)
```

| Parça | Dosya | Görev |
|---|---|---|
| Liste | `mufettis/corpus/legislation_sources.json` + `legislation_sources.py` | Belge başına mevzuat.gov.tr kodu, resmî ad, tür, numara, kurum, öncelik, konu grubu, gerekçe. Test listeyi doğrular: yalnızca resmî alan adları, tekrar yok, zorunlu alanlar dolu. |
| İndirici | `mufettis/harvesting/official_legislation.py`, `scripts/fetch_legislation.py` | Dürüst kullanıcı kimliği (`MufettisAI/…`), robots.txt, istekler arasında en az 3 sn. Kanunlar PDF olarak iner. Yönetmelik, tebliğ ve kararların PDF'i yok; aynı sitenin güncel, değişiklikleri işlenmiş resmî metin sayfası HTML olarak alınır. Doğrulama: listedeki resmî adın kelimelerinin en az %80'i ilk sayfalarda geçmeli; kanunlarda kanun numarası da aranır. Yanlış kod yanlış belge indirirse dosya kabul edilmez. `downloads.json` kaydı klasöre göreli tutulur; doğrulanmış dosya yeniden indirilmez. |
| Yükleyici | `mufettis/importers/legislation_importer.py`, `scripts/import_legislation.py` | Belgeler tek tek işlenir ve mevcut sürümleme hattına (`versioned_ingest_text`) girer. Aynı özetli belge atlandığı için kesilen çalışma kaldığı yerden sürer. Bir belgenin hatası çalışmayı durdurmaz. Belge başına süre ve işlem belleği `import_runs.jsonl`'a yazılır. |
| Ayıklama | `mufettis/corpus/curation.py`, `scripts/curate_corpus.py` | Mevzuat olmayan kayıtlar (M6.5); aşağıda. |

Betikler üretim `data/` dizinine yalnızca `--production` ile yazar. Bu bayrak kullanıcı onayı gerektirir
(M6.10). Onaysız çalışmada `MUFETTIS_DATA_DIR` bir veri kopyasını göstermek zorundadır.

## Belge kimliği

Önceden kimlik başlık ve metinden tahmin ediliyordu. Bu iki sınıf hata üretti:

- Gövdedeki "5411 sayılı Kanun" atfı yönetmeliğe kanun numarası olarak yazılabiliyordu.
- "Sayı" kalıbı "sayılı" kelimesini de yakaladığı için `document_number` "lı", "sı" oluyordu. Bu değer
  raporda "Belge No: lı" olarak görünüyordu.

Şimdi:

- **Liste kimliği önde:** Listeden gelen belgenin kimliği `listed_document_type`, `listed_law_number`,
  `listed_document_number` ve `listed_document_key` alanlarıyla taşınır. `extract_legal_metadata`
  bunları tahminin önüne koyar; listede boş olan alan boş kalır. Alanlar düz tutulduğu için kayıtta ve parça
  meta verisinde saklanır; yeniden parçalamada kimlik yine listeden gelir.
- **Sürüm anahtarı:** `mevzuat:<kod>`. Aynı kodun yeni metni önceki sürümün yerini alır (`superseded`).
- **"Sayı" kalıbı:** Kelime olarak aranır ve numara en az bir rakam içermek zorundadır.

Kaynak: `mevzuat_gov` (Mevzuat Bilgi Sistemi), `publisher_kind = primary_official`.

Diğer kimlik kuralları (M6.13):

- **Kalıcı kimlik (`declared_work_id`):** Kanun dışındaki liste belgeleri `mevzuat:<kod>` kimliğini
  taşır. Başlık kimliği 80 karakterde kesildiği için adı yalnızca sonunda ayrışan belgeler (32 sayılı Karara
  ilişkin tebliğler) birbirinin sürümü sayılıyordu. Kanunlar resmî numarayla (`tr/kanun/N`) kaynaktan
  bağımsız kimliği korur.
- **Tür:** Liste türü `declared_instrument_type` olarak taşınır.
- **Tür desenleri:** "Tebligat" tebliğ değildir. Başlığa bitişik dipnot işareti ("KANUNU1") ne türü ne de
  beyan edilen adı bozar.

## Adla ve seri numarasıyla arama

- **Seri numarası (M6.11, M6.13):** Sorgudaki "Sıra No: N" ya da "Tebliğ No: 2008-32/34" belge
  kimliğidir. Numarası tutan seri üyesi kanun numarası eşleşmesi gibi öne geçer.
- **Kanun adı (M6.13):** Sorguda adıyla anılan kanun ("İcra ve İflas Kanunu …") atıf çözücüsüyle
  (`analysis.cited_references`) korpustaki kanun kataloğuna karşı numaraya çözülür
  (`LocalRAG.named_law_number`). Sonra numaralı sorguyla aynı yoldan gider.
- **Madde numarası (M6.14):** Kanun belli olduğunda (numarayla ya da adla) sorgudaki madde de çözülür:
  "54 üncü madde", "md. 89", "584. madde", "geçici madde 5" (`extract_query_article`). O maddenin parçaları
  fıkra sırasıyla doğrudan başa gelir (`LocalRAG._article_chunks`); anlamsal benzerlik maddeyi seçmez. Madde
  korpusta yoksa kanunun diğer parçalarıyla devam edilir.

## Mevzuat olmayan kayıtlar (M6.5)

Kural: resmî olmayan bir yayıncıdan gelen ve hiç madde yapısı olmayan kayıt.

- **İkincil site** (ör. mevzuat.org portal, giriş ve hesaplama sayfaları): `excluded` durumuna geçer.
- **Kullanıcının yerel dosyası:** Yalnızca incelemeye önerilir, karar kullanıcınındır. Bankanın maddesiz iç
  düzenlemeleri (yetki tablosu, ücret tarifesi; M8) de yerel dosya olacaktır.
- **Dokunulmayanlar:** Resmî kaynaklı maddesiz metinler (BDDK rehberi, genelgesi) ve kurum içi kayıtlar
  hiçbir zaman önerilmez.

Ayıklama silme değildir:

- `excluded` durumu `corpus.validity.EXCLUDED_STATUSES` içinde tanımlıdır. `_chunk_is_retrievable`
  (bütün arama yollarının geçtiği kapı) bu durumdaki parçaları almaz.
- Terim istatistiği, atıf kataloğu ve yeniden parçalama `INACTIVE_STATUSES` ile bu kaydı etkin saymaz.
- Önceki durum kayıtta saklanır; `--restore` ile geri alınır. Her işlem denetim kaydına yazılır.

İlk çalıştırmada:

- **Ayıklanan (3):** mevzuat.org ana sayfası, e-Beyan giriş paneli, hesaplama kütüphanesi (29 parça).
- **İncelemeye düşen (1):** `mufettis_ai_test_temiz` (yerel test dosyası).

## Güncel tutma (M6.8)

`mufettis/corpus/legislation_updates.py`, `scripts/update_legislation.py` iki sinyal kullanır:

1. **Resmî Gazete fihristi** (varsayılan, son N gün): Listedeki bir belgenin adını anan yayım o belgeyi
   denetime alır. Örnek: "… Yönetmelikte Değişiklik Yapılmasına Dair Yönetmelik". Eşleşme için belge
   adının anlamlı kelimelerinin en az %80'i yayım başlığında geçmeli. Kelimeler Türkçe ekler için ilk 6
   harfle karşılaştırılır; tür adları ve bağlaçlar sayılmaz.
2. **Tam denetim** (`--all`): Torba kanunların başlığı değiştirdiği kanunları saymaz ("Bazı Kanunlarda
   Değişiklik…"). Bu yüzden listedeki bütün belgeler dönem dönem yeniden indirilir.

Değişiklik, çıkarılan metnin özetiyle (`text_sha256`) ölçülür. Aynı metnin yeniden üretilmiş dosyası yeni
sürüm sayılmaz. Metni değişen belge yeniden yüklenir; sürüm anahtarı aynı olduğundan eski sürüm
`superseded` olur.

mevzuat.gov.tr dışındaki kaynaklarda (TCMB genelgeleri, MASAK rehberleri) güncel metin yeni bir dosya
adıyla yayımlanır. Adres değişince listenin elle güncellenmesi gerekir.

## Metin olarak alınamayanlar

- **Hawala ve Benzeri Alternatif Değer Transfer Sistemleri ŞİB Rehberi (MASAK, 2026):**
  - İndirildi ve doğrulandı, ama yükleme hattının metin kalite kapısı onu karantinaya aldı (puan 0,81,
    otomatik onarım çözemedi). Tasarımı yoğun bir PDF; çıkarılan metin bozuk.
  - Kapı işini doğru yaptığı için karantinada bırakıldı.
  - Bankalara özgü asıl ŞİB rehberi sorunsuz yüklendi. M12 (tipoloji uyarıları) bu rehberi gerekli
    görürse metni ayrıca ele alınır.

- **KKDF kesintisi kararı (Cumhurbaşkanı Kararı 11205, 2026):** PDF'inde yalnızca imza sayfası metin olarak
  var; oranları içeren ek görüntü olarak basılmış. mevzuat.gov.tr metin sayfası da boş. Doğrulama kaydı
  reddetti ve kayıt listeden çıkarıldı (2026-09-30). Oranlar sık değiştiği için ileride OCR ile alınması
  ayrıca değerlendirilir.

## mevzuat.gov.tr dışındaki resmî kaynaklar (M6.7)

Kurumların kendi sitelerinden, listede `url` alanıyla. Kaynak kimliği alan adından seçilir: `masak_official`
(masak.hmb.gov.tr, dosyalar ms.hmb.gov.tr'de), `tcmb_official` (tcmb.gov.tr).

- **MASAK:**
  - Bankalar ve PTT Şüpheli İşlem Bildirim Rehberi (sürüm 2.0; M12 tipoloji uyarılarının dayanağı)
  - Şüphe Kategorileri Referans Tablosu
  - Hawala ve Benzeri Alternatif Değer Transfer Sistemleri ŞİB Rehberi
- **TCMB:**
  - Sermaye Hareketleri Genelgesi (Temmuz 2026 güncel metni)
  - Görünmeyen İşlemler Genelgesi (18 Mayıs 2026)

Ayrı bir TCMB "finansal tüketici şikâyetleri" düzenlemesi ne mevzuat.gov.tr'de ne TCMB mevzuat sayfalarında
bulundu. Şikâyet belgelerinin ölçütü 6502 sayılı Kanun ve TCMB ücret tebliği ile kalır.

## Sıralayıcı eşiği korpusa özgüdür (M6.1)

Aynı sıralayıcı modelinin puan dağılımı korpusla değişir. Dev bölümünden seçilen eşik eski korpusta -4,48, tam
mevzuatta -2,95. Eski korpusa -2,95 uygulanırsa sonuç 32 / 1 / 0'dan 29 / 1 / 0'a düşer. Bu yüzden eşik
kodda değil, korpusun yanında durur: `DATA_DIR/reranker_calibration.json` (model adı → `min_score`). Dosya yoksa
modelin koddaki varsayılanı (`mufettis/rag/reranker.py`) kullanılır.

Tam mevzuat üretime geçerken (M6.10) bu dosya da yazılır.

Aday havuzu derinliği (`CLAIM_CANDIDATES`) 30, 45 ve 60 için ölçüldü. Havuz isabeti üçünde de 0,885; derinlik 30
kalır. Havuzdan düşen hükümler sıralamanın çok gerisinde, bu yüzden derinlik artışı onları geri getirmiyor.

## Kanunların önceki metinleri (M7.3, M7.4)

mevzuat.gov.tr her kanun için "X ile değişik hali" metinlerini yayımlar (`EskiKanunlar/EskiKanunlarList`, JSON;
`mufettis/importers/historical_laws.py`, `scripts/fetch_historical_laws.py`, M6.10 betiğinde 4c adımı). Listedeki
19 kanunda 50 sürüm var (TMK 14, Bankacılık 17, Harçlar 9, TTK 4, diğerleri 1).

* **Değiştiren işlem** kanun, KHK ya da Anayasa Mahkemesi kararıdır ("2012205" = K. 2012/205); ilk kayıt kanunun ilk
  metni olabilir. Sürüm adı metnin kendi başlık satırından alınır ("… ile değişik hali"; yoksa "ilk metin").
* **Doğrulama:** metnin başında değiştiren işlemin numarası geçer ("2012/205" biçimi dahil); metinde değiştiren
  işlemden sonraki bir değişiklik notu yoktur. Başlık ve kanun numarası indiricide güncel kanunla aynı denetlenir.
* **Aralık:** başlangıç Resmî Gazete tarihi; bitiş bir sonraki değişikliğin kabul tarihi (listedeki sonraki sürüm
  ya da güncel metindeki sonraki ilk not, hangisi önceyse). Kaynakta kabulü yayımından sonra görünen kayıtlarda
  yayım esas alınır; sıralama yayım tarihine göredir. Aralık hiçbir zaman gerçeğinden uzun değildir; bitiş
  bulunamazsa ya da başlangıçtan önceyse metin hiçbir tarihte yürürlükte sayılmaz.
* **Durum** `historical`: güncel sürümün yerini almaz (`link_versions=False`), kimliği ayrıdır
  (`mevzuat:<kod>@<kabul>`), onarımda korunur (`corpus.validity.preserved_status`).
* **Güncel korpus süzgeci** (`rag.engine.current_where`): önceki metinler varsayılan anlamsal sorgulara ve BM25
  istatistiklerine girmez. Bir kanunun 14 neredeyse aynı sürümü ilk sıraları doldurur ve terim sıklıklarını
  değiştirirdi. BM25 indeksi imzası süzgeci içerir, bir kez yeniden kurulur.
* **Olay tarihi (M7.4):** `claim_candidates(as_of=)` havuzu güncel metinle bulur; o tarihte önceki metni
  yürürlükte olan kanunun parçasını aynı madde / fıkra / bentle değiştirir (o tarihte olmayan madde düşer) ve
  önceki metinlerde ayrıca anlamsal arar (sonradan kaldırılan maddeler). Olay tarihi son değişiklikten sonraysa
  sonuç olay tarihsiz aramayla aynıdır. Kanıt adı "— olay tarihindeki metin: … (başlangıç – bitiş)" taşır.

Veri dizini kopyalanınca ingest dizini korunur (M7.3a): eski `ingest_index.json` içerik özetiyle izlenir; zaman
damgasıyla izlendiği için kopyalanan veri dizininde eski JSON SQLite'taki yeni kayıtları siliyordu.

## M6.10 Üretime uygulama (kullanıcı onayıyla)

1. Uygulama kapalıyken:
   `python scripts/apply_legislation_corpus.py --production --exclude 64b009bc9ab3`
   - Yedek, ayıklama, indirme, yükleme ve eşik dosyası tek komutta yapılır.
   - Yedek `data/backups/legislation_corpus_<zaman>/` altına alınır.
   - `64b009bc9ab3` = `mufettis_ai_test_temiz`. Ayıklama geri alınabilir.
2. Üretim verisinin yeni bir kopyası alınır (ör. `datacopy4`). Tabanlar bu kopyada bilinçli olarak yeniden
   yazılır:
   - `MUFETTIS_DATA_DIR=<kopya> python scripts/eval_accuracy.py --split all --write-baseline "M6.10 tam mevzuat"`
   - `MUFETTIS_DATA_DIR=<kopya> python scripts/eval_documents.py --level decision --write-baseline "M6.10 tam mevzuat"`
   - İsteğe bağlı testler artık bu kopyayı gösterir: `MUFETTIS_BENCHMARK_DATA_DIR`,
     `MUFETTIS_TEST_SOURCE_DATA_DIR`.
3. Tüm testler çalıştırılır; `python scripts/check_legislation_retrieval.py --priority 2` kopyada tekrar edilir.
4. Geri dönüş gerekirse yedekteki dizin ve parça deposu geri yüklenir. `reranker_calibration.json` silinirse
   eşik modelin varsayılanına döner.

Aynı betik `datacopy3`'ün taze kopyasında (indirme klasörü olmadan, üretimin birebir benzeri) prova edildi.
