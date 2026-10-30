from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".home.arpa",
    ".lan",
    ".test",
    ".invalid",
    ".onion",
)


def _validated_target(url: str) -> tuple[str, int, bool]:
    """Return (hostname, port, is_ip_literal) for a safe public HTTP(S) URL."""
    try:
        parsed = urlsplit(str(url))
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid provider URL") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Provider URLs must use HTTP or HTTPS")
    if not hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("Provider URL must contain a hostname and no embedded credentials")

    hostname = hostname.rstrip(".").lower()
    if not hostname or "%" in hostname:
        raise ValueError("Invalid provider hostname")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname or any(hostname == suffix[1:] or hostname.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
            raise ValueError("Provider URL must use a public hostname")
        return hostname, port or (443 if parsed.scheme.lower() == "https" else 80), False

    if not address.is_global:
        raise ValueError("Provider URL must not target a private or reserved IP address")
    return hostname, port or (443 if parsed.scheme.lower() == "https" else 80), True


def validate_public_http_url(url: str) -> None:
    """Reject non-public URL literals and local/reserved hostnames."""
    _validated_target(url)


async def validate_public_http_url_async(url: str) -> None:
    """Also resolve DNS and reject hostnames resolving to any non-public address.

    This is used for every browser request, so redirects and subresource requests
    are checked as well as the initial provider URL.
    """
    hostname, port, is_ip_literal = _validated_target(url)
    if is_ip_literal:
        return

    try:
        records = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except (OSError, socket.gaierror) as exc:
        raise ValueError("Provider hostname could not be safely resolved") from exc

    if not records:
        raise ValueError("Provider hostname did not resolve")

    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if not address.is_global:
            raise ValueError("Provider hostname resolves to a private or reserved IP address")
