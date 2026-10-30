from __future__ import annotations

import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, patch

from app.models import ProviderConfig, ProviderCreateRequest
from app.url_security import validate_public_http_url, validate_public_http_url_async


class ProviderUrlSecurityTests(unittest.TestCase):
    def test_accepts_public_http_and_https_urls(self) -> None:
        validate_public_http_url("https://chatgpt.com/")
        validate_public_http_url("http://example.com:8080/chat")

    def test_rejects_non_http_schemes_and_credentials(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/file",
            "javascript:alert(1)",
            "https://user:password@example.com/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_public_http_url(url)

    def test_rejects_loopback_private_link_local_and_reserved_ip_addresses(self) -> None:
        for host in (
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "::1",
            "fc00::1",
            "fe80::1",
            "0.0.0.0",
        ):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_public_http_url(f"http://[{host}]/" if ":" in host else f"http://{host}/")

    def test_rejects_local_and_ambiguous_numeric_hostnames(self) -> None:
        for host in (
            "localhost",
            "printer.local",
            "service.internal",
            "router.lan",
            "metadata.google.internal",
            "127.1",
            "0177.0.0.1",
            "0x7f000001",
            "single-label",
        ):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_public_http_url(f"http://{host}/")

    def test_provider_models_enforce_url_validation(self) -> None:
        fields = {
            "name": "test",
            "display_name": "Test",
            "url": "http://127.0.0.1:8080/",
            "input_selector": "textarea",
            "response_selector": ".response",
        }
        with self.assertRaises(ValueError):
            ProviderCreateRequest(**fields)
        with self.assertRaises(ValueError):
            ProviderConfig(**fields)


class ProviderUrlDnsSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_hostname_resolving_to_private_address(self) -> None:
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.8", 443))
        ]
        with patch("asyncio.BaseEventLoop.getaddrinfo", new=AsyncMock(return_value=records)):
            with self.assertRaisesRegex(ValueError, "private or reserved"):
                await validate_public_http_url_async("https://public-looking.example/")

    async def test_rejects_hostname_with_mixed_public_and_private_dns_answers(self) -> None:
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.10", 443)),
        ]
        with patch("asyncio.BaseEventLoop.getaddrinfo", new=AsyncMock(return_value=records)):
            with self.assertRaisesRegex(ValueError, "private or reserved"):
                await validate_public_http_url_async("https://mixed.example/")

    async def test_rejects_hostname_when_dns_resolution_fails(self) -> None:
        with patch(
            "asyncio.BaseEventLoop.getaddrinfo",
            new=AsyncMock(side_effect=socket.gaierror("no DNS")),
        ):
            with self.assertRaisesRegex(ValueError, "could not be safely resolved"):
                await validate_public_http_url_async("https://unresolvable.example/")

    async def test_accepts_hostname_resolving_only_to_public_addresses(self) -> None:
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
        ]
        with patch("asyncio.BaseEventLoop.getaddrinfo", new=AsyncMock(return_value=records)):
            await validate_public_http_url_async("https://example.com/")


if __name__ == "__main__":
    unittest.main()
