"""
Content QA checks: word count, publish date, broken links, old/new
sentence verification, and raw page text for ad-hoc analysis.

Brand guide parsing/checks live in brand_guide.py; Gemini-based checks
(grammar, per-page recommendations) live in gemini_checks.py — both were
split out of this file since they're structurally different concerns
(a whole separate parsed-data-format, and the only LLM-calling checks in
this package, respectively).
"""

from __future__ import annotations

import datetime
import json
import re
from urllib.parse import urlparse

import httpx

from .fetcher import fetch_parsed, main_content_text
from .url_safety import assert_safe_url, UnsafeURLError

WORD_LIMIT = 600

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

_LINK_SKIP_DOMAINS = (
    "shutterstock.com", "facebook.com/sharer", "twitter.com/intent",
    "x.com/intent", "pinterest.com/pin/create", "linkedin.com/shareArticle",
    "instagram.com", "gettyimages.com", "istockphoto.com", "adobe.com/stock",
)


def _r(label: str, status: str, detail: str) -> dict:
    return {"label": label, "status": status, "detail": detail}


# ---------------------------------------------------------------------------
# Word count
# ---------------------------------------------------------------------------

async def check_word_count(url: str) -> list[dict]:
    """Count words in the main content area (falls back to full body)."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    text = main_content_text(p)
    wc = len(text.split()) if text else 0
    if wc == 0:
        return [_r("Word Count", "warn", "Could not determine word count")]
    status = "pass" if wc <= WORD_LIMIT else "warn"
    return [_r("Word Count", status,
               f"{wc} words ({'under' if wc <= WORD_LIMIT else 'exceeds'} {WORD_LIMIT}-word limit)")]


# ---------------------------------------------------------------------------
# Publish date
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> datetime.date | None:
    raw = raw.strip()
    clean = re.sub(r"\.\d+", "", raw)
    clean = re.sub(r"([+-]\d{2}:\d{2}|Z)$", "", clean).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y",
                "%Y-%m-%d", "%m-%d-%Y"):
        for candidate in (clean, raw):
            try:
                return datetime.datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


async def check_publish_date(url: str, expected_date: str | None = None) -> list[dict]:
    """Find the publish date on a page and optionally compare to an expected value."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    soup = p.soup
    found: str | None = None

    if soup:
        # <time> tag
        t = soup.find("time")
        if t:
            found = t.get("datetime") or t.get_text(strip=True) or None

        # JSON-LD datePublished
        if not found:
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if isinstance(item, dict):
                            for key in ("datePublished", "dateCreated", "pubDate"):
                                if key in item:
                                    found = str(item[key])
                                    break
                        if found:
                            break
                except Exception:
                    pass

        # Common meta tags
        if not found:
            for attr in [("property", "article:published_time"),
                          ("name", "pubdate"), ("name", "date"),
                          ("itemprop", "datePublished")]:
                tag = soup.find("meta", {attr[0]: attr[1]})
                if tag and tag.get("content"):
                    found = tag["content"].strip()
                    break

        # Date patterns in visible text
        if not found:
            for pat in [
                r"\b(?:January|February|March|April|May|June|July|August|September|"
                r"October|November|December)\s+\d{1,2},?\s+\d{4}\b",
                r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
                r"\b\d{4}-\d{2}-\d{2}\b",
            ]:
                m = re.search(pat, p.body_text[:3000])
                if m:
                    found = m.group(0)
                    break

    if not expected_date:
        return [_r("Publish Date", "info" if found else "warn",
                   found or "No publish date found on page")]

    if not found:
        return [_r("Publish Date", "warn",
                   f"Expected {expected_date} but no date found on page")]

    exp = _parse_date(expected_date)
    got = _parse_date(found)
    if exp and got:
        if exp == got:
            return [_r("Publish Date", "pass", f"{found} matches workbook ({expected_date})")]
        return [_r("Publish Date", "fail",
                   f'Page shows "{found}", workbook expects "{expected_date}"')]

    # Fallback: text comparison
    if expected_date.lower() in found.lower() or found.lower() in expected_date.lower():
        return [_r("Publish Date", "pass", f'"{found}" matches workbook')]
    return [_r("Publish Date", "warn",
               f'Page shows "{found}", expected "{expected_date}" — verify manually')]


# ---------------------------------------------------------------------------
# Raw page text (for callers doing their own analysis over page content)
# ---------------------------------------------------------------------------

async def get_page_text(url: str, max_chars: int = 4000) -> dict:
    """Fetch a page and return its main-content text, truncated to max_chars.

    Unlike the check_* functions this isn't a pass/fail check — it's raw
    material for a caller (e.g. the agent doing an ad-hoc /run review) to
    run its own analysis on, so it returns a plain dict rather than a
    checks list.
    """
    p = await fetch_parsed(url)
    if p.error:
        return {"url": url, "text": "", "truncated": False, "error": p.error}

    text = main_content_text(p)
    truncated = len(text) > max_chars
    return {
        "url": url,
        "text": text[:max_chars],
        "truncated": truncated,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Broken links
# ---------------------------------------------------------------------------

async def check_broken_links(url: str) -> list[dict]:
    """Check content-area links for broken URLs (4xx/5xx)."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    soup = p.soup
    if not soup:
        return [_r("Broken Links", "warn", "Could not parse page")]

    content_area = (
        soup.find("article") or soup.find("main") or
        soup.find(class_=re.compile(
            r"entry-content|post-content|page-content|aiq-blog-content|"
            r"blog-content|article-content|content-area|ct-text-block",
            re.I))
    )
    links = (content_area or soup).find_all("a", href=True)

    parsed_base = urlparse(url)
    results = []
    checked = broken = bot_blocked = skipped = 0

    async with httpx.AsyncClient(follow_redirects=True, timeout=10, headers=_HEADERS) as client:
        for a in links:
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                skipped += 1
                continue
            if any(d in href for d in _LINK_SKIP_DOMAINS):
                skipped += 1
                continue
            if href.startswith("/"):
                href = f"{parsed_base.scheme}://{parsed_base.netloc}{href}"
            if not href.startswith("http"):
                skipped += 1
                continue
            # These hrefs come straight from page content the fetched site
            # controls — don't follow one into an internal/private address.
            try:
                assert_safe_url(href)
            except UnsafeURLError:
                skipped += 1
                continue

            checked += 1
            try:
                r = await client.head(href)
                if r.status_code >= 400:
                    r = await client.get(href)
                if r.status_code in (403, 429):
                    bot_blocked += 1
                    results.append(_r("Link Check", "warn",
                                       f"HTTP {r.status_code} (likely bot block) — {href[:80]}"))
                elif r.status_code >= 400:
                    broken += 1
                    results.append(_r("Broken Link", "fail", f"HTTP {r.status_code} — {href[:80]}"))
            except Exception as exc:
                broken += 1
                results.append(_r("Broken Link", "fail", f"{str(exc)[:60]} — {href[:80]}"))

    if checked == 0:
        results.append(_r("Content Links", "warn", "No content links found to check"))
    elif broken:
        results.append(_r("Content Links", "warn",
                           f"{broken} broken of {checked} checked ({bot_blocked} bot-blocked)"))
    else:
        results.append(_r("Content Links", "pass",
                           f"All {checked} content link(s) OK"
                           + (f" ({bot_blocked} bot-blocked — verify manually)" if bot_blocked else "")))

    return results


# ---------------------------------------------------------------------------
# Sentence verification (opts_qa style)
# ---------------------------------------------------------------------------

async def check_sentences(
    url: str,
    old_sentences: list[str],
    new_sentences: list[str],
) -> list[dict]:
    """Verify old sentences are gone and new sentences are live."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    text = p.body_text
    results = []
    for i, (old_s, new_s) in enumerate(zip(old_sentences, new_sentences), 1):
        label = f"Sentence {i}" if len(old_sentences) > 1 else "Content Sentence"
        old_found = old_s.lower()[:60] in text.lower() if old_s else False
        new_found = new_s.lower()[:60] in text.lower() if new_s else False
        if new_found and not old_found:
            results.append(_r(label, "pass", "New sentence live, old sentence removed"))
        elif new_found and old_found:
            results.append(_r(label, "warn", "New sentence found but old still present"))
        elif old_found:
            results.append(_r(label, "fail",
                               f'Old still live, new not found. Expected: "{new_s[:80]}"'))
        else:
            results.append(_r(label, "warn",
                               f'Neither found — may have been rewritten. Expected: "{new_s[:80]}"'))
    return results
