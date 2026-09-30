# -*- coding: utf-8 -*-
"""Belge türü benchmark'ı: vaka biçimi, alan normalleştirme (M4.2).

benchmark.py "hangi hüküm dayanak oldu" sorusunu ölçer; burada ise belgenin kendisi ölçülür: türü doğru mu,
alanları (tutar, tarih, kimlik numarası, IBAN) çıkarıldı mı, uygunluk kararı (kusurlu / kusursuz) doğru mu.
Vakalar benchmarks/documents/*.json dosyalarındadır; sentetik örnekler scripts/import_synthetic.py ile gelir.

Alan değerleri karşılaştırılabilir tek biçime çevrilir: tutar "1250000.00 TRY", tarih "2023-04-05",
TCKN / VKN yalnızca rakam, IBAN boşluksuz büyük harf. Cevap anahtarı serbest yazılabildiği için ("Limit:
5.000.000 TL", {"tutar": "250.000,00 TL"}) değerler metin içinde aranır.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from mufettis.analysis import document_taxonomy as tax
from mufettis.analysis.field_extraction import (
    field_values, find_amounts, find_dates, find_ibans, find_rates, find_tckns, find_vkns, format_amount,
    format_rate, mask_ibans, normalize_account, normalize_person, parse_number,
)
from mufettis.analysis.turkish_text import turkish_lower

CASES_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "documents"
SPLITS = ("dev", "holdout", "sealed")
SEVERITIES = ("yüksek", "orta", "düşük", "")
FIELD_KINDS = ("amounts", "dates", "tckn", "vkn", "iban", "accounts", "rates", "persons")   # ölçülen alanlar
RAW_FIELD_KINDS = ("roles", "signatures")   # yalnızca saklanan (M9 kontrollerinin girdisi)


# --- Cevap anahtarını normalleştirme ------------------------------------------------------------------------
# Değer ayrıştırıcıları motorla ortaktır (field_extraction); cevap anahtarı serbest yazıldığı için burada daha
# hoşgörülü okunur (çıplak sayı, "ad: değer", iç içe sözlük).

_ACCOUNT_RE = re.compile(r"(?<![\w])\d[\d\s\-/]{4,30}\d(?![\w])")
_PERSON_SPLIT_RE = re.compile(r"\s[-–—]\s|\s*[:(,/]\s*|\s[-–—]$")


def key_amounts(entry: str) -> List[str]:
    """Cevap anahtarının "tutarlar" listesindeki bir kayıt. Para birimli tutarlar alınır; kayıt (ya da
    "ad: değer" kaydının değeri) yalnızca bir sayıysa TRY sayılır ("250.000,00", "kredi_limiti: 5000000")."""
    found = find_amounts(entry)
    if found:
        return found
    value = parse_number(entry.rsplit(":", 1)[-1].strip())
    return [format_amount(value, "TRY")] if value is not None else []


def key_rates(entry: str) -> List[str]:
    """"oranlar" kaydı yüzde işaretsiz yazılmışsa ("faiz: 54,00") değer tek başına oran sayılır."""
    value = parse_number(entry.rsplit(":", 1)[-1].strip())
    return [format_rate(value)] if value is not None else []


def key_accounts(entry: str) -> List[str]:
    """Cevap anahtarının "hesap_numaralari" kaydı → yalnızca rakam (IBAN ayrıca ölçülür, burada sayılmaz)."""
    value = mask_ibans(entry.rsplit(":", 1)[-1])
    return [a for a in (normalize_account(m.group(0)) for m in _ACCOUNT_RE.finditer(value)) if a]


def key_person(entry: Any) -> Optional[str]:
    """Cevap anahtarının "kisiler_ve_rolleri" kaydı ("Ad Soyad - rol" ya da {"ad": ..., "rol": ...}) → ad."""
    if isinstance(entry, dict):
        for key in ("ad_soyad", "adi_soyadi", "ad", "isim", "kisi", "name"):
            if entry.get(key):
                return normalize_person(str(entry[key]))
        return None
    return normalize_person(_PERSON_SPLIT_RE.split(str(entry), 1)[0])


def flatten_text(value: Any) -> Iterator[str]:
    """Cevap anahtarındaki iç içe liste / sözlükten metin parçaları (sözlük anahtarları dahil)."""
    if value is None:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            for text in flatten_text(item):
                yield f"{key}: {text}"
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from flatten_text(item)
    else:
        yield str(value)


def _unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(v for v in values if v))


# --- Vaka biçimi ------------------------------------------------------------------------------------------

@dataclass
class ExpectedFields:
    amounts: List[str] = field(default_factory=list)
    dates: List[str] = field(default_factory=list)
    tckn: List[str] = field(default_factory=list)
    vkn: List[str] = field(default_factory=list)
    iban: List[str] = field(default_factory=list)
    accounts: List[str] = field(default_factory=list)
    rates: List[str] = field(default_factory=list)
    persons: List[str] = field(default_factory=list)     # normalleştirilmiş ad ("ahmet yılmaz")
    roles: List[str] = field(default_factory=list)       # ham "Ad Soyad - rol" kayıtları
    signatures: List[str] = field(default_factory=list)
    other: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_answer_key(cls, alanlar: Dict[str, Any]) -> "ExpectedFields":
        """BRIEF cevap anahtarının "alanlar" nesnesi → normalleştirilmiş alanlar. Kimlik numaraları yalnızca
        "kimlik_numaralari"ndan alınır (başka yerdeki 10-11 haneli sayılar telefon olabilir); IBAN her yerden."""
        alanlar = alanlar or {}
        amounts = [a for t in flatten_text(alanlar.get("tutarlar")) for a in key_amounts(t)]
        ids = list(flatten_text(alanlar.get("kimlik_numaralari")))
        people = alanlar.get("kisiler_ve_rolleri") or []
        people = people if isinstance(people, list) else [people]
        return cls(
            amounts=_unique(amounts),
            dates=_unique(d for t in flatten_text(alanlar.get("tarihler")) for d in find_dates(t)),
            tckn=_unique(n for t in ids for n in find_tckns(t)),
            vkn=_unique(n for t in ids for n in find_vkns(t)),
            iban=_unique(i for t in flatten_text(alanlar) for i in find_ibans(t)),
            accounts=_unique(a for t in flatten_text(alanlar.get("hesap_numaralari")) for a in key_accounts(t)),
            rates=_unique(r for t in flatten_text(alanlar.get("oranlar")) for r in find_rates(t) or key_rates(t)),
            persons=_unique(n for n in map(key_person, people) if n),
            roles=_unique(flatten_text(people)),
            signatures=_unique(flatten_text(alanlar.get("imza_onay"))),
            other=alanlar.get("diger") if isinstance(alanlar.get("diger"), dict) else {},
        )


@dataclass
class Defect:
    description: str
    quote: str = ""          # belgedeki yer; metinde bulunamazsa boş (bkz. locate_quote)
    regulation: str = ""
    severity: str = ""
    scope: str = "belge"     # "belge" ya da "dosya" (D01: belgeler arası kusur)


@dataclass
class SubDocument:
    document_type: str
    title: str
    text: str


@dataclass
class DocumentCase:
    case_id: str
    split: str
    document_type: str       # tek belgede kanonik kod; dosya paketinde "" (türler documents içinde)
    text: str
    compliant: bool
    defects: List[Defect] = field(default_factory=list)
    fields: ExpectedFields = field(default_factory=ExpectedFields)
    documents: List[SubDocument] = field(default_factory=list)
    issue_date: str = ""
    event_date: str = ""
    source: Dict[str, str] = field(default_factory=dict)   # model, task, file

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentCase":
        case = cls(
            case_id=data["id"], split=data.get("split", "dev"), document_type=data.get("document_type", ""),
            text=data.get("text", ""), compliant=bool(data["compliant"]),
            defects=[Defect(**d) for d in data.get("defects", [])],
            fields=ExpectedFields(**data.get("fields", {})),
            documents=[SubDocument(**d) for d in data.get("documents", [])],
            issue_date=data.get("issue_date", ""), event_date=data.get("event_date", ""),
            source=dict(data.get("source", {})),
        )
        problems = case.problems()
        if problems:
            raise ValueError(f"{case.case_id}: " + "; ".join(problems))
        return case

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["id"] = data.pop("case_id")
        return data

    def problems(self) -> List[str]:
        """Vakayı ölçüme uygunsuz kılan kusurlar (boşsa geçerli)."""
        out = []
        if self.split not in SPLITS:
            out.append(f"bilinmeyen bölüm '{self.split}'")
        if not self.text.strip():
            out.append("metin boş")
        if self.documents:
            if self.document_type:
                out.append("dosya paketinde tür belgelerde yazılır")
            for doc in self.documents:
                if doc.document_type not in tax.CODES:
                    out.append(f"bilinmeyen belge türü '{doc.document_type}' ({doc.title})")
        elif self.document_type not in tax.CODES:
            out.append(f"bilinmeyen belge türü '{self.document_type}'")
        if self.compliant and self.defects:
            out.append("kusursuz denmiş ama kusur listelenmiş")
        if not self.compliant and not self.defects:
            out.append("kusurlu denmiş ama kusur yok")
        for name in ("issue_date", "event_date"):
            value = getattr(self, name)
            if value and find_dates(value) != [value]:
                out.append(f"{name} ISO tarih değil: '{value}'")
        for defect in self.defects:
            if defect.severity not in SEVERITIES:
                out.append(f"bilinmeyen önem '{defect.severity}'")
            if defect.scope not in ("belge", "dosya"):
                out.append(f"bilinmeyen kusur kapsamı '{defect.scope}'")
        for kind in FIELD_KINDS:
            for value in getattr(self.fields, kind):
                if not _FIELD_FORMAT[kind].fullmatch(value):
                    out.append(f"{kind} normalleştirilmemiş: '{value}'")
        return out


_FIELD_FORMAT = {
    "amounts": re.compile(r"\d+\.\d{2} [A-Z]{3}"),
    "dates": re.compile(r"\d{4}-\d{2}-\d{2}"),
    "tckn": re.compile(r"[1-9]\d{10}"),
    "vkn": re.compile(r"\d{10}"),
    "iban": re.compile(r"TR\d{24}"),
    "accounts": re.compile(r"\d{6,20}"),
    "rates": re.compile(r"\d+\.\d{2}"),
    "persons": re.compile(r"[a-zçğıöşüâîû]+(?: [a-zçğıöşüâîû]+){1,3}"),
}


def _quote_key(text: str) -> str:
    text = turkish_lower(text or "")
    text = re.sub(r"[\"'“”‘’«»]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def locate_quote(quote: str, text: str) -> bool:
    """Kusur alıntısı belgede var mı? Büyük / küçük harf, tırnak ve boşluk farkı sayılmaz; "..." ile
    kısaltılmış alıntının her parçası sırayla aranır."""
    haystack = _quote_key(text)
    position = 0
    parts = [p for p in (_quote_key(part) for part in re.split(r"\.\.\.|…|\[\.\.\.\]", quote or "")) if p]
    if not parts:
        return False
    for part in parts:
        found = haystack.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    return True


def load_cases(path: Path) -> List[DocumentCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [DocumentCase.from_dict(c) for c in data["cases"]]


def load_case_dir(directory: Path = CASES_DIR, include_sealed: bool = False) -> List[DocumentCase]:
    """Dizindeki tüm vaka dosyaları; mühürlü bölüm yalnızca açıkça istenince (son doğrulama)."""
    cases: List[DocumentCase] = []
    for path in sorted(Path(directory).glob("*.json")):
        cases += [c for c in load_cases(path) if include_sealed or c.split != "sealed"]
    ids = [c.case_id for c in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"yinelenen vaka kimlikleri: {duplicates}")
    return cases


def write_cases(path: Path, cases: Iterable[DocumentCase], note: str = "") -> None:
    payload = {"note": note, "cases": [c.to_dict() for c in cases]}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --- Tahmin ve metrikler (M4.4) ---------------------------------------------------------------------------

# Uygunluk kararı sonuçları
DECISION_CORRECT = "correct"          # kusurlu belgeye bulgu ya da kusursuz belgeye bulgu yok
MISSED_DEFECT = "missed_defect"       # kusurlu belge kusursuz sanıldı (bulgu yok)
FALSE_ALARM = "false_alarm"           # kusursuz belgeye bulgu üretildi


@dataclass
class DocumentPrediction:
    """Motorun bir vaka için söyledikleri. document_types: tek belgede bir öğe, dosya paketinde belge sırasıyla.
    defective None ise karar ölçülmedi (understanding seviyesi)."""
    document_types: List[str]
    fields: Dict[str, List[str]]
    defective: Optional[bool] = None
    findings: List[str] = field(default_factory=list)


# Alan çıkarıcısının türleri (models.ExtractedField.kind) → benchmark alanları
_FIELD_KIND_MAP = {"amount": "amounts", "date": "dates", "tckn": "tckn", "vkn": "vkn", "iban": "iban",
                   "account": "accounts", "rate": "rates", "person": "persons"}


def predicted_fields(doc: Any) -> Dict[str, List[str]]:
    """DocumentUnderstanding.fields → ölçülen alanlar. Yalnızca motorun çıkardığı değerler kullanılır; belge
    metni burada yeniden taranmaz (yoksa motor değil benchmark'ın kendisi ölçülürdü)."""
    values = field_values(list(doc.fields))
    return {kind: list(values.get(engine_kind, [])) for engine_kind, kind in _FIELD_KIND_MAP.items()}


@dataclass
class DocumentCaseResult:
    case_id: str
    split: str
    expected_types: List[str]
    predicted_types: List[str]
    field_counts: Dict[str, Dict[str, int]]          # alan türü → {tp, fp, fn}
    decision: Optional[str] = None
    compliant: bool = True
    missing_fields: Dict[str, List[str]] = field(default_factory=dict)
    extra_fields: Dict[str, List[str]] = field(default_factory=dict)
    findings: List[str] = field(default_factory=list)
    seconds: float = 0.0


def evaluate_document_case(case: DocumentCase, prediction: DocumentPrediction,
                           seconds: float = 0.0) -> DocumentCaseResult:
    expected_types = [d.document_type for d in case.documents] if case.documents else [case.document_type]
    if len(prediction.document_types) != len(expected_types):
        raise ValueError(f"{case.case_id}: {len(expected_types)} belge türü beklenirken "
                         f"{len(prediction.document_types)} tahmin")
    counts, missing, extra = {}, {}, {}
    for kind in FIELD_KINDS:
        want, got = set(getattr(case.fields, kind)), set(prediction.fields.get(kind, []))
        counts[kind] = {"tp": len(want & got), "fp": len(got - want), "fn": len(want - got)}
        if want - got:
            missing[kind] = sorted(want - got)
        if got - want:
            extra[kind] = sorted(got - want)
    decision = None
    if prediction.defective is not None:
        if prediction.defective == (not case.compliant):
            decision = DECISION_CORRECT
        else:
            decision = MISSED_DEFECT if not case.compliant else FALSE_ALARM
    return DocumentCaseResult(
        case_id=case.case_id, split=case.split, expected_types=expected_types,
        predicted_types=list(prediction.document_types), field_counts=counts, decision=decision,
        compliant=case.compliant, missing_fields=missing, extra_fields=extra, findings=list(prediction.findings),
        seconds=round(seconds, 2),
    )


def _ratio(a: int, b: int) -> Optional[float]:
    return round(a / b, 3) if b else None


def summarize_documents(results: Sequence[DocumentCaseResult]) -> Dict[str, Dict[str, Any]]:
    """Bölüm başına (ve "all"): tür doğruluğu + karışıklık, alan türü başına kesinlik / duyarlılık,
    uygunluk kararı; tür başına kırılım."""
    out: Dict[str, Dict[str, Any]] = {}
    for split in sorted({r.split for r in results}) + ["all"]:
        rs = [r for r in results if split in ("all", r.split)]
        pairs = [(e, p) for r in rs for e, p in zip(r.expected_types, r.predicted_types)]
        confusion: Dict[str, Dict[str, int]] = {}
        by_type: Dict[str, Dict[str, int]] = {}
        for expected, predicted in pairs:
            row = confusion.setdefault(expected, {})
            row[predicted] = row.get(predicted, 0) + 1
            counts = by_type.setdefault(expected, {"documents": 0, "type_correct": 0, "decided": 0,
                                                   "decision_correct": 0})
            counts["documents"] += 1
            counts["type_correct"] += int(expected == predicted)
        fields_summary = {}
        for kind in FIELD_KINDS:
            tp, fp, fn = (sum(r.field_counts[kind][k] for r in rs) for k in ("tp", "fp", "fn"))
            fields_summary[kind] = {"tp": tp, "fp": fp, "fn": fn,
                                    "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn)}
        decided = [r for r in rs if r.decision is not None]
        for r in decided:
            if len(r.expected_types) == 1:            # dosya paketinin kararı tek bir türe yazılmaz
                by_type[r.expected_types[0]]["decided"] += 1
                by_type[r.expected_types[0]]["decision_correct"] += int(r.decision == DECISION_CORRECT)
        type_correct = sum(1 for e, p in pairs if e == p)
        out[split] = {
            "cases": len(rs),
            "documents": len(pairs),
            "type_correct": type_correct,
            "type_accuracy": _ratio(type_correct, len(pairs)),
            "confusion": confusion,
            "fields": fields_summary,
            "decided": len(decided),
            "decision_correct": sum(1 for r in decided if r.decision == DECISION_CORRECT),
            "missed_defects": sum(1 for r in decided if r.decision == MISSED_DEFECT),
            "false_alarms": sum(1 for r in decided if r.decision == FALSE_ALARM),
            "by_type": by_type,
        }
    return out


def compare_document_results(previous: Sequence[Dict[str, Any]],
                             current: Sequence[DocumentCaseResult]) -> List[str]:
    """Önceki koşuya göre türü, kararı ya da alan eşleşmesi değişen vakalar."""
    before = {r["case_id"]: r for r in previous}
    lines = []
    for r in current:
        old = before.get(r.case_id)
        if not old:
            continue
        changes = []
        if old["predicted_types"] != r.predicted_types:
            changes.append(f"tür {old['predicted_types']} -> {r.predicted_types}")
        if old.get("decision") != r.decision:
            changes.append(f"karar {old.get('decision')} -> {r.decision}")
        changed_fields = [k for k in FIELD_KINDS if old["field_counts"].get(k) != r.field_counts[k]]
        if changed_fields:
            changes.append("alan " + ", ".join(changed_fields))
        if changes:
            lines.append(f"{r.case_id}: " + "; ".join(changes))
    return lines


def document_results_to_json(results: Sequence[DocumentCaseResult]) -> List[Dict[str, Any]]:
    return [asdict(r) for r in results]


# --- Koşu ve taban (M4.5) ---------------------------------------------------------------------------------

LEVELS = ("understanding", "decision")
DOCUMENT_BASELINE_FILE = Path(__file__).resolve().parents[2] / "benchmarks" / "document_baseline.json"


def understand(text: str, doc_service: Any = None, classifier: Any = None) -> Tuple[str, Dict[str, List[str]]]:
    """Belge anlama + sınıflandırma (korpussuz): kanonik tür ve alanlar."""
    from mufettis.analysis.classifier import DocumentClassifierService
    from mufettis.analysis.document_understanding import DocumentUnderstandingService

    doc = (doc_service or DocumentUnderstandingService()).analyze(text)
    classification = (classifier or DocumentClassifierService()).classify(doc)
    return tax.from_engine(classification.document_type), predicted_fields(doc)


def predict_understanding(case: DocumentCase, doc_service: Any = None, classifier: Any = None) -> DocumentPrediction:
    """understanding seviyesi: tür (dosya paketinde belge başına) ve alanlar (tüm metinden); karar yok."""
    doc_type, fields = understand(case.text, doc_service, classifier)
    types = [understand(d.text, doc_service, classifier)[0] for d in case.documents] if case.documents else [doc_type]
    return DocumentPrediction(document_types=types, fields=fields)


def predict_decision(case: DocumentCase, execution: Any, doc_service: Any = None,
                     classifier: Any = None) -> DocumentPrediction:
    """decision seviyesi: tam hattın sonucu (AnalysisPresenter.execute_analysis). Bugünkü karar tanımı: en az bir
    bulgu = kusurlu (M9'da şart kataloğunun kararı gelir). Dosya paketinde tür yine belge başına anlamadan."""
    if not execution.success or execution.result is None:
        raise RuntimeError(f"{case.case_id}: analiz başarısız: {execution.error_message}")
    result = execution.result
    types = ([understand(d.text, doc_service, classifier)[0] for d in case.documents] if case.documents
             else [tax.from_engine(result.classification.document_type)])
    return DocumentPrediction(
        document_types=types, fields=predicted_fields(result.document), defective=bool(result.findings),
        findings=[f.regulation_reference for f in result.findings],
    )


def case_set_fingerprint(cases: Iterable[DocumentCase]) -> str:
    """Vaka kümesinin kimliği: vaka eklenince / değişince taban sayıları anlamını yitirir."""
    import hashlib

    payload = json.dumps(sorted((c.to_dict() for c in cases), key=lambda d: d["id"]), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# (metrik yolu, daha büyük daha iyi mi, seviye); alan metrikleri her iki seviyede ölçülür
_BASELINE_METRICS: Tuple[Tuple[str, bool, str], ...] = (
    ("type_correct", True, "understanding"),
    *((f"fields.{k}.tp", True, "understanding") for k in FIELD_KINDS),
    *((f"fields.{k}.fp", False, "understanding") for k in FIELD_KINDS),
    ("decision_correct", True, "decision"),
    ("missed_defects", False, "decision"),
    ("false_alarms", False, "decision"),
)


def _metric(split_summary: Dict[str, Any], path: str) -> Any:
    value: Any = split_summary
    for part in path.split("."):
        value = value[part]
    return value


def baseline_metrics(summary: Dict[str, Dict[str, Any]], level: str) -> Dict[str, Dict[str, Any]]:
    wanted = [m for m, _, lv in _BASELINE_METRICS if lv == "understanding" or lv == level]
    return {split: {m: _metric(values, m) for m in wanted} for split, values in summary.items()}


def check_document_baseline(summary: Dict[str, Dict[str, Any]], baseline_level: Dict[str, Any],
                            fingerprint: str) -> List[str]:
    """Tabana göre kötüleşen metrikler (boşsa geçti). Vaka kümesi değiştiyse sayılar karşılaştırılamaz."""
    if baseline_level.get("cases_fingerprint") != fingerprint:
        return [f"vaka kümesi değişti (taban {baseline_level.get('cases_fingerprint')}, şimdi {fingerprint}); "
                "taban bilinçli olarak yeniden yazılmalı (--write-baseline)"]
    problems = []
    better = {m: higher for m, higher, _ in _BASELINE_METRICS}
    for split, expected in (baseline_level.get("splits") or {}).items():
        got = summary.get(split)
        if got is None:
            problems.append(f"{split}: sonuç yok")
            continue
        for metric, value in expected.items():
            now = _metric(got, metric)
            if (now < value) if better[metric] else (now > value):
                problems.append(f"{split}.{metric}: {now} (taban {value})")
    return problems


def baseline_level_entry(summary: Dict[str, Dict[str, Any]], level: str, fingerprint: str, note: str,
                         reranker: str = "") -> Dict[str, Any]:
    entry = {"note": note, "cases_fingerprint": fingerprint, "splits": baseline_metrics(summary, level)}
    if level == "decision":
        entry["reranker"] = reranker
    return entry


__all__ = [
    "CASES_DIR", "SPLITS", "SEVERITIES", "FIELD_KINDS", "RAW_FIELD_KINDS",
    "parse_number", "format_amount", "find_amounts", "key_amounts", "find_rates", "key_rates", "key_accounts",
    "normalize_account", "normalize_person", "key_person", "find_dates", "find_ibans", "find_tckns", "find_vkns",
    "flatten_text", "ExpectedFields", "Defect", "SubDocument", "DocumentCase", "locate_quote",
    "load_cases", "load_case_dir", "write_cases",
    "DECISION_CORRECT", "MISSED_DEFECT", "FALSE_ALARM", "DocumentPrediction", "predicted_fields",
    "DocumentCaseResult", "evaluate_document_case", "summarize_documents", "compare_document_results",
    "document_results_to_json",
    "LEVELS", "DOCUMENT_BASELINE_FILE", "understand", "predict_understanding", "predict_decision",
    "case_set_fingerprint", "baseline_metrics", "check_document_baseline", "baseline_level_entry",
]
