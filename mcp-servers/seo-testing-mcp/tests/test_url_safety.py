"""Tests for mcp-server/tools/url_safety.py"""
import socket

import pytest

from seo_testing_mcp.tools import url_safety
from seo_testing_mcp.tools.url_safety import assert_safe_url, UnsafeURLError


def _mock_resolves_to(monkeypatch, ip: str):
    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)


class TestAssertSafeUrl:
    def test_public_https_url_passes(self, monkeypatch):
        _mock_resolves_to(monkeypatch, "93.184.216.34")
        assert_safe_url("https://example.com/page")

    def test_public_http_url_passes(self, monkeypatch):
        _mock_resolves_to(monkeypatch, "93.184.216.34")
        assert_safe_url("http://example.com/page")

    def test_private_ip_literal_blocked(self, monkeypatch):
        _mock_resolves_to(monkeypatch, "10.0.0.5")
        with pytest.raises(UnsafeURLError):
            assert_safe_url("http://10.0.0.5/")

    def test_192_168_ip_literal_blocked(self, monkeypatch):
        _mock_resolves_to(monkeypatch, "192.168.1.1")
        with pytest.raises(UnsafeURLError):
            assert_safe_url("http://192.168.1.1/")

    def test_loopback_blocked(self, monkeypatch):
        _mock_resolves_to(monkeypatch, "127.0.0.1")
        with pytest.raises(UnsafeURLError):
            assert_safe_url("http://127.0.0.1/")

    def test_domain_resolving_to_loopback_blocked(self, monkeypatch):
        # DNS-based bypass attempt: a public-looking hostname that resolves
        # to a private address must still be blocked (not just IP literals).
        _mock_resolves_to(monkeypatch, "127.0.0.1")
        with pytest.raises(UnsafeURLError):
            assert_safe_url("http://sketchy-domain.example/")

    def test_cloud_metadata_ip_blocked(self, monkeypatch):
        _mock_resolves_to(monkeypatch, "169.254.169.254")
        with pytest.raises(UnsafeURLError):
            assert_safe_url("http://169.254.169.254/computeMetadata/v1/")

    def test_ipv6_loopback_blocked(self, monkeypatch):
        def fake_getaddrinfo(host, port):
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0))]
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(UnsafeURLError):
            assert_safe_url("http://[::1]/")

    def test_unsupported_scheme_blocked(self):
        with pytest.raises(UnsafeURLError):
            assert_safe_url("file:///etc/passwd")

    def test_ftp_scheme_blocked(self):
        with pytest.raises(UnsafeURLError):
            assert_safe_url("ftp://example.com/")

    def test_no_hostname_blocked(self):
        with pytest.raises(UnsafeURLError):
            assert_safe_url("http:///path")

    def test_unresolvable_host_blocked(self, monkeypatch):
        def fake_getaddrinfo(host, port):
            raise socket.gaierror("Name or service not known")
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(UnsafeURLError):
            assert_safe_url("http://this-host-does-not-exist.invalid/")
