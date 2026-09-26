# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Tuple

def repair_mojibake(value: Any) -> str:
    """UTF-8/Windows mojibake'i doğru Türkçe karakterlerle birlikte güvenle onar."""
    if value is None:
        return ""
    original = str(value)
    if not original:
        return original

    markers = ("Ã", "Â", "â€", "â€“", "â€”", "â€¦", "Ä", "Å", "ï¿½")
    original_whitespace = tuple(ch for ch in original if ch.isspace())

    def whitespace_signature(s: str) -> Tuple[str, ...]:
        # Mojibake onarımı whitespace ekleyemez, silemez veya türünü değiştiremez.
        return tuple(ch for ch in s if ch.isspace())

    def score(s: str) -> int:
        bad = sum(s.count(m) for m in markers)
        replacement = s.count("ï¿½")
        tr = sum(s.count(ch) for ch in "çğıöşüÇĞİÖŞÜ")
        # Türkçe harfleri koru; mojibake marker'larını güçlü şekilde cezalandır.
        return (tr * 4) - (bad * 10) - (replacement * 40)

    def whitespace_invariant(s: str) -> bool:
        return whitespace_signature(s) == original_whitespace

    candidates = [original]

    # Whole-string dönüşümler yalnızca encode edilebiliyorsa denenir.
    for enc in ("latin1", "cp1252"):
        try:
            candidates.append(original.encode(enc).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    # Karışık doğru+bozuk stringlerde sorunlu karakter kümelerini parça parça dene.
    # Örnek: "TÃ¼rkiye ğ ı İ"
    repaired = original
    replacements = {
        "Ã¼": "ü", "Ãœ": "Ü", "Ã§": "ç", "Ã‡": "Ç",
        "Ä±": "ı", "Ä°": "İ", "Äž": "Ğ", "ÄŸ": "ğ",
        "Ã¶": "ö", "Ã–": "Ö", "ÅŸ": "ş", "Åž": "Ş",
        "Ãª": "ê", "Ã®": "î", "Ã»": "û",
        "â€“": "–", "â€”": "—", "â€¦": "…", "Â ": " ",
        "ï¿½": "",
    }
    for bad, good in replacements.items():
        repaired = repaired.replace(bad, good)

    candidates.append(repaired)

    # Iterative replacement; en fazla iki tur.
    for _ in range(2):
        current = repaired
        for bad, good in replacements.items():
            current = current.replace(bad, good)
        repaired = current
        candidates.append(repaired)

    # Bir string hem doğru hem bozuk Türkçe karakterler içeriyorsa önce parça
    # bazlı düzeltmeyi tercih et; mevcut whitespace aynen korunmalıdır.
    invariant_candidates = [candidate for candidate in candidates if whitespace_invariant(candidate)]
    if not invariant_candidates:
        return original
    best = max(invariant_candidates, key=score)
    # Son güvenlik kapısı: invariant bozulduysa onarılmış sonucu kullanma.
    if whitespace_signature(best) != original_whitespace:
        return original
    return best
def _repair_glued_uppercase_words(text: str) -> str:
    """PDF'nin boşluksuz birleştirdiği uzun Türkçe büyük-harf kelime dizilerini
    aynı belge içindeki kelime sözlüğünden temkinli biçimde ayırır."""
    s = str(text or "")
    s = re.sub(r"TEMİNATLILHİRAÇAT", "TEMİNATLI İHRACAT", s)
    tokens = re.findall(r"[A-ZÇĞİÖŞÜ]{3,}", s)
    vocab = {t for t in tokens if len(t) <= 12}
    vocab.update({
        "TEMİNATLI", "İHRACAT", "FAKTORİNG", "ALACAKLARI", "RÜCÜ", "EDİLEBİLİR",
        "İŞTİRAKLER", "BAĞLI", "ORTAKLIKLAR", "DİĞER", "MÜŞTERİLER", "KAMU", "ÖZEL",
        "KASA", "EFEKTİF", "DEPOSU", "YOLDAKİ", "PARALAR", "SATIN", "ALINAN", "BANKA",
        "ÇEKLERİ", "MEVDUATLAR", "SERMAYE", "YÖNETİM", "KONTROLÜ", "KREDİLER", "BORÇLAR",
        "HESAPLAR", "HESABI", "GELİRLER", "GİDERLER", "FAİZ", "KARŞILIKLAR", "RİSK", "TAKİP",
        "KREDİ", "TAHSİS", "İZLEME", "SÜREÇLERİ", "İŞLEMLER", "BANKALAR", "FİNANSAL",
        "RAPORLAMA", "SENDİKASYON", "TABANLI", "STANDARTLAR", "BELİRLENMELİDİR", "KALDIRAÇLI",
        "DÜZENLİ", "OLARAK", "GÖZDEN", "GEÇİRİLMESİ", "TÜRÜ", "VADESİ", "POLİTİKALARI",
        "PROSEDÜRLER", "VERİ", "KALİTESİ", "HESAP", "PLANI", "İZAHNAME", "DÖVİZ", "İŞTİRAK",
        "YURTDIŞI", "İŞLEMLERİNDEN", "İKTİSADİ", "DEVLET", "TEŞEKKÜLLERİ", "REPO", "BANKALARCA",
        "UYGULANACAK", "TEKDÜZEN", "İZAHNAMESİ", "HESABININ", "HESABINA", "HESAPLARIN",
        "HESAPLARI", "BORCUNA", "ALACAĞINA", "KARŞILIĞI", "KAYDEDİLİR", "KAYDEDİLEN", "TÜRK",
        "PARASINA", "DÖNÜŞTÜRÜLÜR",
    })

    def segment(run: str) -> Optional[str]:
        if len(run) < 10:
            return None
        n = len(run)
        dp = [None] * (n + 1)
        dp[0] = (0.0, [])
        for i in range(n):
            if dp[i] is None:
                continue
            base_score, base_parts = dp[i]
            for j in range(min(n, i + 30), i + 2, -1):
                word = run[i:j]
                if word in vocab:
                    # Uzun, tam eşleşen kelimeleri tercih et; iki-üç harflik
                    # parçalar ancak başka çözüm yoksa kullanılabilsin.
                    penalty = 0.0 if len(word) >= 4 else 2.0
                    score = base_score + (len(word) * len(word)) - penalty
                    cand = (score, base_parts + [word])
                    if dp[j] is None or score > dp[j][0]:
                        dp[j] = cand
        if dp[n] is None or len(dp[n][1]) < 2:
            return None
        return " ".join(dp[n][1])

    return re.sub(
        r"(?<![A-ZÇĞİÖŞÜ])([A-ZÇĞİÖŞÜ]{10,})(?![A-ZÇĞİÖŞÜ])",
        lambda m: segment(m.group(1)) or m.group(1),
        s,
    )
_TURKISH_TEXT_WORDS = {
    "bu", "bir", "ve", "veya", "ile", "için", "olarak", "olan", "olup", "olur", "olması",
    "ise", "de", "da", "den", "dan", "dir", "dır", "dur", "dür", "gibi", "kadar", "sonra",
    "önce", "üzere", "ancak", "ayrıca", "hem", "her", "hiç", "daha", "çok", "en", "ilgili",
    "ilişkin", "ilişkili", "kapsamında", "çerçevesinde", "amacıyla", "nedeniyle", "dolayı",
    "kaynaklanan", "kaynaklanır", "kaynaklı", "bulunan", "bulunur", "bulunması", "bulunmaktadır",
    "ülkemizde", "tedavülde", "banknot", "madeni", "paraların", "paralar", "bankaya", "yatırılmasından",
    "çekilmesinden", "işlemleri", "işlemin", "işlem", "işlemlerinin", "kayda", "kaydedilir", "kaydedilen",
    "hesapta", "hesabın", "hesabına", "hesabında", "hesabının", "hesabı", "mevcudu", "mevcut", "yalnızca",
    "türk", "türkçe", "türk", "para", "parası", "parasına", "değerlerden", "oluşur", "girişler",
    "çıkışlar", "borç", "alacak", "borçlu", "alacaklı", "aktif", "niteli", "niteliğinde", "bakiyesi",
    "gösterir", "göstermek", "gösterdiği", "hesap", "hesapları", "hesaplanan", "hesaplama", "hesaplaması",
    "bankanın", "şubeleri", "diğer", "birimleriyle", "nakit", "hareketlerini", "alım", "satım", "yatırma",
    "çekme", "yapılan", "yapılan", "cinsinden", "değerleri", "yapılan", "girişler", "çıkışlar", "kayıt",
    "kayıtlı", "kayıtlarının", "rapor", "raporlama", "finansal", "muhasebe", "muhasebesel", "tekdüzen",
    "izahname", "izahnamesi", "bankalarca", "uygulanacak", "kasa", "efektif", "deposu", "efektifi",
    "vaziyeti", "karşılığı", "alınan", "cinsine", "göre", "miktar", "sabit", "fiyat", "üzerinden",
    "amaçla", "kullanılır", "kullanılan", "kullanılırken", "ilişkin", "izlemek", "gereken", "gereği",
    "rehberde", "rehber", "yer", "alan", "kredilerdir", "çevresel", "sürdürülebilir", "kredilendirme", "kredilendirme:",
    "ekonomik", "faaliyetlerin", "finansmanında", "kullanılan", "krediler", "söz", "konusu", "edilen",
    "ve", "karşılığında", "ihraç", "edilen", "hisse", "senedi", "menkul", "kıymet", "yatırımları",
    "finansman", "sağlayan", "sağlayacak", "finansman", "bir", "proje", "sürdürülebilir", "finansmanın",
    "biçimi", "borç", "kredi", "sermaye", "yeterlilik", "hesaplamaları", "bankacılık", "hesaplarında",
    "veya", "alım", "satım", "hesaplarında", "sınıflandırıldığı", "bakılmaksızın", "borcun", "kredisinin",
    "nispetesinde", "toplam", "borç", "faiz", "vergi", "amortisman", "öncesi", "kazanç", "işlemler",
    "ödemede", "davranış", "davranışları", "yaşanan", "yeterlilik", "sermaye", "çalışmaları", "risk",
    "yönetimi", "politikaları", "prosedürleri", "veri", "kalitesi", "düzenli", "gözden", "geçirilmesi",
    "gereklidir", "gerekmektedir", "belirlenmelidir", "takip", "izleme", "süreçleri", "tahsis", "kredi",
    "kredinin", "kredilerin", "borçların", "türü", "vadesi", "müşteri", "müşterinin", "müşteriler",
    "işletme", "şirket", "kuruluş", "kurum", "kamu", "özel", "devlet", "teşekkülleri", "yurtdışı",
    "yurt", "dışı", "işlemlerinden", "ödemeler", "ödeme", "ödemelerin", "teminat", "teminatlı", "ihracat",
    "faktoring", "alacakları", "rücu", "edilebilir", "iştirakler", "bağlı", "ortaklıklar", "borçlarına",
    "borcuna", "alacağına", "kaydedilmiştir", "kaydedilmiştir", "dönüştürülür", "satın", "alınmış", "alınan",
    "mevzuat", "madde", "maddesi", "fıkra", "bent", "alt", "bölüm", "numaralı", "sayı", "sayılı", "hüküm",
    "hükümleri", "belge", "belgenin", "belgeler", "başlık", "açıklama", "açıklaması", "tanım", "tanımlanan",
    "uygulama", "uygulanır", "uygulanması", "uygulanan", "düzenleme", "düzenlemeler", "belirleme", "belirtilen",
    "belirtilmiştir", "zorunlu", "zorunluluk", "yükümlülük", "yükümlülükleri", "gereken", "sağlanması",
}
def _repair_glued_turkish_words(text: str) -> str:
    """Boşluksuz birleşmiş Türkçe kelimeleri güvenli DP ile ayır.

    Sadece en az 10 harflik boşluksuz diziler hedeflenir. Kelime sözlüğü,
    metnin zaten boşluklu kelimelerinden ve sınırlı mevzuat/finans sözlüğünden
    oluşturulur; karakter silinmez veya yeni içerik üretilmez.
    """
    s = str(text or "")
    if not s:
        return s

    observed = {
        w.lower() for w in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3,}", s)
        if len(w) <= 12
    }
    vocab = observed | _TURKISH_TEXT_WORDS

    def segment(run: str) -> Optional[str]:
        if len(run) < 10:
            return None
        low = run.lower()
        n = len(low)
        dp = [None] * (n + 1)
        dp[0] = (0.0, [])
        for i in range(n):
            if dp[i] is None:
                continue
            base_score, parts = dp[i]
            max_len = min(30, n - i)
            for length in range(max_len, 1, -1):
                word = low[i:i + length]
                if word not in vocab:
                    continue
                # Uzun ve sözlükte güvenilir kelimeler tercih edilir; kısa
                # bağlaçlar yalnızca tam kapsama için gerektiğinde kullanılır.
                penalty = 0.0 if length >= 4 else 4.0
                score = base_score + (length * length) - penalty
                if dp[i + length] is None or score > dp[i + length][0]:
                    dp[i + length] = (score, parts + [run[i:i + length]])
        if dp[n] is None or len(dp[n][1]) < 2:
            return None
        # En az bir anlamlı kelime uzunluğu ve tam karakter kapsamı şartı.
        if any(len(x) < 3 for x in dp[n][1]) and len(dp[n][1]) <= 2:
            return None
        return " ".join(dp[n][1])

    return re.sub(
        r"(?<![A-Za-zÇĞİÖŞÜçğıöşü])([A-Za-zÇĞİÖŞÜçğıöşü]{10,})(?![A-Za-zÇĞİÖŞÜçğıöşü])",
        lambda m: segment(m.group(1)) or m.group(1),
        s,
    )
def repair_account_code_spacing(value: str, account_code: str = "") -> str:
    """Hesap planı chunk'larında kod-başlık sınırını deterministik olarak ayırır.

    PDF/HTML çıkarımı 010KASA veya 280DİĞER biçiminde boşluğu düşürebilir.
    Chunk zaten belirli bir account_code taşıyorsa yalnızca o kodun sınırını
    düzeltir; böylece normal metne rastgele boşluk eklenmez.
    """
    s = str(value or "")
    code = str(account_code or "").strip()
    if not code:
        return s
    if not re.fullmatch(r"\d{3,6}", code):
        return s
    upper = r"A-ZÇĞİÖŞÜ"
    # Kod doğrudan harfe yapışmışsa ayır: 010KASA -> 010 KASA.
    s = re.sub(rf"(?<!\d)({re.escape(code)})(?=[{upper}])", r"\1 ", s)
    return s
def repair_extracted_spacing(value: str, title: str = "", document_type: str = "") -> str:
    """PDF/HTML extractionındaki kayıp kelime ve yapı boşluklarını temkinli onarır.

    Amaç genel metne rastgele boşluk serpiştirmek değil; PDF extractor'ın sıkça
    kaybettiği yapı sınırlarını geri kurmaktır. Hesap planlarında hesap kodu /
    başlık sınırı, rehberlerde numaralı hüküm sınırı ve noktalama sonrası kelime
    sınırı özellikle korunur.
    """
    s = str(value or "").replace("\u00a0", " ").replace("\u200b", "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # PDF satır sonlarında kelime bölünmesi: satır sonu tireyi doğrudan
    # yapıştırmak bazen iki kelimeyi birbirine kaynatır. Önce satır sonunu
    # kaldırıp tek akışa getir; ardından sözlük tabanlı kelime ayırıcı çalışır.
    s = re.sub(
        rf"(?<=[A-Za-zÇĞİÖŞÜçğıöşü])-\n(?=[A-Za-zÇĞİÖŞÜçğıöşü])",
        "",
        s,
    )
    # Mevcut boşluk dizisini koru; yalnızca yapısal sınır düzeltmeleri aşağıdaki
    # hedefli regexlerle yapılır.
    s = _repair_glued_uppercase_words(s)
    s = _repair_glued_turkish_words(s)

    upper = r"A-ZÇĞİÖŞÜ"
    lower = r"a-zçğıöşü"

    # Yapı belirteçlerinden sonra extractor boşluk düşürdüyse geri koy.
    s = re.sub(rf"(?<=\d)(?=[{upper}])", " ", s)
    s = re.sub(rf"(?<=[.)\]:;!?])(?=[{upper}])", " ", s)
    s = re.sub(rf"(?<=[{lower}])(?=[{upper}])", " ", s)

    # PDF extractor bazen tamamen büyük hesap başlığını hemen ardından gelen
    # normal cümleyle birleştirir: KASAÜlkemizde -> KASA Ülkemizde,
    # DEPOSUAlım -> DEPOSU Alım. Büyük harf dizisinin ardından gelen
    # büyük+küçük harfli kelime başlangıcını güvenli bir kelime sınırı say.
    s = re.sub(
        rf"([{upper}]{{2,}})(?=[{upper}][{lower}])",
        r"\1 ",
        s,
    )

    # Numaralı hüküm/section başlangıçları: 240)SENDİKASYON -> 240) SENDİKASYON.
    s = re.sub(rf"(?<!\d)(\d{{1,3}}\s*[-.)])(?=[{upper}])", r"\1 ", s)

    # Hesap planı sinyali satırdan kaybolsa bile kod-başlık sınırını geri kur.
    # Bu kural yalnızca yeterli tekrar eden kod sinyali bulunan metinlerde uygulanır.
    inline_codes = re.findall(rf"(?<!\d)(\d{{3,6}})(?=[{upper}])", s)
    title_norm = str(title or "").lower()
    type_norm = str(document_type or "").lower()
    accounting_hint = (
        "hesap plan" in title_norm
        or "hesap plan" in s.lower()
        or len(inline_codes) >= 5
    )
    if accounting_hint:
        s = re.sub(rf"(?<!\d)(\d{{3,6}})(?=[{upper}])", r"\1 ", s)
        s = re.sub(r"(?<!\n)(?<!\d)(?=\d{3,6}\s+[A-ZÇĞİÖŞÜ])", "\n", s)

    # Sık görülen PDF birleştirmelerinde noktalama/başlık sınırlarını ayır.
    s = re.sub(r"([,:;!?])(?=[A-ZÇĞİÖŞÜ])", r"\1 ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()
_C0_CONTROL_RE = re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_extracted_text(value: str) -> str:
    """Extraction metnini normalize eder; mevcut yatay whitespace'i korur."""
    protected = {}
    original_horizontal_whitespace = [
        m.group(0) for m in re.finditer(r"[ \t]+", str(value or ""))
    ]

    def restore_horizontal_whitespace(original: str, normalized: str) -> str:
        # Yapısal newline/kelime bölme gerçekten değişmediyse mevcut boşluk
        # dizilerini birebir geri koy. Böylece genel normalization mevcut
        # horizontal whitespace'i sıkıştıramaz.
        original_parts = re.split(r"([ \t]+)", original)
        normalized_parts = re.split(r"([ \t]+)", normalized)
        if len(original_parts) != len(normalized_parts):
            return normalized
        for i, part in enumerate(original_parts):
            if re.fullmatch(r"[ \t]+", part or ""):
                normalized_parts[i] = part
        return "".join(normalized_parts)
    def protect(match):
        key = f"__LEGAL_MARKER_{len(protected)}__"
        protected[key] = match.group(0)
        return key

    # Hukuki marker'ı tüm normalization adımlarından önce koru; böylece
    # boşluk/başlık düzeltmeleri marker'ın iç yapısını değiştiremez.
    raw = str(value or "")
    raw = re.sub(
        r"(?i)\b(?:GEÇİCİ|EK)\s+MADDE\s*[-:]?\s*\d+[A-Za-z]?\b(?:\s*[–—-]\s*)?",
        protect,
        raw,
    )
    # C0 kontrol karakterleri (sekme/satır sonu hariç) metin değildir: PDF'te Unicode eşlemesi
    # olmayan glifler (ör. tablo sembolleri) bu kodlarla gelir. Önceden yalnızca NUL temizleniyordu;
    # kalanlar parçalara girip kapının kalite taramasında belgeyi reddettiriyordu.
    s = _C0_CONTROL_RE.sub("", repair_mojibake(raw).replace("\x00", " "))

    # C: Mevcut whitespace'i değiştirmemesi gereken sıradan metinlerde
    # destructive spacing-repair katmanına hiç girme. Yalnızca gerçekten
    # yapışık PDF sınırı / hukuki yapı onarımı gerektiren metinlerde devam et.
    has_structural_signal = bool(re.search(
        r"(?i)(?:\d{3,6}[A-ZÇĞİÖŞÜ]|[A-ZÇĞİÖŞÜ]{2,}[A-ZÇĞİÖŞÜ][a-zçğıöşü]|(?:MADDE|GEÇİCİ|EK)\s*)",
        s,
    ))
    if not has_structural_signal and not re.search(r"[ \t]*\n", s):
        return s

    s = repair_extracted_spacing(s)

    # C: normalize_extracted_text mevcut iç whitespace dizisini genel olarak daraltmaz.
    # Yalnızca hukuki yapı sınırlarını hedefli regexler ayırır.

    s = re.sub(
        r"(?i)\s+(?=MADDE\s*[-:]?\s*\d+[A-Za-z]?\b)",
        "\n",
        s,
    )
    s = re.sub(
        r"(?<!\n)\s+(?=\(\d{1,3}\)\s+)",
        "\n",
        s,
    )

    for key, original in protected.items():
        s = s.replace(key, original)

    s = re.sub(r"\n[ \t]+\n", "\n\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.strip()

    # C invariant: normalization mevcut horizontal whitespace dizisini
    # değiştirmediyse, orijinal diziyi birebir koru. Kelime/structure split
    # üretildiğinde parça sayısı değişeceği için bu geri yükleme uygulanmaz.
    if original_horizontal_whitespace:
        s = restore_horizontal_whitespace(str(value or "").strip(), s)
    return s
def normalize_legal_whitespace(value: str) -> str:
    """PDF extraction'daki yapışık hukuki belirteçleri ayırırken prefix'leri korur."""
    s=normalize_extracted_text(value)
    s=s.replace("\r\n","\n").replace("\r","\n")

    protected={}
    def protect(match):
        key=f"__LEGAL_PREFIX_{len(protected)}__"
        protected[key]=re.sub(r"\s+"," ",match.group(0)).strip()
        return key

    # GEÇİCİ MADDE / EK MADDE başlığının tamamını koru.
    s=re.sub(
        r"(?i)(?:GEÇİCİ|EK)\s+MADDE\s*[-:]?\s*\d+[A-Za-z]?(?:\s*[–—-]\s*)?",
        protect,
        s,
    )

    s=re.sub(r"[ \t]+"," ",s)
    s=re.sub(
        r"(?i)\s+(?=MADDE\s*[-:]?\s*\d+[A-Za-z]?\b)",
        "\n",
        s,
    )
    s=re.sub(
        r"(?<!\n)\s+(?=\(\d{1,3}\)\s+)",
        "\n",
        s,
    )
    s=re.sub(r"\n{3,}","\n\n",s)

    for key,original in protected.items():
        s=s.replace(key,original)

    return s.strip()
def clean_evidence_text(value: Any, max_chars: int = 1800) -> str:
    """RAG/DOCX için HTML/XML artıkları ve encoding kalıntılarını temizle."""
    s = repair_mojibake(value)
    if not s:
        return ""

    s = re.sub(r"<\?xml[^>]*\?>", " ", s, flags=re.I)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"</?[A-Za-z_][^>]*>", " ", s)
    s = html.unescape(s)
    s = s.replace("\x00", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = repair_extracted_spacing(s)
    s = re.sub(r"\n[ \t]+\n", "\n\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()[:max_chars]
def extract_legal_identifier(query: str) -> Optional[str]:
    """5549, 5411 gibi açık kanun numarasını sorgudan çıkar."""
    q = str(query or "")
    patterns = [
        # "5411 sayılı Bankacılık Kanunu", "193 sayılı Gelir Vergisi Kanununun": kanun adı araya
        # girebilir; "sayılı" açıkça yazıldığında 3 haneli kanunlar (193, 213, 488) da kabul edilir.
        r"(?<!\d)(\d{3,6})\s*sayılı(?:\s+[^\s\d]+){0,8}?\s+kanun\w*",
        r"\b(\d{4,6})\s*(?:kanun|kanunu|kanunun)\b",
        r"\b(?:kanun|kanunu)\s*(?:no|numarası)?\s*[:\-]?\s*(\d{4,6})\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return m.group(1)
    return None


__all__ = [
    "repair_mojibake",
    "repair_account_code_spacing",
    "repair_extracted_spacing",
    "normalize_extracted_text",
    "normalize_legal_whitespace",
    "clean_evidence_text",
    "extract_legal_identifier",
]
