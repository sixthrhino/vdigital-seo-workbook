"""SSRF guard for anything that fetches a caller-supplied URL.

The page-update dialog fetches whatever URL a specialist types into a Chat
form — untrusted input, same as anything reaching a public endpoint.
Without this, a URL like http://169.254.169.254/... (the cloud metadata
server) would be fetched same as any other page.

Mirrors mcp-servers/seo-testing-mcp/src/seo_testing_mcp/tools/url_safety.py
exactly — duplicated rather than imported, since this package deliberately
has no dependency on that one (see the repo root CLAUDE.md).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """Raised when a URL fails the safety check — never let this escape as
    an unhandled exception; callers should catch it like any other fetch
    failure."""


def assert_safe_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Unsupported URL scheme: {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError(f"URL has no hostname: {url!r}")

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host: {hostname}") from exc

    for family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeURLError(f"{hostname} resolves to a non-public address ({ip})")
