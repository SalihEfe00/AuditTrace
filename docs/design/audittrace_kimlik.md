# AuditTrace görsel kimliği (M16.2, 2026-10-08)

## Fikir: kalem izi

Müfettiş çalışma kâğıdında kontrol ettiği her rakamın yanına kalemle tik atar. Rakamı kaynağına kadar izlediyse
bunu da işaretler. AuditTrace'in amblemi bu hareketi anlatır:

- **Zemin:** köşesi kıvrık bir çalışma kâğıdı.
- **İz:** küçükten büyüğe üç kırmızı kalem noktası.
- **Tik:** izin vardığı doğrulama işareti.

Yazı biçiminde "Audit" mürekkep renginde ve dik, "Trace" yeşil kalem renginde ve italiktir.

Kaçınılanlar: mor geçişler, parlama efektleri, emoji başlıklar, robot ya da beyin simgeleri, "AI" vurgusu. Ürün,
kâğıt üzerinde çalışan bir müfettişin aracı gibi görünmelidir.

## Dosyalar (`mufettis/ui/brand/`)

| Dosya | Kullanım |
|---|---|
| `mark.svg` | Amblem (24 piksel ve üstü). Arayüz başlığı satır içi olarak bunu kullanır. |
| `mark_small.svg` | 16–32 piksel için sade çizim: tek iz noktası, kalın tik. |
| `logo.svg` | Amblem ve yazı. Yazı tipi yoksa Georgia'ya düşer. |
| `icon.ico` | exe ve kısayol simgesi: 16, 20, 24, 32, 40, 48, 64, 128 ve 256 piksel. |
| `icon_256.png` | Word raporu kapağı. |
| `favicon.png` | Pencere ve sekme simgesi. |

## Renkler (`mufettis/brand.py`; Streamlit teması `mufettis/ui/theme.py`)

| Ad | Değer | Nerede |
|---|---|---|
| Kâğıt | `#F7F5F0` | Zemin |
| Kâğıt 2 | `#EFEBE3` | Kenar çubuğu, giriş kutusu, tablo başlığı, Word'de etiket sütunu |
| Çizgi | `#DDD6C8` | Kenarlık ve ayırıcı |
| Mürekkep | `#1E2A32` | Metin ve amblem |
| Soluk mürekkep | `#5B6770` | İkincil metin |
| Yeşil kalem | `#1F5E55` | Birincil düğme, bağlantı ve "Trace" |
| Kırmızı kalem | `#E0654A` | Yalnızca amblemdeki iz |

Durum renkleri metin, zemin ve kenar şeridi üçlüsüdür. Metin ile zemin arasındaki karşıtlık en az 4,5:1, kenar
şeridi ile kâğıt arasındaki karşıtlık en az 3:1'dir. Bu eşikler `tests/test_theme.py` ile denetlenir.

| Durum | İşaret | Metin / zemin / kenar |
|---|---|---|
| Bulgu | ✕ | `#8E2B21` / `#F6E5E1` / `#B5382B` |
| Karar verilemedi (ve elle kontrol ✎) | ? | `#6E4C10` / `#F5ECD8` / `#A3741E` |
| Uygun | ✓ | `#245640` / `#E3EEE6` / `#2F6B52` |
| Yapılamayan / uygulanmaz | – | `#434C54` / `#EAE8E3` / `#7A8289` |

## Yazı tipleri

Arayüz Streamlit'le birlikte gelen Source ailesini kullanır (SIL OFL lisanslı). İnternet bağlantısı ya da ayrıca
indirme gerekmez.

- **Başlıklar:** Source Serif
- **Metin:** Source Sans
- **Kural kimlikleri ve kod:** Source Code Pro

Word raporlarında başlıklar Georgia, metin Arial'dir. İkisi de her Windows'ta bulunur.

## Bileşen dili

- **Köşeler:** 6 piksel yuvarlak.
- **Kartlar:** gölgesiz, ince çizgili.
- **Bulgu kartları:** sol kenarda 3 piksellik durum şeridi.
- **Sayı kutuları:** sayı serif yazı tipinde.
- **Durum işaretleri:** kalem işaretleri (✓ ✕ ? ✎ –), emoji kullanılmaz.
- **Birincil işlem:** yeşil kalem renginde tek düğme (Word çalışma kâğıdı, Analizi başlat).

## Değerlendirilen yönler

| Yön | Özet | Neden seçilmedi |
|---|---|---|
| **A · Kalem izi (seçilen)** | Kâğıt, mürekkep ve yeşil kalem; serif başlıklar; tik ve iz amblemi | — |
| B · Defter | Defter ızgarası, tek aralıklı yazı, siyah-beyaz ve hardal | Terminal havası; uzun Türkçe metinler tek aralıklı yazıyla zor okunuyor |
| C · Mühür | Yuvarlak mühür, büyük harf serif, bordo ve altın | Ağır ve resmî; bordo renk bulgunun kırmızısıyla karışıyor |

İlk amblem denemesi kare içinde bir tikti ve sıradan bir onay kutusuna benziyordu. İkinci denemede tikin önüne
kırmızı bir çizgi kondu, ama bu da eksi işareti gibi okundu. Son hâlde noktalardan oluşan iz tike varıyor ve
"izlendi, doğrulandı" anlamını taşıyor.
