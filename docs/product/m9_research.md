# M9 Araştırma Raporu: Belgede Eksik Öğenin Mevzuat Dayanağıyla Tespiti

Tarih: 2026-10-02. Yöntem: web araması + makale özet sayfalarının okunması. Tam metni okunamayan veya yalnızca arama özetinden bilinen noktalar "doğrulanamadı" ile işaretlidir. Ayrıca bazı 2026 tarihli arXiv çalışmaları yeni ve hakemsiz (preprint) olduğundan "ön baskı" olarak anılmıştır.

> **Doğrulama notu (M9.0, 2026-10-02):** Rapor bir alt ajanla (Sonnet) hazırlandı. Üç ana kaynak özet sayfasından ayrıca denetlendi: PolicyGuard (arXiv 2606.32004; başlık, yazarlar, "typed relational logic rules and atom-level extraction questions" + sembolik değerlendirici doğru), NevIR (EACL 2024; sıralama: çapraz-kodlayıcı > geç etkileşim > ikili-kodlayıcı/seyrek, çoğu model rastgele düzeyde ya da altında; yüzde değerleri makale gövdesinden, özette yok) ve Amaral vd. (arXiv 2209.09722; P %89,1, R %82,4, 30 DPA doğru). "DERECHA" adı özet sayfasında geçmiyor, denetlenmedi. "doğrulanamadı" işaretli maddeler tasarım kararına dayanak yapılmaz.


## 0. Ana bulgu

Literatürde "yokluk tespiti" için iki yerleşik kalıp var ve ikisi de M9 tasarımıyla uyumlu:
1. **Gereksinim-merkezli denetim**: Belgeye "bu cümle neyi çağrıştırıyor" diye bakmak yerine, mevzuattan çıkarılmış gereksinim listesinden başlayıp her gereksinim için belgede kanıt aranır; kanıt bulunamazsa "eksik" denir (Amaral vd. DPA çalışması; PolicyGuard; CUAD'ın "bu madde yok" etiketi). Bu, bizim şart kataloğunun kendisidir.
2. **Çıkarım ile karar ayrımı**: Belirsiz bileşen (çıkarım) ile belirlenimci bileşen (kural değerlendirme) ayrılır; kural motoru üç/dört değerli sonuç üretir.

Benzerlik araması yokluğu yapısal olarak bulamaz; bu, bilinen ve ölçülmüş bir sınırlamadır (bölüm 5).

## 1. Sözleşmede eksik madde / öğe tespiti

**1.1 CUAD (Contract Understanding Atticus Dataset)**
- Kaynak: Hendrycks, Burns, Chen, Ball, NeurIPS 2021 Datasets&Benchmarks. https://arxiv.org/abs/2103.06268 ; https://github.com/The-Atticus-Project/cuad
- Ne çözer: 500'ün üzerinde (EDGAR'dan) sözleşmede 41 madde kategorisinin uzmanlarca işaretlenmesi, 13.000'den fazla etiket. Görev, ilgili madde metnini bulmaktır (soru-cevap/aralık çıkarımı). Bir kategori sözleşmede yoksa "cevapsız" kalır; yani "yokluk" bir etiket durumu olarak vardır (bu ayrıntı makale/depo bilgisidir; ayrıntıyı tam metinden doğrulamadım).
- Veri ihtiyacı: büyük, uzman etiketli; İngilizce. Belirlenimci değil (transformer). Yerel çalışır (model indirilirse). Türkçe: doğrudan uygulanamaz; ancak **ölçüm şeması** taşınabilir.
- M9'a katkısı: (a) her kural için "kanıt aralığı" alanı, bulunamazsa açıkça "bulunamadı" durumu; (b) ölçümde yokluk vakalarının ayrı bir sınıf olarak etiketlenmesi (bizim 30 negatif vaka bu amaçla yeniden tanımlanmalı: "madde var / yok / boş bırakılmış").

**1.2 Eksik madde için "Better Call CLAUSE"**
- Choudhury vd., 2025. https://arxiv.org/abs/2511.00340 (arXiv html özeti okundu).
- CUAD ve ContractNLI'den türetilmiş 7.500+ bozulmuş sözleşme; 10 bozulma türü, bunlar arasında "omission" (kritik bilgi/madde çıkarılması), hem yasal hem metin-içi tutarsızlık boyutunda.
- Bulgu: LLM'ler özellikle yasal eksiklikte zayıf (örn. bir NLI alt kümesinde GPT-4o-mini yasal omission F1 yaklaşık %31,6; özetten alındı).
- M9'a katkısı: **sentetik bozma ile ölçüm**. Elimizdeki 13 sentetik belgeyi, sağlam bir belgeden "öğe silme/boş bırakma" mutasyonlarıyla (eş rızasını sil, imzayı boş bırak, tutarı sil, tarihi sırasız yap) otomatik çoğaltmak; böylece binlerce etiketsiz-maliyetli yokluk vakası elde edilir. Beklenen sonuç mutasyondan bellidir.

**1.3 Gizlilik politikalarında eksik madde**: Torre vd. 2020 (SVM ile eksik madde tespiti; P 0,85 / R 0,96 raporlanıyor; yalnızca arama özeti, makaleye ulaşılmadı, **doğrulanamadı**). Aynı fikir: madde kategorisi sınıflandırıcısı + "hiçbir cümle o kategoriye düşmedi = eksik".

**1.4 ContractNLI** (Koreeda & Manning, Findings of EMNLP 2021; ayrıntıyı arama özetinden aldım, makale doğrulanmadı): hipotez ↔ sözleşme için Entailment / Contradiction / NotMentioned + kanıt aralığı. Üç değerli yapı bizim "karşılandı / karşılanmadı / belirsiz" sınıflarına çok yakın; "NotMentioned" = bizim "belirsiz/manuel" ile "karşılanmadı" arasındaki farkı zorunlu kılar (bkz. öneri 2).

**1.5 AGB-DE** (Alman tüketici sözleşmesi madde değerlendirme korpusu, arXiv 2406.06809): hukuki değerlendirmeli, hukuki dayanaklı madde veri seti; Almanca/medeni hukuk; ayrıntılı sonuçları çekemedim, **doğrulanamadı**. Türk medeni hukukuna yakın bir sistem olduğu için örnek şema olarak bakılabilir.

## 2. Mevzuattan yükümlülük çıkarımı ve otomatik uyum denetimi

**2.1 LegalRuleML (OASIS)**
- Kaynak: LegalRuleML Core Spec v1.0, OASIS Standard, 2021. https://docs.oasis-open.org/legalruleml/legalruleml-core-spec/v1.0/os/legalruleml-core-spec-v1.0-os.html
- Ne çözer: Normları makine-okunur biçimde ifade etme standardı; deontik özellikler (Obligation, Prohibition, Permission, Right), ihlal ve uyum (Compliance/Violation) kavramları, kural ↔ kaynak madde bağı (Association/Authority/Source), kural geçerlilik zamanı (temporal).
- Veri ihtiyacı: elle modelleme; belirlenimci; XML, tamamen çevrimdışı; Türkçeye dilden bağımsız şema olarak uygulanabilir (metinleri Türkçe kalır).
- Önemli kavram: spesifikasyona göre Obligation, yerine getirilmezse ihlal doğurur; yani "olmaması gereken yokluk" = ihlal. Zayıf izin (weak permission) = zıt yükümlülüğün yokluğu.
- M9'a katkısı: tam XML'e geçmeden **şemayı ödünç alma**: katalog kuralına `norm_türü` (yükümlülük/yasak/izin), `bearer`/yükümlü taraf, `geçerlilik_tarihi` (olay tarihine göre madde sürümü; M7 ile uyumlu), `kaynak_madde` ve `istisna` bağlantısı. Gerekirse sonradan LegalRuleML'e dışa aktarım yapılabilir.

**2.2 İş süreçlerinde uyum kontrolü: Governatori & Sadiq**
- Rule Based Business Process Compliance (Governatori, Shek; CEUR Vol-874): https://ceur-ws.org/Vol-874/paper5.pdf ; Regorous aracı (arama özetleri).
- FCL (Formal Contract Language): defeasible mantık + ihlal deontiği; **achievement** (belirli bir noktaya kadar bir şey yapılmış olmalı) ve **maintenance** (bir süre boyunca koşul korunmalı) yükümlülükleri ayrılır; ihlal ve telafi zincirleri (reparation).
- Belirlenimci, yerel. Türkçe: dilden bağımsız mantık.
- M9'a katkısı: kontrol türlerimizdeki "tarih sırası" ve "eşik" kontrolleri aslında achievement/maintenance kalıplarıdır; örn. "eş rızası, kefalet akdinden önce/aynı anda verilmiş olmalı" = achievement. Ayrıca **istisna/üstünlük (defeasible)**: "genel kural + istisna" (ör. TBK 584'te eş rızası gerekliliğinin istisnaları ve 583'teki şekil şartları) katalogda `istisna_kural` alanıyla modellenmeli; yoksa yanlış "karşılanmadı" üretilir.

**2.3 Yükümlülük çıkarımı**
- Sleimi, Sannier, Sabetzadeh, Briand, Dann, "An automated framework for the extraction of semantic legal metadata from legal texts", Empirical Software Engineering 2021. https://link.springer.com/article/10.1007/s10664-020-09933-5 (özet okundu): hukuk metninden bağımlılık ayrıştırıcı + elle kurallarla yükümlü, eylem, koşul, istisna gibi anlamsal üst veri çıkarımı.
- "An Obligations Extraction System for Heterogeneous Legal Documents", CLiC-it 2021: https://aclanthology.org/2021.clicit-1.57.pdf (başlık/özet düzeyi).
- M9'a katkısı: maddeden katalog kuralı çıkarımı otomatik yapılabilir ama **v1'de elle yazılması önerilir** (kefalet için ~15-25 kural). Çıkarım şeması (yükümlü, eylem, nesne, koşul, istisna, süre) katalog kayıt şablonuna alınmalı; yarı otomatik öneri sonraya.

**2.4 DPA'nın GDPR'a uyumu: DERECHA**
- Amaral, Azeem, Abualhaija, Briand, "NLP-based Automated Compliance Checking of Data Processing Agreements against GDPR", IEEE TSE 2023. https://arxiv.org/abs/2209.09722 (arXiv html okundu).
- Çalışma en yakın akraba: GDPR'dan avukatlarla 45 "shall" gereksinimi çıkarılmış; her biri anlamsal çerçeve (predicate + rol argümanları) olarak temsil edilmiş; sözleşme cümleleri otomatik çerçeveye çevrilip gereksinimle hizalanmış; eşleşme derecesi düşükse "eksik bilgi" raporu üretilmiş. 30 DPA üzerinde P %89,1, R %82,4; ilk uyarlama için avukat denetimi gerekiyor. Eğitim verisi az olduğundan ML yerine kural tabanlı.
- Veri ihtiyacı: 54 gerçek DPA, 7.048 cümle etiketi (bizim ölçeğimizin çok üstü). İngilizce.
- M9'a katkısı: (a) **gereksinim başına eksik-argüman raporu**: bulgu "madde 584 ihlal" değil "madde 584: eş rızası alanı boş; beklenen: ad-soyad + imza + tarih" biçiminde, hangi argümanın eksik olduğunu söylemeli; (b) "eşleşme derecesi" yerine bizde alan bazlı tam kontrol (belirlenimci) daha sade ve yeterli; (c) kendi ölçüm yöntemleri: P/R'yi **ihlal sayısı** üzerinden hesaplama (ihlal = gereksinim×belge çifti).

## 3. Form / belge doğrulama

**3.1 Anahtar–değer çıkarımı ve boş alan**
- FUNSD (Jaume, Ekenel, Thiran, 2019): 199 taranmış form, 9.707 anlamsal varlık, soru/cevap/başlık ilişkileri. https://arxiv.org/abs/1905.13538 (arama özeti). KVP10k (arXiv 2405.00505): iş belgelerinde anahtar-değer. LayoutLM/LiLT (arXiv 2202.13669, dilden bağımsız): yerleşim farkındalıklı modeller.
- Ne çözer: etiket ("Eş Adı Soyadı") ile değer çiftini yerleşimle eşler. **Boş değer = "etiket var, cevap yok"**; bu durum çıkarım çıktısının açık bir durumu olmalıdır.
- Çevrimdışı çalışır (modeller indirilir), ama model tabanlı (belirlenimci inferans mümkün; tohum/sürüm sabitlenirse). Türkçe: LiLT dilden bağımsız; Türkçe form verisi ayrıca gerekir (doğrulanamadı: Türkçe etiketli form seti bulunamadı).
- M9'a katkısı: çıkarılan her alan için üç durumlu **alan durumu**: `dolu(değer, güven, konum)` / `boş(etiket bulundu, değer yok)` / `bulunamadı(etiket bile yok)`. Kural motoru bu üçünü ayırır: boş → büyük olasılıkla "karşılanmadı"; bulunamadı → "belirsiz/manuel" (OCR kaçırmış olabilir). Bizim örnekteki "........" satırı tipik `boş`: noktalı-çizgi/alt çizgi kalıpları boş değer işaretidir ve kural tabanlı (regex) tespit edilebilir.

**3.2 İmza var/yok tespiti**
- Tobacco800 + Roboflow imza seti ile eğitilmiş YOLOv8s imza dedektörü (Hugging Face model kartı, tech4humans/yolov8s-signature-detector; 2.819 görüntülük birleşik set; model kartı verisi). Ayrıca Engin vd. 2020, "Offline Signature Verification on Real-World Documents", https://arxiv.org/abs/2004.12104 (yalnızca arama sonucu).
- Ne çözer: sayfada imza bölgesi var mı. İmza *kimin* değil *var mı* sorusu; doğrulama (kimin imzası) ayrı ve bizim kapsamımız dışı. Yerel çalışır, GPU/CPU kısa; belirlenimci inferans mümkün; Türkçeye bağımlı değil (görsel).
- Sınırlar: İnce kalem/silik imza, mühür-imza karışması. Bu yüzden çıkış **olasılıklı**; kural motoru eşiğin altında "belirsiz/manuel" demeli.
- M9'a katkısı: `imza_var` alanı `{evet, hayır, belirsiz}` + bölge koordinatı; kontrol türü "imza alanı" için **etiket-yakınlık kuralı**: imza etiketi ("İmza:") yanında/altında dedektör kutusu var mı. Belirsizlik bandı katalogda tanımlı eşik olarak kalsın.

## 4. Kural + model karma (neuro-symbolic) ve denetim otomasyonu

- **PolicyGuard** (Malik, Singh, Azad, 2026, ön baskı): https://arxiv.org/abs/2606.32004. Politikayı yapılandırılmış "rulecard" → tipli ilişkisel mantık kuralı → atom düzeyinde evet/hayır çıkarım soruları; LLM yalnızca atomları belgeden doğrular, belirlenimci değerlendirici (Z3) karar verir. 475 politika-sözleşme kararında doğruluk %93,4, uyumsuzluk sınıfı F1 %73,7; tekrar çalıştırma tutarlılığı baz çizgilerden belirgin yüksek. Kendi sınırlama notu: yokluk birinci sınıf bir temsil değil, hedefli sorularla yakalanıyor. Bu bizim M9'un "yokluğu birinci sınıf durum yap" önerisini destekler (bizdeki fark: LLM yok, çıkarım küçük modeller/kural).
- **Neuro-Symbolic Compliance: LLM + SMT çözücü** (arXiv 2601.06181, finansal yasal analiz): LLM-artı-çözücü mimarisi; ayrıntıları okumadım (yalnızca özet).
- **Deterministic Fuzzy Triage** (Atri, 2026, arXiv 2603.07390): belirlenimci bulanık mantık ve "yetersiz kanıt" sınıfı; özetim sığ, **doğrulanamadı**; yalnızca "belirlenimci üç/dört sınıf" fikrini destekleyici dolaylı kaynak olarak anılabilir.
- Bankacılık/kredi dosyası kontrol listesi otomasyonuna dair hakemli bir çalışma bulamadım (aramada ticari bloglar çıktı; güvenilir değil). **Doğrulanamadı / bulunamadı.** Bu, projenin boşluk iddiasını destekler ama "literatürde yok" diye kesin yazılmamalı.
- Ortak ders: çıkarım hatası kural motoruna yayılır. PolicyGuard ve DERECHA'da hataların çoğu çıkarım katmanından gelir; bu yüzden **çıkarım ve kural katmanları ayrı ölçülmeli** (bölüm öneriler, madde 4).

## 5. Alma (retrieval) sistemlerinde yokluk ve olumsuzluk

- **NevIR** (Weller, Lawrie, Van Durme, EACL 2024): https://aclanthology.org/2024.eacl-long.139.pdf (arXiv html özeti okundu). 2.556 çift; belge çiftleri yalnızca olumsuzlukla farklı. İkili-kodlayıcılar (bi-encoder) %6,8–11,1, seyrek modeller %2–8,7, ColBERT %13–19,7, çapraz-kodlayıcılar %22,4–50,6 (MonoT5-3B %50,6); rastgele taban %25; insan %100. Bi-encoder temsilleri olumsuzluk eklense de neredeyse aynı. Çözüm: olumsuzluk verisiyle ince ayar (ama klasik benchmark'ta kayıp riski).
- **ExcluIR** (dışlayıcı sorgular): NevIR ile birlikte anılan başka kıyas kümesi; birinde ince ayar diğerine güvenilir biçimde aktarılmıyor. Aynı arama özetinden; makaleyi açmadım (başlık/yazar **doğrulanamadı**). Ayrıca "Reproducing NevIR" (arXiv 2502.13506) ve negasyon taksonomisi (arXiv 2507.22337) var.
- Çıkarım: bi-encoder + BM25 (bizim aday arama) olumsuzlukta yapısal olarak kördür; bizim cross-encoder yeniden sıralayıcı biraz daha iyidir ama yetersizdir. **Yokluk problemi bir arama problemi değil, bir denetim listesi problemidir.** Bu yüzden M9'un listeden-başlayan yapısı doğru.
- LLM'ler de yokluğu zor yakalıyor: CLAUSE bulguları (1.2) ve hukuki LLM'lerde "abstention" başarısızlığı üzerine arama özeti (kaynak adı net değil, **doğrulanamadı**).

## 6. Önerilen tasarım eklemeleri (öncelik sırasıyla)

1. **Üç durumlu alan çıkarımı** (dolu / boş / bulunamadı) ve kural motorunda farklı muamele. (FUNSD mantığı, PolicyGuard sınırlaması, ContractNLI NotMentioned.) Çözdüğü şey: bizim ana vakamız ("........" satır). Maliyet düşük: regex boş-değer kalıpları ("\.{3,}", "_{3,}", "…").
2. **Sonuç sınıfı semantiği**: `karşılandı` yalnızca olumlu kanıtla; `karşılanmadı` yalnızca etiket-var-değer-boş veya açık çelişki ile; `bulunamadı` → `belirsiz`; `manuel` insan gerektiren kural. "Yokluk = ihlal" yalnızca katalogda `yokluk_ihlal: true` işaretli yükümlülüklerde (LegalRuleML Obligation semantiği).
3. **Katalog şeması genişletmeleri** (LegalRuleML/Governatori'den): `norm_türü`, `yükümlü`, `kontrol_türü`, `kanıt_aralığı` (bulunamadıysa null), `istisna_kural`, `geçerlilik_tarihi`, `achievement/maintenance` (tarih sırası ve süre kontrolleri için). Çıktıda **eksik argüman** raporu (DERECHA tarzı): hangi alt öğe eksik.
4. **Çıkarım ve kural katmanlarının ayrı ölçülmesi**: (a) alan çıkarımı doğruluğu (dolu/boş/yok sınıflandırması, F1), (b) kural motoru doğruluğu (altın alanlar üzerinde, çıkarım hatasız), (c) uçtan uca. Aksi halde hata kaynağı bilinemez (PolicyGuard/DERECHA deneyimi).
5. **Sentetik mutasyonla yokluk testi**: sağlam sentetik belgelerden otomatik öğe silme/boş bırakma/tarih bozma üretimi (CLAUSE yaklaşımı); beklenen bulgu mutasyondan türetilir. Gerçek belge azlığını kısmen giderir. Küçük ölçekli: 13 belge × ~8 mutasyon.
6. **İmza için belirsizlik bandı ve etiket-yakınlık kuralı**; dedektör kullanılırsa yalnızca "var mı?" sorusu, eşik altı → manuel.
7. **Arama katmanının rolünü daraltma**: aday arama yalnızca "bu belge hangi katalog bloklarını ilgilendirir" yönlendirmesi ve yeni/bilinmeyen durumlarda öneri için kalsın; bulgu kararı kataloğundan gelsin. NevIR sonuçları bunu destekliyor.
8. **Kural çıkarım şablonu** (Sleimi vd.): yükümlü / eylem / nesne / koşul / istisna / süre. Elle yazım; yarı otomatik öneri v1.1.

## 7. Tasarımımızda zaten karşılananlar

- Veri dosyası olarak katalog, madde ve alıntı bağlantısı = kural-kaynak bağı (LegalRuleML Source/Authority fikri).
- Belirlenimci kural motoru, model yalnızca alan çıkarımında = PolicyGuard/DERECHA ayrımı.
- "Eksik alan asla karşılandı olmaz" = yokluk güvenliği; PolicyGuard sınırlamasına karşı bilinçli çözüm.
- Dört sonuç sınıfı (karşılandı/karşılanmadı/belirsiz/manuel) = ContractNLI üç sınıfı + insan; fuzzy triage "yetersiz kanıt" sınıfı ile uyumlu.
- Kontrol türü çeşitliliği (alan var / değer / tarih sırası / eşik / metin kalıbı / insan) = FCL achievement/maintenance + DERECHA argüman kontrollerinin sade karşılığı.

Henüz karşılanmamış: üç durumlu alan çıkarımı, eksik-argüman raporu, istisna/geçerlilik alanları, katmanlı ölçüm, sentetik mutasyon testi, imza belirsizlik bandı.

## Kaynak listesi
- Hendrycks vd., CUAD, NeurIPS 2021: https://arxiv.org/abs/2103.06268
- Choudhury vd., Better Call CLAUSE, 2025: https://arxiv.org/abs/2511.00340
- Amaral vd., DPA-GDPR uyum denetimi (DERECHA), 2023: https://arxiv.org/abs/2209.09722
- Malik vd., PolicyGuard, 2026 (ön baskı): https://arxiv.org/abs/2606.32004
- Weller vd., NevIR, EACL 2024: https://aclanthology.org/2024.eacl-long.139.pdf
- OASIS LegalRuleML Core 1.0: https://docs.oasis-open.org/legalruleml/legalruleml-core-spec/v1.0/os/legalruleml-core-spec-v1.0-os.html
- Governatori & Shek, Rule Based Business Process Compliance: https://ceur-ws.org/Vol-874/paper5.pdf
- Sleimi vd., Semantic legal metadata, EMSE: https://link.springer.com/article/10.1007/s10664-020-09933-5
- CLiC-it 2021 obligations extraction: https://aclanthology.org/2021.clicit-1.57.pdf
- FUNSD: https://arxiv.org/abs/1905.13538 ; KVP10k: https://arxiv.org/abs/2405.00505 ; LiLT: https://arxiv.org/abs/2202.13669
- İmza dedektörü model kartı: https://huggingface.co/tech4humans/yolov8s-signature-detector ; Engin vd.: https://arxiv.org/abs/2004.12104
- AGB-DE: https://arxiv.org/abs/2406.06809 (zayıf doğrulama)
- Neuro-symbolic SMT: https://arxiv.org/abs/2601.06181 ; Fuzzy triage: https://arxiv.org/abs/2603.07390 (zayıf doğrulama)
- ContractNLI (Koreeda & Manning 2021), Torre vd. 2020, ExcluIR: yalnızca arama özetinden; doğrulanamadı.
