# Paketleme ve arayüz teknolojisi (M3)

Hedef makine: Windows 10/11, 8 GB RAM, GPU'suz i5, internetsiz ilk açılış. Karar M3.4'te bu belgeye yazılır;
aşağıdaki ölçümler kararın girdisidir.

## Karar (M3.4, 2026-09-30)

**Arayüz: Streamlit kalır; kullanıcıya Edge'in `--app` penceresinde (sekmesiz, adres çubuksuz, ayrı profil)
gösterilir.** Sunucu yalnızca 127.0.0.1'i dinler (M3.6). Edge bulunamazsa varsayılan tarayıcıya düşülür.

**Paketleme: PyInstaller, onedir** (tek dosya kipi 2 GB'ı her açılışta geçici klasöre açar — elenir), üzerine
kurulum programı M17'de.

**Model çalışma zamanı: ONNX Runtime, "eager + birleşim + yalnızca kelime tablosu int8"** (M3.5); torch,
transformers, sentence-transformers, sklearn ve scipy pakete girmez.

Gerekçe (ölçümler aşağıdaki bölümlerde):

| Seçenek | Sonuç |
|---|---|
| Streamlit + varsayılan tarayıcı | ✗ varsayılan kip değil: temiz Windows'ta `http` ilişkilendirmesi yok (M3.3); kullanıcının tarayıcısı ve ayarları belirsiz |
| Streamlit + pywebview (WebView2) | ✗ temiz Windows'ta WebView2 yok, pencere boş kalıyor (M3.3); WebView2 sabit sürümünü paketlemek yüzlerce MB ekler ve Edge penceresine göre kazanç yok |
| **Streamlit + Edge `--app`** | ✓ Windows 10/11'de Edge hazır gelir; temiz, internetsiz Sandbox'ta sekmesiz açıldı ve 26 234 parçayı gördü (M3.3); pencere kapanınca sunucu kapanır |
| Arayüzü masaüstü araç takımıyla (Qt vb.) yeniden yazmak | ✗ denenmedi: Streamlit arayüzü ölçütleri karşılıyor; tüm ekranları yeniden yazmak M15'i büyütür, getirisi yok. Risk: Streamlit görsel serbestliği sınırlı — M15 tema ve CSS ile çalışır |
| torch çalışma zamanı | ✗ paket ~2 GB, model belleği ~2,0 GB, kurulumdan sonraki ilk açılış ~82 sn (Defender yeni DLL'leri tarıyor; ikinci açılış 10 sn) |
| **ONNX (gather8)** | ✓ benchmark tabanı aynen (dev eşiği −4,50); modeller 946 → 347 MB, model belleği 2,0 → 0,94 GB, kütüphaneler ~0,85 GB azalır, içe aktarma 5,3 → 0,2 sn; puanlama %20 yavaş |

Hedef makinede (8 GB, GPU'suz) beklenen toplam: sunucu ~1,2 GB (ONNX) + Edge penceresi ~0,3–0,5 GB.

**Sonraki taşlara etkiler** (plan satırlarına işlendi):

- **M6** (tam mevzuat): korpus vektörleri bugünkü gömme modeliyle üretilir; gather8 sorguları bu vektörlerle
  ölçüldü (taban aynen). Model değişirse vektörler yeniden üretilir.
- **M14** (eğitim turları): her yeni sıralayıcı `scripts/onnx_models/export.py` ile dönüştürülüp
  `eval_with_onnx.py` ile ölçülür; eşik ONNX puanlarından seçilir; kabul, gönderilecek (ONNX) modelle verilir.
- **M15** (arayüz): Streamlit üzerinde; Edge `--app` penceresi boyutunda ve düşük çözünürlükte denenir.
- **M17** (paketleme): ONNX arka ucu (`tokenizers` ile, transformers'sız) ve torch ile sonuç eşitliği testi;
  Edge `--app` varsayılan kip; `vcruntime140_threads.dll` gibi çalışma zamanı DLL'leri ve pakette eksik DLL
  taraması testi; günlükler ve yazılabilir veri `%LOCALAPPDATA%`; derleme her zaman `--clean`; kurulum sırasında
  bir kez ısıtma (Defender taraması kullanıcının ilk açılışına kalmasın); sunucu alt süreci iş nesnesine
  (Job Object) bağlı; Streamlit dosya izleyicisi kapalı; chromadb pakette yok.

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

Tarif repoda: `scripts/onnx_models/export.py` (dışa aktarma, birleşim, nicemleme; aynı girdiden bayt bayt aynı
modelleri üretir), `eval_with_onnx.py` (benchmark), `measure_memory.py` (torch'suz bellek / hız).

Açık kalanlar (uygulama adımında): gömme ve sıralayıcı için ONNX arka ucu (`tokenizers` ile, transformers'sız) ve
torch arka ucuyla sonuç eşitliği testi; dönüştürülmüş modellerin özetleri `reranker.MODELS` benzeri bir kayıtta.

## M3.2 Deneme paketleri (2026-09-30)

`packaging/launcher.py` + `packaging/mufettis_trial.spec` (PyInstaller 6.22.3, onedir, bugünkü torch yığını).
Derleme: `python -m PyInstaller packaging/mufettis_trial.spec --noconfirm` (ilk derleme ~6 dk, sonrakiler ~3 dk).
Veri, modeller ve gömme modelinin HF önbelleği paketin yanına `data/`, `models/`, `hf/` olarak konur; başlatıcı
bunları ortam değişkenleriyle gösterir ve `HF_HUB_OFFLINE=1` ile çalışır.

| Kalem | Boyut |
|---|---|
| Uygulama + kütüphaneler (`MufettisAI.exe` + `_internal`) | 782 MB |
| Sıralayıcı modeli (`models/`) | 466 MB |
| Gömme modeli (`hf/`) | 458 MB |
| Veri (deneme kopyası; 98 MB yedek dahil) | 263 MB |
| Toplam klasör | ~2,0 GB |

Kipler (aynı paket): tarayıcı (Streamlit + varsayılan tarayıcı), `--window` (sunucu alt süreçte, arayüz pywebview /
Edge WebView2 penceresinde), `--serve PORT` (yalnızca sunucu).

| Ölçüm (geliştirme makinesi, ılık disk) | Sonuç |
|---|---|
| Sunucunun hazır olması | 1,0–1,4 sn (ilk açılışta 5,6 sn) |
| Pencere kipinde pencerenin görünmesi | 2,1 sn |
| İlk analiz (uygulama modülleri ve modeller bu sırada yüklenir) | 11,4 sn — kaynaktaki 6,0 + 5,5 sn ile aynı |
| Sonraki analiz | 2,6 sn (kaynakla aynı) |
| Sunucu belleği, analiz sonrası | 1,3 GB çalışma kümesi, 2,1 GB özel (kaynakla aynı) |
| Pencere kipi ek yükü | WebView2 süreç ağacı ~300 MB özel (515 MB çalışma kümesi) + başlatıcı 71 MB |
| Sonuç | Örnek şikâyette sınıflandırma, iddialar ve 97 kanıt parçası kaynakla aynı |

Paketlemede bulunan ve çözülen sorunlar (tam paketlemede M17 de aynılarını gerektirir):
1. **transformers 5 arşivde çalışmıyor**: içe aktarma yapısını kendi `.py` dosyalarını diskte tarayarak kurar;
   PyInstaller arşivindeyken ilk analiz sonsuza dek bekler (hata yok, işlemci boşta). Çözüm:
   `module_collection_mode={"transformers": "py"}`. Teşhis `MUFETTIS_TRACE_AFTER=<sn>` (faulthandler yığın dökümü).
2. **Streamlit ayarları**: `bootstrap.run`'dan önce `bootstrap.load_config_options` bayrak biçimli anahtarlarla
   (`server_port` vb.) çağrılmalı; yoksa port / adres / geliştirici kipi ayarları uygulanmaz.
3. **Yerel ağa açık sunucu ve kullanım istatistiği**: M3.6'da kapatıldı (paket ve kaynak).
4. **Konsolsuz pakette `sys.stdout` / `sys.stderr` yok**: çıktı paket yanındaki `launcher_output.log`'a yönlendirilir.

M17'ye notlar: başlatıcı ana süreci zorla sonlandırılırsa sunucu alt süreci öksüz kalır (pencere normal
kapatıldığında sonlanır) — iş nesnesi (Job Object) ile bağlanmalı; soğuk diskte ve Windows Defender taramasıyla ilk
açılış M3.3'te ölçülür.

## M3.3 Temiz ortamda deneme (2026-09-30)

Windows Sandbox (Windows 11 10.0.26100, internet kapalı, GPU kapalı, 6 GB bellek sınırı), paket klasörü salt okunur
eşlenip Sandbox masaüstüne kopyalanarak. Yapılandırmalar: `MufettisAI_deneme.wsb` (elle deneme),
`MufettisAI_olcum.wsb` (kopyalar ve `MufettisAI.exe --measure` çalıştırır, sonucu eşlenen yazılabilir klasöre yazar).

Ölçüm (`--measure`, ilk açılış; Windows Defender Sandbox'ta etkin; aynı anda elle açılmış bir uygulama da
çalıştığından süreler üst sınırdır):

| Aşama | Sandbox | Geliştirme makinesi |
|---|---|---|
| Sunucunun hazır olması | 1,0 sn | 1,0 sn |
| Uygulama içe aktarma (torch zinciri) | 16,5 sn | 6,0 sn |
| İlk analiz (modeller yüklenir) | 12,2 sn | 9,7 sn |
| Sonraki analizler | 5,2 / 6,7 sn | 3,0 / 4,6 sn |
| Bellek (analiz sonrası) | 2,1 GB özel, en yüksek çalışma kümesi 1,43 GB | aynı |
| Sonuç | 3 analizin 3'ü başarılı; arayüz 26 234 parçayı görüyor | aynı |

Temiz Windows'ta bulunan ve paket kusuru olan sorunlar (hepsi yalnızca temiz makinede görünür):

1. **Eksik Visual C++ DLL'i**: `torch_cpu.dll` Visual C++ 2022 çalışma zamanının `vcruntime140_threads.dll`'ine
   bağlı; geliştirme makinesinde `System32`'de, temiz Windows'ta yok; PyInstaller `System32`'yi toplamaz. torch
   yüklenemeyince arayüz yalnızca "RAG hazır değil" diyordu. Çözüm: spec'te `binaries`; tüm paket tarandı
   (`pefile`), eksik başka çalışma zamanı DLL'i yok.
2. **Hata sebebi yutuluyordu**: `engine.py` içe aktarma hatasını, `ui/app._get_rag` sebebi saklamadan `None`
   dönüyordu. Artık sebep saklanır, günlüğe yazılır ve "RAG hazır değil. Sebep: …" olarak gösterilir
   (`tests/test_rag_unavailable_reason.py`).
3. **Varsayılan tarayıcı atanmamış**: temiz Windows'ta `http` için ilişkilendirilmiş uygulama yok ("Bu 'http'
   bağlantısını açamıyoruz"); tarayıcı kipi kullanıcının varsayılan tarayıcı ayarına bağımlı.
4. **WebView2 yok**: pywebview eski MSHTML motoruna düşüyor, Streamlit boş sayfa kalıyor (`webview2` sürümü boş).
5. **Salt okunur kurulum klasörü**: başlatıcı günlüğü exe'nin yanına yazmaya çalışınca çöküyor. Gerçek kurulumda
   (`Program Files`, yönetici olmayan kullanıcı) aynısı olur; günlükler ve yazılabilir veri `%LOCALAPPDATA%`'ya.
6. **PyInstaller önbelleği**: değişen uygulama modülleri pakete girmemişti (`admin`, `app`, `engine` ilk derlemeden
   kalmıştı); `--clean` zorunlu, spec başlığında yazılı.

Çözülen: 1, 2, 6 (paket ve kod); 3 ve 4 karar girdisi (M3.4: Edge `--app` penceresi); 5 M17'ye.
