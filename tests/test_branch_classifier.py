# -*- coding: utf-8 -*-
"""Şube belge türü sınıflandırıcısı (M5.6): başlık / gövde işaretleri, tablo yapısı, belirsizlikte "bilinmiyor"."""
from __future__ import annotations

import unittest

from mufettis.analysis import document_taxonomy as tax
from mufettis.analysis.branch_classifier import classify_branch_type
from mufettis.analysis.classifier import DocumentClassifierService
from mufettis.analysis.document_understanding import DocumentUnderstandingService

EXAMPLES = {
    "teminat": "İPOTEK AKİT TABLOSU ÖZETİ\nİpotek veren: Selim Kara\nAda 112 Parsel 4, 2. derece ipotek\n"
               "Tapu tescil: 03.05.2021\nAsıl borçlu: Kara İnşaat Ltd. Şti.",
    "kasa_sayim": "ATM DOLUM VE SAYIM TUTANAĞI\nATM No: 4412\nKaset toplamı ve sistem bakiyesi karşılaştırıldı.\n"
                  "Fark: 0\nSayımı yapan: Veznedar\nNezaret eden: Operasyon Yetkilisi",
    "resmi_yazi": "T.C.\nANKARA 3. İCRA DAİRESİ\nDosya No: 2024/1182 Esas\nHACİZ İHBARNAMESİ\n"
                  "Borçlunun mevduatına haciz konulmuştur. Tebliğden itibaren 7 gün içinde bildiriniz.",
    "mali_tablo": "2023 YILI BİLANÇO ÖZETİ\nDönen varlıklar: 12.400.000 TL\nDuran varlıklar: 8.100.000 TL\n"
                  "Özkaynak: 6.300.000 TL\nNet satışlar: 31.000.000 TL",
    "kkb_istihbarat": "FİNDEKS RİSK RAPORU\nSorgu tarihi: 11.02.2024\nKredi notu: 1.230\nGecikmedeki risk: 0 TL\n"
                      "Karşılıksız çek: yok",
    "personel": "PERSONEL GÖREVLENDİRME YAZISI\nSicil No: 44821\nGörev yeri: Kadıköy Şubesi\n"
                "Adı geçen personel 01.03.2024 tarihinden itibaren gişe yetkilisi olarak görevlendirilmiştir.",
    "denetim_raporu": "İÇ KONTROL BULGU TAKİP FORMU\nBulgu No: IK-2023-14\nRisk derecesi: Orta\n"
                      "Öneri: Çift imza kuralına uyulması.\nAksiyon tarihi: 30.09.2023\nKapanış: Açık",
}


class TestBranchClassifier(unittest.TestCase):
    def test_types_from_general_vocabulary(self):
        for code, text in EXAMPLES.items():
            decision = classify_branch_type(text)
            self.assertEqual(decision.code, code, f"{code}: {decision}")
            self.assertTrue(decision.signals)

    def test_transaction_table_structure(self):
        rows = "\n".join(f"2024-01-{d:02d};GİDEN;KART;MARKET;-{d * 10},50;{9000 - d},25" for d in range(1, 8))
        self.assertEqual(classify_branch_type("tarih;yon;kanal;karsi_taraf;tutar;bakiye\n" + rows).code,
                         "islem_listesi")

    def test_weak_or_ambiguous_evidence_is_unknown(self):
        self.assertEqual(classify_branch_type("Merhaba, yarın görüşelim.").code, tax.UNKNOWN)
        self.assertEqual(classify_branch_type("").code, tax.UNKNOWN)

    def test_prose_sentence_in_first_lines_is_not_a_title(self):
        text = "Bankanın hesap hareketlerimi üçüncü kişilere verdiğini öğrendim ve buna itiraz ediyorum."
        self.assertNotEqual(classify_branch_type(text).code, "islem_listesi")

    def test_routing_type_carries_complaints_and_internal_notes(self):
        self.assertEqual(classify_branch_type("Paramı geri istiyorum.", "complaint").code, "musteri_sikayeti")
        self.assertEqual(classify_branch_type("Toplantı notu.", "internal_correspondence").code, "ic_yazisma")

    def test_classification_carries_branch_type_and_keeps_routing(self):
        text = ("Sayın Yetkili,\nKredi kartımdan bilgim dışında 1.200 TL çekildi. Bu tutarın iadesini talep "
                "ediyorum. Mağdur edildim.")
        result = DocumentClassifierService().classify(DocumentUnderstandingService().analyze(text))
        self.assertEqual((result.document_type, result.branch_type), ("complaint", "musteri_sikayeti"))
        self.assertTrue(any(s.startswith("yönlendirme:") for s in result.branch_signals))


if __name__ == "__main__":
    unittest.main()
