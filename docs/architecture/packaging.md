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
