# Mevzuat envanteri hattı (M6.15 / K1)

`mufettis/corpus/legislation_sources.json` listesini üreten araştırma hattı. 2026-10-02'de scratchpad'den
taşındı; yalnızca yollar taşınabilir yapıldı. Taşıma sonrası `merge.py`, eski konumdaki kopyasıyla bayt bayt aynı çıktıyı verdi.
Adımların sırası ve gerekçeleri: `docs/product/corpus_plan.md` (K1).

## Çalıştırma

Betikler `records/` klasöründe çalıştırılır, çünkü girdi ve çıktı dosyalarını bulundukları klasöre göre okurlar:

    cd scripts/inventory/records
    python ../merge.py                # kataloglar + aramalar + atıflar + şube haritası → inventory.json
    python ../gen_sources.py          # kuru çalıştırma; --write → legislation_sources.json

`MUFETTIS_DATA_DIR` isteyen betikler (`cite_round.py`, `screen.py`, `build_evidence.py`, `ibk_cites.py`,
`report.py`, `run_fetch.py`, `run_delist.py`) yalnızca **veri kopyasıyla** çalıştırılır. Üretim `data/` klasörüne
yazılmaz.

## Ağ erişimi

`web.py` nazik istemcidir. Dürüst UA kullanır, robots.txt'e uyar, istekler arasında 3 sn bekler, aynı sunucuya tek akışla bağlanır ve
`records/cache/` klasöründe önbellek tutar. Ağ kullanan betikler (`*_catalog`, `mevzuat_*`, `search_k13`, `resolve_cites*`, `rg_*`,
`k17_basedates`) yalnızca resmî kaynaklara gider. Giriş, CAPTCHA aşma ve kişisel veri toplama yoktur.

## Betik grupları

| Grup | Betikler |
|---|---|
| Kurum katalogları | `catalog.py`, `catalog_rules.py`, `bddk_catalog.py`, `bddk_classify.py`, `kvkk_decisions.py`, `masak_candidates.py` |
| mevzuat.gov.tr araması | `mevzuat_search.py`, `search_rules.py`, `mevzuat_content.py`, `search_k13.py` |
| Atıflar | `cite_round.py`, `resolve_cites.py`, `resolve_cites2.py` |
| Birleştirme ve liste | `branch_map.py`, `merge.py`, `gen_sources.py`, `report.py`, `sec3_check.py` |
| İndirme ve eleme | `run_fetch.py`, `run_delist.py`, `screen.py`, `build_evidence.py` |
| Resmî Gazete yargı bölümü (K1.4) | `rg_judiciary_scan.py` → `rg_fetch_texts.py` → `rg_ocr.py` / `rg_fallback.py` → `rg_screen.py`, `ibk_cites.py` |
| Eski temel metinler (K1.7) | `k17_basedates.py` |

## Kayıtlar

`records/*.json` dosyaları git'te tutulur, toplamı yaklaşık 22 MB.

Büyük ara çıktılar git dışındadır (`records/.gitignore`): `cache/`, `rg_pdf/` (663 taranmış PDF), `rg_full/`, `rg_docs/`
(RG metinleri ve OCR çıktıları), `rg_docs_old_htm/`, `pilot_dl/`. Bunların yedeği Masaüstündeki
`Mufettis_AI_yedek_2026-10-02/inv` klasöründedir. Tek seferlik yama ve yoklama betikleri depoya alınmadı; onlar da aynı yedekte durur.
