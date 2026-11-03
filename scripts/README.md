# Betikler

Uygulamanın kendisi bu betiklere ihtiyaç duymaz. Bunlar mevzuat kütüphanesini kurmak, bakımını yapmak ve kalite
ölçmek için geliştirici araçlarıdır. Her betiğin ayrıntısı ve kullanımı kendi açıklamasında yazar:
`python scripts/<betik>.py --help`.

Ortak kurallar:

- Veri dizini `MUFETTIS_DATA_DIR` ile seçilir.
- Yazan betikler varsayılan olarak kuru çalışır, yani yalnızca ne yapacaklarını gösterir. Üretim `data/` dizinine
  ancak açık bir bayrakla (`--apply`, `--production`) yazarlar.

## Mevzuat kütüphanesini kurma ve güncelleme

| Betik | Ne yapar |
|---|---|
| `fetch_legislation.py` | Mevzuat listesindeki belgeleri resmî kaynaktan indirir |
| `import_legislation.py` | İndirilenleri kütüphaneye yükler (kaldığı yerden sürer) |
| `apply_legislation_corpus.py` | Yedekleme → ayıklama → indirme → yükleme → değişiklik notları → eşik adımlarını tek seferde uygular |
| `update_legislation.py` | Resmî Gazete fihristinden listedeki belgelerin değişikliklerini bulur, değişeni yeni sürüm olarak yükler |
| `fetch_historical_laws.py` | Kanunların önceki metinlerini tarih aralığıyla yükler (olay tarihine göre kural için) |
| `curate_corpus.py` | Mevzuat olmayan kayıtları ayıklar (silmez, `--restore` ile geri alınır) |
| `rechunk_outdated_documents.py` | Parçalayıcı değişince, parçaları güncel olmayan belgeleri yeniden parçalar |
| `install_reranker_model.py` | Yeniden sıralayıcı modelini (~490 MB) kurar ve doğrular |
| `build_package_data.py` | Windows paketine girecek veri klasörünü seçerek kopyalar |

## Ölçüm

| Betik | Ne ölçer |
|---|---|
| `eval_documents.py` | Belge türü ve alan çıkarımı (`understanding`, korpussuz) ya da tam hat (`decision`); `benchmarks/document_baseline.json` |
| `eval_accuracy.py` | Gerçek korpusta iddia–madde eşleştirme doğruluğu |
| `check_legislation_retrieval.py` | Yüklenen her belgenin aramada bulunabildiği |
| `rules_case_benchmark.py` | Kuralların tek belgelik vakalarda cevap anahtarına uyumu |
| `rules_file_benchmark.py` | Kuralların kredi dosyası vakalarında cevap anahtarına uyumu |
| `rules_scan_benchmark.py` | Doldurulup taranmış şablonların OCR sonrası aynı ölçümü |
| `rules_review_table.py` | Kural kataloğunu hukuki gözden geçirme için HTML tabloya döker |

## Vaka hazırlama

| Betik | Ne yapar |
|---|---|
| `split_cases.py` | Dil modellerine yazdırılan vaka dosyalarını tek tek vakalara böler |
| `import_synthetic.py` | Sentetik örnekleri `benchmarks/documents/` vakalarına çevirir |
| `make_synthetic_prompts.py` | Sentetik örnek görevlerinden kopyalanmaya hazır istem dosyaları üretir |

## Eski veri dizinlerini güncelleme (tek seferlik)

Bunlar geçmişte bulunan veri sorunlarını mevcut bir `data/` dizininde düzeltmek için yazıldı. Güncel betiklerle
sıfırdan kurulan bir kütüphanede gerekmeyebilir. Önce kuru çalıştırıp ne değişeceğine bakın.

| Betik | Ne düzeltir |
|---|---|
| `migrate_chroma_to_sqlite.py` | Chroma koleksiyonunu SQLite parça deposuna taşır ve doğrular |
| `backfill_canonical_metadata.py` | Kanonik meta veri alanlarını mevcut kayıtlara yazar (metin aynı kalır) |
| `backfill_amendment_metadata.py` | Mevcut parçalara madde değişiklik tarihlerini ekler |
| `recover_legacy_laws.py` | Eski şemadaki parçalarda kalan mevzuatı kurtarır (E7) |
| `repair_e8_corpus.py` | PDF çıkarma bozulmalarını, bitişik parçaları ve sürüm zincirlerini onarır (E8) |
| `clear_stale_version_links.py` | Aktif belgelerde kalmış yanlış sürüm bağlarını temizler |

## Test

| Betik | Ne yapar |
|---|---|
| `setup_rag_lab.py` | Testler için yalıtılmış küçük kütüphaneyi (`data/rag_lab`) kurar; üretim verisine dokunmaz |

## Alt klasörler

| Klasör | İçerik |
|---|---|
| `inventory/` | Mevzuat listesini (`mufettis/corpus/legislation_sources.json`) üreten araştırma hattı; kendi README'si var |
| `onnx_models/` | Gömme ve sıralayıcı modellerini ONNX'e çevirme, kurma ve bellek ölçümü (Windows paketi için) |
| `reranker_finetune/` | Sıralayıcı modelinin ince ayarı: eğitim grupları, eğitim, eşik seçimi |
