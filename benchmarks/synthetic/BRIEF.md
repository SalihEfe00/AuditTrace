# Sentetik belge üretimi — talimatlar

Bu klasör, v1 ölçümü (docs/product/domain_scope.md, M2/M3/M4/M5/M8/M9) için **farklı yapay zekâ modellerine**
ürettirilen gerçekçi banka şubesi belgelerini toplar.

## Nasıl kullanılır

1. Aşağıdaki **"Ortak talimat"** bölümünü ve **bir** belge türü görevini (T1–T12) kopyalayıp modele yapıştır.
   Modele bu proje hakkında bilgi verme (üslup ve beklenti sızmasın).
2. Modelin cevabını `incoming/<model-adı>/<görev-kodu>_<sıra>.md` olarak kaydet
   (ör. `incoming/gpt-5/T01_001.md`, `incoming/gemini/T04_003.md`).
3. Her modelden her görev için birkaç örnek yeter; çeşitlilik (model × tür) sayıdan önemlidir.
4. **Bir iki modelin tüm çıktısını hiç açmadan `sealed/<model-adı>/` altına koy**: son doğrulama bunlarla yapılır
   (sistem o modellerin üslubunu hiç görmemiş olur).
5. Örnekleri ölçüme almak için: `python scripts/import_synthetic.py` (reddedilen dosyaları sebebiyle listeler;
   düzeltip tekrar çalıştır), ardından `python scripts/eval_documents.py --write-baseline "<not>"` ile belge
   tabanı bilinçli olarak yeniden yazılır (ayrıntı: `docs/architecture/document_benchmark.md`). Mühürlü örnekler
   yalnızca son doğrulamada `--sealed` ile çevrilir ve ölçülür.

Cevap anahtarı modelin kendi beyanıdır; yanlış hukuki atıf içerebilir. Kusur tanımları (ne eksik / ne çelişkili)
esas alınır; mevzuat atıfları gözden geçirmede düzeltilir.

---

## Ortak talimat (her görevin başına ekle)

```
Türkiye'de bir ticari bankanın şubesinde düzenlenmiş ya da şubeye gelmiş gerçekçi bir belge üretmeni istiyorum.
Kurallar:
- Dil Türkçe; bankacılık pratiğindeki gerçek üslup, kısaltmalar, form düzeni ve alışılmış eksiklikler.
- Tüm kişi, şirket, adres, TCKN, VKN, IBAN, hesap ve telefon numaraları HAYALİ olmalı ama doğru biçimde yazılmalı
  (11 haneli TCKN, 10 haneli VKN, TR ile başlayan 26 karakterli IBAN).
- Belge 2019–2026 arasında bir tarihte düzenlenmiş olsun; tarihleri belgede açıkça yaz.
- Belgeyi ya tamamen usulüne uygun ya da gerçek hayatta görülebilecek 1–3 kusurla üret (karikatürize etme;
  kusur ancak dikkatli bir denetçinin fark edebileceği kadar ince olsun). Hangisini yaptığını kendin seç;
  örneklerin yaklaşık üçte biri kusursuz olsun.
- Önce "=== BELGE ===" başlığı altında yalnızca belgenin kendisini yaz (form, tutanak, yazı, tablo — ne ise).
- Sonra "=== CEVAP ANAHTARI ===" başlığı altında şu JSON'u ver:
  {"belge_turu": "...", "duzenlenme_tarihi": "YYYY-AA-GG", "olay_tarihi": "YYYY-AA-GG",
   "kusursuz": true/false,
   "kusurlar": [{"aciklama": "...", "belgedeki_yer": "belgeden kısa alıntı", "ilgili_mevzuat": "bildiğin kadarıyla",
                 "onem": "yüksek/orta/düşük"}],
   "alanlar": {"tutarlar": [...], "tarihler": [...], "kisiler_ve_rolleri": [...], "imza_onay": [...],
               "kimlik_numaralari": [...], "diger": {...}}}
```

## Görevler

- **T01 Kredi teklif ve onay belgesi** — Şubenin ticari kredi teklifi, limit, vade, faiz, teminatlar,
  yetkili onayları (kredi komitesi / bölge / genel müdürlük yetki seviyesi), istihbarat özeti.
- **T02 Teminat belgeleri** — Kefalet sözleşmesi ya da ipotek / rehin belgesi; kefilin medeni hali, eş rızası,
  kefalet tutarı ve tarihi, el yazısı şartları.
- **T03 Kredi kullandırım dosyası özeti** — Kullandırım talimatı, dekont, kullandırım tarihi ile teminat tesis
  tarihleri, sözleşme tarihi.
- **T04 Hesap açılış ve müşteri tanıma** — Gerçek ya da tüzel kişi hesap açılış formu, kimlik tespiti ve teyidi,
  imza sirküleri / vekaletname, ticaret sicil bilgileri, adres teyidi.
- **T05 Kasa sayım tutanağı** — Şube kasası, ATM veya kıymetli evrak sayımı; sayım ile sistem bakiyesi, sayımı
  yapanlar, imzalar.
- **T06 Nakit / havale / EFT / döviz işlem dekontları** — Yüksek tutarlı ya da birbirini izleyen işlemler, işlemi
  yapan kişi (kendi adına / başkası adına), kimlik bilgisi.
- **T07 Müşteri şikâyeti ve bankanın cevabı** — Şikâyet dilekçesi + banka cevap yazısı (tarihleriyle); konu ücret,
  kart, kredi, mevduat, dijital bankacılık olabilir.
- **T08 Ücret / komisyon tahsilatı** — Müşteriden alınan ücretlerin dökümü, sözleşme ve ücret tarifesi bilgisi.
- **T09 Resmî kurum yazısı ve cevabı** — Mahkeme, icra dairesi, MASAK, BDDK ya da vergi dairesi bilgi talebi / haciz
  yazısı ve şubenin cevabı (tarihleriyle).
- **T10 İç yazışma / talimat / yetki devri** — Şube içi e-posta veya yazı, genel müdürlük talimatı, yetki devri.
- **T11 Teftiş / iç kontrol raporu bulgusu** — Önceki denetimden bir bulgu, alınan aksiyon, kapanış durumu.
- **T12 İşlem listesi (CSV)** — Bir müşterinin 1–3 aylık hesap hareketleri (tarih, tutar, yön, karşı taraf,
  kanal, açıklama); bazıları olağan, bazılarında şüpheli işlem kalıbı olabilir. Belgeyi CSV olarak ver.

### Dosya paketi görevi (çoklu belge)

- **D01 Kredi dosyası paketi** — Aynı müşteri için T01 + T02 + T03 + mali tablo özeti + KKB/istihbarat özeti
  birlikte; belgeler arasında (varsa) tutarsızlık olsun: tarih sırası, imzalayan kişinin yetkisi, tutar farkları.
  Her belgeyi "=== BELGE n: <tür> ===" başlığıyla ayır; cevap anahtarında belgeler arası kusurları ayrıca listele.
