"""
Shared HTTP fetcher with an in-process cache and bot-mitigation resilience.

Tools call fetch_parsed(url) which returns a FetchResult. The cache is an
LRU bounded to _CACHE_MAXSIZE entries, so multiple tools checking the same
URL in one agent turn hit the network only once — which is exactly why
getting that one fetch right matters: many WAFs / bot-mitigation layers
(Wordfence, Sucuri, Cloudflare, site builders like GoDaddy Website Builder)
serve a plain HTTP 200 with a stripped <head> to a request that doesn't
look like a real browser, rather than blocking it outright. A status-code
check alone won't catch that, and since the result is cached and reused by
every check for this URL, one bad fetch would otherwise silently poison
every one of them (Title Tag, Meta Description, H1, Schema, keywords —
all reporting false fails against the same bad snapshot).

fetch_parsed tries multiple browser header profiles and a cache-busting
retry before accepting a response that looks bot-gated (see
_looks_suspicious); if nothing clean turns up it returns the best
candidate found with suspicious=True rather than hard-failing, so callers
can flag it instead of trusting it silently. A hard transport failure
(DNS, connection refused, timeout) fails fast without retrying across
profiles — a different header set doesn't fix a dead connection, only a
bot-mitigation block.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from collections import OrderedDict
from dataclasses import dataclass, field

from .url_safety import assert_safe_url

# Tried in order. Many WAFs reject a bare/minimal request but allow a
# near-identical one a moment later with fuller browser headers
# (Sec-Fetch-*, Referer, Accept-Language) — this catches that common case
# without needing a real browser engine.
BROWSER_HEADER_PROFILES: list[dict] = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Referer": "https://www.google.com/",
        "Cache-Control": "no-cache",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    },
]

# Between profile attempts against the same domain — avoids tripping
# rate-based WAF rules further while retrying.
_RETRY_DELAY_SECONDS = 1.5
_BACKOFF_BASE_SECONDS = 2.0
_MAX_RETRY_AFTER_WAIT = 30.0

# Minimum gap between requests to the same domain, regardless of which
# check/row triggered them. A batch routinely fetches many URLs on one
# client's site concurrently (check_orchestrator bounds it to 8 in-flight
# MCP calls, but that's global, not per-domain) — bursting that many
# requests at one domain at once is exactly the kind of traffic pattern
# that trips rate-based WAF rules in the first place.
_REQUEST_DELAY_SECONDS = 1.0
_DOMAIN_THROTTLE_MAXSIZE = 512
_domain_last_request: OrderedDict[str, float] = OrderedDict()
_domain_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

_CACHE_MAXSIZE = 256
_cache: OrderedDict[str, "FetchResult"] = OrderedDict()


@dataclass
class FetchResult:
    url: str
    status_code: int
    soup: BeautifulSoup | None          # full HTML — scripts intact for schema/OG/canonical
    body_text: str                      # visible text, scripts/styles stripped
    response_headers: dict = field(default_factory=dict)
    error: str | None = None
    # True when every fetch attempt came back looking bot-gated (see
    # _looks_suspicious) — this is still the best response found, but
    # Title/Meta/H1/etc. results against it should be treated with caution
    # rather than trusted outright.
    suspicious: bool = False


def main_content_text(p: "FetchResult") -> str:
    """Text of the main content area (article/main/known content classes),
    falling back to the full page's visible body text. Shared by every tool
    that wants "the actual written content" rather than the whole page
    (word count, grammar, raw-text export) — lives here rather than in one
    of those tools since it's really a derived property of FetchResult."""
    soup = p.soup
    content_area = None
    if soup:
        content_area = (
            soup.find("article") or soup.find("main") or
            soup.find(class_=re.compile(
                r"entry-content|post-content|page-content|aiq-blog-content|"
                r"blog-content|article-content|post-body|entry-body|content-area|ct-text-block",
                re.I))
        )

    if content_area:
        for t in content_area(["script", "style"]):
            t.decompose()
        return content_area.get_text(separator=" ", strip=True)
    return p.body_text


def body_soup_excluding_chrome(p: "FetchResult") -> BeautifulSoup | None:
    """Copy of the page's <body> with <nav>/<header>/<footer> removed — for
    checks that need real tags (links, phone numbers) rather than just text,
    where boilerplate nav/header/footer markup (menu items, footer social/
    association links, address/phone in the footer, etc.) would otherwise
    produce false positives that have nothing to do with the actual page
    content.

    Operates on a copy of the cached soup's <body> rather than decomposing
    tags in place — p.soup is cached and reused by every other check for
    this same URL, so mutating it here would silently corrupt their results.
    """
    soup = p.soup
    if not soup:
        return None
    body = soup.find("body")
    if not body:
        return None
    body_copy = BeautifulSoup(str(body), "html.parser")
    for tag in body_copy.find_all(["nav", "header", "footer"]):
        tag.decompose()
    return body_copy


def body_text_excluding_chrome(p: "FetchResult") -> str:
    """Visible body text with <nav>/<header>/<footer> removed — for checks
    that scan for content mentions (keyword optimization, geo accuracy)."""
    body_copy = body_soup_excluding_chrome(p)
    if body_copy is None:
        return p.body_text
    return body_copy.get_text(" ", strip=True)


def _looks_suspicious(soup: BeautifulSoup | None, body_text: str) -> bool:
    """A real page essentially never ships with zero <title>, zero meta
    description, AND zero <h1> tags while still carrying a real,
    substantial body. That combination is the signature of a
    bot-mitigation or caching layer serving a stripped <head> to a
    non-browser-looking request rather than a genuine content problem."""
    if soup is None:
        return False
    title_tag = soup.find("title")
    has_title = bool(title_tag and title_tag.get_text(strip=True))
    meta_tag = soup.find("meta", {"name": "description"})
    has_meta = bool(meta_tag and meta_tag.get("content", "").strip())
    has_h1 = bool(soup.find("h1"))
    word_count = len(body_text.split()) if body_text else 0
    return not has_title and not has_meta and not has_h1 and word_count > 100


def _parse(html: str) -> tuple[BeautifulSoup, str]:
    # Full soup — keep <script> tags so schema / OG / canonical checks work
    full_soup = BeautifulSoup(html, "html.parser")
    # Visible-text copy — decompose noise tags
    text_soup = BeautifulSoup(html, "html.parser")
    for tag in text_soup(["script", "style", "noscript"]):
        tag.decompose()
    return full_soup, text_soup.get_text(separator=" ", strip=True)


def _result_from_response(url: str, status_code: int, html: str, headers: dict, *, suspicious: bool = False) -> "FetchResult":
    soup, body_text = _parse(html)
    return FetchResult(url=url, status_code=status_code, soup=soup, body_text=body_text,
                        response_headers=headers, suspicious=suspicious)


def _get_domain_lock(domain: str) -> asyncio.Lock:
    if domain in _domain_locks:
        _domain_locks.move_to_end(domain)
        return _domain_locks[domain]
    lock = asyncio.Lock()
    _domain_locks[domain] = lock
    if len(_domain_locks) > _DOMAIN_THROTTLE_MAXSIZE:
        _domain_locks.popitem(last=False)
    return lock


async def _throttle(url: str) -> None:
    """Sleep just enough to keep _REQUEST_DELAY_SECONDS between requests to
    the same domain. The lock serializes concurrent coroutines targeting the
    same domain so they queue up instead of all reading a stale
    "last request" time and firing at once."""
    domain = urlparse(url).netloc.lower()
    lock = _get_domain_lock(domain)
    async with lock:
        last = _domain_last_request.get(domain)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < _REQUEST_DELAY_SECONDS:
                await asyncio.sleep(_REQUEST_DELAY_SECONDS - elapsed)
        _domain_last_request[domain] = time.monotonic()
        _domain_last_request.move_to_end(domain)
        if len(_domain_last_request) > _DOMAIN_THROTTLE_MAXSIZE:
            _domain_last_request.popitem(last=False)


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    """Wait time for a 429: prefer the server's own Retry-After header
    (delta-seconds or an HTTP-date) — it's a direct answer to "how long
    until you'll accept another request" — falling back to exponential
    backoff when the server doesn't provide one."""
    header = response.headers.get("Retry-After", "").strip()
    if header:
        try:
            wait = float(header)
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(header)
                wait = (dt - datetime.now(dt.tzinfo)).total_seconds()
            except Exception:
                wait = _BACKOFF_BASE_SECONDS * (2 ** attempt)
        return max(1.0, min(wait, _MAX_RETRY_AFTER_WAIT))
    return min(_BACKOFF_BASE_SECONDS * (2 ** attempt), _MAX_RETRY_AFTER_WAIT)


async def _fetch_with_retries(client: httpx.AsyncClient, url: str) -> FetchResult:
    last_status = 0
    best_suspicious: FetchResult | None = None

    for i, profile in enumerate(BROWSER_HEADER_PROFILES):
        await _throttle(url)
        try:
            r = await client.get(url, headers=profile)
        except Exception as exc:
            # Transport-level failure (DNS, connection refused, timeout) —
            # a different header set won't fix a dead connection, so fail
            # fast instead of retrying pointlessly across every profile.
            return FetchResult(url=url, status_code=0, soup=None, body_text="", error=str(exc))

        if r.status_code in (403, 429):
            last_status = r.status_code
            if i < len(BROWSER_HEADER_PROFILES) - 1:
                delay = _retry_after_seconds(r, i) if r.status_code == 429 else _RETRY_DELAY_SECONDS
                await asyncio.sleep(delay)
            continue

        if not _looks_suspicious(*_parse(r.text)):
            return _result_from_response(url, r.status_code, r.text, dict(r.headers))

        candidate = _result_from_response(url, r.status_code, r.text, dict(r.headers), suspicious=True)
        if best_suspicious is None or len(candidate.body_text.split()) > len(best_suspicious.body_text.split()):
            best_suspicious = candidate
        if i < len(BROWSER_HEADER_PROFILES) - 1:
            await asyncio.sleep(_RETRY_DELAY_SECONDS)

    # Every header profile either got blocked or looked bot-gated — one
    # more try with a cache-busting query param, in case a CDN/WAF edge is
    # just serving a stale cached response for this exact URL rather than
    # actively blocking it. Cheap to try before settling for the best
    # candidate found so far.
    if best_suspicious is not None or last_status in (403, 429):
        sep = "&" if "?" in url else "?"
        busted_url = f"{url}{sep}_cb={int(time.time() * 1000)}"
        await _throttle(url)
        try:
            r = await client.get(busted_url, headers=BROWSER_HEADER_PROFILES[0])
        except Exception:
            r = None
        if r is not None and r.status_code not in (403, 429):
            if not _looks_suspicious(*_parse(r.text)):
                return _result_from_response(url, r.status_code, r.text, dict(r.headers))
            candidate = _result_from_response(url, r.status_code, r.text, dict(r.headers), suspicious=True)
            if best_suspicious is None or len(candidate.body_text.split()) > len(best_suspicious.body_text.split()):
                best_suspicious = candidate
        elif r is not None:
            last_status = r.status_code

    if best_suspicious is not None:
        return best_suspicious
    if last_status:
        return FetchResult(url=url, status_code=last_status, soup=None, body_text="", error=f"HTTP {last_status}")
    return FetchResult(url=url, status_code=0, soup=None, body_text="", error="Fetch failed")


async def fetch_parsed(url: str) -> FetchResult:
    """Fetch *url* and return a FetchResult. LRU-cached up to _CACHE_MAXSIZE entries."""
    url = url.strip()
    if url in _cache:
        _cache.move_to_end(url)
        return _cache[url]

    try:
        assert_safe_url(url)
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            result = await _fetch_with_retries(client, url)
    except Exception as exc:
        result = FetchResult(url=url, status_code=0, soup=None, body_text="", error=str(exc))

    _cache[url] = result
    _cache.move_to_end(url)
    if len(_cache) > _CACHE_MAXSIZE:
        _cache.popitem(last=False)
    return result


def clear_cache() -> None:
    _cache.clear()
    _domain_last_request.clear()
    _domain_locks.clear()
