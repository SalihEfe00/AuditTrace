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

## M5 sonrası (2026-09-30)

Alan çıkarıcısı (`mufettis/analysis/field_extraction.py`, `DocumentUnderstanding.fields`) ve şube belge türü
sınıflandırıcısı (`mufettis/analysis/branch_classifier.py`, `DocumentClassification.branch_type`) eklendi.
Cevap anahtarına hesap numarası, oran ve "Ad Soyad - rol" biçiminde kişi alanı eklendi (M5.1); başlangıç
anahtarları "belgede geçen tüm tutarlar" kuralına göre tamamlandı (bakiye, kupür).

| Seviye | Tür | Alanlar | Karar |
|---|---|---|---|
| understanding | 17 / 17 | tutar 90 / 90, tarih 56 / 56, TCKN 12 / 12, VKN 4 / 4, IBAN 10 / 10, oran 5 / 5, kişi 38 / 38; fazla 0 | — |
| decision | aynı | aynı | 4 / 13 (değişmedi; karar M9'da şart kataloğundan gelecek) |

Bu sonuç, sistemi geliştiren modelin yazdığı örneklerde alınmıştır ve iyimserdir. Asıl ölçüm, kullanıcının
başka modellerle ürettiği örnekler geldiğinde yapılır; aradaki fark M5'e alt adım olarak eklenir.

- **Kimlik numaraları:** TCKN etiketle ya da denetim hanesiyle bulunur. VKN yalnızca etiketle bulunur, çünkü rastgele 10 haneli sayıların yaklaşık %10'u denetim hanesini tesadüfen tutar. Telefon, IBAN ve hesap numarasının içindeki rakamlar kimlik sayılmaz.
- **Para birimsiz tutar:** Yalnızca tutar adlı bir tablo sütununda (CSV, noktalı virgüllü, Markdown) ya da "Toplam: 9.280,43" gibi bir etiketin yanında tutar sayılır. Oran ve adet etiketlerinin yanındaki sayılar tutar sayılmaz.
- **Kişiler:** Rol etiketinden, unvan + addan, ad + rolden ya da kimlik numarasıyla anılan addan bulunur. Kurum ve birim adları ayıklanır. Not: metin düzeltici "A.Ş."yi "A. Ş." biçimine çeviriyor; şirket ayıklaması bu yazımı da tanır.
- **Belge türü:** İşaret tablosu `document_taxonomy.SIGNALS`'dadır.
  - Başlık ifadeleri yalnızca ilk satırlardaki başlık gibi görünen satırlarda aranır, çünkü kısa bir dilekçenin cümleleri de ilk satırlardadır.
  - Eski şikâyet / iç yazışma kararı tek başına bir türü taşır; başka türün belirgin işareti varsa o tür kazanır.
  - Puan düşükse ya da ilk iki aday arasındaki fark azsa tür "bilinmiyor" olur.
- **Gerileme:** Tutar ve IBAN varlıkları artık alan çıkarıcısından geliyor. Gerçek korpus tabanı 32 / 1 / 0 korunuyor.

## Olay tarihi (M7.1)

`DocumentUnderstanding.event_date` belgenin anlattığı işlemin tarihidir (`mufettis/analysis/event_date.py`).
Dayanağı `event_date_basis` alanında tutulur: etiket, düzyazı, işlem tablosu, düzenlenme tarihi ya da yok.

Her tarih alanı bağlamından bir rol alır:
- **olay:** "İşlem / Kullandırım / Tebliğ Tarihi:" etiketleri, düzyazıda "… tarihinde", işlem tablosu satırı.
- **düzenlenme:** Başlıktaki ilk "Tarih:", "Rapor / Teklif Tarihi:".
- **diğer:** Vade, doğum, sorgu, dönem, "… tarihli" başka bir belgeye atıf, "… itibarıyla".

Olay tarihi en erken olay rolündeki tarihtir; yoksa düzenlenme tarihi alınır.

Benchmark ölçütü `event_date_ok`: tahmin cevap anahtarındaki olay tarihine 30 gün içinde yakınsa doğru sayılır.
Bu tolerans bilinçli: dayanak seçimi (M7.4) için olayın dönemi yeterli, çünkü mevzuat sürümleri yıllar içinde
değişir. Kusurlu işlemin günü (ör. altı aylık ücret tablosundaki tek bir satır) belgeye özgüdür ve kurala
dökülmez.

Başlangıç setinde sonuç 12/13. Tek kaçan vaka, 6 aylık ücret tablosunda kusurlu satırın beklendiği vaka.
