# Kanonik mevzuat corpus modeli: kimlik, kaynak, otorite, geçerlilik

Kod: `mufettis/corpus/` · Testler: `tests/test_corpus_canonical_model.py` ·
Backfill: `scripts/backfill_canonical_metadata.py`

## Amaç

Bugünkü corpus (≈100 belge) bir geliştirme ve doğrulama kümesidir. Hedef, Türkiye mevzuatının
tamamının (kanun, CBK, yönetmelik, tebliğ, genelge, kurul kararı, atıflar) farklı kaynaklardan
ve formatlardan yüklenmesidir. Bu yüzden model hiçbir kuralı mevcut dosyalara, klasörlere
(`mevzuat_deposu`) veya kaynak kimliği listelerine bağlamaz. Güven yalnızca iki yerden gelir:
**kaynak kaydındaki beyan** ve **doğrulanabilir kanıt**.

## Dört bağımsız eksen

| Eksen | Soru | Alanlar | Nereden gelir |
|---|---|---|---|
| Kimlik | Bu hangi düzenleme, hangi sürüm, hangi kopya? | `work_id`, `expression_id`, `manifestation_id`, `identity_basis`, `identity_confidence` | Belgenin kendi resmî numarası, pipeline beyanı (ELI), yayıncının kalıcı kimliği, başlık |
| Kaynak | Kopyayı kim yayımladı, resmî metinle özdeşliği nasıl biliniyor? | `publisher_kind`, `verification` | Kaynak kaydı (`SOURCE_DEFINITIONS` / `source_registry.json`), URL'nin kayıtlı resmî host'a ait olması, içerik özeti eşleşmesi |
| Normatif konum | Belge türü ne, hiyerarşideki yeri ne, kural koyuyor mu? | `instrument_type`, `instrument_basis`, `issuer`, `normative_rank`, `normative_role` | Başlığın baş ismi, resmî numara, parser tür etiketi; rol için parça yapısı |
| Geçerlilik | Yürürlükte mi? | (saklanmaz) | `status`, `effective_date`/`effective_from`, `valid_to`/`effective_to`; okunurken hesaplanır |

Eksenler birbirinden türetilmez. Kanonik alanlar `DERIVATION_INPUTS` beyaz listesindeki alanlardan
türetilir. Dosya yolu ve adı (`local_path`, `relative_path`, `file_name` …) bu listede yoktur; bu
kural testle korunur.

### Kimlik (FRBR / ELI)

- **Work**: soyut düzenleme. Kaynaktan bağımsızdır: `tr/kanun/5464`, hangi kanaldan gelirse gelsin
  aynıdır. Öncelik sırası: pipeline beyanı (`declared_work_id` / `eli`) → resmî numara (yalnızca
  tür kanunsa) → yayıncının kalıcı kimliği (`src/<source_id>/<id>`, kaynağa özgü, orta güven) →
  başlık (`title/<slug>`, düşük güven) → içerik.
- **Expression**: belirli metin sürümü. Pipeline sürüm işareti verdiyse (`declared_version`) o
  kullanılır. Vermediyse içerik özeti kullanılır: yalnızca metni birebir aynı (boşluk, satır, büyük
  küçük harf ve format farkı hariç) kopyalar aynı sürüm sayılır. Tarih tahminiyle sürüm
  birleştirilmez.
- **Manifestation**: somut kopya (`file_hash`).
- Parça yapısı (ör. hesap planı satırları) kimliği **değiştirmez**, yalnızca rolü değiştirir. Index
  kaydı ile parçaları aynı work/expression kimliğini taşır.

### Kaynak

- `publisher_kind`: `primary_official` (Resmî Gazete, mevzuat portalı), `issuing_authority`
  (düzenlemeyi çıkaran kurumun kendi yayını, ör. BDDK), `secondary`, `user_supplied`,
  `internal`, `unknown`. Yerel içe aktarma bir yayıncı değil, bir **kanaldır**
  (`config.INGESTION_CHANNELS`): kullanıcının yüklediği kanun metni "ikincil yayıncı" sayılmaz.
  Yayıncısı bilinmeyen bir kopyadır.
- `verification` (güçlüden zayıfa):
  - `official_channel`: resmî nitelikli kayıtlı kaynağın izinli host'undan alınmış.
  - `fingerprint_matched`: içerik özeti resmî kanaldan alınmış bir kopyayla aynı
    (`reconcile_fingerprints`, backfill sırasında).
  - `self_declared`: metin kendi resmî kimliğini beyan ediyor, kanal doğrulanmamış.
  - `unverified`.

### Normatif konum

- `normative_rank`: Anayasa 100, kanun 90, CBK 85, CB kararı 80, yönetmelik 70, tebliğ 60,
  usul ve esas 55, kurul kararı ve genelge 50, karar 45, rehber 40, hesap planı 30, diğer 20.
- `normative_role`: `rule`, `guidance`, `classification`, `informational`, `unknown`. Parça
  yapısının rolü belge türünden önce gelir: bir tebliğin eki olan hesap planının satırları
  `classification` rolündedir.
- Belge türü gövde metninden tahmin edildiyse (`instrument_basis=document_type`), çıkarıcı kurum
  (`issuer`) iddia edilmez.

## Retrieval: dört aşama

1. **Uygunluk** (`EligibilityPolicy`): evet/hayır kurallarıdır ve alaka hesabından önce uygulanır.
   Analiz planı kaynak kimliği listesi değil, politika üretir: şikâyet ve genel metinlerde
   `exclude_roles=[classification]`, her analizde `exclude_publisher_kinds=[secondary]`. Vektör
   aramasına ön filtre olarak da eklenir (`chroma_clauses`). Chroma `$nin` koşulunda alanı olmayan
   kaydı geçirdiğinden, kanonik alanı olmayan eski parçalar için rolün yapıdan türetilen karşılığı
   (`structure_type $nin [accounting_plan]`) da filtrelenir. Sonuç ayrıca `admits` ile kanonik
   modele göre elenir.
2. **Alaka** (`_relevance_score`): anlamsal benzerlik (0.40), sözcüksel örtüşme (0.22), başlık ve
   yayıncı adı eşleşmesi, sorgudaki açık kimliklerin (kanun no, belge kimliği, hesap kodu)
   eşleşmesi. Kaynağın resmî olup olmaması burada **yer almaz**.
3. **Otorite önceliği** (`_authority_prior`): `0.06 × (½ hiyerarşi + ½ kopya güveni)`. İkincil
   yayıncının kopya güveni en fazla 0.5 olabilir. Üst sınır küçüktür; yalnızca alakası başa baş
   adayları ayırır.
4. **Geçerlilik katmanı** (`_validity_tier`): puana eklenmez. Yürürlükteki belgeler önce gelir,
   ardından henüz yürürlüğe girmemiş olanlar, en son yürürlükten kalkmış olanlar.

Son olarak **kimlik tekilleştirmesi** yapılır: aynı expression'ın farklı kopyalarındaki aynı parça
bir kez seçilir. Bu kural hem `_legal_rerank`'te hem `AnalysisEngine.retrieve_evidence`'ta
uygulanır.

## Yazma ve okuma yolları

- Tüm yeni kayıtlar kanonik alanları taşır:
  - `extract_legal_metadata` index kaydına yazar; içerik özeti tam metinden hesaplanır.
  - `LocalRAG.ingest_text` ve `repair/committer.upsert_candidate_chroma` her parçaya yazar.
- `update_hash_metadata`, bir türetme girdisi değiştiğinde (ör. `law_number` düzeltmesi) kanonik
  alanları yeniler.
- `canonical_view(meta)`: kayıttaki alanlar güncel şemadaysa onları kullanır, değilse aynı
  fonksiyonla türetir. Retrieval kararı, corpus'un ne kadarının backfill edildiğinden bağımsızdır
  (test: `test_legacy_chunks_without_canonical_fields_rank_like_backfilled_ones`).
- Sürüm zinciri (`find_previous_version`) iki kayıt da `work_id` taşıyorsa onu, taşımıyorsa eski
  `document_key`'i kullanır. Farklı kaynaklardaki kopyalar birbirinin sürümü değildir.

## Gelecekteki ingestion pipeline'ı için sözleşme

Yeni bir kaynak eklemek için:

1. Kaynak kaydına `publisher_kind`, `base_url` ve (kendi düzenlemelerini yayımlıyorsa) `issuer`
   girilir. Retrieval kodu değişmez.
2. Pipeline biliyorsa `declared_work_id` (ELI), `declared_instrument_type`, `declared_version`
   ve `issuer` alanlarını verir. Bunlar türetilen değerlerin önüne geçer.
3. Değişiklik ve atıf ilişkileri (ör. değiştiren, yürürlükten kaldıran, atıf yapan) Work
   kimlikleri arasında kurulur. Model bunun için hazırdır; ilişki tablosu henüz yoktur.

Şema değişikliğinde `CANONICAL_SCHEMA` artırılır ve backfill yeniden çalıştırılır. Backfill
idempotenttir: değişiklik yoksa hiçbir şey yazmaz.

## Görünen ad

Atıflarda belgenin **kendi beyan ettiği ad** kullanılır, dosya adı değil:

- `declared_title`: kanunlarda "Kanun Numarası" beyanının üstündeki büyük harfli ad bloğu;
  Resmî Gazete biçiminde yayımlayan kurum satırının ("...Kurumundan:") altındaki blok; mevzuat
  metinlerinde belgenin en başındaki blok (yalnızca bir tür adıyla bitiyorsa). Şema 2'den beri
  kanonik türün de ilk dayanağıdır (`instrument_basis=declared_title`): adın sonundaki baş isim
  ("... İLİŞKİN YÖNETMELİK") dosya adından ve gövdedeki kanun atıflarından güvenilirdir.
  Web sayfasından alınan metinlerde ad büyük harfle yazılmaz ve önünde sayfa öğeleri bulunur
  ("Mevzuat Detay", "Kapat"). Bu durumda ilk satırlardan ad sayılan satır şu koşulları sağlar: bir
  tür adıyla biter; sözcükleri büyük harfle başlar; etiket ("Konu:"), numaralı başlık ("I. …") ya
  da cümle değildir.
- `declared_subject`: resmî yazışmadaki "Konu:" satırı (genelgelerde numarasıyla). Yalnızca
  başlık bir yayıncı numarasıysa kullanılır.

İkisi de aynı işlevle türetilir: `parser.declared_names`, yani yüklemedeki `extract_legal_metadata`
ile aynı onarılmış baş metin (`legal_head`). Backfill daha önce ham önbellek metninden türetiyordu.
Bu yüzden çalıştırıldığında, yüklemede doğru çıkan adların yerine PDF bölünmeli adları
("FA İZ ORANI R İSK") yazıyordu.

## Bilinen sınırlar

- Başlığı yalnızca bir sayı olan ve beyan edilen adı çıkarılamayan belgelerde tür, parser'ın
  gövde tahminine dayanır (`instrument_basis=document_type`). Pipeline beyanı bu eksikliği giderir.
- Aynı kanunun iki yayıncıdaki kopyaları metin olarak birebir aynı değilse ayrı expression sayılır
  (ör. biri konsolide metin, biri değişiklik öncesi). Doğru birleştirme için pipeline'ın sürüm
  işareti vermesi gerekir.
- Saf vektör araması bazı sorgularda ilgili maddeyi aday havuzuna getiremiyordu (ör. kart aidatı →
  5464 Md.24/25). Bu bir otorite değil alaka sorunuydu. Serbest aramada artık BM25 adayları da
  eklenir (`mufettis/rag/lexical.py`). Bu adayların uzaklıkları kayıtlı embedding'lerden hesaplanır
  ve aynı alaka sıralamasından geçer; uygunluk politikası sıralamadan önce uygulanır.
- İddia ile terim paylaşmayan doğru hüküm (ör. "aidat ücreti" ↔ "sözleşmede yer almayan … masraf")
  sözcüksel eşleştiricide bulunamaz. İsteğe bağlı yerel dil modeli doğrulayıcısı
  (`mufettis/analysis/claim_verifier.py`) yalnızca getirilen adaylar arasından seçim yapar ve
  alıntısı birebir doğrulanır. Gerçek bir yerel modelle değerlendirmesi henüz yapılmadı.
