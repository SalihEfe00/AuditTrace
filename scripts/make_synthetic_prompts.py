# -*- coding: utf-8 -*-
"""BRIEF görevlerinden kopyalanmaya hazır istem dosyaları üretir (benchmarks/synthetic/prompts/<görev>.txt).

Her istem modele cevabının ilk satırına "KAYIT: <model adı> <görev>" yazmasını söyler; kabul oturumu
(benchmarks/synthetic/INTAKE.md) dosya adını bu satırdan çıkarır.

    python scripts/make_synthetic_prompts.py [--out KLASÖR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmarks" / "synthetic" / "prompts"

COMMON = """Türkiye'de bir ticari bankanın şubesinde düzenlenmiş ya da şubeye gelmiş gerçekçi bir belge üretmeni istiyorum.
Kurallar:
- Cevabının EN ÜSTÜNE, her şeyden önce tek satır olarak şunu yaz: KAYIT: <kendi model adın ve sürümün> {task}
  (ör. "KAYIT: GPT-4o {task}" ya da "KAYIT: Gemini 2.5 Pro {task}"). Bu satırı değiştirmeden ve başka açıklama eklemeden yaz.
- Dil Türkçe; bankacılık pratiğindeki gerçek üslup, kısaltmalar, form düzeni ve alışılmış eksiklikler.
- Tüm kişi, şirket, adres, TCKN, VKN, IBAN, hesap ve telefon numaraları HAYALİ olmalı ama doğru biçimde yazılmalı
  (11 haneli TCKN, 10 haneli VKN, TR ile başlayan 26 karakterli IBAN).
- Belge 2019–2026 arasında bir tarihte düzenlenmiş olsun; tarihleri belgede açıkça yaz.
- Belgeyi ya tamamen usulüne uygun ya da gerçek hayatta görülebilecek 1–3 kusurla üret (karikatürize etme;
  kusur ancak dikkatli bir denetçinin fark edebileceği kadar ince olsun). Hangisini yaptığını kendin seç;
  örneklerin yaklaşık üçte biri kusursuz olsun.
- Önce "=== BELGE ===" başlığı altında yalnızca belgenin kendisini yaz (form, tutanak, yazı, tablo — ne ise).
- Sonra "=== CEVAP ANAHTARI ===" başlığı altında şu JSON'u ver:
  {{"belge_turu": "...", "duzenlenme_tarihi": "YYYY-AA-GG", "olay_tarihi": "YYYY-AA-GG",
   "kusursuz": true/false,
   "kusurlar": [{{"aciklama": "...", "belgedeki_yer": "belgeden kısa alıntı", "ilgili_mevzuat": "bildiğin kadarıyla",
                 "onem": "yüksek/orta/düşük"}}],
   "alanlar": {{"tutarlar": [...], "tarihler": [...], "kisiler_ve_rolleri": ["Ad Soyad - rol", ...], "imza_onay": [...],
               "kimlik_numaralari": [...], "hesap_numaralari": [...], "oranlar": [...], "diger": {{...}}}}}}
  "alanlar" içinde belgede geçen TÜM tutarları, tarihleri, kişileri, kimlik numaralarını (TCKN / VKN), IBAN dışındaki
  hesap numaralarını ve yüzde oranlarını belgede yazıldığı gibi listele.

Görev: {text}
"""

TASKS = {
    "T01": "Kredi teklif ve onay belgesi — Şubenin ticari kredi teklifi, limit, vade, faiz, teminatlar, yetkili onayları\n(kredi komitesi / bölge / genel müdürlük yetki seviyesi), istihbarat özeti.",
    "T02": "Teminat belgeleri — Kefalet sözleşmesi ya da ipotek / rehin belgesi; kefilin medeni hali, eş rızası,\nkefalet tutarı ve tarihi, el yazısı şartları.",
    "T03": "Kredi kullandırım dosyası özeti — Kullandırım talimatı, dekont, kullandırım tarihi ile teminat tesis\ntarihleri, sözleşme tarihi.",
    "T04": "Hesap açılış ve müşteri tanıma — Gerçek ya da tüzel kişi hesap açılış formu, kimlik tespiti ve teyidi,\nimza sirküleri / vekaletname, ticaret sicil bilgileri, adres teyidi.",
    "T05": "Kasa sayım tutanağı — Şube kasası, ATM veya kıymetli evrak sayımı; sayım ile sistem bakiyesi, sayımı\nyapanlar, imzalar.",
    "T06": "Nakit / havale / EFT / döviz işlem dekontları — Yüksek tutarlı ya da birbirini izleyen işlemler, işlemi\nyapan kişi (kendi adına / başkası adına), kimlik bilgisi.",
    "T07": "Müşteri şikâyeti ve bankanın cevabı — Şikâyet dilekçesi + banka cevap yazısı (tarihleriyle); konu ücret,\nkart, kredi, mevduat, dijital bankacılık olabilir.",
    "T08": "Ücret / komisyon tahsilatı — Müşteriden alınan ücretlerin dökümü, sözleşme ve ücret tarifesi bilgisi.",
    "T09": "Resmî kurum yazısı ve cevabı — Mahkeme, icra dairesi, MASAK, BDDK ya da vergi dairesi bilgi talebi / haciz\nyazısı ve şubenin cevabı (tarihleriyle).",
    "T10": "İç yazışma / talimat / yetki devri — Şube içi e-posta veya yazı, genel müdürlük talimatı, yetki devri.",
    "T11": "Teftiş / iç kontrol raporu bulgusu — Önceki denetimden bir bulgu, alınan aksiyon, kapanış durumu.",
    "T12": "İşlem listesi (CSV) — Bir müşterinin 1–3 aylık hesap hareketleri (tarih, tutar, yön, karşı taraf, kanal,\naçıklama); bazıları olağan, bazılarında şüpheli işlem kalıbı olabilir. Belgeyi CSV olarak ver.",
    "D01": "Kredi dosyası paketi — Aynı müşteri için kredi teklif ve onay belgesi + teminat belgesi (kefalet / ipotek /\nrehin) + kredi kullandırım belgesi + mali tablo özeti + KKB / istihbarat özeti birlikte; belgeler arasında (varsa)\ntutarsızlık olsun: tarih sırası, imzalayan kişinin yetkisi, tutar farkları. Her belgeyi \"=== BELGE n: <tür> ===\"\nbaşlığıyla ayır (ör. \"=== BELGE 2: Kefalet Sözleşmesi ===\"); cevap anahtarında belgeler arası kusurları\n\"belgeler_arasi_kusurlar\" adlı ayrı bir listede ver.",
}

NAMES = {
    "T01": "kredi_teklif_onay", "T02": "teminat", "T03": "kredi_kullandirim", "T04": "hesap_acilis",
    "T05": "kasa_sayim", "T06": "islem_dekontu", "T07": "sikayet_cevap", "T08": "ucret_komisyon",
    "T09": "resmi_yazi", "T10": "ic_yazisma", "T11": "teftis_bulgusu", "T12": "islem_listesi_csv",
    "D01": "kredi_dosyasi_paketi",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for task, text in TASKS.items():
        path = args.out / f"{task}_{NAMES[task]}.txt"
        # Not Defteri için UTF-8 BOM ve Windows satır sonu
        with open(path, "w", encoding="utf-8-sig", newline="\r\n") as fh:
            fh.write(COMMON.format(task=task, text=text))
    print(f"{len(TASKS)} istem yazıldı: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
