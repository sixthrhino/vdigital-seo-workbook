"""
Page element checks: images, links, backlinks, Google Maps, YouTube,
FAQ sections, table of contents, and phone links.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse, unquote

import httpx

from .fetcher import fetch_parsed, body_soup_excluding_chrome
from .url_safety import assert_safe_url, UnsafeURLError

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Cache-Control": "no-cache",
}

_GENERIC_ALT = re.compile(
    r"^(image\d*|img\d*|screenshot|photo\d*|picture\d*|banner\d*)$", re.I
)

_CTA_KEYWORDS = [
    "contact", "get a quote", "free estimate", "schedule", "call us",
    "book", "request", "get started", "free inspection", "learn more",
    "call now", "get help", "sign up", "try free", "buy now", "order now",
    "get pricing", "chat", "speak to", "talk to", "reach out",
]


def _r(label: str, status: str, detail: str) -> dict:
    return {"label": label, "status": status, "detail": detail}


def _normalize_url_for_match(u: str) -> str:
    """Normalize a URL for comparison only (not for fetching/display) —
    strips scheme, leading "www.", trailing slash, and query/fragment, so
    equivalent URLs differing only in http vs https, www vs non-www, or a
    trailing slash aren't treated as different links."""
    if not u:
        return ""
    u = u.strip()
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    u = u.split("#")[0].split("?")[0]
    u = u.rstrip("/")
    return u.lower()


# ---------------------------------------------------------------------------
# Images — alt text and format/compression
# ---------------------------------------------------------------------------

async def check_images(url: str) -> list[dict]:
    """Check image alt text coverage and modern format usage."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    imgs = p.soup.find_all("img") if p.soup else []
    if not imgs:
        return [_r("Images", "info", "No images found on page")]

    missing_alt = [img for img in imgs if not img.get("alt", "").strip()]
    generic_alt = [img.get("alt", "") for img in imgs
                   if img.get("alt") and _GENERIC_ALT.match(img.get("alt", "").strip())]

    results = [
        _r("Alt Text Coverage",
           "pass" if not missing_alt else "fail",
           f"All {len(imgs)} images have alt text" if not missing_alt
           else f"{len(missing_alt)}/{len(imgs)} missing alt text"),
    ]
    if generic_alt:
        results.append(_r("Generic Alt Text", "warn",
                           f"{len(generic_alt)} image(s) with generic alt: {', '.join(generic_alt[:3])}"))

    # Image formats
    modern = (".webp", ".avif")
    legacy = (".jpg", ".jpeg", ".png", ".gif", ".bmp")
    modern_count = sum(1 for img in imgs
                       if any(img.get("src", "").lower().split("?")[0].endswith(f) for f in modern))
    legacy_count = sum(1 for img in imgs
                       if any(img.get("src", "").lower().split("?")[0].endswith(f) for f in legacy))

    if legacy_count and not modern_count:
        results.append(_r("Image Formats", "fail",
                           f"All {legacy_count} image(s) are legacy format (JPG/PNG) — no WebP/AVIF"))
    elif legacy_count and modern_count:
        results.append(_r("Image Formats", "warn",
                           f"{modern_count} modern, {legacy_count} legacy — consider converting remaining"))
    elif modern_count:
        results.append(_r("Image Formats", "pass",
                           f"All {modern_count} image(s) use modern formats (WebP/AVIF)"))

    # Imagify compression plugin (WordPress)
    resp_headers = {k.lower(): v for k, v in p.response_headers.items()}
    imagify_hit = next(
        (f"{k}: {v}" for k, v in resp_headers.items() if "imagify" in k),
        None,
    )
    if imagify_hit:
        results.append(_r("Imagify Plugin", "pass", f"Active — {imagify_hit}"))
    else:
        results.append(_r("Imagify Plugin", "info",
                           "No Imagify response headers detected — verify plugin is active in WP dashboard"))

    return results


# ---------------------------------------------------------------------------
# Links — internal, external, anchor text, phone links, CTAs
# ---------------------------------------------------------------------------

async def check_links(url: str) -> list[dict]:
    """Check internal links, external links, anchor text quality, phone links, and CTAs."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    soup = p.soup
    if not soup:
        return [_r("Links", "warn", "Could not parse page")]

    parsed_base = urlparse(url)
    domain = parsed_base.netloc
    results: list[dict] = []

    # Nav/header/footer boilerplate (menu items, footer social/association
    # links, etc.) is excluded so it doesn't get scored as page content —
    # same rationale as the keyword/geo chrome exclusion.
    chrome_soup = body_soup_excluding_chrome(p) or soup
    all_links = chrome_soup.find_all("a", href=True)
    bad_anchors = {"click here", "learn more", "read more", "here", "this link", "link", "more"}

    internal, external, ctas = [], [], []
    flagged_anchors: list[str] = []

    for a in all_links:
        text_nodes = [t.strip() for t in a.strings if t.strip()]
        link_text = max(text_nodes, key=len) if text_nodes else a.get_text(strip=True)
        href = a.get("href", "")
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(url, href)
        link_text_lc = link_text.lower()

        if urlparse(full).netloc == domain:
            if any(k in link_text_lc for k in _CTA_KEYWORDS):
                ctas.append(f'"{link_text[:40]}" → {full[:60]}')
            else:
                internal.append(f'"{link_text[:40]}" → {full[:60]}')
        else:
            external.append((full, a.get("target", ""), link_text))

        if link_text_lc in bad_anchors:
            flagged_anchors.append(f'"{link_text}"')

    # Internal links
    n = len(internal)
    results.append(_r("Internal Links",
                       "pass" if n >= 3 else "warn" if n > 0 else "fail",
                       (f"{n} found: " + "; ".join(internal[:4]) + ("..." if n > 4 else ""))
                       if n else "No internal links found"))

    # External links — target=_blank
    ext_no_blank = [href for href, target, _ in external if target != "_blank"]
    if external:
        results.append(_r("External Links target=_blank",
                           "pass" if not ext_no_blank else "fail",
                           f"All {len(external)} external links open in new tab" if not ext_no_blank
                           else f"{len(ext_no_blank)} external link(s) missing target='_blank'"))

    # Anchor text quality
    if flagged_anchors:
        results.append(_r("Anchor Text Quality", "fail",
                           f"Generic anchor text: {', '.join(flagged_anchors[:5])}"))
    else:
        results.append(_r("Anchor Text Quality", "pass", "No generic anchor text detected"))

    # CTAs
    results.append(_r("CTA Links",
                       "pass" if ctas else "warn",
                       ("; ".join(ctas[:5]) + ("..." if len(ctas) > 5 else "")) if ctas
                       else "No CTA links detected — verify page has clear calls to action"))

    # Phone links — visible number must match tel: href
    for a in chrome_soup.find_all("a", href=True):
        text_nodes = [t.strip() for t in a.strings if t.strip()]
        lt = max(text_nodes, key=len) if text_nodes else a.get_text(strip=True)
        digits_text = re.sub(r"\D", "", lt)
        if len(digits_text) in (10, 11):
            norm_text = digits_text[-10:]
            href = a.get("href", "")
            if href.startswith("tel:"):
                tel_digits = re.sub(r"\D", "", unquote(href[4:]).split(";")[0])[-10:]
                status = "pass" if norm_text == tel_digits else "fail"
                fmt = lambda d: f"{d[:3]}-{d[3:6]}-{d[6:]}"
                detail = (f"{fmt(norm_text)} links correctly" if status == "pass"
                          else f"{fmt(norm_text)} shown but tel: dials {fmt(tel_digits)} — mismatch!")
                results.append(_r("Phone Link", status, detail))
            else:
                results.append(_r("Phone Link", "warn",
                                   f"{norm_text[:3]}-{norm_text[3:6]}-{norm_text[6:]} "
                                   f"is not a tel: link"))

    return results


# ---------------------------------------------------------------------------
# Expected links — specific target URLs this page should link to
# ---------------------------------------------------------------------------

async def check_expected_links(url: str, expected_links: list[str]) -> list[dict]:
    """Verify specific target URLs are actually linked from this page.

    For claims like an opt_note's own "Internal link here to
    https://..." text — distinct from check_links (generic internal/
    external link-quality scan, no awareness of any specific expected
    destination) and check_backlink (checks a DIFFERENT page links back to
    this one, the opposite direction).
    """
    if not expected_links:
        return []

    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    soup = p.soup
    if not soup:
        return [_r("Expected Links", "warn", "Could not parse page")]

    normalized_hrefs = {
        _normalize_url_for_match(urljoin(url, a["href"]))
        for a in soup.find_all("a", href=True) if a.get("href")
    }

    results = []
    for target in expected_links:
        target = target.strip()
        if not target:
            continue
        norm_target = _normalize_url_for_match(target)
        found = any(h == norm_target or h.startswith(norm_target + "/") for h in normalized_hrefs)
        results.append(_r(f'Expected Link: "{target}"', "pass" if found else "fail",
                           f'Link to {target} {"found" if found else "not found"} on page'))
    return results


# ---------------------------------------------------------------------------
# Backlink verification
# ---------------------------------------------------------------------------

async def check_backlink(referring_url: str, expected_target_url: str) -> list[dict]:
    """Verify a referring page links to the expected target URL."""
    results: list[dict] = []

    try:
        assert_safe_url(referring_url.strip())
        assert_safe_url(expected_target_url.strip())
    except UnsafeURLError as exc:
        return [_r("Backlink", "fail", f"Unsafe URL: {exc}")]

    # Check target is live
    async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers=_HEADERS) as client:
        try:
            tr = await client.get(expected_target_url.strip())
            target_live = tr.status_code == 200
            target_note = ("" if target_live
                           else f" Note: target returned HTTP {tr.status_code} — may be bot-blocked, verify manually.")
        except Exception as exc:
            target_live, target_note = False, f" Could not confirm target is live: {exc}"

        try:
            rr = await client.get(referring_url.strip())
        except Exception as exc:
            err = str(exc)
            if "NameResolutionError" in err or "Failed to resolve" in err:
                return [_r("Backlink", "warn",
                            "Referring domain no longer resolves (DNS failed) — dead backlink source.")]
            return [_r("Backlink", "warn", f"Could not reach referring page: {err}")]

    if rr.status_code in (403, 429):
        return [_r("Backlink", "warn", f"Referring page returned HTTP {rr.status_code} — likely bot block.")]
    if rr.status_code != 200:
        return [_r("Backlink", "fail", f"Referring page returned HTTP {rr.status_code}.")]

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(rr.text, "html.parser")
    target_domain = urlparse(expected_target_url.strip()).netloc.lower()
    expected_norm = expected_target_url.strip().rstrip("/").lower()

    found_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(str(rr.url), href).rstrip("/")
        if urlparse(absolute).netloc.lower() == target_domain and absolute not in found_links:
            found_links.append(absolute)

    if expected_norm in [l.lower() for l in found_links]:
        return [_r("Backlink", "pass" if target_live else "warn",
                   f"Found link to {expected_target_url}.{target_note}")]
    elif found_links:
        shown = "; ".join(found_links[:5])
        return [_r("Backlink", "warn",
                   f"Expected link not found. Found {len(found_links)} other link(s) to {target_domain}: {shown}{target_note}")]
    return [_r("Backlink", "fail",
               f"No links to {target_domain} found on referring page.{target_note}")]


# ---------------------------------------------------------------------------
# Internal redirects
# ---------------------------------------------------------------------------

async def check_internal_redirects(url: str) -> list[dict]:
    """Sample up to 15 internal links and flag any that are redirects (3xx)."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    soup = p.soup
    domain = urlparse(url).netloc
    links = []
    for a in (soup.find_all("a", href=True) if soup else []):
        href = a["href"]
        if href.startswith("/") or urlparse(urljoin(url, href)).netloc == domain:
            full = urljoin(url, href)
            if full not in links:
                links.append(full)

    sample = links[:15]
    redirects = []
    async with httpx.AsyncClient(timeout=8, headers=_HEADERS) as client:
        for link in sample:
            # link came from the page's own <a href> content — don't follow
            # it into an internal/private address.
            try:
                assert_safe_url(link)
            except UnsafeURLError:
                continue
            try:
                r = await client.head(link, follow_redirects=False)
                if r.status_code in (301, 302, 307, 308):
                    redirects.append(link)
            except Exception:
                pass

    if redirects:
        return [_r("Internal Redirects", "fail",
                   f"{len(redirects)} internal link(s) are redirects: " + "; ".join(r[:60] for r in redirects[:3]))]
    return [_r("Internal Redirects", "pass",
               f"{len(sample)} internal links sampled — no redirects found")]


# ---------------------------------------------------------------------------
# Google Maps embed
# ---------------------------------------------------------------------------

async def check_google_maps(url: str) -> list[dict]:
    """Check for a Google Maps iframe and verify it uses a GBP link."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    iframes = [i for i in (p.soup.find_all("iframe") if p.soup else [])
               if "google.com/maps" in i.get("src", "")]
    if not iframes:
        return [_r("Google Maps", "info", "No Google Maps embed found")]

    results = [_r("Google Maps", "pass", f"{len(iframes)} Maps iframe(s) found")]
    for i in iframes:
        src = i.get("src", "")
        if any(x in src for x in ["/place/", "cid=", "ftid="]):
            results.append(_r("Maps Embed Type", "pass", "Uses a GBP/location-specific link"))
        else:
            results.append(_r("Maps Embed Type", "warn",
                               "Verify this uses the GBP link, not a generic address"))
    return results


# ---------------------------------------------------------------------------
# YouTube embed
# ---------------------------------------------------------------------------

async def check_youtube(url: str) -> list[dict]:
    """Check for a YouTube embed and VideoObject schema."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    iframes = [i for i in (p.soup.find_all("iframe") if p.soup else [])
               if "youtube.com/embed" in i.get("src", "")
               or "youtube-nocookie.com" in i.get("src", "")]
    if not iframes:
        return [_r("YouTube", "info", "No YouTube embed found")]

    import json
    has_video_schema = False
    for script in (p.soup.find_all("script", type="application/ld+json") if p.soup else []):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            if any(i.get("@type") == "VideoObject" for i in items if isinstance(i, dict)):
                has_video_schema = True
        except Exception:
            pass

    return [
        _r("YouTube Embed", "pass", f"{len(iframes)} iframe(s) found"),
        _r("VideoObject Schema",
           "pass" if has_video_schema else "fail",
           "VideoObject schema found" if has_video_schema
           else "YouTube embed present but no VideoObject schema"),
    ]


# ---------------------------------------------------------------------------
# FAQ section + FAQPage schema
# ---------------------------------------------------------------------------

async def check_faq(url: str) -> list[dict]:
    """Check for a FAQ section and FAQPage schema."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    import json
    soup = p.soup
    if not soup:
        return [_r("FAQ", "warn", "Could not parse page")]

    faq_headings = [h for h in soup.find_all(["h2", "h3", "h4"])
                    if "?" in h.get_text() or "faq" in h.get_text().lower()
                    or "frequently" in h.get_text().lower()]
    if not faq_headings:
        return [_r("FAQ Section", "info", "No FAQ section detected")]

    results = [_r("FAQ Headings", "pass", f"{len(faq_headings)} FAQ-style heading(s) found")]

    has_faq_schema = False
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            if any(i.get("@type") == "FAQPage" for i in items if isinstance(i, dict)):
                has_faq_schema = True
        except Exception:
            pass

    results.append(_r("FAQPage Schema",
                       "pass" if has_faq_schema else "fail",
                       "FAQPage JSON-LD present" if has_faq_schema
                       else "FAQ content found but no FAQPage schema"))
    return results


# ---------------------------------------------------------------------------
# Table of contents
# ---------------------------------------------------------------------------

async def check_toc(url: str) -> list[dict]:
    """Check for a table of contents (anchor links) and validate targets exist."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    soup = p.soup
    if not soup:
        return [_r("TOC", "warn", "Could not parse page")]

    anchors = soup.find_all("a", href=lambda h: h and h.startswith("#"))
    if len(anchors) < 3:
        return [_r("TOC", "info", "No table of contents detected (fewer than 3 anchor links)")]

    results = [_r("TOC Anchor Links", "pass", f"{len(anchors)} anchor links found")]
    broken = [a["href"] for a in anchors[:10] if not soup.find(id=a["href"][1:])]
    results.append(_r("TOC Anchor Targets",
                       "fail" if broken else "pass",
                       f"Broken anchors: {', '.join(broken[:3])}" if broken
                       else "All checked anchor targets exist on page"))
    return results
