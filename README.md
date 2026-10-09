# AuditTrace

Banka şubesi kredi dosyaları ve teminat belgeleri için kurallı kontrol aracı. Belgeleri okur, belirlenimci
kurallarla denetler ve her bulguyu ölçüt, belgeden alıntılanan kanıt ve öneriyle verir. Tamamen yerel çalışır;
belgeler bilgisayardan çıkmaz.

> Kişisel portföy ve öğrenme projesidir; bir banka ürünü değildir. Kuralların hukuki gözden geçirmesi
> yapılmamıştır. Depodaki tüm örnek belgeler ve kişi, kimlik, IBAN bilgileri uydurmadır.

*English: AuditTrace is a local, rule-based checker for bank-branch credit files and collateral documents
(Turkish legislation). Every finding comes with its criterion, a verbatim quote from the document and a
recommendation. Personal portfolio project, not a bank product.*

## Neler yapar

- **Kural kontrolü:** 43 kural, 7 belge türü (kredi onayı, kullandırım talimatı, ipotek, kefalet sözleşmesi,
  imza sirküleri vb.). Her kontrolün sonucu: bulgu, uygun, karar verilemedi ya da eksik belge yüzünden yapılamadı.
- **Kanıt:** Bulgunun ölçütü (mevzuat maddesi ya da iç kontrol şartı) ve belgedeki satırıyla birebir alıntı.
- **Sorular:** Belgeden anlaşılamayan bilgi tahmin edilmez, müfettişe sorulur; cevap kanıta "müfettiş beyanı"
  olarak yazılır ve ilgili kontrol yeniden çalışır.
- **OCR:** Taranmış PDF'ler Tesseract ile yerelde okunur; okunan metin düzeltilebilir.
- **Mevzuat yardımcısı:** Şikâyet ve not metinlerindeki iddiaları 836 belgelik yerel mevzuat kütüphanesinde
  arar (anlam benzerliği + yeniden sıralama). İsteğe bağlı yerel dil modeli (Ollama) yalnızca zayıf
  eşleşmeleri reddedebilir, kendi başına bulgu üretmez.
- **Çıktı:** Word çalışma kâğıdı; geçmiş kararlar denetim izinde.

## Kurulum (kaynaktan)

Python 3.12, Windows üzerinde test edildi.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

- Tam ortam: `requirements-lock.txt`. İsteğe bağlı özellikler: `requirements-optional.txt`.
- OCR için sistemde Tesseract (Türkçe dil paketiyle) kurulu olmalıdır.
- Mevzuat kütüphanesi (`data/`, ~1 GB) depoda yoktur; mevzuat yardımcısı ve mevzuat araması için Admin →
  "İlk mevzuat kurulumu" ile resmî kaynaklardan kurulur (internet gerekir).
- Yerel dil modeli isteğe bağlıdır: [Ollama](https://ollama.com) ve `qwen2.5:7b`.

Windows kurulum paketi `packaging/` altındaki betiklerle üretilir (PyInstaller + ONNX Runtime); ayrıntı
[docs/architecture/packaging.md](docs/architecture/packaging.md).

## Testler

```bash
pytest
```

## Klasörler

| Klasör | İçerik |
|---|---|
| `mufettis/` | Uygulama: belge okuma, alan çıkarma, kurallar, mevzuat arama, arayüz |
| `mufettis/rules/catalog/` | Kural kataloğu (JSON) |
| `tests/` | Birim ve uçtan uca testler |
| `benchmarks/` | Sentetik vakalar ve ölçüm betikleri |
| `scripts/` | Mevzuat derleme, envanter ve bakım betikleri |
| `packaging/` | Windows paketi ve kurulum |
| `docs/` | Kapsam, plan ve mimari kararlar |

## Lisans

[MIT](LICENSE). Pakete giren üçüncü taraf bileşenlerin lisans bildirimleri `packaging/notices.py` ile üretilir;
yazı tipleri (Libre Franklin, Libre Baskerville) SIL Open Font License altındadır (`static/fonts/`).
