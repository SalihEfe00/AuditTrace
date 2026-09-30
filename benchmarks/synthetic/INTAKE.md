# Sentetik örnek kabul görevi (ayrı oturum için talimat)

Bu dosyayı okuyan ajan, **geliştirme oturumundan bağımsız** olarak kullanıcının başka yapay zekâ modellerinden
topladığı örnek belgeleri kaydeder ve ölçüme alır. Amaç, sistemi geliştiren oturumun örneklerin içeriğini
görmemesi. Kullanıcıyla her zaman Türkçe konuş.

## Görevin

Kullanıcı sohbete bir ya da birden çok model cevabı yapıştırır. Model ve görev şuradan okunur:
- Cevabın ilk satırı `KAYIT: <model adı> <görev>` olur (istemler bunu modele yazdırır,
  `benchmarks/synthetic/prompts/`), ör. `KAYIT: GPT-4o T04`.
- Kullanıcı cevabın üstüne kendisi `<model> <görev> [sıra]` yazdıysa (ör. `gemini D01 2`) o esas alınır.
- İkisi de yoksa ya da çelişiyorsa (modelin kendi adını yanlış bildirmesi olabilir) kaydetmeden önce kullanıcıya sor.
- Aynı model farklı adlarla gelirse (`GPT-4o`, `ChatGPT 4o`) kullanıcıya hangisinin kullanılacağını bir kez sor ve
  sonra tutarlı kullan.

Her cevap için şunları yap:

1. **Adı belirle.**
   - Model adı küçük harfe çevrilir; boşluk ve özel karakterler `-` olur (`GPT 5` → `gpt-5`).
   - Görev kodu iki haneye tamamlanır (`T4` → `T04`).
   - Sıra verilmemişse o model ve görev için klasördeki bir sonraki numarayı ver (`001`, `002`, …).
2. **Kaydet.** Cevabı **değiştirmeden**, olduğu gibi `benchmarks/synthetic/incoming/<model>/<görev>_<sıra>.md` dosyasına yaz. Başındaki `KAYIT:` satırı ve kullanıcının eklediği model / görev satırı dosyaya girmez. Var olan bir dosyanın üzerine asla yazma.
3. **Dönüştür.** Tüm yapıştırılanlar kaydedilince `.venv/Scripts/python scripts/import_synthetic.py` çalıştır.
   - `RED` satırlarını sebebiyle kullanıcıya bildir. Reddedilen dosya kalır; kullanıcı modelden düzeltilmiş cevap isteyip yeniden yapıştırabilir. O zaman eski dosyanın yerine yenisini kaydet.
   - `UYARI` satırlarını kısaca özetle (ör. "3 örnekte kusur alıntısı belgede bulunamadı").
4. **Tabanı yeniden yaz** (vaka kümesi değiştiği için zorunlu):
   - Önce üretim verisinin bir kopyasını kendi geçici (scratchpad) klasörüne al: `data/` klasörünü oraya kopyala (yaklaşık 263 MB). **`data/` içinde hiçbir şeyi değiştirme.**
   - `.venv/Scripts/python scripts/eval_documents.py --write-baseline "<tarih>: <eklenen modeller ve sayılar>"` (anlama seviyesi).
   - `MUFETTIS_DATA_DIR=<kopya> HF_HUB_OFFLINE=1 OMP_NUM_THREADS=2 .venv/Scripts/python scripts/eval_documents.py --level decision --write-baseline "<aynı not>"` (karar seviyesi; birkaç dakika sürebilir, `nice` ile düşük öncelikte çalıştır).
   - Kontrol: `.venv/Scripts/python -m pytest -q tests/test_document_baseline.py tests/test_synthetic_import.py` geçmeli.
5. **Commit et.**
   - `master` dalına, yalnızca şu yolları ekle: `benchmarks/synthetic/incoming/`, `benchmarks/documents/`, `benchmarks/document_baseline.json`.
   - Mesaj: `Synthetic samples - <model listesi>: <n> örnek eklendi, <r> red`. Son satırı: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
6. **Kullanıcıya bildir.** Yalnızca sayıları ver: kaç örnek kaydedildi, kaç tanesi reddedildi (sebepleriyle), toplam vaka sayısı. Ayrıca model × görev tablosu ver: her görev için kaç örnek var, hangi görevler hâlâ eksik (T01–T12, D01).

## Yapma

- Kod, test ya da doküman değiştirme; bu oturumun işi yalnızca örnek kabulüdür. Dönüştürücüde hata görürsen düzeltme, kullanıcıya bildir ("geliştirme oturumuna iletilmeli").
- **Mühürlü örnek:** Kullanıcı bir modelin çıktısını "mühürlü" diye yapıştırırsa o artık mühürlü sayılamaz, çünkü sen gördün. Bunu kullanıcıya söyle ve `incoming/` altına kaydet. Mühürlü örnekleri kullanıcı kendisi, açmadan `benchmarks/synthetic/sealed/<model>/` altına koyar; sen oraya dokunma ve `--sealed` çalıştırma.
- `claude-opus-5-5` adlı klasöre ekleme yapma; o klasör geliştirme oturumunun başlangıç setidir.
- Örnek içeriğini özetleme ya da başka bir yere kopyalama.

Biçimin ayrıntısı: `benchmarks/synthetic/BRIEF.md` (modellere verilen talimat) ve `docs/architecture/document_benchmark.md`.
