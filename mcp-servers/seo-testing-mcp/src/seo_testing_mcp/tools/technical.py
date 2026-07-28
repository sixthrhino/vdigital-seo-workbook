"""
Technical SEO checks: robots.txt, llms.txt, XML sitemap, URL hygiene,
NAP, page speed, and caching headers.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from .fetcher import fetch_parsed
from .url_safety import assert_safe_url, UnsafeURLError

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _r(label: str, status: str, detail: str) -> dict:
    return {"label": label, "status": status, "detail": detail}


def _domain(url: str) -> str:
    p = urlparse(url.strip())
    return f"{p.scheme}://{p.netloc}"


# ---------------------------------------------------------------------------
# Robots.txt
# ---------------------------------------------------------------------------

# AI / answer-engine crawlers worth verifying explicitly for AIO (AI-search
# visibility) work. Keyed by the user-agent token that appears in
# robots.txt, grouped by vendor for the report detail. bingbot is included
# because it feeds Copilot answers.
_AI_CRAWLER_BOTS = [
    ("GPTBot", "OpenAI"), ("OAI-SearchBot", "OpenAI"), ("ChatGPT-User", "OpenAI"),
    ("ClaudeBot", "Anthropic"), ("Claude-User", "Anthropic"), ("anthropic-ai", "Anthropic"),
    ("PerplexityBot", "Perplexity"), ("Perplexity-User", "Perplexity"),
    ("Google-Extended", "Google AI"), ("Applebot-Extended", "Apple Intelligence"),
    ("bingbot", "Microsoft/Bing AI"), ("CCBot", "Common Crawl"),
]


def _parse_robots_groups(robots_text: str) -> list[tuple[set[str], list[tuple[str, str]]]]:
    """Parse robots.txt into [(set_of_user_agents_lowercased, [(directive, value), ...])]."""
    groups: list[tuple[set[str], list[tuple[str, str]]]] = []
    current_agents: set[str] = set()
    current_rules: list[tuple[str, str]] = []
    for line in robots_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            # A user-agent line after rules starts a new group; consecutive
            # user-agent lines share one group.
            if current_rules:
                groups.append((current_agents, current_rules))
                current_agents, current_rules = set(), []
            current_agents.add(value.lower())
        elif field in ("allow", "disallow"):
            current_rules.append((field, value))
    if current_agents or current_rules:
        groups.append((current_agents, current_rules))
    return groups


def _bot_blocked_by_robots(bot_token: str, groups: list[tuple[set[str], list[tuple[str, str]]]]) -> bool:
    """True if the bot's effective robots.txt group disallows the site root.
    Per the robots standard, a bot obeys the most specific matching group
    (its own name beats the * wildcard) and ignores all other groups."""
    bot_lower = bot_token.lower()
    specific = [rules for agents, rules in groups if bot_lower in agents]
    wildcard = [rules for agents, rules in groups if "*" in agents]
    effective = specific if specific else wildcard
    if not effective:
        return False  # no matching group at all means full access
    for rules in effective:
        allows_root = any(d == "allow" and v in ("/", "") for d, v in rules)
        disallows_all = any(d == "disallow" and v == "/" for d, v in rules)
        if disallows_all and not allows_root:
            return True
    return False


async def check_robots_txt(domain: str) -> list[dict]:
    """Check robots.txt — existence, sitemap reference, AI crawler access."""
    url = _domain(domain) + "/robots.txt"
    try:
        assert_safe_url(url)
    except UnsafeURLError as exc:
        return [_r("robots.txt", "fail", f"Unsafe URL: {exc}")]
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(url, headers=_HEADERS)
        except Exception as exc:
            return [_r("robots.txt", "fail", f"Could not fetch: {exc}")]

    if r.status_code != 200:
        return [_r("robots.txt", "fail", f"HTTP {r.status_code}")]

    content = r.text
    results = [_r("robots.txt", "pass", f"Accessible at {url}")]

    results.append(_r("Sitemap in robots.txt",
                       "pass" if "sitemap" in content.lower() else "warn",
                       "Sitemap URL referenced" if "sitemap" in content.lower()
                       else "No Sitemap: line found"))

    # Checking whether a bot's *name* is mentioned anywhere in robots.txt
    # (the previous approach here) can't tell an explicit Allow from an
    # explicit Disallow — a robots.txt that blocks GPTBot outright would
    # have reported "pass" under that check. This parses the actual
    # directive groups and evaluates each crawler's effective access per
    # the robots standard (most-specific matching group wins).
    groups = _parse_robots_groups(content)
    blocked = [(bot, vendor) for bot, vendor in _AI_CRAWLER_BOTS if _bot_blocked_by_robots(bot, groups)]
    if blocked:
        blocked_list = ", ".join(f"{bot} ({vendor})" for bot, vendor in blocked)
        results.append(_r("AI Crawler Access", "fail",
                           f"{len(blocked)} AI crawler(s) blocked from the site root: {blocked_list}. "
                           "If AIO/AI-search visibility is part of this opt, these should be allowed."))
    else:
        explicit = {a for agents, _ in groups for a in agents if a != "*"}
        named = [bot for bot, _ in _AI_CRAWLER_BOTS if bot.lower() in explicit]
        if named:
            results.append(_r("AI Crawler Access", "pass",
                               f"All {len(_AI_CRAWLER_BOTS)} known AI crawlers have site access. "
                               f"{len(named)} are explicitly named and allowed: {', '.join(named)}."))
        else:
            results.append(_r("AI Crawler Access", "pass",
                               f"All {len(_AI_CRAWLER_BOTS)} known AI crawlers have site access via the "
                               "wildcard rules. None are explicitly named, which works but is less "
                               "future-proof than naming them (explicit allowances survive a later "
                               "restrictive edit to the * group)."))
    return results


# ---------------------------------------------------------------------------
# llms.txt
# ---------------------------------------------------------------------------

async def check_llms_txt(domain: str) -> list[dict]:
    """Check whether llms.txt exists and meets basic structural requirements."""
    url = _domain(domain) + "/llms.txt"
    try:
        assert_safe_url(url)
    except UnsafeURLError as exc:
        return [_r("llms.txt", "fail", f"Unsafe URL: {exc}")]
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(url, headers=_HEADERS)
        except Exception as exc:
            return [_r("llms.txt", "fail", f"Could not fetch: {exc}")]

    if r.status_code == 404:
        return [_r("llms.txt", "warn", "File not found — consider creating one for AEO/GEO visibility")]
    if r.status_code != 200:
        return [_r("llms.txt", "fail", f"Unexpected status: {r.status_code}")]

    content = r.text
    return [
        _r("llms.txt", "pass", f"Found at {url}"),
        _r("llms.txt H1 heading",
           "pass" if content.strip().startswith("#") else "warn",
           "Starts with H1 heading" if content.strip().startswith("#")
           else "Should start with a # H1 heading"),
        _r("llms.txt links",
           "pass" if ("http" in content or "[" in content) else "warn",
           "Links detected" if ("http" in content or "[" in content)
           else "No links detected — add key page URLs"),
        _r("llms.txt summary blockquote",
           "pass" if ">" in content else "warn",
           "Blockquote summary detected" if ">" in content
           else "Consider adding a summary blockquote"),
    ]


# ---------------------------------------------------------------------------
# XML sitemap
# ---------------------------------------------------------------------------

_SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml", "/wp-sitemap.xml"]


def _looks_like_sitemap(text: str) -> bool:
    head = (text or "")[:500].lower()
    return "<urlset" in head or "<sitemapindex" in head or ("<?xml" in head and "sitemap" in head)


async def check_xml_sitemap(domain: str) -> list[dict]:
    """Check for a reachable XML sitemap via robots.txt and common paths."""
    base = _domain(domain)
    try:
        assert_safe_url(base + "/robots.txt")
    except UnsafeURLError as exc:
        return [_r("XML Sitemap", "fail", f"Unsafe URL: {exc}")]
    results = []

    async with httpx.AsyncClient(follow_redirects=True, timeout=10, headers=_HEADERS) as client:
        # robots.txt Sitemap: line
        sitemap_urls_in_robots: list[str] = []
        try:
            rb = await client.get(base + "/robots.txt")
            if rb.status_code == 200:
                sitemap_urls_in_robots = re.findall(r"(?im)^sitemap:\s*(\S+)", rb.text)
        except Exception:
            pass

        for sm_url in sitemap_urls_in_robots:
            # sm_url came from the site's own robots.txt content — don't
            # follow it into an internal/private address.
            try:
                assert_safe_url(sm_url)
            except UnsafeURLError:
                continue
            try:
                r = await client.get(sm_url)
                if r.status_code == 200 and _looks_like_sitemap(r.text):
                    return [_r("XML Sitemap", "pass", f"Found via robots.txt: {sm_url}")]
                if r.status_code == 200:
                    return [_r("XML Sitemap", "warn",
                               f"robots.txt lists {sm_url} but it doesn't look like sitemap XML")]
            except Exception:
                pass

        # Common paths
        for path in _SITEMAP_PATHS:
            try:
                r = await client.get(base + path)
                if r.status_code == 200 and _looks_like_sitemap(r.text):
                    note = " (not in robots.txt — add Sitemap: line)" if not sitemap_urls_in_robots else ""
                    return [_r("XML Sitemap", "pass" if not note else "warn",
                               f"Found at {base + path}{note}")]
            except Exception:
                pass

    return [_r("XML Sitemap", "fail",
               "No sitemap found at common paths and not referenced in robots.txt")]


# ---------------------------------------------------------------------------
# HTML sitemap in footer
# ---------------------------------------------------------------------------

async def check_html_sitemap_footer(domain: str) -> list[dict]:
    """Check whether the homepage footer contains a sitemap link."""
    p = await fetch_parsed(domain if domain.startswith("http") else "https://" + domain)
    if p.error or not p.soup:
        return [_r("HTML Sitemap", "warn", f"Could not load domain to check footer")]

    footer = p.soup.find("footer")
    scope = footer or p.soup
    for a in scope.find_all("a", href=True):
        if "sitemap" in a.get_text(strip=True).lower() or "sitemap" in a["href"].lower():
            note = "" if footer else " (found on page but not in <footer> — verify visibility)"
            return [_r("HTML Sitemap", "pass",
                       f'Found: "{a.get_text(strip=True)}" → {a["href"]}{note}')]

    return [_r("HTML Sitemap", "fail", "No sitemap link in footer")]


# ---------------------------------------------------------------------------
# URL hygiene
# ---------------------------------------------------------------------------

def check_url_hygiene(url: str) -> list[dict]:
    """Check URL structure — lowercase, hyphens, no staging domain."""
    parsed = urlparse(url)
    path = parsed.path
    results = [
        _r("URL Lowercase", "pass" if path == path.lower() else "fail",
           "All lowercase" if path == path.lower() else "Contains uppercase letters"),
        _r("URL Hyphens", "pass" if "_" not in path else "fail",
           "Uses hyphens correctly" if "_" not in path else "Contains underscores — use hyphens"),
    ]
    staging = ["staging.", "stage.", "dev.", "test.", "localhost"]
    is_staging = any(s in url.lower() for s in staging)
    results.append(_r("URL Domain",
                       "fail" if is_staging else "pass",
                       "Staging domain detected — run QA on live URL" if is_staging
                       else "Appears to be a live domain"))
    return results


# ---------------------------------------------------------------------------
# NAP
# ---------------------------------------------------------------------------

async def check_nap(url: str) -> list[dict]:
    """Check for Name/Address/Phone signals on the page."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    text = p.body_text
    phones = re.findall(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}", text)
    addr_words = ["street", " st.", " ave", " blvd", "suite", " ste.", " rd.", "drive", " dr."]
    has_addr = any(w.lower() in text.lower() for w in addr_words)

    return [
        _r("NAP Phone", "pass" if phones else "warn",
           f"Phone found: {phones[0]}" if phones else "No phone number detected"),
        _r("NAP Address", "pass" if has_addr else "warn",
           "Address-like content detected" if has_addr else "No address content detected — verify NAP"),
        _r("NAP GBP Match", "info", "Manually verify NAP exactly matches Google Business Profile"),
    ]


# ---------------------------------------------------------------------------
# Page speed (PageSpeed Insights API — no key needed for public URLs)
# ---------------------------------------------------------------------------

async def check_page_speed(url: str) -> list[dict]:
    """Run a PageSpeed Insights mobile check."""
    api = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={url}&strategy=mobile"
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.get(api)
            data = r.json()
        except Exception as exc:
            return [_r("PageSpeed", "warn",
                       f"Could not retrieve — check manually at pagespeed.web.dev. Error: {exc}")]

    lh = data.get("lighthouseResult", {})
    score = lh.get("categories", {}).get("performance", {}).get("score")
    audits = lh.get("audits", {})

    results = []
    if score is not None:
        s = int(score * 100)
        results.append(_r("PageSpeed Mobile",
                           "pass" if s >= 90 else "warn" if s >= 50 else "fail",
                           f"{s}/100"))

    lcp = audits.get("largest-contentful-paint", {}).get("displayValue", "N/A")
    cls = audits.get("cumulative-layout-shift", {}).get("displayValue", "N/A")
    results += [
        _r("LCP", "info", lcp),
        _r("CLS", "info", cls),
    ]
    return results


# ---------------------------------------------------------------------------
# Caching headers
# ---------------------------------------------------------------------------

async def check_caching(url: str) -> list[dict]:
    """Detect caching plugin signals from response headers."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    headers = {k.lower(): v for k, v in p.response_headers.items()}

    cache_signals = {
        "WP Rocket":      ["x-wp-rocket", "x-rocket-id"],
        "LiteSpeed":      ["x-litespeed-cache", "x-litespeed-tag", "x-lsadc"],
        "Cloudflare":     ["cf-cache-status", "cf-ray"],
        "WP Super Cache": ["x-wpsc-pre-compressed"],
        "W3 Total Cache": ["x3-powered-by"],
        "Generic Cache":  ["x-cache", "x-cache-hits", "x-cache-status"],
    }

    detected = []
    for plugin, keys in cache_signals.items():
        for k in keys:
            if k in headers:
                detected.append(f"{plugin} ({k}: {headers[k]})")
                break

    results = [
        _r("Caching", "pass" if detected else "warn",
           " | ".join(detected) if detected
           else "No caching headers found — verify WP Rocket or LiteSpeed Cache is active"),
    ]

    encoding = headers.get("content-encoding", "")
    results.append(_r("Compression",
                       "pass" if encoding in ("gzip", "br", "zstd") else "warn",
                       f"Compressed ({encoding})" if encoding
                       else "No compression detected (gzip/brotli) — flag for dev team"))
    return results


# ---------------------------------------------------------------------------
# Noindex check
# ---------------------------------------------------------------------------

async def check_noindex(url: str) -> list[dict]:
    """Check for a noindex meta robots directive on the page."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    meta = (p.soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
            if p.soup else None)
    if meta and "noindex" in meta.get("content", "").lower():
        return [_r("Noindex", "fail",
                   f"Page has noindex directive (content=\"{meta.get('content','')}\")"
                   " — should NOT be set on a live optimised page")]
    return [_r("Noindex / Robots Meta", "pass", "No noindex directive found")]


# ---------------------------------------------------------------------------
# Single-URL redirect checker
# ---------------------------------------------------------------------------

async def check_redirect(url: str) -> list[dict]:
    """Follow a URL and report its HTTP status and redirect chain.

    Useful for verifying that an old URL (from the 'Redirection?' workbook column)
    now redirects cleanly to the new destination, or that a newly created page is live.
    """
    try:
        assert_safe_url(url)
    except UnsafeURLError as exc:
        return [_r("URL Check", "fail", f"Unsafe URL: {exc}")]

    async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers=_HEADERS) as client:
        try:
            r = await client.get(url)
        except Exception as exc:
            return [_r("URL Check", "fail", f"Could not reach: {exc}")]

    hops = len(r.history)
    final = str(r.url)

    if r.status_code == 200 and hops == 0:
        return [_r("URL", "pass", f"Returns 200 directly — {url}")]
    elif r.status_code == 200 and hops == 1:
        return [_r("Redirect", "pass", f"Redirects cleanly (1 hop) → {final}")]
    elif r.status_code == 200 and hops > 1:
        chain = " → ".join(str(h.url) for h in r.history) + f" → {final}"
        return [_r("Redirect", "warn",
                   f"Redirect chain ({hops} hops) — consider collapsing: {chain[:200]}")]
    elif r.status_code in (403, 429):
        return [_r("URL Check", "warn",
                   f"HTTP {r.status_code} — likely bot/WAF block, verify manually in browser")]
    else:
        return [_r("URL Check", "fail", f"HTTP {r.status_code} — {final}")]


# ---------------------------------------------------------------------------
# Batch URL status checker
# ---------------------------------------------------------------------------

async def check_url_batch(urls: list[str]) -> list[dict]:
    """Check a list of URLs and report HTTP status for each.

    Designed for the 'Fixed 404s & Broken URLs' workflow — paste the previously
    broken URLs to confirm they now return 200.  Caps at 30 URLs per call.
    """
    if not urls:
        return [_r("URL Batch", "warn", "No URLs provided")]

    results: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=10, headers=_HEADERS) as client:
        for url in urls[:30]:
            url = url.strip()
            if not url:
                continue
            try:
                assert_safe_url(url)
            except UnsafeURLError as exc:
                results.append(_r(url[:80], "fail", f"Unsafe URL: {exc}"))
                continue
            try:
                r = await client.get(url)
                if r.status_code == 200 and r.history:
                    results.append(_r(url[:80], "warn",
                                       f"Redirected ({len(r.history)} hop) → {str(r.url)[:60]}"))
                elif r.status_code == 200:
                    results.append(_r(url[:80], "pass", "200 OK"))
                elif r.status_code in (403, 429):
                    results.append(_r(url[:80], "warn",
                                       f"HTTP {r.status_code} — bot block, verify manually"))
                else:
                    results.append(_r(url[:80], "fail", f"HTTP {r.status_code}"))
            except Exception as exc:
                results.append(_r(url[:80], "fail", f"Could not reach: {str(exc)[:60]}"))

    if len(urls) > 30:
        results.append(_r("Note", "info",
                           f"Checked first 30 of {len(urls)} URLs — split into batches for full coverage"))
    return results


# ---------------------------------------------------------------------------
# Fetch reliability
# ---------------------------------------------------------------------------

async def check_fetch_reliability(url: str) -> list[dict]:
    """Flag when fetch_parsed's result for this URL looks bot-gated (see
    fetcher._looks_suspicious) — a stripped <head> despite a real,
    substantial body, the signature of a WAF/bot-mitigation layer serving
    a degraded response to a non-browser-looking request.

    fetch_parsed's result is cached and reused by every other check for
    this URL — if it's untrustworthy, Title Tag / Meta Description / H1 /
    Schema / keyword results for this same URL should all be read with
    that in mind, since they're one bad fetch away from being false fails.
    """
    p = await fetch_parsed(url)
    if p.error:
        return []  # a genuine load failure is already reported by whichever check ran first
    if not p.suspicious:
        return []
    word_count = len(p.body_text.split()) if p.body_text else 0
    return [_r(
        "Suspicious Response", "warn",
        f"Page returned HTTP {p.status_code} with {word_count} words of real body content "
        "but zero <title>, meta description, or H1 on every fetch attempt tried — that "
        "combination is not normal for a real page. This looks like the host is gating the "
        "real <head> behind bot-mitigation rather than a one-off block. Title Tag / Meta "
        "Description / H1 / Schema / keyword results for this URL may be unreliable — verify "
        "by viewing the actual page source (Ctrl+U / \"View Page Source\", not just Inspect "
        "Element) in a real browser before trusting them."
    )]
