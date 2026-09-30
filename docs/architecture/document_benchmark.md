# Belge benchmark'ı (M4)

`benchmarks/real_corpus_cases.json` ve `lay_cases_*.json` yalnızca "bulgunun dayanağı doğru hüküm mü" sorusunu
ölçer (`mufettis/analysis/benchmark.py`, taban `benchmarks/baseline.json`). Belge benchmark'ı ise belgenin
kendisini ölçer: türü doğru tanındı mı, alanları çıkarıldı mı, uygunluk kararı doğru mu. M5 (türler, alan
çıkarımı), M7, M9 ve M10 bununla kabul ya da ret edilir.

## Parçalar

| Parça | Dosya | Görev |
|---|---|---|
| Etiket kümesi | `mufettis/analysis/document_taxonomy.py` | Bölüm 2'deki 8 gruba bağlı 15 kanonik tür + `bilinmiyor`; BRIEF görev kodu → tür; motorun eski türleri → kanonik kod |
| Vaka biçimi | `mufettis/analysis/document_benchmark.py` | `DocumentCase`, yükleyici ve doğrulama, alan normalleştirme, metrikler, taban |
| Dönüştürücü | `mufettis/analysis/synthetic_import.py`, `scripts/import_synthetic.py` | `benchmarks/synthetic/incoming/<model>/<görev>_<n>.md` → `benchmarks/documents/synthetic_cases.json` |
| Koşucu | `scripts/eval_documents.py` | `understanding` ve `decision` seviyeleri, `--check-baseline`, `--write-baseline`, `--compare`, `--sealed` |
| Taban | `benchmarks/document_baseline.json` | Seviye başına vaka kümesi parmak izi ve bölüm başına sayılar |
| Koruma | `tests/test_document_baseline.py` | understanding her koşuda; decision `MUFETTIS_BENCHMARK_DATA_DIR` ile |

## Vaka

Bir vaka: kimlik, bölüm (`dev` / `holdout` / `sealed`), kaynak (model, görev, dosya, modelin beyan ettiği tür),
kanonik tür (dosya paketinde boş, türler `documents` içinde), metin, düzenlenme ve olay tarihi, `compliant`,
kusurlar (açıklama, belgedeki alıntı, mevzuat, önem, kapsam `belge` / `dosya`) ve beklenen alanlar.

Beklenen alanlar tek biçime çevrilir: tutar `1250000.00 TRY`, tarih `2023-04-05`, TCKN ve VKN yalnızca rakam,
IBAN boşluksuz büyük harf. Cevap anahtarı serbest yazıldığı için ("Limit: 5.000.000 TL", iç içe sözlük, çıplak
sayı) değerler metin içinde aranır. Kimlik numaraları yalnızca `kimlik_numaralari` listesinden alınır, çünkü
başka alanlardaki 10–11 haneli sayılar telefon olabilir. IBAN her yerden alınır. Kişi-rol ve imza / onay
şimdilik yalnızca saklanır; M5'te ölçülür.

Yükleyici tutarsız vakayı sebebiyle reddeder: bilinmeyen tür, "kusursuz" ile kusur listesinin çelişmesi, ISO
olmayan tarih, normalleştirilmemiş alan. Belgede bulunamayan kusur alıntısı vakayı düşürmez. Dönüştürücü bu
durumda uyarır ve alıntıyı boşaltır; böylece tür ve alan ölçümü kaybolmaz.

## Dönüştürücü

- **Tür:** Görev kodundan gelir (T01 → `kredi_teklif_onay`). Modelin yazdığı `belge_turu` uyuşmazsa uyarı verilir.
- **D01 başlıkları:** `=== BELGE n: <tür> ===` başlıklarındaki serbest tür adları anahtar kelimeyle eşlenir. En uzun eşleşme kazanır ve eşleşme kelime başında aranır ("eft" kelimesi "defter" içinde sayılmaz). Tanınmayan bir başlık dosyayı reddettirir.
- **Tolerans:**
  - Markdown başlıkları, kod çiti ve sondaki virgül kabul edilir.
  - Bölüm sınırı yalnızca `BELGE` ve `CEVAP ANAHTARI` işaretleridir; belgenin içindeki başka `=== … ===` satırları metinde kalır.
- **Bölüm:** `sealed/` altındaki dosyalar `sealed` olur. Diğerleri dosya yolunun SHA-256 özetine göre ayrılır: yaklaşık üçte biri `holdout`, gerisi `dev`. Aynı dosya her zaman aynı bölüme düşer.
- **Mühürlü örnekler:** Yalnızca `--sealed` ile ve ayrı bir dosyaya (`sealed_cases.json`) çevrilir. Ekrana yalnızca sayılar basılır; red sebebi ve uyarı gibi içerik ipuçları basılmaz. Koşucu da mühürlü bölümü yalnızca `--sealed` ile, yalnızca özet olarak çalıştırır. Taban mühürlü bölümü içermez.

## Metrikler

- **Belge türü:**
  - Doğru sayısı ve karışıklık tablosu.
  - Dosya paketinde her belge ayrı sayılır: her belge kendi metniyle ayrıca sınıflandırılır.
- **Alanlar:**
  - Tür başına doğru bulunan (tp), fazla (fp) ve eksik (fn) sayıları; bunlardan kesinlik ve duyarlılık.
  - Tahmin yalnızca motorun çıkardığı değerlerden kurulur (`DocumentUnderstanding.entities`, belge tarihi, olay tarihleri); bu değerler normalleştirilir.
  - Belge metni benchmark tarafından yeniden taranmaz; taranırsa motor değil, benchmark'ın kendi ayrıştırıcısı ölçülmüş olurdu.
- **Uygunluk kararı** (yalnızca `decision` seviyesinde):
  - Sonuç üç sınıfa ayrılır: doğru karar, kaçan kusur (kusurlu belge kusursuz sanıldı), yanlış alarm.
  - Bugünkü tanım: en az bir bulgu varsa belge "kusurlu" sayılır.
  - M9'da karar şart kataloğundan okunacak; o zaman tanım değişir ve taban yeniden yazılır.
- **Kırılım:** Her şey bölüm başına ve `all` için hesaplanır; tür başına kırılım da vardır.

## Taban

- **Neyi korur:** Taban sayıları kötüleşmemeli.
  - Büyüdükçe iyi olanlar: doğru tür, alan tp'leri, doğru karar.
  - Küçüldükçe iyi olanlar: alan fp'leri, kaçan kusur, yanlış alarm.
- **Parmak izi:** Taban bir vaka kümesi parmak izi taşır. Vaka eklenir ya da değişirse karşılaştırma yapılmaz; `--write-baseline` ile taban bilinçli olarak yeniden yazılır. Yeni sentetik örnekler geldiğinde de bu adım atılır.
- **`understanding` seviyesi:** Korpus ve model gerektirmez, bir saniyenin altında biter; her test koşusunda korunur.
- **`decision` seviyesi:** Tam hattı çalıştırır, 13 vaka yaklaşık 40 saniye sürer.
  - Bir veri kopyası ister (`MUFETTIS_DATA_DIR`).
  - Test yalnızca `MUFETTIS_BENCHMARK_DATA_DIR` verildiğinde çalışır.
  - Yeniden sıralayıcının adı tabana yazılır; farklı bir sıralayıcıyla koşulursa uyarı verilir.

## Başlangıç seti (M4.6)

Kullanıcının 7–8 modelle ürettiği örnekler gelene kadar bu oturumdaki model (Claude) BRIEF talimatıyla 13 örnek
yazdı (`incoming/claude-opus-5-5/`, T01–T12 + D01; 5'i kusursuz). Bu model sistemi geliştiren model olduğu
için hiçbir zaman mühürlü sayılmaz. Bu setin tabanı (M5 öncesi motor):

| Seviye | Tür | Alan (bulunan / beklenen) | Karar |
|---|---|---|---|
| understanding | 1 / 17 | tutar 64 / 72 (+6 fazla), tarih 33 / 56, TCKN 0 / 12, VKN 0 / 4, IBAN 10 / 10 | — |
| decision | aynı | aynı | 4 / 13: kaçan kusur 8, yanlış alarm 1 |

Görülenler (M5'in girdisi):
- **Türler:** Motor yalnızca şikâyet ve iç yazışmayı tanıyor.
- **Kimlik numaraları:** TCKN ve VKN çıkarılmıyor.
- **Para birimsiz tutarlar:** CSV'deki "49.000,00" gibi tutarlar kaçıyor.
- **Tutar ifadesi satır sonunu aşıyor:** "Fark: 0" satırının sonundaki 0, alt satırdaki "EUR" ile birleşip "0 EUR" tutarı olarak okunuyor.
- **Banknot kupürleri:** "200 TL x 1.240 adet" ifadesindeki kupür tutar olarak alınıyor.
- **Tarihler:** Satır başına yalnızca ilk tarih alınıyor.
