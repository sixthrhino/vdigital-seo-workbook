"""SSRF guard for anything that fetches a caller-supplied URL.

Every check tool eventually fetches a URL that traces back to either the
public, unauthenticated /run endpoint or a workbook row — neither is a
trusted input. Worse, the agent's own next tool call can be steered by page
content it just fetched (indirect prompt injection). Without this, a URL
like http://169.254.169.254/... (the cloud metadata server) would be fetched
same as any other page.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """Raised when a URL fails the safety check — never let this escape as
    an unhandled exception; callers should catch it like any other fetch
    failure and report a normal fail/error check result."""


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
