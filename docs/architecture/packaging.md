# Paketleme ve arayüz teknolojisi (M3)

Hedef makine: Windows 10/11, 8 GB RAM, GPU'suz i5, internetsiz ilk açılış. Karar M3.4'te bu belgeye yazılır;
aşağıdaki ölçümler kararın girdisidir.

## M3.1 Mevcut yükün ölçümü (2026-09-30)

Ortam: geliştirme makinesi, `.venv` (CPython 3.12), GPU kullanılmadan, `OMP_NUM_THREADS=2`, veri kopyası
(26 234 parça). Dosyalar işletim sistemi önbelleğinde (ılık disk); temiz makinede ilk açılış daha yavaş olur (M3.3).
Betikler: oturum çalışma alanındaki `m31_measure.py`, `m31_models.py` (Windows çalışma kümesi ve özel bellek).

### Disk

| Kalem | Boyut | Not |
|---|---|---|
| Python kütüphaneleri (`site-packages`) | 1,45 GB | ayrıntı aşağıda |
| Gömme modeli (paraphrase-multilingual-MiniLM-L12-v2, fp32) | 458 MB | çalışma anında zorunlu |
| Sıralayıcı `mufettis-reranker-ft1` (fp32) | 488 MB | isteğe bağlı ama doğruluk için gerekli |
| Parça deposu + sözcüksel dizin + dizin + kaynak önbelleği | ~140 MB | tam mevzuatla (M6) büyüyecek |

En büyük kütüphaneler: torch 502 MB (zaten yalnızca CPU sürümü), scipy 109, transformers 94, pyarrow 86 (pandas /
streamlit), kubernetes 75, sympy 66, chromadb_rust_bindings 60, pandas 60, onnxruntime 44, sklearn 41, streamlit 31.

`chromadb` artık yalnızca eski bir Chroma veritabanını SQLite'a taşımak için kullanılır (M2'den sonra etkin depo
SQLite). Kendisi ve yalnızca onun için gelenler (kubernetes, grpc, rust bağlantıları vb.) yaklaşık 250 MB'tır ve
pakete girmesi gerekmez; bugün `mufettis/rag/engine.py` onu açılışta içe aktarıyor (0,6 sn).

Mevcut yığınla kaba paket tahmini (sıkıştırmasız): kütüphaneler ~1,2 GB (chromadb hariç) + modeller 0,95 GB +
veri 0,14 GB ≈ **2,3 GB**.

### Süre ve bellek

| Aşama | Süre | Çalışma kümesi | Özel bellek |
|---|---|---|---|
| Streamlit içe aktarma | 0,4 sn | 51 MB | 35 MB |
| Uygulama modülleri içe aktarma | 6,0 sn | 477 MB | 497 MB |
| İlk analiz (modeller bu sırada yüklenir) | 5,5 sn | 1 260 MB | 2 082 MB |
| İkinci analiz, aynı belge | 2,6 sn | 1 272 MB | 2 093 MB |
| Üçüncü analiz, iç denetim notu | 3,4 sn | 1 357 MB (en yüksek 1 447) | 2 230 MB |

İçe aktarma süresinin 5,3 sn'si `sentence_transformers` zincirinden gelir (torch, transformers, sklearn).

Özel belleğin dağılımı (modeller ayrı süreçte tek tek yüklenerek):

| Bileşen | Özel bellek |
|---|---|
| torch + sentence-transformers | 440 MB |
| + gömme modeli | +865 MB |
| + sıralayıcı | +730 MB |

Bellek yükünün çoğu (≈1,6 GB) kütüphaneler değil fp32 modellerdir. Streamlit ayrıca bir tarayıcı sekmesi ister
(Edge / WebView2 ayrıca ~300–500 MB).

### Küçültme adayları

1. **chromadb'yi paketten çıkarmak**: ~250 MB disk, 0,6 sn açılış; `engine.py`'deki içe aktarma yalnızca Chroma
   deposu seçildiğinde yapılmalı. Doğruluğa etkisi yok.
2. **Modelleri ONNX Runtime + int8 ile çalıştırmak** (M3.5): iki model ~0,95 GB → ~0,25 GB disk, bellek payı
   ≈1,6 GB → tahmini ~0,4 GB; torch / transformers / sentence-transformers / sklearn çalışma anında gerekmez
   (~0,7 GB disk, ~5 sn açılış). Risk: puanlar değişir; sıralayıcı eşikleri yeniden seçilmeli ve benchmark
   tabanı (`--check-baseline`) korunmalı. onnxruntime zaten kurulu (chromadb ile gelmiş).
3. Ağırlıkları diskte fp16 tutmak: yalnızca disk yarıya iner, CPU'da bellek ve süre değişmez — 2'nin gerisinde.

Ek gözlem: kullanıcı önbelleğinde kodun kullanmadığı `intfloat/multilingual-e5-small` modeli iki kopya
(~1 GB) duruyor; depo dışıdır, pakete girmez.

## M3.5 Modeller ONNX Runtime ile (2026-09-30)

Dışa aktarma `torch.onnx.export` (opset 17, `attn_implementation="eager"`), ardından
`onnxruntime.transformers.optimizer` (model_type bert, 12 kafa, 384 boyut; SkipLayerNorm + BiasGelu birleşimi —
dikkat katmanı transformers 5'in maske yapısı yüzünden birleşmiyor) ve `quantize_dynamic`. Ölçüm ürün koduna
dokunmadan, modeller süreç içinde ONNX oturumlarıyla değiştirilerek; korpus vektörleri değiştirilmedi (fp32 torch).
Eşik her varyantta yalnızca dev'den seçildi (`scripts/reranker_finetune/thresholds.py`). Taban 32/1/0
(dev 10/1/0, holdout 22/0/0) ve kapı bölüm bazında katıdır.

| Varyant (gömme + sıralayıcı) | Modeller | dev eşiği | dev | holdout | Sonuç |
|---|---|---|---|---|---|
| fp32 ONNX | 897 MB | −4,48 | 10/1/0 | 22/0/0 | torch ile birebir (puan farkı 0,0000) |
| tam int8 (dinamik) | 226 MB | −8,07 | 12/1/1 | 19/1/0 | ✗ yanlış pozitif |
| gömme int8 + sıralayıcı fp32 | 562 MB | −4,54 | 11/1/0 | 21/0/0 | ✗ holdout doğru −1 |
| gömme fp32 + sıralayıcı int8 | 561 MB | −7,94 | 12/1/1 | 19/1/0 | ✗ |
| yalnızca kelime tablosu int8 ("gather8") | 347 MB | −4,50 | 10/1/0 | 22/0/0 | ✓ taban aynen |
| gather8 + kanal bazlı int8 sıralayıcı | 287 MB | −5,60 | 10/2/0 | 22/1/0 | ✗ |
| **eager + birleşim + gather8** | 347 MB | −4,50 | 10/1/0 | 22/0/0 | ✓ taban aynen |

Tam int8'in sorunu sıralayıcıdır (MatMul etkinleşim nicemlemesi puanları 0,3'e kadar kaydırıyor). Modellerin
boyutunun ~%80'i 250 bin sözcüklük gömme tablosudur; yalnızca onu int8 yapmak hesap katmanlarını fp32 bıraktığı
için doğruluğu korur. Ürün eşiği −4,48 bu modelde dev'de bir doğruyu kaçırır; eşik −4,50 olmalıdır (bir dev vakası
iki değer arasında — marj ince, M6/M14 ölçümlerinde yeniden seçilir).

Torch'suz süreçte bellek ve hız (`onnxruntime` + `tokenizers`, 2 iş parçacığı; 64 çift × ~384 token puanlama):

| Çalışma zamanı | Özel bellek (çalışma zamanı + iki model) | En yüksek çalışma kümesi | 64 çift |
|---|---|---|---|
| torch (bugünkü) | ~2,0 GB (440 MB kütüphane + 1,6 GB model) | — | 4,5 sn |
| ONNX fp32, bellek havuzu açık | 2,07 GB | 1,89 GB | 6,8 sn |
| ONNX fp32, havuz kapalı | 1,50 GB | 1,84 GB | 8,1 sn |
| ONNX gather8, havuz kapalı | 0,95 GB | 1,31 GB | 8,0 sn |
| **ONNX eager + birleşim + gather8, havuz kapalı** | **0,94 GB** | **1,14 GB** | **5,4 sn** |

ONNX Runtime'ın bellek havuzu (`enable_cpu_mem_arena`) en yüksek kullanımı geri vermez; kapatılmalıdır. Eager
dışa aktarma + birleşim puanlamayı 8,0 → 5,4 sn'ye indirir (torch'tan %20 yavaş). İçe aktarma: onnxruntime +
tokenizers 0,2 sn (torch zinciri 5,3 sn).

Uygulama kodu torch, transformers, sentence-transformers, sklearn, scipy, sympy, networkx'i doğrudan içe aktarmaz
(yalnızca `engine.py` ve `reranker.py` sentence-transformers / torch üzerinden); ONNX çalışma zamanıyla bunlar
paketten çıkar (~0,85 GB). Kaba paket tahmini: kütüphaneler ~0,4 GB + modeller 0,35 GB + veri 0,14 GB ≈ **0,9 GB**
(bugünkü yığınla ~2,3 GB).

Açık kalanlar (uygulama adımında): gömme ve sıralayıcı için ONNX arka ucu (`tokenizers` ile, transformers'sız) ve
torch arka ucuyla sonuç eşitliği testi; dönüştürme tarifinin repoya betik olarak alınması (bugün oturum çalışma
alanında `m35_export.py`, `m35_eval.py`, `m35_mem.py`); dönüştürülmüş modellerin özetleri `reranker.MODELS`
benzeri bir kayıtta.
