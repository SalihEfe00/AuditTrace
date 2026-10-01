"""M6.15.5e geniş katman içerik eleği: indirilen metinde bankaya / şubeye yükümlülük getiren hüküm var mı?

Dört sayı:
- bank: banka öznesi geçen cümle (banka, katılım bankası, kredi kuruluşu, ödeme / elektronik para kuruluşu, PTT);
- obligation: banka öznesi + yükümlülük / işlem fiili aynı cümlede (geniş, gevşek);
- actor: bankanın işi YAPAN / yükümlü taraf olduğu cümle ("bankalarca", "banka tarafından", "bankalar ...
  zorundadır / bildirir / bloke eder" ...) — asıl karar ölçüsü;
- product: banka ürünü / işlemi terimi geçen cümle (mevduat, katılma hesabı, kredi kartı, çek, teminat mektubu,
  havale, döviz tevdiat ...) — banka adı geçmeden bankaya iş yükleyen metinler (ör. mevduat stopajı) için.
Çıktı: screen.json {anahtar: {bank, obligation, actor, product, examples, actor_examples}}.
Kullanım: MUFETTIS_DATA_DIR=<kopya> python screen.py @anahtarlar.txt
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mufettis.importers.legislation_importer import document_text  # noqa: E402

SUBJ = (r"(?:bankalar?|bankanın|bankaların|bankaya|bankalara|bankada|bankalarda|katılım bankalar?\w*|"
        r"kredi kuruluşlar?\w*|ödeme kuruluşlar?\w*|elektronik para kuruluşlar?\w*|PTT|Posta ve Telgraf Teşkilatı)")
BANK = re.compile(r"\b(bankalar?\w*|katılım bankas\w*|kredi kuruluş\w*|ödeme kuruluş\w*|elektronik para kuruluş\w*|"
                  r"PTT|Posta ve Telgraf Teşkilatı)", re.I)
DUTY = re.compile(r"(zorunlu|yükümlü|bildir|gönder|sorumlu|aktar|bloke|blokaj|haciz|haczed|el kon|öde[nrmy]|ödeme|"
                  r"tahsil|yatır|açıl|açar|açtır|kapat|iade|ihbar|talep ed|izin ver|yasak|uygula|kontrol|tespit|"
                  r"sakla|muhafaza|kayıt|tutar|dondur|ertele|kesinti|kesil|teminat|kefil|kredi kullandır)", re.I)
ACTOR_VERB = (r"(zorunda|zorunlu|yükümlü|sorumlu|bildir\w*|ihbar ed\w*|gönder\w*|aktar\w*|bloke ed\w*|"
              r"dondur\w*|öde(?:r|nir|mek|meye)\w*|iade ed\w*|tahsil ed\w*|saklar|sakla(?:mak|nır)\w*|"
              r"muhafaza ed\w*|tespit ed\w*|kontrol ed\w*|uygula(?:r|nır|mak)\w*|kesinti yap\w*|tevkif ed\w*|"
              r"kayd\w* (?:eder|edilir)|açar|açılır|kapatır|kapatılır|veremez|yapamaz|açamaz|kullandıramaz|"
              r"düzenler|verir|ister|talep ed\w*|alır|yatırır|yatırılır|aktarılır|bildirilir)")
ACTOR = re.compile(
    r"\b(?:bankalarca|bankaca|katılım bankalarınca|kredi kuruluşlarınca|ödeme kuruluşlarınca|"
    r"elektronik para kuruluşlarınca|PTT'ce|PTT tarafından)\b"
    r"|\b(?:banka\w*|kuruluş\w*) tarafından\b"
    r"|\b" + SUBJ + r"\b[^.;:]{0,140}?\b" + ACTOR_VERB, re.I)
PRODUCT = re.compile(r"\b(mevduat\w*|katılma hesab\w*|kredi kart\w*|banka kart\w*|çek(?:ler|in|e|i)?\b|"
                     r"teminat mektu[bp]\w*|havale\w*|EFT\b|döviz tevdiat\w*|vadesiz hesap\w*|vadeli hesap\w*|"
                     r"kiralık kasa\w*|tüketici kredi\w*|konut kredi\w*|hesap özeti\w*)", re.I)
SENT = re.compile(r"(?<=[.;:])\s+|\n{2,}")


def screen_text(text):
    out = {"bank": 0, "obligation": 0, "actor": 0, "product": 0, "examples": [], "actor_examples": []}
    for sent in SENT.split(text or ""):
        flat = re.sub(r"\s+", " ", sent).strip()
        if PRODUCT.search(flat):
            out["product"] += 1
        if not BANK.search(flat):
            continue
        out["bank"] += 1
        if DUTY.search(flat):
            out["obligation"] += 1
            if len(out["examples"]) < 3:
                out["examples"].append(flat[:220])
        if ACTOR.search(flat):
            out["actor"] += 1
            if len(out["actor_examples"]) < 3:
                out["actor_examples"].append(flat[:260])
    return out


if __name__ == "__main__":
    data_dir = Path(os.environ["MUFETTIS_DATA_DIR"])
    dl = data_dir / "legislation_downloads"
    records = json.loads((dl / "downloads.json").read_text(encoding="utf-8"))
    keys = set(Path(sys.argv[1][1:]).read_text(encoding="utf-8").split()) if sys.argv[1:2] and sys.argv[1].startswith("@") else set(sys.argv[1:])
    out_path = Path("screen.json")
    out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    out = {k: v for k, v in out.items() if "actor" in v or "error" in v}  # eski ölçüyle elenenler yeniden
    for key, rec in records.items():
        if rec.get("status") not in ("downloaded", "cached") or key in out or (keys and key not in keys):
            continue
        path = dl / Path(rec["path"]).name
        try:
            text, _, _ = document_text(path, path.read_bytes(), main_content=not re.fullmatch(r"\d+\.\d\.\d+", key))
        except Exception as exc:
            out[key] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        out[key] = screen_text(text)
        if len(out) % 100 == 0:
            out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            print(len(out), flush=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(out), "belge elendi")
