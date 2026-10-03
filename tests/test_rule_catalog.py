import copy
import datetime as dt
import json

import pytest

from mufettis.rules.catalog import CATALOG_DIR, CatalogError, load_catalog, parse_rule, quote_in_text

# TBK 583 / 584 metninden (PDF satır sonlarıyla, korpustaki gibi)
TBK = {
    "583": "MADDE 583- Kefalet sözleşmesi, yazılı şekilde yapılmadıkça ve kefilin sorumlu \nolacağı azamî miktar ile "
           "kefalet tarihi belirtilmedikçe geçerli olmaz. Kefilin, sorumlu olduğu \nazamî miktarı, kefalet tarihini ve "
           "müteselsil kefil olması durumunda, bu sıfatla veya bu \nanlama gelen herhangi bir ifadeyle yükümlülük altına "
           "girdiğini kefalet sözleşmesinde kendi el \nyazısıyla belirtmesi şarttır.",
    "584": "MADDE 584- Eşlerden biri mahkemece verilmiş bir ayrılık kararı olmadıkça veya \nyasal olarak ayrı yaşama "
           "hakkı doğmadıkça, ancak diğerinin yazılı rızasıyla kefil olabilir; bu \nrızanın sözleşmenin kurulmasından "
           "önce ya da en geç kurulması anında verilmiş olması \nşarttır. ... kooperatif \nortaklarına kullandırılacak "
           "kredilerde verilecek kefaletler için eşin rızası aranmaz.",
}


def lookup(law, article):
    return TBK.get(article) if law == "6098" else None


def _rules():
    return json.loads((CATALOG_DIR / "kefalet.json").read_text(encoding="utf-8"))["rules"]


def test_shipped_catalog_loads_and_quotes_match_article_text():
    rules = load_catalog(article_text=lookup)
    assert {r.id for r in rules} >= {"TBK583-azami-tutar-el-yazisi", "TBK584-es-rizasi"}
    spouse = next(r for r in rules if r.id == "TBK584-es-rizasi")
    assert spouse.exceptions and spouse.exceptions[0].basis.paragraph == "3"
    assert spouse.applies_on(dt.date(2024, 1, 1)) and not spouse.applies_on(dt.date(2012, 8, 1))


def test_quote_match_ignores_line_breaks_and_case_but_not_words():
    assert quote_in_text("kendi el yazısıyla", "kendi el \nyazısıyla")
    assert not quote_in_text("kendi el yazısı ile", "kendi el yazısıyla")


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["check"].update(field="olmayan_alan"), "bilinmeyen alan"),
    (lambda r: r.pop("basis"), "dayanak yok"),
    (lambda r: r["basis"].update(quote="Kefil her zaman sorumludur."), "metninde geçmiyor"),
    (lambda r: r["basis"].update(article="999"), "korpusta bulunamadı"),
    (lambda r: r.update(result_class="kesin"), "geçersiz result_class"),
    (lambda r: r.update(fazla="x"), "bilinmeyen anahtar"),
    (lambda r: r.update(absence_is_violation="evet"), "absence_is_violation"),
    (lambda r: r["check"].update(type="sezgi"), "bilinmeyen kontrol türü"),
])
def test_broken_rule_is_rejected(mutate, message):
    raw = copy.deepcopy(_rules()[0])
    mutate(raw)
    with pytest.raises(CatalogError, match=message):
        parse_rule(raw, lookup)


def test_exception_needs_known_condition_field_and_basis(tmp_path):
    raw = copy.deepcopy(_rules()[1])
    raw["exceptions"][0]["condition"]["field"] = "meslek"
    with pytest.raises(CatalogError, match="bilinmeyen koşul alanı"):
        parse_rule(raw, lookup)


def test_duplicate_ids_are_rejected(tmp_path):
    rule = _rules()[0]
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"rules": [rule, rule]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CatalogError, match="kimlik tekrarı"):
        load_catalog([path], lookup)
