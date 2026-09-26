# -*- coding: utf-8 -*-
"""Aşama D2/D3: kişisel/finansal veri maskeleme ve denetim/LLM yollarında zorunlu maskeleme."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from mufettis.analysis.llm_support import execute_structured_llm_call
from mufettis.audit.service import append_audit
from mufettis.common.utils import mask_sensitive, mask_sensitive_payload
from mufettis.config import AUDIT_LOG_DIR

SENSITIVE_TEXT = (
    "Müşteri ali.veli+x@banka.com.tr, TCKN 12345678901, VKN 1234567890, tel 0532 123 45 67, "
    "IBAN TR12 0001 0002 0003 0004 0005 00, kart 4543 1234 5678 9012."
)
SENSITIVE_VALUES = [
    "ali.veli+x@banka.com.tr", "12345678901", "1234567890", "0532 123 45 67",
    "TR12 0001 0002 0003 0004 0005 00", "4543 1234 5678 9012",
]


class TestMaskSensitive(unittest.TestCase):
    def test_iban_variants(self):
        for iban in (
            "TR12 0001 0002 0003 0004 0005 00",
            "TR120001000200030004000500",
            "tr12 0001 0002 0003 0004 0005 00",
            "TR12 0001 0002 0003",  # kısmi / kısaltılmış yazım
        ):
            self.assertEqual(mask_sensitive(f"IBAN {iban} nolu"), "IBAN [IBAN] nolu", iban)

    def test_card_numbers(self):
        for card in ("4543 1234 5678 9012", "4543-1234-5678-9012", "4543123456789012", "3782 822463 10005", "378282246310005"):
            self.assertEqual(mask_sensitive(f"kart {card}."), "kart [KART].", card)

    def test_phone_numbers(self):
        for phone in ("0532 123 45 67", "+90 532 123 4567", "5321234567", "05321234567", "532-123-45-67"):
            self.assertEqual(mask_sensitive(f"tel {phone}"), "tel [TELEFON]", phone)

    def test_identity_numbers_and_email(self):
        self.assertEqual(mask_sensitive("TCKN 12345678901"), "TCKN [TCKN/VKN]")
        self.assertEqual(mask_sensitive("VKN 1234567890"), "VKN [TCKN/VKN]")
        self.assertEqual(mask_sensitive("e-posta ali.veli+x@banka.com.tr"), "e-posta [E-POSTA]")

    def test_non_sensitive_values_are_preserved(self):
        text = (
            "Tutar 15.000.000 TL, tarih 15.02.2026, madde 12, 5411 sayılı Kanun, hesap kodu 280013, "
            "sayı 2026/894, saat 02:22:45, run 75928ac9c4bb48b9bfe1c88f53fcfcd1"
        )
        self.assertEqual(mask_sensitive(text), text)

    def test_idempotent_and_empty(self):
        once = mask_sensitive(SENSITIVE_TEXT)
        self.assertEqual(mask_sensitive(once), once)
        self.assertEqual(mask_sensitive(""), "")
        self.assertIsNone(mask_sensitive(None))

    def test_payload_masking_is_recursive_and_keeps_non_strings(self):
        payload = {"a": SENSITIVE_TEXT, "n": 5, "nested": {"list": [SENSITIVE_TEXT, 1.5, None]}}
        masked = mask_sensitive_payload(payload)
        blob = json.dumps(masked, ensure_ascii=False)
        for value in SENSITIVE_VALUES:
            self.assertNotIn(value, blob)
        self.assertEqual(masked["n"], 5)
        self.assertEqual(masked["nested"]["list"][1:], [1.5, None])


class TestMandatoryMasking(unittest.TestCase):
    def test_append_audit_masks_payload(self):
        event = "privacy_masking_test_event"
        append_audit(event, {"note": SENSITIVE_TEXT, "details": {"items": [SENSITIVE_TEXT]}})

        lines = []
        for path in AUDIT_LOG_DIR.glob("audit_*.jsonl"):
            lines += [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        written = [l for l in lines if l["event"] == event]
        self.assertTrue(written)
        blob = json.dumps(written[-1], ensure_ascii=False)
        for value in SENSITIVE_VALUES:
            self.assertNotIn(value, blob)
        self.assertIn("[IBAN]", blob)

    def test_ollama_generate_masks_prompt(self):
        from mufettis.llm import client

        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = ['{"response": "tamam", "done": true}']
        with patch.object(client.requests, "post", return_value=response) as post:
            self.assertEqual(client.ollama_generate(SENSITIVE_TEXT), "tamam")
        sent_prompt = post.call_args.kwargs["json"]["prompt"]
        for value in SENSITIVE_VALUES:
            self.assertNotIn(value, sent_prompt)

    def test_structured_llm_call_masks_prompt(self):
        seen = []

        def fake_generate(prompt):
            seen.append(prompt)
            return '{"ok": true}'

        result = execute_structured_llm_call(SENSITIVE_TEXT, fake_generate, lambda: {"ok": False})
        self.assertEqual(result, {"ok": True})
        for value in SENSITIVE_VALUES:
            self.assertNotIn(value, seen[0])


if __name__ == "__main__":
    unittest.main()
