# İddia-kanıt eşleştirme

Belgedeki her iddia için mevzuattan tek bir dayanak hüküm seçilir. Yeterince güvenilir bir dayanak yoksa "bulgu yok" denir.

Kod:
- `mufettis/analysis/engine.py`: `retrieve_claim_candidates`, `_gated_ranking`, `_pick`
- `mufettis/rag/engine.py`: `LocalRAG.claim_candidates`
- `mufettis/rag/fusion.py`
- `mufettis/rag/reranker.py`

Ölçüm:
- `scripts/eval_accuracy.py`
- `benchmarks/real_corpus_cases.json`
- `benchmarks/baseline.json`

## İlke

**Yanlış atıf, kaçan bulgudan daha kötüdür.** Denetçi yanlış hükme dayanan bir bulguyu raporda görürse sisteme güveni kalmaz. Kaçan bulguyu ise kendi incelemesiyle bulur. Bu yüzden bütün eşikler yanlış pozitifi sıfırda tutacak biçimde seçilir.

## Akış

1. **Belge düzeyi havuz** (`retrieve_evidence`): Başlık, konu ve iddia sorgularının sonuçları sıra sıra harmanlanır. Hukuki yeniden sıralama (`_legal_rerank`) uygulanır.
2. **İddia adayları** (`LocalRAG.claim_candidates`): Her iddia kendi cümlesiyle aranır.
   - Aday üretimi: BM25 (Türkçe kök anahtarlı) ve vektör sıraları ağırlıklı RRF ile birleştirilir (BM25 1.0, vektör 0.5, k=10), ilk 30 aday alınır.
   - Hukuki yeniden sıralama bu adımda **uygulanmaz**; adaylar ham alaka sırasıyla gider.
3. **Kelime kapısı** (`_rank_evidence_for_claim`): Kanıtın tek bir cümlesi, iddiayla en az iki içerik kökü paylaşmalıdır. Bu köklerden en az biri iddia cümlesinden gelmeli ve ayırt edici olmalıdır (ya da ortak bir eşdizim olmalıdır). Kapı **sıralama yapmaz, yalnızca eler**.
4. **Yeniden sıralayıcı** (cross-encoder, `mmarco-mMiniLMv2-L12-H384-v1`): Kapıyı geçenler iddia cümlesiyle birlikte okunur ve puanlanır. En yüksek puanlı aday seçilir.
5. **Eşik** (`reranker.MIN_SCORES`): Seçilen adayın puanı modelin eşiğinin altındaysa bulgu çıkmaz.
   - Eşik modele özgüdür, model değişirse yeniden ölçülür.
   - Yalnızca geliştirme (dev) vakalarıyla seçilir: negatiflerin en yüksek puanı -2.57, doğruların en düşüğü -1.74; eşik aradaki orta nokta, -2.15.
6. **İsteğe bağlı dil modeli doğrulayıcısı** aynı sıralamayı (`_gated_ranking`) kullanır.

Model kurulu değilse sistem eski davranışa döner:
- Geniş iddia havuzu kullanılmaz.
- Seçim yalnızca kelime sıralamasıyla yapılır.
- Analiz sonucunda uyarı gösterilir (`MISSING_MODEL_WARNING`).

Geniş havuz yalnızca onu doğru okuyabilen bir sıralayıcıyla birlikte kullanılır. Kelime sıralaması geniş havuzda yanlış pozitif üretir: ölçümde dev negatiflerinde 2/11'den 5/11'e çıktı.

## Ölçümle alınan kararlar

Vakalar:
- **dev:** kural ve eşik seçimi için.
- **holdout:** yalnızca doğrulama için.
- **ho2-:** sonradan eklenen, sistem çıktısına bakılmadan madde metninden etiketlenmiş vakalar.

| Karar | Ölçüm |
|---|---|
| Geniş havuz + yeniden sıralayıcı + eşik | Eski sistem: holdout 6/14 doğru, 7 yanlış dayanak. Yeni sistem: 10/14 doğru, 0 yanlış dayanak, 0 yanlış pozitif. ho2- vakalarında 3/10'dan 5/10'a; yanlış dayanak 4'ten 0'a. |
| Vektör modeli değiştirilmedi (MiniLM kaldı) | e5-small aday recall'unu artırmadı. Tüm korpusu yeniden gömmek gerekirdi. |

### Reddedilen deneyler

Hepsi dev'de kötüleştirdi ya da gürültü düzeyinde kaldı.

**Seçim tarafı:**
- Aday sırası ile yeniden sıralayıcı sırasının RRF birleşimi
- Geçici madde cezası
- Kelime kapısını kaldırmak ya da yüksek puanlıya kapıyı atlatmak: dev'e yanlış pozitif ekledi.
- Kanıtı pencerelere bölüp en yüksek pencere puanını almak: dev 12'den 10'a düştü.
- Yan cümle düzeyinde iddia algılama: +1 doğru, +3 yanlış dayanak.
- Birinci kişi olgu yüklemleri ("yatırdım", "bilemedim", "alamıyorum") iddia sayılsın: dev değişmedi, ancak holdout'ta +1 doğru, +2 yanlış dayanak çıktı. Nedenleri: yeni iddialardan birinin doğru hükmü havuzda yok (5411 Md.60); ötekinde model komşu maddeyi seçiyor (5464 Md.19 yerine Md.22). Bu yüzden iddia algılamayı genişletmek, seçim güçlenmeden yapılmamalı.

**Arama tarafı:**
- `_legal_rerank` içinde BM25 karışımı
- IDF ağırlıklı seçim
- LocalRAG içinde RRF

## Kalite tabanı

`benchmarks/baseline.json`, üretim korpusu ve bu model için tabanı tutar.

Kurallar:
- Doğru sayısı düşmemeli.
- Yanlış dayanak, yanlış pozitif ve yanlış bulgu sayıları artmamalı.

Komutlar:
- Denetim: `python scripts/eval_accuracy.py --split all --check-baseline`. Kötüleşme varsa çıkış kodu 1 döner.
- İsteğe bağlı test: `MUFETTIS_BENCHMARK_DATA_DIR=<data kopyası> python -m pytest tests/test_accuracy_baseline.py`.
- Tabanı güncellemek bilinçli bir işlemdir: `--write-baseline "<not>"`.

Yeniden sıralayıcı kapalıyken denetim 10 metrikte başarısız olur: holdout doğru 15'ten 9'a düşer, yanlış pozitif 0'dan 6'ya çıkar. Model doğruluğun temelidir.

## Model kurulumu

```bash
python scripts/install_reranker_model.py            # durum
python scripts/install_reranker_model.py --download # sabit sürüm, özet doğrulamalı
python scripts/install_reranker_model.py --from D:/model   # internetsiz makine
```

- **Sabitler:** Sürüm (`MODEL_REVISION`) ve ağırlık özeti (`MODEL_WEIGHTS_SHA256`) sabittir.
- **Güvenli kurulum:** Dosyalar önce geçici klasöre alınır ve doğrulanır, ardından yerine taşınır. Bozuk bir kaynak kurulu modeli bozmaz.
- **Donanım:** Model CPU'da 2 iş parçacığıyla çalışır ve iddia başına yaklaşık 1–2 saniye sürer.
  - Daha büyük çok dilli modeller (ör. bge-reranker-v2-m3, ~2.3 GB) i5 ve 8 GB RAM hedefinde iddia başına onlarca saniye sürer.
  - Bu yüzden şimdilik kullanılmıyor. Denenecekse yalnızca güçlü makinelerde isteğe bağlı bir katman olmalı.

## Bilinen sınırlar

- **Model:** Türkçe hukuk metninde aynı kanunun komşu maddelerini karıştırabiliyor (5464 Md.8 ile Md.10 gibi). Puanlar arasındaki fark küçük olduğunda seçim kararsız kalıyor.
- **Kelime kapısı:** Eşanlamlı ifadelerde doğru hükmü engelleyebiliyor. Örnek: "işlem yapmadığım hesap" ile "zamanaşımı" (5411 Md.62).
- **İddia algılama:** Yan cümledeki olguları ve birinci kişi anlatımını ("paramı yatırdım, geri alamıyorum") kaçırabiliyor. Genişletme denendi ve reddedildi (yukarıya bakın). Bu metinlerde hiç iddia çıkmadığı için bulgu da çıkmıyor; güvenli ama eksik bir davranış.
