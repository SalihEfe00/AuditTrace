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

## Liste dışı resmî kaynaklar

Aşağıdakiler mevzuat.gov.tr'de yok. Kurumların kendi sitelerinden tam sete (M6.7) eklenir:

- MASAK tipoloji ve şüpheli işlem rehberleri (masak.hmb.gov.tr)
- TCMB finansal tüketici şikâyet düzenlemesi ve Sermaye Hareketleri Genelgesi (tcmb.gov.tr)
