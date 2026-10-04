"""M9.3 katmanlı ölçüm: alan katmanı ve doğru alanlarla motor katmanı (scripts/rules_case_benchmark.py)."""
import importlib.util
from pathlib import Path

from mufettis.rules.catalog import load_catalog
from mufettis.rules.engine import EMPTY, NOT_FOUND, FieldValue

_spec = importlib.util.spec_from_file_location(
    "rules_case_benchmark", Path(__file__).resolve().parents[1] / "scripts" / "rules_case_benchmark.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


def test_field_layer_verdicts():
    fields = {"azami_tutar": FieldValue.filled("100"), "es_rizasi": FieldValue(EMPTY),
              "kefil_medeni_hal": FieldValue.filled("evli")}
    truth = {"azami_tutar": "boş", "es_rizasi": "boş", "kefil_medeni_hal": "evli", "imza_turu": "ıslak",
             "kefil_ticari_sifat": "bilinmiyor"}
    got = {name: verdict for name, _w, _g, verdict in bench.field_layer(fields, truth)}
    assert got == {"azami_tutar": "yanlış", "es_rizasi": "doğru", "kefil_medeni_hal": "doğru", "imza_turu": "güvenli",
                   "kefil_ticari_sifat": "doğru"}


def test_engine_layer_uses_truth_and_flags_placeholders():
    rules = load_catalog()
    # belge tutarı yanlış okumuş olsa da gerçek alan (boş) verilince motor doğru karar verir
    res = bench.engine_layer("", "kefalet_sozlesmesi", {"azami_tutar": FieldValue.filled("1")},
                             {"azami_tutar": "boş"}, {}, rules)
    assert res["TBK583-azami-miktar-belirtilmis"] == ("karşılanmadı", False)
    # gerçekte dolu ama okunamamış tarih yer tutucuyla verilir; ona dayanan karar "alan kaynaklı" işaretlenir
    res = bench.engine_layer("", "kefalet_sozlesmesi", {"kefil_medeni_hal": FieldValue.filled("evli"),
                                                        "es_rizasi": FieldValue.filled("var"),
                                                        "kefalet_tarihi": FieldValue(NOT_FOUND)},
                             {"kefalet_tarihi": "dolu", "es_rizasi_tarihi": "dolu"}, {}, rules)
    assert res["TBK584-riza-zamani"][1] is True
