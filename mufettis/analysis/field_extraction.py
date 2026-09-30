# -*- coding: utf-8 -*-
"""Şube belgelerinden yapılandırılmış alan çıkarımı (M5.2–M5.5).

Belge metninden türü, normalleştirilmiş değeri, metindeki yazımı ve konumuyla alan listesi üretir:
tutar, tarih, oran, IBAN, TCKN, VKN, hesap numarası, kişi (rolüyle), imza / onay durumu, teminat, limit.
Değer ayrıştırıcıları (parse_number, find_dates, …) belge benchmark'ı da kullanır (cevap anahtarını aynı
biçime çevirmek için); belgede alan bulma kuralları (etiket, sütun, denetim hanesi) yalnızca buradadır.

Normalleştirilmiş biçimler: tutar "1250000.00 TRY", tarih "2023-04-05", oran "54.00", TCKN / VKN / hesap no
yalnızca rakam, IBAN boşluksuz büyük harf, kişi adı küçük harf ("ahmet yılmaz").
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterator, List, Optional, Tuple

from mufettis.analysis.models import ExtractedField
from mufettis.analysis.turkish_text import turkish_lower

# --- Değer ayrıştırıcıları ---------------------------------------------------------------------------------

_CURRENCIES = (
    ("TRY", r"TL|TRY|₺|Türk\s+Lirası|Türk\s+lirası|TRL"),
    ("USD", r"USD|US\$|\$|ABD\s+Doları|Dolar"),
    ("EUR", r"EUR|€|Avro|Euro"),
    ("GBP", r"GBP|£|Sterlin"),
)
_CURRENCY_RE = "|".join(f"(?:{pattern})" for _, pattern in _CURRENCIES)
_MULTIPLIERS = {"bin": Decimal(1000), "milyon": Decimal(10) ** 6, "milyar": Decimal(10) ** 9}
# Binlik ayırıcı nokta, virgül ya da boşluk olabilir; satır sonu değil ("Fark: 0\nEUR: 9.750" iki ayrı satırdır).
_NUMBER = r"\d{1,3}(?:[., ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_AMOUNT_RE = re.compile(
    rf"(?<![\w.,%])(?:(?P<pre>{_CURRENCY_RE})[ \t]*)?(?P<num>{_NUMBER})(?:[ \t]*(?P<mult>bin|milyon|milyar))?"
    rf"(?:[ \t]*(?P<post>{_CURRENCY_RE}))?(?![\w%])",
    re.IGNORECASE,
)


def _currency_code(token: str) -> str:
    for code, pattern in _CURRENCIES:
        if re.fullmatch(pattern, token.strip(), re.IGNORECASE):
            return code
    return ""


def parse_number(text: str) -> Optional[Decimal]:
    """Türkçe ya da İngilizce biçimli sayı: "1.250.000,50", "1,250,000.50", "250.000", "2,5", "1 250 000"."""
    s = re.sub(r"\s", "", text or "")
    if s.startswith("-"):
        s = s[1:]
    if not s or not re.fullmatch(r"[\d.,]+", s) or not s[0].isdigit():
        return None
    if "." in s and "," in s:
        decimal_sep = "," if s.rfind(",") > s.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        s = s.replace(thousands, "").replace(decimal_sep, ".")
    elif s.count(".") + s.count(",") > 1:          # tek tür ayırıcı birden çok kez: binlik
        s = s.replace(".", "").replace(",", "")
    elif "." in s:                                   # "250.000" binlik, "12.5" ondalık
        s = s.replace(".", "") if re.fullmatch(r"\d{1,3}\.\d{3}", s) else s
    elif "," in s:                                   # Türkçe ondalık
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def format_amount(value: Decimal, currency: str) -> str:
    return f"{value.quantize(Decimal('0.01'))} {currency}"


def iter_amounts(text: str) -> Iterator[Tuple[int, int, str]]:
    """Para birimi yazılmış tutarlar: (başlangıç, bitiş, "1250000.00 TRY")."""
    for m in _AMOUNT_RE.finditer(text or ""):
        currency = _currency_code(m.group("pre") or m.group("post") or "")
        value = parse_number(m.group("num")) if currency else None
        if value is None:
            continue
        if m.group("mult"):
            value *= _MULTIPLIERS[m.group("mult").lower()]
        yield m.start(), m.end(), format_amount(value, currency)


def find_amounts(text: str) -> List[str]:
    """Metindeki para tutarları ("1250000.00 TRY"). Para birimi yazılmamış sayı tutar sayılmaz (vade, adet,
    oran olabilir); etiketli ya da tablo sütunundaki çıplak tutarlar için bkz. extract_fields."""
    return [value for _, _, value in iter_amounts(text)]


_MONTHS = {name: i for i, name in enumerate(
    ("ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık"),
    start=1)}
_DATE_RES = (
    (re.compile(r"(?<!\d)(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?!\d)"), ("y", "m", "d")),
    (re.compile(r"(?<!\d)(\d{1,2})[-./](\d{1,2})[-./](\d{4})(?!\d)"), ("d", "m", "y")),
    (re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})(?!\d)"), ("d", "mon", "y")),
)


def iter_dates(text: str) -> Iterator[Tuple[int, int, str]]:
    """Geçerli takvim tarihleri: (başlangıç, bitiş, "2023-04-05"), metindeki sırayla."""
    lowered = turkish_lower(text or "")
    found = []
    for pattern, order in _DATE_RES:
        for m in pattern.finditer(lowered):
            parts = dict(zip(order, m.groups()))
            month = _MONTHS[parts["mon"]] if "mon" in parts else int(parts["m"])
            try:
                found.append((m.start(), m.end(), _dt.date(int(parts["y"]), month, int(parts["d"])).isoformat()))
            except ValueError:
                continue
    return iter(sorted(found))


def find_dates(text: str) -> List[str]:
    return [value for _, _, value in iter_dates(text)]


_RATE_RE = re.compile(r"%[ \t]?(\d+(?:[.,]\d+)?)|(?<![\d.,])(\d+(?:[.,]\d+)?)[ \t]?%|yüzde\s+(\d+(?:[.,]\d+)?)",
                      re.IGNORECASE)


def format_rate(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def iter_rates(text: str) -> Iterator[Tuple[int, int, str]]:
    """Yüzde oranlar ("%54,00", "4,5 %", "yüzde 2"): (başlangıç, bitiş, "54.00")."""
    for m in _RATE_RE.finditer(text or ""):
        value = parse_number(next(g for g in m.groups() if g))
        if value is not None:
            yield m.start(), m.end(), format_rate(value)


def find_rates(text: str) -> List[str]:
    return [value for _, _, value in iter_rates(text)]


_IBAN_RE = re.compile(r"(?<![A-Za-z0-9])TR[ ]?\d{2}(?:[ ]?\d){22}(?!\d)", re.IGNORECASE)


def iter_ibans(text: str) -> Iterator[Tuple[int, int, str]]:
    for m in _IBAN_RE.finditer(text or ""):
        yield m.start(), m.end(), re.sub(r"\s", "", m.group(0)).upper()


def find_ibans(text: str) -> List[str]:
    return [value for _, _, value in iter_ibans(text)]


def mask_ibans(text: str) -> str:
    """IBAN'ları aynı uzunlukta boşlukla örter (içindeki rakamlar kimlik / hesap no sanılmasın; konumlar korunur)."""
    return _IBAN_RE.sub(lambda m: " " * len(m.group(0)), text or "")


# Ondalıklı / binlik sayının parçası olmayan (ör. "12345678901,50" değil) 11 ve 10 haneli sayılar.
_TCKN_RE = re.compile(r"(?<!\d)(?<!\d[.,])[1-9]\d{10}(?!\d|[.,]\d)")
_VKN_RE = re.compile(r"(?<!\d)(?<!\d[.,])\d{10}(?!\d|[.,]\d)")


def find_tckns(text: str) -> List[str]:
    """Biçim olarak TCKN olabilecek 11 haneli sayılar (0 ile başlamaz; telefon değildir)."""
    return _TCKN_RE.findall(mask_ibans(text))


def find_vkns(text: str) -> List[str]:
    return _VKN_RE.findall(mask_ibans(text))


def tckn_checksum_ok(number: str) -> bool:
    """T.C. kimlik numarası denetim haneleri (10. ve 11. hane)."""
    if not re.fullmatch(r"[1-9]\d{10}", number or ""):
        return False
    d = [int(c) for c in number]
    tenth = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    return tenth == d[9] and sum(d[:10]) % 10 == d[10]


def vkn_checksum_ok(number: str) -> bool:
    """Vergi kimlik numarası denetim hanesi (Gelir İdaresi algoritması)."""
    if not re.fullmatch(r"\d{10}", number or ""):
        return False
    total = 0
    for i, ch in enumerate(number[:9]):
        tmp = (int(ch) + 9 - i) % 10
        if tmp:
            tmp = (tmp * 2 ** (9 - i)) % 9 or 9
        total += tmp
    return (10 - total % 10) % 10 == int(number[9])


ACCOUNT_DIGITS = (6, 20)


def normalize_account(text: str) -> Optional[str]:
    digits = re.sub(r"[\s\-/]", "", text or "")
    return digits if digits.isdigit() and ACCOUNT_DIGITS[0] <= len(digits) <= ACCOUNT_DIGITS[1] else None


_COMPANY_RE = re.compile(r"\b(?:a\.?\s?ş\.?|ltd|şti|anonim|limited|bankası|holding|kooperatif|şirketi|san\.|tic\.)"
                         r"(?![a-zçğıöşü])|\ba\.s\.")
_PERSON_TITLE_RE = re.compile(r"^(?:sayın|av\.|dr\.|sn\.|bay|bayan)\s+")


def normalize_person(name: str) -> Optional[str]:
    """Kişi adı → küçük harf, noktalama yok, 2–4 kelime; şirket ve boş değer None."""
    text = turkish_lower(name or "").strip()
    if not text or _COMPANY_RE.search(text):
        return None
    text = _PERSON_TITLE_RE.sub("", text)
    text = re.sub(r"[^a-zçğıöşüâîû\s]", " ", text)
    words = text.split()
    return " ".join(words) if 2 <= len(words) <= 4 else None


# --- Belgede alan bulma ------------------------------------------------------------------------------------

def _line_bounds(text: str, pos: int) -> Tuple[int, int]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return start, len(text) if end < 0 else end


def _before(text: str, pos: int, width: int) -> str:
    """Aynı satırda konumdan önceki en çok width karakter, küçük harfle."""
    line_start, _ = _line_bounds(text, pos)
    return turkish_lower(text[max(line_start, pos - width):pos])


def _field(kind: str, text: str, start: int, end: int, value: str, label: str = "", **attributes) -> ExtractedField:
    return ExtractedField(kind=kind, text=text[start:end].strip() or value, value=value, start=start, end=end,
                          label=label, attributes=dict(attributes))


_TCKN_LABEL = re.compile(r"(?<![a-zçğıöşü])(?:tckn|t\.?\s?c\.?\s*(?:kimlik)?\s*(?:no|numarası)?|kimlik\s*(?:no|numarası))\W*(?:\w+\W+){0,2}$")
_VKN_LABEL = re.compile(r"(?<![a-zçğıöşü])(?:vkn|vergi\s*(?:kimlik)?\s*(?:no|numarası)?|v\.?\s?d\.?)\W*(?:[\w.]+\W+){0,3}$")
_PHONE_LABEL = re.compile(r"(?:tel|telefon|gsm|cep|faks|fax)\b[^\n]{0,12}$")
_ACCOUNT_LABEL = re.compile(r"hesap\s*(?:no|numarası|nosu)\s*[:.]?\s*$")
_ACCOUNT_NUMBER = re.compile(r"\d[\d \-/]{4,30}\d")


def _identity_fields(text: str) -> List[ExtractedField]:
    """TCKN: etiketliyse ya da denetim hanesi tutuyorsa; VKN: etiketliyse (10 haneli sayıların %10'u denetim
    hanesini tesadüfen tutar, etiketsiz VKN telefon / hesap no ile karışır). Telefon etiketliler sayılmaz."""
    masked = mask_ibans(text)
    out = []
    for m in _TCKN_RE.finditer(masked):
        before = _before(masked, m.start(), 40)
        if _PHONE_LABEL.search(before):
            continue
        labeled = bool(_TCKN_LABEL.search(before))
        if labeled or tckn_checksum_ok(m.group(0)):
            out.append(_field("tckn", text, m.start(), m.end(), m.group(0), "etiket" if labeled else "denetim hanesi"))
    for m in _VKN_RE.finditer(masked):
        before = _before(masked, m.start(), 40)
        if _PHONE_LABEL.search(before) or not _VKN_LABEL.search(before):
            continue
        out.append(_field("vkn", text, m.start(), m.end(), m.group(0), "etiket",
                          checksum_ok=vkn_checksum_ok(m.group(0))))
    return out


def _account_fields(text: str) -> List[ExtractedField]:
    """Etiketli ("Hesap No: 0418-60221874-351") IBAN dışı hesap numaraları."""
    masked = mask_ibans(text)
    out = []
    for m in _ACCOUNT_NUMBER.finditer(masked):
        value = normalize_account(m.group(0))
        if value and _ACCOUNT_LABEL.search(_before(masked, m.start(), 30)):
            out.append(_field("account", text, m.start(), m.end(), value, "etiket"))
    return out


# Para birimsiz tutar ancak bu adlı bir etiketin ya da tablo sütununun altındaysa tutardır.
_AMOUNT_WORDS = re.compile(r"tutar|bakiye|bedel|miktar|borç|alacak|limit|ücret|masraf|komisyon|toplam|kullandırılan"
                           r"|yatırılan|ödenen|tahsil")
_BARE_NUMBER = re.compile(r"(?<![\w.,%/-])-?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?(?![\w%./-])|(?<![\w.,%/-])-?\d+[.,]\d{2}(?![\w%./-])")
_RATIO_LABEL = re.compile(r"/|oran|rasyo|katsayı|adet|sayısı")
_LABELED_BARE = re.compile(r"([^\n:]{1,40}):[ \t]*\"?(-?[\d.,]+)\"?[ \t]*$", re.MULTILINE)


def _table_rows(text: str) -> Iterator[Tuple[List[str], int, List[Tuple[str, int, int]]]]:
    """Metindeki CSV / noktalı virgüllü / Markdown tablo satırları: (başlık hücreleri, satır başı, [(hücre,
    başlangıç, bitiş)]). Başlık, aynı ayırıcıyla aynı sayıda hücresi olan ardışık satırların ilkidir."""
    lines = text.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    i = 0
    while i < len(lines):
        line = lines[i]
        delimiter = "|" if line.strip().startswith("|") else (";" if line.count(";") >= 2 else ("," if line.count(",") >= 2 else ""))
        if not delimiter:
            i += 1
            continue
        header = _split_row(line, delimiter)
        j = i + 1
        rows = []
        while j < len(lines):
            if delimiter == "|" and re.fullmatch(r"\s*\|?[\s:\-|]+\|?\s*", lines[j]):
                j += 1
                continue
            cells = _cells_with_offsets(lines[j], delimiter, offsets[j])
            if len(cells) != len(header):
                break
            rows.append(cells)
            j += 1
        if len(rows) >= 1 and any(_AMOUNT_WORDS.search(turkish_lower(h)) for h in header):
            for cells in rows:
                yield header, offsets[i], cells
        i = max(j, i + 1)


def _split_row(line: str, delimiter: str) -> List[str]:
    if delimiter == "|":
        return [c.strip() for c in line.strip().strip("|").split("|")]
    return [c.strip() for c in _csv_cells(line, delimiter)]


def _csv_cells(line: str, delimiter: str) -> List[str]:
    return next(csv.reader(io.StringIO(line), delimiter=delimiter), [])


def _cells_with_offsets(line: str, delimiter: str, base: int) -> List[Tuple[str, int, int]]:
    if delimiter == "|":
        body_start = line.find("|") + 1 if line.strip().startswith("|") else 0
        body = line[body_start:line.rstrip().rfind("|")] if line.rstrip().endswith("|") else line[body_start:]
        cells, cursor = [], base + body_start
        for part in body.split("|"):
            lead = len(part) - len(part.lstrip())
            cells.append((part.strip(), cursor + lead, cursor + lead + len(part.strip())))
            cursor += len(part) + 1
        return cells
    cells, cursor = [], 0
    for value in _csv_cells(line, delimiter):
        raw_start = line.find(value, cursor)
        if raw_start < 0:
            raw_start = cursor
        cells.append((value.strip(), base + raw_start, base + raw_start + len(value)))
        cursor = raw_start + len(value)
    return cells


def _bare_amount_fields(text: str, taken: List[Tuple[int, int]]) -> List[ExtractedField]:
    """Para birimi yazılmamış ama tutar sütununda ya da tutar etiketinin yanında olan sayılar (TRY sayılır)."""
    out = []

    def free(start: int, end: int) -> bool:
        return not any(s < end and start < e for s, e in taken)

    for header, _, cells in _table_rows(text):
        for (value, start, end), name in zip(cells, header):
            if not _AMOUNT_WORDS.search(turkish_lower(name)) or not _BARE_NUMBER.fullmatch(value) or not free(start, end):
                continue
            number = parse_number(value)
            if number is not None:
                out.append(_field("amount", text, start, end, format_amount(number, "TRY"), f"sütun: {name}"))
                taken.append((start, end))
    for m in _LABELED_BARE.finditer(text):
        label, raw = m.group(1), m.group(2)
        start = m.start(2)
        end = start + len(raw)
        lowered = turkish_lower(label)
        if (not _AMOUNT_WORDS.search(lowered) or _RATIO_LABEL.search(lowered) or not _BARE_NUMBER.fullmatch(raw)
                or not free(start, end)):
            continue
        number = parse_number(raw)
        if number is not None:
            out.append(_field("amount", text, start, end, format_amount(number, "TRY"), f"etiket: {label.strip()}"))
            taken.append((start, end))
    return out


# --- Kişi-rol, imza / onay, teminat, limit (M5.5) -----------------------------------------------------------

_UPPER = "A-ZÇĞİÖŞÜÂÎÛ"
_LOWER = "a-zçğıöşüâîû"
_NAME_TOKEN = rf"(?:[{_UPPER}][{_LOWER}]+|[{_UPPER}]{{2,}})"
_NAME_RE = re.compile(rf"(?<![\w.]){_NAME_TOKEN}(?:[ ]{_NAME_TOKEN}){{1,3}}(?:'[{_LOWER}]+)?(?![\w.])")
# Ad olamayacak kelimeler: unvan, birim, kurum, belge ve adres sözcükleri.
_NOT_NAME_WORDS = frozenset("""
şube şubesi müdür müdürü müdürlüğü müdürlüğüne yardımcısı yetkili yetkilisi operasyon yönetmeni kredi komitesi
komite kurulu kurul banka bankası bankamız bölge genel başkan başkanı başkanlığı başkanlığına teftiş iç kontrol
birimi birim merkez merkezi pazarlama müşteri temsilcisi veznedar hazırlayan kontrol eden onaylayan sayımı yapan
nezaret alacaklı borçlu kefil kefilin kefalet asıl hesap vadesiz vadeli türk lirası tl try usd eur ticari
bireysel ltd şti tic san a.ş anonim şirketi şirket holding sayın saygılarımızla bilgilerinize konu sayı tarih
imza imzası adı soyadı ad soyad eşi eşinin eş rızası t.c tckn vkn iban no numarası mah mahallesi cad caddesi sok
sokak bulvarı apt daire kat il ilçe dosya esas icra dairesi mahkemesi haciz ihbarnamesi tutanak tutanağı form
formu belge belgesi sözleşme sözleşmesi dekont dekontu talimat talimatı rapor raporu bulgu kasa atm sistem
toplam tutar tutarı dönem vade faiz limit teminat teminatlar ipotek rehin not açıklama ek ekler kayıt bilgi
bilgileri beyan beyanlar kimlik kimliği adres telefon eposta e-posta kime kimden ve ile de da için ortaklar
ortak yönetim kurulu üyesi masak bddk tcmb spk kkb findeks tbb kurumu kurumlar vergi vergisi dairesi
gerçek tüzel kişi kişisi merkezi ofisi mali tablo özeti rapor özet istihbarat ekspertiz değerleme kabul ret
""".split())
_ROLE_WORDS = re.compile(
    r"kefil|borçlu|müşteri|hazırlayan|yapan|kontrol\s+eden|onaylayan|sayımı\s+yapan|nezaret\s+eden|talimat\s*(?:ı)?\s*veren"
    r"|yatıran|adı\s+soyadı|ad\s+soyad|eşi|eşinin|hesap\s+sahibi|kimden|kime|bilgi|sorumlu|kapatan|teslim\s+alan"
    r"|veznedar|yetkili|temsilci|vekil|ortak|müdür|yönetmen|başkan|memur|uzman|şef|denetçi|müfettiş|imza"
    r"|düzenleyen|başvuran|şikayetçi|şikâyetçi|dilekçe\s+sahibi|alıcı|gönderen|lehdar|keşideci|ciranta")
_ROLE_TITLE = re.compile(
    r"(?:(?:şube|bölge|genel|operasyon|pazarlama|kredi|satış|gişe|kasa|servis|birim|hesap|müşteri|portföy)\s+){0,2}"
    r"(?:müdür\s+yardımcısı|müdürü|müdür|yönetmeni|yönetmen|yetkilisi|yetkili|temsilcisi|sorumlusu|şefi|memuru"
    r"|uzmanı|veznedar|icra\s+müdürü|müfettişi|müfettiş|denetçisi|başkanı|üyesi)")
_SEGMENT_END = re.compile(r"\s[—–-]\s|\(|;|\s{2,}|$")


def _person_names(segment: str, base: int) -> Iterator[Tuple[int, int, str]]:
    """Segmentteki ad dizileri: (başlangıç, bitiş, normalleştirilmiş ad). Unvan kelimeleri adın başından atılır
    ("Şube Müdürü Murat Aksoy" → "murat aksoy"); kurum adları ve unvan dizileri ad sayılmaz."""
    for m in _NAME_RE.finditer(segment):
        tokens = m.group(0).split(" ")
        tokens[-1] = tokens[-1].split("'")[0]
        while tokens and turkish_lower(tokens[0]) in _NOT_NAME_WORDS:
            tokens.pop(0)
        if len(tokens) < 2 or any(turkish_lower(t) in _NOT_NAME_WORDS for t in tokens):
            continue
        name = normalize_person(" ".join(tokens))
        if name:
            start = base + m.start() + m.group(0).find(tokens[0])
            yield start, start + len(" ".join(tokens)), name


def _person_fields(text: str) -> List[ExtractedField]:
    """Kişiler rolüyle: (a) rol etiketli satırlar ("Kefil: Kemal Özdemir", "Hazırlayan: Pazarlama Yetkilisi Burak
    Soylu — İmza"), (b) unvan + ad ("Şube Müdürü Murat Aksoy"), (c) ad + rol ("Onur Balcı, Veznedar";
    "Nazlı Erdem (Şube Müdür Yardımcısı)"), (d) kimlik numarasıyla anılan ad ("Hüseyin Toprak'ın (TCKN …)")."""
    found: Dict[int, ExtractedField] = {}

    def add(start: int, end: int, name: str, role: str, rule: str) -> None:
        # Adın ardından aynı satırda ayırıcıya kadar gelen kısım şirket ekiyle bitiyorsa kurum adıdır
        # ("Deniz Tekstil Konfeksiyon San. Tic. A.Ş.").
        line_end = text.find("\n", end)
        window = text[start:len(text) if line_end < 0 else line_end][:end - start + 40]
        window = re.split(r"[(;—–]|\s-\s", window, maxsplit=1)[0]
        if start not in found and not _COMPANY_RE.search(turkish_lower(window)):
            found[start] = _field("person", text, start, end, name, rule, role=role.strip(" :—-").lower())

    offset = 0
    for line in text.split("\n"):
        label_match = re.match(r"\s*([^:\n]{2,45}):\s*(.+)$", line)
        if label_match and _ROLE_WORDS.search(turkish_lower(label_match.group(1))):
            role, rest = label_match.group(1), label_match.group(2)
            rest_base = offset + label_match.start(2)
            for start, end, name in _person_names(rest, rest_base):
                add(start, end, name, role, "rol etiketi")
        for m in _ROLE_TITLE.finditer(turkish_lower(line)):
            after = line[m.end():]
            names = list(_person_names(after[:40], offset + m.end()))
            if names and names[0][0] - (offset + m.end()) <= 2:
                add(*names[0], line[m.start():m.end()], "unvan + ad")
        for start, end, name in _person_names(line, offset):
            tail = turkish_lower(line[end - offset:end - offset + 45])
            role_after = re.match(r"\s*(?:,|\(|[—–-])\s*(.{0,40})", tail)
            if role_after and _ROLE_TITLE.match(role_after.group(1)):
                add(start, end, name, _SEGMENT_END.split(role_after.group(1), 1)[0], "ad + rol")
            elif re.match(r"(?:'[a-zçğıöşü]+)?\s*[(,]?\s*(?:tckn|t\.c\.)", tail):
                add(start, end, name, "", "kimlik no ile")
        offset += len(line) + 1
    return [found[k] for k in sorted(found)]


_SIGNED = re.compile(r"\(\s*(?:e-?\s*)?imzal[ıi]\s*\)|\be-?\s*imzal[ıi]\b|\bimzalanm[ıi]şt[ıi]r\b|\bimzal[ıi]\b")
_EMPTY_SIGNATURE = re.compile(r"imza(?:sı)?\s*:\s*(?:\.{3,}|…+|—|-|_+|\s*$)")
_APPROVAL = re.compile(r"onaylan(?:dı|mıştır|mış)\b|\bonaylandi\b|\bonay\s*:\s*(?:var|verildi|evet)")
_EMPTY_APPROVAL = re.compile(r"(?:onay|komite(?:si)?|karar[ıi]?)\s*:\s*(?:—|-|\.{3,}|…+|yok)\s*$")


def _signature_fields(text: str, persons: List[ExtractedField]) -> List[ExtractedField]:
    """Satır başına imza / onay durumu: imzalı, e-imzalı, boş (imza yeri doldurulmamış), onaylandı, onay yok;
    satırdaki kişi ve rol eklenir."""
    out = []
    offset = 0
    for line in text.split("\n"):
        lower = turkish_lower(line)
        on_line = [p for p in persons if offset <= p.start < offset + len(line)]
        who = {"person": on_line[0].value, "role": on_line[0].attributes.get("role", "")} if on_line else {}
        checks = (
            ("signature", _SIGNED, lambda m: "e-imzalı" if "e" in m.group(0).split("imza")[0] else "imzalı"),
            ("signature", _EMPTY_SIGNATURE, lambda m: "boş"),
            ("approval", _APPROVAL, lambda m: "onaylandı"),
            ("approval", _EMPTY_APPROVAL, lambda m: "yok"),
        )
        for kind, pattern, status in checks:
            for m in pattern.finditer(lower):
                out.append(_field(kind, text, offset + m.start(), offset + m.end(), status(m), "satır",
                                  line=line.strip()[:120], **who))
        offset += len(line) + 1
    return out


_COLLATERAL_WORDS = (
    ("kefalet", r"kefalet|kefil"), ("ipotek", r"ipotek"), ("rehin", r"rehin"), ("temlik", r"temlik"),
    ("teminat_mektubu", r"teminat\s+mektub"), ("blokaj", r"bloke|blokaj"), ("teminat", r"teminat"),
)
_LIMIT_RE = re.compile(r"\blimit")
_AUTHORITY_RE = re.compile(r"yetki|komite|onay\s+sınır|kadar")


def _collateral_and_limit_fields(text: str, amounts: List[ExtractedField]) -> List[ExtractedField]:
    """Aynı satırda teminat sözcüğü ve tutar → teminat (türüyle); "limit" ve tutar → limit (kredi limiti ya da
    yetki limiti). Satırda birden çok tutar varsa ilki alınır."""
    out = []
    offset = 0
    for line in text.split("\n"):
        lower = turkish_lower(line)
        on_line = [a for a in amounts if offset <= a.start < offset + len(line)]
        if on_line:
            first = on_line[0]
            kind_name = next((name for name, pattern in _COLLATERAL_WORDS if re.search(pattern, lower)), "")
            if kind_name:
                out.append(_field("collateral", text, first.start, first.end, first.value, "satır",
                                  collateral_type=kind_name, line=line.strip()[:120]))
            if _LIMIT_RE.search(lower):
                scope = "yetki_limiti" if _AUTHORITY_RE.search(lower) else "kredi_limiti"
                out.append(_field("limit", text, first.start, first.end, first.value, "satır", scope=scope,
                                  line=line.strip()[:120]))
        offset += len(line) + 1
    return out


def extract_fields(text: str) -> List[ExtractedField]:
    """Belgedeki tüm alanlar, metindeki sırayla."""
    text = text or ""
    fields: List[ExtractedField] = []
    amount_spans: List[Tuple[int, int]] = []
    for start, end, value in iter_amounts(text):
        fields.append(_field("amount", text, start, end, value))
        amount_spans.append((start, end))
    fields += _bare_amount_fields(text, amount_spans)
    fields += [_field("date", text, s, e, v) for s, e, v in iter_dates(text)]
    fields += [_field("rate", text, s, e, v) for s, e, v in iter_rates(text)]
    fields += [_field("iban", text, s, e, v) for s, e, v in iter_ibans(text)]
    fields += _identity_fields(text)
    fields += _account_fields(text)
    persons = _person_fields(text)
    fields += persons
    fields += _signature_fields(text, persons)
    fields += _collateral_and_limit_fields(text, [f for f in fields if f.kind == "amount"])
    return sorted(fields, key=lambda f: (f.start, f.kind))


def field_values(fields: List[ExtractedField]) -> Dict[str, List[str]]:
    """Tür → benzersiz değerler (ilk görülme sırasıyla)."""
    out: Dict[str, List[str]] = {}
    for f in fields:
        values = out.setdefault(f.kind, [])
        if f.value not in values:
            values.append(f.value)
    return out


__all__ = [
    "parse_number", "format_amount", "iter_amounts", "find_amounts", "iter_dates", "find_dates", "format_rate",
    "iter_rates", "find_rates", "iter_ibans", "find_ibans", "mask_ibans", "find_tckns", "find_vkns",
    "tckn_checksum_ok", "vkn_checksum_ok", "ACCOUNT_DIGITS", "normalize_account", "normalize_person",
    "extract_fields", "field_values",
]
