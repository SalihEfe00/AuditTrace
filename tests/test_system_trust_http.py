# -*- coding: utf-8 -*-
"""
Aşama D1: resmî kaynaklara HTTPS erişimi işletim sistemi güven deposuyla yapılır.

BDDK ve Resmî Gazete ara sertifikayı göndermediği için certifi ile doğrulama
başarısız oluyordu. Bu testler ağ gerektirmez; canlı doğrulama
MUFETTIS_LIVE_TESTS=1 ile tests/test_faz19_live_pilot.py üzerinden yapılır.
"""
from __future__ import annotations

import os
import ssl
import unittest
from unittest.mock import patch

import requests

from mufettis.common import http as http_mod
from mufettis.common.http import SystemTrustAdapter, build_http_session, system_trust_available


class TestSystemTrustAdapter(unittest.TestCase):
    def test_truststore_is_installed(self):
        """requirements.txt'te; yoksa resmî kaynaklara erişim sertifika hatasıyla düşer."""
        self.assertTrue(system_trust_available())

    def test_session_uses_system_trust_adapter_for_https(self):
        session = build_http_session()
        self.assertIsInstance(session.get_adapter("https://www.bddk.org.tr/"), SystemTrustAdapter)
        self.assertIsInstance(session.get_adapter("https://www.resmigazete.gov.tr/"), SystemTrustAdapter)

    def test_pool_manager_gets_verifying_system_context(self):
        adapter = SystemTrustAdapter()
        context = adapter.poolmanager.connection_pool_kw.get("ssl_context")
        self.assertIsNotNone(context)
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_falls_back_to_default_when_truststore_missing(self):
        with patch.object(http_mod, "truststore", None):
            adapter = SystemTrustAdapter()
            self.assertNotIn("ssl_context", adapter.poolmanager.connection_pool_kw)

    def test_harvesters_mount_system_trust_adapter(self):
        from mufettis.harvesting.resmi_gazete import ResmiGazeteScraper
        from mufettis.harvesting.web_harvester import WebSourceHarvester

        for harvester in (WebSourceHarvester("bddk_official", local_only=True), ResmiGazeteScraper(local_only=True)):
            adapter = harvester._build_session().get_adapter("https://example.org/")
            self.assertIsInstance(adapter, SystemTrustAdapter, type(harvester).__name__)
            self.assertGreater(adapter.max_retries.total, 0, "Yeniden deneme ayarı korunmalı")


@unittest.skipUnless(os.environ.get("MUFETTIS_LIVE_TESTS", "").strip() == "1", "Canlı ağ testi: MUFETTIS_LIVE_TESTS=1")
class TestSystemTrustLive(unittest.TestCase):
    def test_official_sources_reachable_and_bad_certs_rejected(self):
        session = build_http_session()
        for url in ("https://www.bddk.org.tr/Mevzuat", "https://www.resmigazete.gov.tr/"):
            self.assertEqual(session.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).status_code, 200, url)
        for bad in ("https://expired.badssl.com/", "https://self-signed.badssl.com/", "https://wrong.host.badssl.com/"):
            with self.assertRaises(requests.exceptions.SSLError, msg=bad):
                session.get(bad, timeout=20)


if __name__ == "__main__":
    unittest.main()
