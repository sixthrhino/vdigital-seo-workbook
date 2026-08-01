from __future__ import annotations

import asyncio

from .url_safety import UnsafeURLError, assert_safe_url

# Tried in order. Many WAFs/CDNs (confirmed live against a real client site:
# a JS "checking your browser" challenge from a CDN calling itself "hcdn")
# reject a bare/minimal request but let an otherwise-identical one through
# directly — no JS execution needed — once it carries a fuller browser
# header profile (Sec-Fetch-*, Referer, Accept-Language). Mirrors
# seo-testing-mcp's fetcher.py's BROWSER_HEADER_PROFILES (confirmed there
# first, on this exact site, before porting it here) — duplicated rather
# than imported, since this package deliberately has no dependency on that
# one (see the repo root CLAUDE.md). The caching/domain-throttling
# machinery in that version is batch-oriented (many concurrent fetches
# against one client's site per QA run) and isn't needed here — the dialog
# only ever fetches one URL at a time, waiting on the specialist's own
# clicks between steps.
_BROWSER_HEADER_PROFILES: list[dict[str, str]] = [
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

_TIMEOUT = 10
_RETRY_DELAY_SECONDS = 1.5

_EMPTY_CURRENT_VALUES = {"title": "", "meta_description": "", "h1": ""}


def _parse_current_values(html: str) -> dict[str, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else ""

    meta_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = (meta_tag.get("content") or "").strip() if meta_tag else ""

    h1_tag = soup.find("h1")
    h1 = h1_tag.get_text(strip=True) if h1_tag else ""

    return {"title": title, "meta_description": meta_description, "h1": h1}


def _looks_bot_gated(values: dict[str, str], html: str) -> bool:
    """A real page essentially never ships with zero title, zero meta
    description, AND zero H1 while still carrying substantial body text —
    that combination is the signature of a bot-mitigation layer serving a
    stripped/challenge page rather than a genuine content gap. Mirrors
    seo-testing-mcp's fetcher._looks_suspicious."""
    if values["title"] or values["meta_description"] or values["h1"]:
        return False
    return len(html.split()) > 100


async def fetch_current_page_values(url: str) -> dict[str, str]:
    """Best-effort fetch of a live page's current title/meta description/H1
    — shown as hint text on the corresponding new-value fields in the
    page-fields dialog step (see dialog_cards.build_url_entry_dialog), so a
    specialist doesn't have to separately look up and paste in what's
    already there.

    Tries each browser header profile in turn when a response comes back
    403/429 or looking bot-gated (see _looks_bot_gated) before giving up —
    confirmed necessary live: a real client site rejected a bare request
    with a JS "checking your browser" challenge page, but the same URL
    with a fuller browser header profile got the real page directly, no
    JS execution required.

    Never raises: any failure (unsafe/unresolvable URL, timeout, every
    profile blocked) just leaves every field's value as "".
    """
    try:
        assert_safe_url(url)
    except UnsafeURLError:
        return dict(_EMPTY_CURRENT_VALUES)

    import httpx

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT) as client:
            for i, profile in enumerate(_BROWSER_HEADER_PROFILES):
                try:
                    response = await client.get(url, headers=profile)
                except Exception:
                    return dict(_EMPTY_CURRENT_VALUES)

                is_last_profile = i == len(_BROWSER_HEADER_PROFILES) - 1
                if response.status_code in (403, 429):
                    if not is_last_profile:
                        await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                if response.status_code >= 400:
                    return dict(_EMPTY_CURRENT_VALUES)

                values = _parse_current_values(response.text)
                if not _looks_bot_gated(values, response.text):
                    return values
                if not is_last_profile:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
    except Exception:
        return dict(_EMPTY_CURRENT_VALUES)

    return dict(_EMPTY_CURRENT_VALUES)
