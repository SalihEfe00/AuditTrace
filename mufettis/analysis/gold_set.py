# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Stage 3 Analysis Intelligence
Semantic Gold Set & Evaluation Harness.

Provides 18 realistic, anonymized / synthetic test cases covering consumer
complaints, internal audit correspondence, and negative/unsupported controls.
Evaluates semantic correctness, evidence grounding, citation validity, and
false positive/negative rates.

Tüm kanıt metinleri SENTETİKTİR (gerçek mevzuat metni değildir); yalnızca
motorun iddia/kanıt alakasını ölçmek için yazılmıştır.

Vaka ilkeleri (Aşama C4):
- Pozitif vakalar yalnızca konuyla gerçekten ilgili kanıtla eşleştirilir; anlamca
  ilgisiz bir kanıtla "bulgu beklemek" yanlış pozitifi ödüllendirir.
- Çeldirici vakalar (TC-15..18) gerçek şikâyet/tespit metinlerini bankacılık dili
  taşıyan ama konu dışı kanıtlarla eşleştirir ve bulgu BEKLEMEZ; yanlış pozitif
  riskini ölçen asıl vakalar bunlardır.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mufettis.analysis.classifier import DocumentClassifierService
from mufettis.analysis.document_understanding import DocumentUnderstandingService
from mufettis.analysis.engine import AnalysisEngine
from mufettis.analysis.models import AnalysisResult, Evidence
from mufettis.analysis.planner import AnalysisPlannerService
from mufettis.analysis.retrieval_planner import RetrievalPlannerService


@dataclass
class GoldTestCase:
    case_id: str
    description: str
    input_text: str
    expected_document_type: str
    expected_subject: str
    expected_analysis_type: str
    expected_has_findings: bool
    expected_unsupported_claim: bool
    mock_evidence: List[Evidence] = field(default_factory=list)


@dataclass
class CaseEvaluationResult:
    case_id: str
    description: str
    outcome: str  # "PASS", "PARTIAL", "FAIL"
    doc_type_actual: str
    doc_type_expected: str
    doc_type_match: bool
    findings_count: int
    unsupported_count: int
    citations_verified: int
    details: List[str] = field(default_factory=list)


@dataclass
class GoldSetEvaluationReport:
    total_cases: int
    pass_count: int
    partial_count: int
    fail_count: int
    accuracy: float
    false_positives: int
    false_negatives: int
    unsupported_claims_caught: int
    case_results: List[CaseEvaluationResult] = field(default_factory=list)


def build_semantic_gold_set() -> List[GoldTestCase]:
    """Builds 14 realistic, anonymized test cases with synthetic/mock regulation evidence."""

    # Shared mock evidence items
    ev_fee_regulation = Evidence(
        source_document="Finansal Tüketicilerden Alınacak Ücretlere İlişkin Yönetmelik",
        document_identity="29138",
        chunk_reference="Madde 13 Fıkra 1",
        text_span=(
            "Bankalar tarafından finansal tüketicilere sunulan vadesiz mevduat hesaplarından "
            "hesap işletim ücreti veya benzeri adlar altında herhangi bir ücret alınamaz."
        ),
        metadata={"law_number": "5411", "source": "BDDK", "file_hash": "fee_hash_01"},
    )

    ev_unauthorized_tx = Evidence(
        source_document="5464 Sayılı Banka Kartları ve Kredi Kartları Kanunu",
        document_identity="5464",
        chunk_reference="Madde 12 Fıkra 2",
        text_span=(
            "Kart hamili, yetkisiz veya hileli bir işlemin varlığı halinde bankayı derhal haberdar eder; "
            "kart çıkaran kuruluş, bildirimi müteakip derhal kartı kullanıma kapatır ve şüpheli işlemi askıya alır."
        ),
        metadata={"law_number": "5464", "source": "Resmi Gazete", "file_hash": "tx_hash_02"},
    )

    ev_credit_prepayment = Evidence(
        source_document="6502 Sayılı Tüketicinin Korunması Hakkında Kanun",
        document_identity="6502",
        chunk_reference="Madde 28 Fıkra 1",
        text_span=(
            "Tüketici, konut finansmanı sözleşmesi kapsamındaki borcun tamamını veya bir kısmını "
            "vadesinden önce ödeyebilir. Erken ödeme halinde faiz ve diğer masraflarda gerekli indirim yapılır."
        ),
        metadata={"law_number": "6502", "source": "Resmi Gazete", "file_hash": "credit_hash_03"},
    )

    ev_authority_limits = Evidence(
        source_document="Bankaların İç Sistemleri ve İç Kontrol Yönetmeliği",
        document_identity="26333",
        chunk_reference="Madde 19 Fıkra 3",
        text_span=(
            "Kredi tahsis yetkileri yönetim kurulu tarafından belirlenen limitler dahilinde devredilebilir; "
            "şube müdürlükleri belirlenen onay sınırlarını aşan kredi kullandırım işlemlerini genel müdürlüğe intikal ettirir."
        ),
        metadata={"law_number": "5411", "source": "BDDK", "file_hash": "audit_hash_04"},
    )

    ev_masak_reporting = Evidence(
        source_document="5549 Sayılı Suç Gelirlerinin Aklanmasının Önlenmesi Kanunu",
        document_identity="5549",
        chunk_reference="Madde 4 Fıkra 1",
        text_span=(
            "Yükümlüler nezdinde yapılan veya yapılmaya teşebbüs edilen işlemlere konu malvarlığının "
            "yasa dışı yollardan elde edildiğine dair şüphe bulunması halinde durum en geç on iş günü içinde Başkanlığa bildirilir."
        ),
        metadata={"law_number": "5549", "source": "MASAK", "file_hash": "masak_hash_05"},
    )

    ev_card_fee = Evidence(
        source_document="Finansal Tüketicilerden Alınacak Ücretlere İlişkin Yönetmelik",
        document_identity="29138",
        chunk_reference="Madde 10 Fıkra 2",
        text_span=(
            "Kredi kartı yıllık üyelik aidatı ve benzeri kart ücretleri, finansal tüketicinin açık onayı "
            "alınmadan ve ücret tutarı önceden bildirilmeden tahsil edilemez."
        ),
        metadata={"law_number": "5411", "source": "BDDK", "file_hash": "card_fee_hash_06"},
    )

    ev_collateral_valuation = Evidence(
        source_document="Kredi İşlemlerine İlişkin Yönetmelik",
        document_identity="kredi_islemleri",
        chunk_reference="Madde 14 Fıkra 4",
        text_span=(
            "Teminat olarak alınan gayrimenkullerin ekspertiz değerleme raporları en geç yılda bir yenilenir; "
            "süresinde yenilenmeyen raporlar teminat değerinin tespitinde dikkate alınmaz."
        ),
        metadata={"law_number": "5411", "source": "BDDK", "file_hash": "collateral_hash_07"},
    )

    ev_payment_refund = Evidence(
        source_document="6493 Sayılı Ödeme ve Menkul Kıymet Mutabakat Sistemleri Kanunu",
        document_identity="6493",
        chunk_reference="Madde 50 Fıkra 1",
        text_span=(
            "Ödeme hizmeti kullanıcısının onayı olmaksızın gerçekleştirilen veya mükerrer olarak "
            "gerçekleştirilen ödeme işlemlerinde tutar gecikmeksizin kullanıcının hesabına iade edilir."
        ),
        metadata={"law_number": "6493", "source": "Resmi Gazete", "file_hash": "payment_hash_08"},
    )

    ev_it_incident = Evidence(
        source_document="Bankaların Bilgi Sistemleri ve Elektronik Bankacılık Hizmetleri Hakkında Yönetmelik",
        document_identity="bilgi_sistemleri",
        chunk_reference="Madde 9 Fıkra 3",
        text_span=(
            "Bilgi sistemlerinde meydana gelen ve hizmet sürekliliğini etkileyen plansız kesintiler ile "
            "veri güvenliği olayları Kuruma gecikmeksizin bildirilir."
        ),
        metadata={"law_number": "5411", "source": "BDDK", "file_hash": "it_hash_09"},
    )

    ev_account_block = Evidence(
        source_document="Mevduat Hesaplarına İlişkin Usul ve Esaslar",
        document_identity="mevduat_usul",
        chunk_reference="Madde 6 Fıkra 1",
        text_span=(
            "Vadesiz mevduat hesaplarına bloke, ancak mahkeme veya icra kararı ya da kanuni bir dayanak "
            "bulunması halinde konulabilir ve hesap sahibine bildirilir."
        ),
        metadata={"law_number": "5411", "source": "BDDK", "file_hash": "block_hash_10"},
    )

    # Çeldiriciler: bankacılık dili taşıyan, şikâyet/tespit konularıyla ilgisiz parçalar.
    distractors = [
        Evidence(
            source_document="Tekdüzen Hesap Planı",
            document_identity="tekduzen",
            chunk_reference="Hesap 100",
            text_span=(
                "Kasa hesabı, bankanın elindeki Türk parası nakit mevcudunu izler. "
                "Hesap işleyişi şube bazında tutulur."
            ),
            metadata={"source": "BDDK", "file_hash": "distractor_01"},
        ),
        Evidence(
            source_document="Bağımsız Denetim Yönetmeliği",
            document_identity="bagimsiz_denetim",
            chunk_reference="Madde 9",
            text_span=(
                "Bağımsız denetim kuruluşu, müşteri bankanın finansal tablolarını denetim standartlarına "
                "göre inceler ve rapor düzenler."
            ),
            metadata={"source": "BDDK", "file_hash": "distractor_02"},
        ),
        Evidence(
            source_document="Bankaların Kuruluş ve Faaliyet İzinleri Yönetmeliği",
            document_identity="kurulus_izni",
            chunk_reference="Madde 4",
            text_span=(
                "Banka kurulabilmesi için kurucuların asgari ödenmiş sermaye şartını sağlaması ve "
                "faaliyet izni başvurusunun Kurula yapılması gerekir."
            ),
            metadata={"source": "BDDK", "file_hash": "distractor_03"},
        ),
    ]

    return [
        # 1. Complaint - Fee
        GoldTestCase(
            case_id="TC-01",
            description="Banka hesap işletim ücreti kesintisi şikâyeti",
            input_text=(
                "Ziraat Bankası Kadıköy Şubesi hesabımdan 15.02.2026 tarihinde onayım olmadan 450 TL "
                "hesap işletim ücreti kesilmiştir. Kesilen tutarın derhal iadesini talep ediyorum ve şikayetçiyim."
            ),
            expected_document_type="complaint",
            expected_subject="ücret_ve_komisyon",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_fee_regulation],
        ),
        # 2. Complaint - Unauthorized transaction
        GoldTestCase(
            case_id="TC-02",
            description="Kredi kartından habersiz nakit çekim şikâyeti",
            input_text=(
                "Garanti BBVA kredi kartımdan bilgim dışında 3.500 TL nakit avans çekilmiş olup "
                "derhal müşteri hizmetlerini aramama rağmen işlem iptal edilmemiştir. Mağdur edildim."
            ),
            expected_document_type="complaint",
            expected_subject="yetkisiz_islem",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_unauthorized_tx],
        ),
        # 3. Complaint - Early payment penalty
        GoldTestCase(
            case_id="TC-03",
            description="Konut kredisi erken ödeme tazminatı şikâyeti",
            input_text=(
                "Akbank konut kredisi borcumu vadesinden önce 20.01.2026 tarihinde kapattım ancak banka benden "
                "yasal limitlerin üzerinde 12.000 TL erken ödeme cezası tahsil etti. Haksız kesintinin iadesini istiyorum."
            ),
            expected_document_type="complaint",
            expected_subject="kredi_ve_faiz",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_credit_prepayment],
        ),
        # 4. Internal Correspondence - Authority Limit
        GoldTestCase(
            case_id="TC-04",
            description="Şube kredi tahsis yetki aşımı iç denetim notu",
            input_text=(
                "Sayı: 2026/894\n"
                "Konu: Kredi Tahsis Yetki Aşımı İncelemesi\n"
                "Teftiş Kurulu Başkanlığına\n"
                "Kadıköy Şubesi tarafından 15.000.000 TL tutarındaki ticari kredinin "
                "yönetim kurulu onay limiti aşılarak kullandırıldığı tespit edilmiştir.\n"
                "Gereğini bilgilerinize arz ederim."
            ),
            expected_document_type="internal_correspondence",
            expected_subject="kredi_ve_faiz",
            expected_analysis_type="compliance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_authority_limits],
        ),
        # 5. Internal Correspondence - MASAK Reporting Delay
        GoldTestCase(
            case_id="TC-05",
            description="Şüpheli işlem bildirimi gecikmesi iç raporu",
            input_text=(
                "Sayı: 2026/MAS-12\n"
                "Konu: Şüpheli İşlem Bildirim Süresi İhlali\n"
                "Uyum Birimi Müdürlüğüne\n"
                "Şube tarafından tespit edilen 500.000 TL tutarındaki şüpheli nakit transferinin "
                "yasal süre olan 10 iş günü aşılarak bildirildiği belirlenmiştir.\n"
                "Bilgilerinize arz ederim."
            ),
            expected_document_type="internal_correspondence",
            expected_subject="odeme_ve_transfer",
            expected_analysis_type="compliance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_masak_reporting],
        ),
        # 6. Negative Case - Unrelated Text (Zero findings expected)
        GoldTestCase(
            case_id="TC-06",
            description="Mevzuatla ilgisi olmayan genel metin",
            input_text=(
                "İstanbul'da bu hafta hava sıcaklığı mevsim normallerinin üzerinde seyredecek. "
                "Meteoroloji Genel Müdürlüğü hafta sonu için yağış uyarısında bulundu."
            ),
            expected_document_type="unknown",
            expected_subject="",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=False,
            expected_unsupported_claim=False,
            mock_evidence=[],
        ),
        # 7. Negative Case - Grievance with Zero Evidence (Unsupported Claim Guard)
        GoldTestCase(
            case_id="TC-07",
            description="Mevzuat dayanağı bulunmayan asılsız şikâyet",
            input_text=(
                "Banka görevlisi bana yeterince güleryüz göstermedi ve işlem sırasında çay ikram etmedi. "
                "Bu durumdan son derece mağdur oldum, şikayetçiyim ve tazminat talep ediyorum."
            ),
            expected_document_type="complaint",
            expected_subject="genel_sikayet",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=False,
            expected_unsupported_claim=True,
            mock_evidence=[],  # No relevant banking regulations for tea service
        ),
        # 8. Complaint - Credit Card Annual Fee
        GoldTestCase(
            case_id="TC-08",
            description="Kredi kartı yıllık üyelik aidatı itirazı",
            input_text=(
                "Yapı Kredi Bankası Worldcard kredi kartımdan 10.03.2026 tarihinde 650 TL "
                "kart aidatı kesilmiştir. Onayım olmadan kesilen bu ücretin iptalini talep ediyorum."
            ),
            expected_document_type="complaint",
            expected_subject="ücret_ve_komisyon",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_card_fee],
        ),
        # 9. Internal Correspondence - Collateral Valuation Delay
        GoldTestCase(
            case_id="TC-09",
            description="Teminat ekspertiz raporu gecikmesi iç yazışması",
            input_text=(
                "Sayı: 2026/KRD-44\n"
                "Konu: Gayrimenkul Teminat Değerleme Raporları\n"
                "Kredi İzleme Müdürlüğüne\n"
                "Portföyümüzde bulunan teminatlı kredilerin ekspertiz raporlarının yasal süresinde "
                "yenilenmediği teftiş kontrolünde tespit edilmiştir.\n"
                "Gereğini rica ederim."
            ),
            expected_document_type="internal_correspondence",
            expected_subject="kredi_ve_faiz",
            expected_analysis_type="compliance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_collateral_valuation],
        ),
        # 10. Complaint - Unauthorized Direct Debit
        GoldTestCase(
            case_id="TC-10",
            description="Otomatik ödeme talimatı hatası şikâyeti",
            input_text=(
                "VakıfBank hesabımdan otomatik fatura ödemesi nedeniyle onayım olmadan mükerrer olarak "
                "2 kez 800 TL elektrik faturası kesilmiştir. Mağdur edildim, paramın iadesini talep ediyorum."
            ),
            expected_document_type="complaint",
            expected_subject="odeme_ve_transfer",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_payment_refund],
        ),
        # 11. Internal Correspondence - Information Security Incident
        GoldTestCase(
            case_id="TC-11",
            description="Bilgi güvenliği olay bildirimi iç yazışması",
            input_text=(
                "Sayı: 2026/BG-03\n"
                "Konu: Bilgi Sistemleri Kesintisi ve Veri Güvenliği Olayı\n"
                "Bilgi Sistemleri Güvenliği Başkanlığına\n"
                "İnternet şubesi altyapısında 2 saatlik plansız kesinti meydana gelmiş olup "
                "BDDK bildirim formu doldurulmuştur.\n"
                "Bilgilerinize arz ederim."
            ),
            expected_document_type="internal_correspondence",
            expected_subject="yetkisiz_islem",
            expected_analysis_type="compliance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_it_incident],
        ),
        # 12. Complaint - Deposit Account Block
        GoldTestCase(
            case_id="TC-12",
            description="Vadesiz mevduat hesabı bloke koyma şikâyeti",
            input_text=(
                "QNB Finansbank vadesiz mevduat hesabıma herhangi bir mahkeme veya icra kararı olmadan "
                "haksız şekilde bloke konulmuştur. Hesabımdaki parayı çekemiyorum ve şikayetçiyim."
            ),
            expected_document_type="complaint",
            expected_subject="mevduat_ve_hesap",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=True,
            expected_unsupported_claim=False,
            mock_evidence=[ev_account_block],
        ),
        # 13. Negative Case - Unsupported Commercial Complaint
        GoldTestCase(
            case_id="TC-13",
            description="Bankacılık dışı ticari uyuşmazlık dilekçesi",
            input_text=(
                "Tedarikçi firma tarafından teslim edilen mobilyalar defolu çıkmıştır. "
                "Firma ürünleri geri almamakta ve paramı iade etmemektedir."
            ),
            expected_document_type="unknown",
            expected_subject="",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=False,
            expected_unsupported_claim=False,
            mock_evidence=[],
        ),
        # 14. Edge Case - Ambiguous Short Complaint with Legal Topic
        GoldTestCase(
            case_id="TC-14",
            description="Kısa eksik şikâyet metni (hangi kesinti olduğu belirsiz)",
            input_text="Hesabımdan para kesildi, şikayetçiyim iade edilsin.",
            expected_document_type="complaint",
            expected_subject="ücret_ve_komisyon",
            expected_analysis_type="regulatory_relevance",
            # Metin ücretin türünü söylemiyor; hesap işletim ücreti hükmüne bağlamak varsayım olur.
            expected_has_findings=False,
            expected_unsupported_claim=True,
            mock_evidence=[ev_fee_regulation],
        ),
        # 15-18. Distractor cases: real grievances/observations with off-topic evidence only
        GoldTestCase(
            case_id="TC-15",
            description="Hesap işletim ücreti şikâyeti — yalnızca ilgisiz kanıt",
            input_text=(
                "Ziraat Bankası Kadıköy Şubesi hesabımdan 15.02.2026 tarihinde onayım olmadan 450 TL "
                "hesap işletim ücreti kesilmiştir. Kesilen tutarın derhal iadesini talep ediyorum ve şikayetçiyim."
            ),
            expected_document_type="complaint",
            expected_subject="ücret_ve_komisyon",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=False,
            expected_unsupported_claim=True,
            mock_evidence=list(distractors),
        ),
        GoldTestCase(
            case_id="TC-16",
            description="Yetkisiz kart işlemi şikâyeti — yalnızca ilgisiz kanıt",
            input_text=(
                "Garanti BBVA kredi kartımdan bilgim dışında 3.500 TL nakit avans çekilmiş olup "
                "derhal müşteri hizmetlerini aramama rağmen işlem iptal edilmemiştir. Mağdur edildim."
            ),
            expected_document_type="complaint",
            expected_subject="yetkisiz_islem",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=False,
            expected_unsupported_claim=True,
            mock_evidence=list(distractors),
        ),
        GoldTestCase(
            case_id="TC-17",
            description="Kredi tahsis yetki aşımı notu — yalnızca ilgisiz kanıt",
            input_text=(
                "Sayı: 2026/894\n"
                "Konu: Kredi Tahsis Yetki Aşımı İncelemesi\n"
                "Teftiş Kurulu Başkanlığına\n"
                "Kadıköy Şubesi tarafından 15.000.000 TL tutarındaki ticari kredinin "
                "yönetim kurulu onay limiti aşılarak kullandırıldığı tespit edilmiştir.\n"
                "Gereğini bilgilerinize arz ederim."
            ),
            expected_document_type="internal_correspondence",
            expected_subject="kredi_ve_faiz",
            expected_analysis_type="compliance",
            expected_has_findings=False,
            expected_unsupported_claim=True,
            mock_evidence=list(distractors),
        ),
        GoldTestCase(
            case_id="TC-18",
            description="Hesap blokesi şikâyeti — kart kanunu kanıtı (konu dışı)",
            input_text=(
                "QNB Finansbank vadesiz mevduat hesabıma herhangi bir mahkeme veya icra kararı olmadan "
                "haksız şekilde bloke konulmuştur. Hesabımdaki parayı çekemiyorum ve şikayetçiyim."
            ),
            expected_document_type="complaint",
            expected_subject="mevduat_ve_hesap",
            expected_analysis_type="regulatory_relevance",
            expected_has_findings=False,
            expected_unsupported_claim=True,
            mock_evidence=[ev_unauthorized_tx] + list(distractors),
        ),
    ]


def evaluate_semantic_gold_set(
    engine: Optional[AnalysisEngine] = None,
    test_cases: Optional[List[GoldTestCase]] = None,
) -> GoldSetEvaluationReport:
    """Evaluates the AnalysisEngine across the semantic gold set."""
    eng = engine or AnalysisEngine()
    cases = test_cases or build_semantic_gold_set()

    doc_service = DocumentUnderstandingService()
    classifier = DocumentClassifierService()
    planner = AnalysisPlannerService()
    retrieval_planner = RetrievalPlannerService()

    case_results: List[CaseEvaluationResult] = []
    pass_count = 0
    partial_count = 0
    fail_count = 0
    false_positives = 0
    false_negatives = 0
    unsupported_caught = 0

    for case in cases:
        doc = doc_service.analyze(case.input_text)
        classification = classifier.classify(doc)
        analysis_plan = planner.plan(doc, classification)
        ret_plan = retrieval_planner.plan_retrieval(analysis_plan, doc)

        result: AnalysisResult = eng.analyze(
            doc=doc,
            classification=classification,
            analysis_plan=analysis_plan,
            retrieval_plan=ret_plan,
            retrieved_evidence=case.mock_evidence,
        )

        doc_match = classification.document_type == case.expected_document_type
        findings_present = len(result.findings) > 0
        # Dayanaksız iddia bekleniyorsa en az bir iddia "unsupported" olarak ayrılmış olmalı.
        unsupported_ok = (not case.expected_unsupported_claim) or len(result.unsupported_claims) > 0
        findings_match = findings_present == case.expected_has_findings and unsupported_ok

        # False positive / negative tracking
        if findings_present and not case.expected_has_findings:
            false_positives += 1
        elif not findings_present and case.expected_has_findings:
            false_negatives += 1

        if len(result.unsupported_claims) > 0:
            unsupported_caught += len(result.unsupported_claims)

        citations_verified = sum(len(f.citations) for f in result.findings)

        # Decide outcome
        details = []
        if not doc_match:
            details.append(f"DocType mismatch: got '{classification.document_type}', expected '{case.expected_document_type}'")
        if findings_present != case.expected_has_findings:
            details.append(f"Findings expectation mismatch: got {len(result.findings)}, expected {case.expected_has_findings}")
        if not unsupported_ok:
            details.append("Expected at least one unsupported claim, got none")

        if doc_match and findings_match:
            outcome = "PASS"
            pass_count += 1
        elif doc_match or findings_match:
            outcome = "PARTIAL"
            partial_count += 1
        else:
            outcome = "FAIL"
            fail_count += 1

        case_results.append(
            CaseEvaluationResult(
                case_id=case.case_id,
                description=case.description,
                outcome=outcome,
                doc_type_actual=classification.document_type,
                doc_type_expected=case.expected_document_type,
                doc_type_match=doc_match,
                findings_count=len(result.findings),
                unsupported_count=len(result.unsupported_claims),
                citations_verified=citations_verified,
                details=details,
            )
        )

    accuracy = round((pass_count + 0.5 * partial_count) / max(1, len(cases)), 3)

    return GoldSetEvaluationReport(
        total_cases=len(cases),
        pass_count=pass_count,
        partial_count=partial_count,
        fail_count=fail_count,
        accuracy=accuracy,
        false_positives=false_positives,
        false_negatives=false_negatives,
        unsupported_claims_caught=unsupported_caught,
        case_results=case_results,
    )
