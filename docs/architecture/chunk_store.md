# Parça deposu (SQLite + disk üzerinde kesin vektör taraması)

Kod: `mufettis/rag/store.py`. Testler: `tests/test_chunk_store.py`.

## Neden

Tam mevzuat ölçeğinde yaklaşık 1 milyon parça hedefleniyor. Hedef donanım i5 9. nesil, 8 GB RAM, GPU yok. Chroma bu ölçekte taşınamaz:

- **Disk:** parça başına yaklaşık 13 KB tutuyor. Sebepleri iki: metadata'yı anahtar başına ayrı satırda indekslemesi ve tam metin için bir trigram indeksi tutması (metnin 8,6 katı yer kaplıyor).
- **Bellek:** HNSW grafiği bellekte duruyor ve silinen öğeleri geri kazanmıyor. Veri kopyasında 26 bin canlı parçaya karşılık grafikte 86 bin öğe vardı.

## Tasarım

- **Arayüz:** Chroma koleksiyonunun projede kullanılan alt kümesi: `get`, `query`, `upsert`, `update`, `delete`, `count` ve istemci tarafında `get_or_create_collection` / `delete_collection`. Sonuç biçimi ve anlam Chroma ile aynı; testler aynı işlemleri gerçek Chroma'da da çalıştırıp karşılaştırıyor.
  - Eşitlik tipe duyarlı: `1`, `"1"` ve `True` birbirine eşit değil.
  - `$ne` ve `$nin` anahtarı olmayan kayıtları da kapsıyor.
  - Sonuçlar ekleme sırasıyla dönüyor.
  - `update` ve `upsert` metadata'yı birleştiriyor; `None` değeri anahtarı siliyor.
  - Metin değişirken vektör verilmezse açık hata veriliyor. Chroma bu durumda vektörü kendi varsayılan modeliyle yeniden hesaplıyor.
- **Metadata iki katmanlı:**
  - `bases`: belge düzeyindeki ortak alanlar. İçerik adresli; aynı içerik tek satır.
  - Parça satırı: yalnızca belge içinde değişen alanlar, örn. madde no, parça no, hesap kodu.
  - Bir anahtar bu iki katmandan yalnızca birinde bulunur.
  - Filtre, taban koşulunu `bases` tablosunda bir kez değerlendirir ve parçalara `base` indeksiyle iner. Parça katmanına yalnızca `delta_keys` içindeki anahtarlar için bakılır.
- **Vektörler:** yalnızca sona eklenen üç dosyada: `vec_i8` (tarama), `vec_f16` (yeniden puanlama) ve `vec_aux` (ölçek ve ‖v‖²).
  - Arama kesin, yaklaşık bir indeks yok: bütün adaylar int8 ile taranır, en iyi 4k aday float16 ile yeniden puanlanır.
  - Mesafe L2'nin karesi.
- **Tutarlılık:**
  - Yazmalar SQLite yazma kilidi (`BEGIN IMMEDIATE`) altında yapılır, süreçler sırayla yazar.
  - Okumalar tek bir anlık görüntüden yapılır.
  - Sıkıştırma (`compact`) canlı satırları yeni bir dosya kümesine kopyalar ve o kümeye tek işlemle geçer. Arama sırasında sıkıştırma olursa sorgu yeniden çalışır.
  - Çöp satırlar canlı satırların %30'unu ve 10 bini aşınca sıkıştırma kendiliğinden çalışır (`delete_hash` sonrası).

## Ölçümler

| Ölçüm | Sonuç |
|---|---|
| Doğruluk (55 bin gerçek vektör; float32 kesin sonuçla örtüşme) | ilk 10'da %99,7, ilk 50'de %99,95 |
| Motorun 160 adayında (veri kopyası, 30 sorgu) | yeni depo %100; Chroma HNSW ortalama %96,1, en düşük %83,8 |
| Hız, 1M parça (int8 tarama, 2 iş parçacığı) | ~0,4–0,55 sn/sorgu |
| Disk, veri kopyası (26 bin parça) | Chroma 477 MB → 45 MB |
| 200 bin parça / 5 bin belge: disk | 362 MB |
| 200 bin parça: yazmadan sonraki ilk filtreli sorgu | 0,37 sn |
| 200 bin parça: önbellekli filtreli sorgu | 53 ms |
| 200 bin parça: belgeye göre okuma / silme | 1 ms / 7 ms |
| 200 bin parça: hesap kodu veya belge kimliğiyle okuma | ~0,13 sn |
| 25 bin belge (tam mevzuat ölçeği) / 100 bin parça: belge kimliğiyle okuma | 82 ms |
| 25 bin belge / 100 bin parça: soğuk uygunluk filtresi (35 bin satır döner) | 0,64 sn |

## Seçim ve geçiş

`MUFETTIS_VECTOR_STORE` üç değer alır: `auto` (varsayılan), `sqlite` ve `chroma`.

- `auto`, taşınmış bir depo varsa (`data/corpus_store/mevzuat/store.sqlite3`) onu, yoksa taşınmamış bir Chroma veritabanı varsa Chroma'yı kullanır. İkisi de yoksa (yeni kurulum) SQLite deposu kullanılır.
- Taşıma komutu kaynağa yazmaz. Kopyalar ve sayı, sıra, metin, metadata ve vektörleri doğrular. Chroma dizini yedek olarak yerinde kalır.

```bash
.venv/Scripts/python.exe scripts/migrate_chroma_to_sqlite.py
```

Eski biçimdeki (şema 1) bir SQLite deposu açılmaz; `--replace` ile yeniden kurulur.
