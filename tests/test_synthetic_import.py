# -*- coding: utf-8 -*-
"""Sentetik örnek dönüştürücüsü (M4.3): BRIEF biçimi, toleranslar, red sebepleri, belirlenimci bölüm."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mufettis.analysis import synthetic_import as si
from mufettis.analysis.document_benchmark import load_cases

ROOT = Path(__file__).resolve().parent.parent

KEY = {
    "belge_turu": "Kefalet Sözleşmesi", "duzenlenme_tarihi": "2023-05-10", "olay_tarihi": "10.05.2023",
    "kusursuz": False,
    "kusurlar": [{"aciklama": "Eş rızası alınmamış", "belgedeki_yer": "Eş Rızası: —", "ilgili_mevzuat": "TBK 584",
                  "onem": "Yüksek"}],
    "alanlar": {"tutarlar": ["Kefalet tutarı: 250.000,00 TL"], "tarihler": ["10.05.2023"],
                "kisiler_ve_rolleri": ["Ali Veli - kefil"], "imza_onay": ["kefil el yazısı"],
                "kimlik_numaralari": ["12345678901"], "diger": {}},
}
DOC = "KEFALET SÖZLEŞMESİ\nKefil: Ali Veli (TCKN 12345678901)\nMedeni Hali: Evli\nEş Rızası: —\nTutar: 250.000,00 TL"


def sample(doc=DOC, key=KEY, key_wrapper="```json\n{}\n```", doc_header="=== BELGE ==="):
    return f"{doc_header}\n{doc}\n\n**=== CEVAP ANAHTARI ===**\n{key_wrapper.replace('{}', json.dumps(key, ensure_ascii=False))}\n"


class TestConvertFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_single_document(self):
        out = si.convert_file(self.write("gpt/T02_001.md", sample()), self.root)
        self.assertEqual(out.rejected, "")
        case = out.case
        self.assertEqual(case.case_id, "gpt-t02_001")
        self.assertEqual(case.document_type, "teminat")
        self.assertFalse(case.compliant)
        self.assertEqual(case.defects[0].severity, "yüksek")
        self.assertEqual(case.defects[0].quote, "Eş Rızası: —")
        self.assertEqual(case.fields.amounts, ["250000.00 TRY"])
        self.assertEqual(case.fields.tckn, ["12345678901"])
        self.assertEqual((case.issue_date, case.event_date), ("2023-05-10", "2023-05-10"))
        self.assertEqual(case.source["model"], "gpt")
        self.assertIn(case.split, ("dev", "holdout"))
        self.assertTrue(case.text.startswith("KEFALET"))

    def test_tolerances(self):
        raw = json.dumps(KEY, ensure_ascii=False)[:-1] + ",}"          # sondaki virgül, çitsiz
        content = sample(key_wrapper="Açıklama: aşağıda.\n{}".replace("{}", raw), doc_header="## === BELGE ===")
        content = content.replace("Tutar:", "=== ÖDEME BİLGİSİ ===\nTutar:")   # belge içi başlık bölüm değildir
        out = si.convert_file(self.write("m/T02_002.md", content), self.root)
        self.assertEqual(out.rejected, "")
        self.assertIn("=== ÖDEME BİLGİSİ ===", out.case.text)

    def test_quote_not_found_is_blanked_with_warning(self):
        key = dict(KEY, kusurlar=[dict(KEY["kusurlar"][0], belgedeki_yer="eş rızası alınmamıştır")])
        out = si.convert_file(self.write("m/T02_003.md", sample(key=key)), self.root)
        self.assertEqual(out.case.defects[0].quote, "")
        self.assertTrue(any("alıntı" in w for w in out.warnings))

    def test_declared_type_mismatch_warns_task_wins(self):
        out = si.convert_file(self.write("m/T05_001.md", sample()), self.root)
        self.assertEqual(out.case.document_type, "kasa_sayim")
        self.assertTrue(any("görev esas" in w for w in out.warnings))

    def test_rejections(self):
        rejects = {
            "m/X01_001.md": (sample(), "dosya adı"),
            "m/T02_004.md": ("=== BELGE ===\nmetin\n", "CEVAP ANAHTARI"),
            "m/T02_005.md": (sample(key_wrapper="anahtar yok"), "JSON yok"),
            "m/T02_006.md": (sample(key_wrapper="{{bozuk: }}"), "JSON değil"),
            "m/T02_007.md": (sample(key=dict(KEY, kusursuz=True)), "kusur listelenmiş"),
            "m/T02_008.md": (sample(key=dict(KEY, kusurlar=[])), "kusur yok"),
            "T02_009.md": (sample(), "<model>"),
        }
        for rel, (content, reason) in rejects.items():
            out = si.convert_file(self.write(rel, content), self.root)
            self.assertIsNone(out.case, rel)
            self.assertIn(reason, out.rejected, rel)

    def test_compliant_derived_when_missing(self):
        key = {k: v for k, v in KEY.items() if k != "kusursuz"}
        key["kusurlar"] = []
        out = si.convert_file(self.write("m/T02_010.md", sample(key=key)), self.root)
        self.assertTrue(out.case.compliant)

    def test_package(self):
        body = ("=== BELGE 1: Kredi Teklif ve Onay Formu ===\nLimit 1.000.000 TL, onay 01.02.2024\n"
                "=== BELGE 2: Kefalet Sözleşmesi ===\nKefil ... 01.03.2024\n"
                "=== BELGE 3: Kullandırım Talimatı ===\nKullandırım 15.02.2024\n")
        key = dict(KEY, kusurlar=[], belgeler_arasi_kusurlar=[
            {"aciklama": "Kullandırım teminattan önce", "belgedeki_yer": "Kullandırım 15.02.2024", "onem": "yüksek"}])
        content = body + "=== CEVAP ANAHTARI ===\n" + json.dumps(key, ensure_ascii=False)
        out = si.convert_file(self.write("m/D01_001.md", content), self.root)
        self.assertEqual(out.rejected, "")
        case = out.case
        self.assertEqual(case.document_type, "")
        self.assertEqual([d.document_type for d in case.documents], ["kredi_teklif_onay", "teminat", "kredi_kullandirim"])
        self.assertEqual([(d.scope, d.quote) for d in case.defects], [("dosya", "Kullandırım 15.02.2024")])
        bad = content.replace("Kefalet Sözleşmesi", "Ne Olduğu Belirsiz Ek")
        self.assertIn("tanınmadı", si.convert_file(self.write("m/D01_002.md", bad), self.root).rejected)

    def test_split_is_deterministic_and_balanced(self):
        splits = [si.split_for(f"model/T01_{i:03d}.md") for i in range(300)]
        self.assertEqual(splits, [si.split_for(f"model/T01_{i:03d}.md") for i in range(300)])
        self.assertTrue(70 < splits.count("holdout") < 130)
        self.assertEqual(si.split_for("a\\T01_001.md"), si.split_for("a/T01_001.md"))


class TestImportScript(unittest.TestCase):
    def test_script_is_repeatable_and_hides_sealed_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sealed" / "gizli-model"
            src.mkdir(parents=True)
            key = dict(KEY, kusurlar=[dict(KEY["kusurlar"][0], belgedeki_yer="GİZLİ ALINTI")])
            (src / "T02_001.md").write_text(sample(key=key), encoding="utf-8")
            (src / "T02_002.md").write_text("bozuk", encoding="utf-8")
            out = Path(tmp) / "sealed_cases.json"
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            cmd = [sys.executable, str(ROOT / "scripts" / "import_synthetic.py"), "--sealed",
                   "--source", str(Path(tmp) / "sealed"), "--out", str(out)]
            first = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            content = out.read_text(encoding="utf-8")
            second = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(out.read_text(encoding="utf-8"), content)
            self.assertNotIn("GİZLİ", first.stdout)
            self.assertNotIn("T02_002", first.stdout, "mühürlü red dosya adı da basılmaz")
            self.assertIn("1 dosya reddedildi", first.stdout)
            self.assertEqual([c.split for c in load_cases(out)], ["sealed"])


if __name__ == "__main__":
    unittest.main()
