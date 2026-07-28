"""Tests for mcp-server/tools/fetcher.py"""

import time

import httpx
import pytest
import respx
from bs4 import BeautifulSoup

from seo_testing_mcp.tools import fetcher
from seo_testing_mcp.tools.fetcher import fetch_parsed, _looks_suspicious


def _mock(url, body="ok", status=200):
    respx.get(url).mock(return_value=httpx.Response(status, text=body))


def _clean_html(title="Real Title", words=5):
    body = " ".join(["word"] * words)
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


def _stripped_html(words=150):
    # No <title>/<meta name=description>/<h1> — the bot-gated signature —
    # but a substantial body, same as a real WAF-stripped response would have.
    body = " ".join(["word"] * words)
    return f"<html><head></head><body>{body}</body></html>"


class TestCache:
    @respx.mock
    async def test_second_call_hits_cache_not_network(self):
        route = respx.get("https://example.com/a").mock(
            return_value=httpx.Response(200, text="hello")
        )
        await fetch_parsed("https://example.com/a")
        await fetch_parsed("https://example.com/a")
        assert route.call_count == 1

    @respx.mock
    async def test_cache_is_bounded_to_maxsize(self, monkeypatch):
        monkeypatch.setattr(fetcher, "_CACHE_MAXSIZE", 3)
        for i in range(5):
            _mock(f"https://example.com/{i}")
            await fetch_parsed(f"https://example.com/{i}")

        assert len(fetcher._cache) == 3
        # oldest entries evicted, most recent retained
        assert "https://example.com/0" not in fetcher._cache
        assert "https://example.com/1" not in fetcher._cache
        assert "https://example.com/4" in fetcher._cache

    @respx.mock
    async def test_reaccessing_entry_protects_it_from_eviction(self, monkeypatch):
        monkeypatch.setattr(fetcher, "_CACHE_MAXSIZE", 3)
        for i in range(3):
            _mock(f"https://example.com/{i}")
            await fetch_parsed(f"https://example.com/{i}")

        # touch the oldest entry so it becomes most-recently-used
        await fetch_parsed("https://example.com/0")

        _mock("https://example.com/3")
        await fetch_parsed("https://example.com/3")

        assert "https://example.com/0" in fetcher._cache
        assert "https://example.com/1" not in fetcher._cache

    @respx.mock
    async def test_failed_fetch_is_cached_too(self):
        route = respx.get("https://example.com/down").mock(
            side_effect=httpx.ConnectError("boom")
        )
        r1 = await fetch_parsed("https://example.com/down")
        r2 = await fetch_parsed("https://example.com/down")
        assert r1.status_code == 0
        assert r1.error is not None
        assert route.call_count == 1
        assert r2 is r1


class TestLooksSuspicious:
    def test_stripped_head_with_long_body_is_suspicious(self):
        soup = BeautifulSoup(_stripped_html(150), "html.parser")
        body_text = soup.get_text(separator=" ", strip=True)
        assert _looks_suspicious(soup, body_text) is True

    def test_has_title_is_not_suspicious(self):
        soup = BeautifulSoup(_clean_html(words=150), "html.parser")
        body_text = soup.get_text(separator=" ", strip=True)
        assert _looks_suspicious(soup, body_text) is False

    def test_stripped_head_with_short_body_is_not_suspicious(self):
        # A genuinely thin/empty page shouldn't be flagged — the signature
        # is specifically real body content with an implausibly empty head.
        soup = BeautifulSoup(_stripped_html(words=10), "html.parser")
        body_text = soup.get_text(separator=" ", strip=True)
        assert _looks_suspicious(soup, body_text) is False

    def test_none_soup_is_not_suspicious(self):
        assert _looks_suspicious(None, "anything") is False


class TestRetryOnBlockedStatus:
    @respx.mock
    async def test_403_then_clean_200_succeeds_on_second_profile(self):
        route = respx.get(url__regex=r"https://example\.com/a.*").mock(
            side_effect=[
                httpx.Response(403, text="blocked"),
                httpx.Response(200, text=_clean_html()),
            ]
        )
        result = await fetch_parsed("https://example.com/a")

        assert route.call_count == 2
        assert result.status_code == 200
        assert result.suspicious is False
        assert result.error is None

    @respx.mock
    async def test_all_profiles_403_then_cache_bust_succeeds(self):
        # len(BROWSER_HEADER_PROFILES) 403s, then the cache-busting retry
        # (a third call, to a _cb=-suffixed URL) comes back clean.
        route = respx.get(url__regex=r"https://example\.com/a.*").mock(
            side_effect=[httpx.Response(403, text="blocked")] * len(fetcher.BROWSER_HEADER_PROFILES)
            + [httpx.Response(200, text=_clean_html())]
        )
        result = await fetch_parsed("https://example.com/a")

        assert route.call_count == len(fetcher.BROWSER_HEADER_PROFILES) + 1
        assert "_cb=" in str(route.calls.last.request.url)
        assert result.status_code == 200
        assert result.suspicious is False

    @respx.mock
    async def test_all_attempts_403_returns_error_result(self):
        respx.get(url__regex=r"https://example\.com/a.*").mock(
            return_value=httpx.Response(403, text="blocked")
        )
        result = await fetch_parsed("https://example.com/a")

        assert result.status_code == 403
        assert result.error == "HTTP 403"
        assert result.soup is None


class TestRetryOnSuspiciousResponse:
    @respx.mock
    async def test_stripped_head_then_clean_profile_uses_clean_result(self):
        respx.get(url__regex=r"https://example\.com/a.*").mock(
            side_effect=[
                httpx.Response(200, text=_stripped_html()),
                httpx.Response(200, text=_clean_html()),
            ]
        )
        result = await fetch_parsed("https://example.com/a")

        assert result.suspicious is False
        assert result.soup.find("title").get_text() == "Real Title"

    @respx.mock
    async def test_every_attempt_stripped_returns_best_suspicious_candidate(self):
        # Every profile plus the cache-bust retry all come back stripped —
        # the second profile's response has more body words, so it should
        # win as "best" even though neither is trustworthy.
        respx.get(url__regex=r"https://example\.com/a.*").mock(
            side_effect=[
                httpx.Response(200, text=_stripped_html(words=120)),
                httpx.Response(200, text=_stripped_html(words=200)),
                httpx.Response(200, text=_stripped_html(words=130)),
            ]
        )
        result = await fetch_parsed("https://example.com/a")

        assert result.suspicious is True
        assert result.error is None  # still a "successful" 200 — just untrustworthy
        assert len(result.body_text.split()) == 200


class TestFailsFastOnTransportError:
    @respx.mock
    async def test_exception_does_not_retry_other_profiles(self):
        # A hard transport failure (DNS/connection/timeout) won't be fixed
        # by a different header set — only one attempt should be made,
        # regardless of how many BROWSER_HEADER_PROFILES exist.
        route = respx.get("https://example.com/down").mock(
            side_effect=httpx.ConnectError("boom")
        )
        result = await fetch_parsed("https://example.com/down")

        assert route.call_count == 1
        assert result.error == "boom"


class TestThrottle:
    async def test_second_request_to_same_domain_waits(self, monkeypatch):
        monkeypatch.setattr(fetcher, "_REQUEST_DELAY_SECONDS", 0.05)
        start = time.monotonic()
        await fetcher._throttle("https://example.com/a")
        await fetcher._throttle("https://example.com/b")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.04

    async def test_different_domains_do_not_throttle_each_other(self, monkeypatch):
        monkeypatch.setattr(fetcher, "_REQUEST_DELAY_SECONDS", 1.0)
        start = time.monotonic()
        await fetcher._throttle("https://example.com/a")
        await fetcher._throttle("https://other.com/a")
        elapsed = time.monotonic() - start
        assert elapsed < 0.5

    async def test_domain_matching_is_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(fetcher, "_REQUEST_DELAY_SECONDS", 0.05)
        start = time.monotonic()
        await fetcher._throttle("https://Example.com/a")
        await fetcher._throttle("https://example.COM/b")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.04

    async def test_clear_cache_resets_throttle_state(self, monkeypatch):
        monkeypatch.setattr(fetcher, "_REQUEST_DELAY_SECONDS", 1.0)
        await fetcher._throttle("https://example.com/a")
        fetcher.clear_cache()
        start = time.monotonic()
        await fetcher._throttle("https://example.com/a")
        elapsed = time.monotonic() - start
        assert elapsed < 0.5


class TestRetryAfterSeconds:
    def test_uses_retry_after_seconds_header(self):
        response = httpx.Response(429, headers={"Retry-After": "5"})
        assert fetcher._retry_after_seconds(response, attempt=0) == 5.0

    def test_clamps_to_max_wait(self):
        response = httpx.Response(429, headers={"Retry-After": "9999"})
        assert fetcher._retry_after_seconds(response, attempt=0) == fetcher._MAX_RETRY_AFTER_WAIT

    def test_enforces_minimum_one_second(self):
        response = httpx.Response(429, headers={"Retry-After": "0"})
        assert fetcher._retry_after_seconds(response, attempt=0) == 1.0

    def test_parses_http_date_header(self):
        from email.utils import format_datetime
        from datetime import datetime, timezone, timedelta
        future = datetime.now(timezone.utc) + timedelta(seconds=10)
        response = httpx.Response(429, headers={"Retry-After": format_datetime(future, usegmt=True)})
        wait = fetcher._retry_after_seconds(response, attempt=0)
        assert 8 <= wait <= 12

    def test_falls_back_to_exponential_backoff_when_no_header(self):
        response = httpx.Response(429)
        assert fetcher._retry_after_seconds(response, attempt=0) == fetcher._BACKOFF_BASE_SECONDS * 1
        assert fetcher._retry_after_seconds(response, attempt=1) == fetcher._BACKOFF_BASE_SECONDS * 2

    def test_malformed_header_falls_back_to_backoff(self):
        response = httpx.Response(429, headers={"Retry-After": "not-a-number-or-date"})
        wait = fetcher._retry_after_seconds(response, attempt=1)
        assert wait == fetcher._BACKOFF_BASE_SECONDS * 2


class TestRetryAfterIntegration:
    @respx.mock
    async def test_429_retry_uses_retry_after_seconds(self, monkeypatch):
        calls = []

        def fake_retry_after(response, attempt):
            calls.append(attempt)
            return 0

        monkeypatch.setattr(fetcher, "_retry_after_seconds", fake_retry_after)
        respx.get(url__regex=r"https://example\.com/a.*").mock(
            side_effect=[
                httpx.Response(429, text="rate limited"),
                httpx.Response(200, text=_clean_html()),
            ]
        )
        result = await fetch_parsed("https://example.com/a")

        assert calls == [0]
        assert result.status_code == 200

    @respx.mock
    async def test_403_does_not_use_retry_after_seconds(self, monkeypatch):
        # 403 isn't a rate-limit response — there's no Retry-After semantics
        # to respect, so it should use the flat retry delay instead.
        calls = []
        monkeypatch.setattr(fetcher, "_retry_after_seconds", lambda r, a: calls.append(a) or 0)
        respx.get(url__regex=r"https://example\.com/a.*").mock(
            side_effect=[
                httpx.Response(403, text="blocked"),
                httpx.Response(200, text=_clean_html()),
            ]
        )
        result = await fetch_parsed("https://example.com/a")

        assert calls == []
        assert result.status_code == 200
