# -*- coding: utf-8 -*-
"""
Resmî kaynaklara HTTPS erişimi için ortak oturum/adaptör.

BDDK (GlobalSign RSA OV SSL CA 2018) ve Resmî Gazete (GeoTrust TLS RSA CA G1)
sunucuları ara sertifikayı göndermiyor. Tarayıcılar ve işletim sistemi eksik ara
sertifikayı AIA ile tamamlar; `requests`'in varsayılan `certifi` deposu tamamlamaz
ve her istek CERTIFICATE_VERIFY_FAILED ile düşer.

`truststore` kuruluysa doğrulama işletim sisteminin sertifika doğrulayıcısına
devredilir (Windows CryptoAPI eksik ara sertifikayı kendisi tamamlar). Sertifika
doğrulaması hiçbir koşulda kapatılmaz; truststore yoksa davranış eskisi gibidir
(certifi).
"""
from __future__ import annotations

import ssl
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

try:
    import truststore
except Exception:  # pragma: no cover - opsiyonel bağımlılık
    truststore = None


def system_trust_available() -> bool:
    return truststore is not None


def _system_ssl_context() -> Optional[ssl.SSLContext]:
    if truststore is None:
        return None
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class SystemTrustAdapter(HTTPAdapter):
    """Bağlantı havuzlarına işletim sistemi güven deposunu kullanan SSLContext verir."""

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        context = _system_ssl_context()
        if context is not None:
            kwargs["ssl_context"] = context
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any):
        context = _system_ssl_context()
        if context is not None:
            proxy_kwargs["ssl_context"] = context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def build_http_session(max_retries: Any = 0) -> requests.Session:
    """http/https için SystemTrustAdapter bağlanmış bir requests.Session döndürür."""
    session = requests.Session()
    adapter = SystemTrustAdapter(max_retries=max_retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


__all__ = ["SystemTrustAdapter", "build_http_session", "system_trust_available"]
