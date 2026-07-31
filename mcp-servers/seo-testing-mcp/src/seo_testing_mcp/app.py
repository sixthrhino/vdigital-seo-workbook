import json
import os
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# mcp-servers/seo-testing-mcp/src/seo_testing_mcp/app.py -> this component's
# own root (mcp-servers/seo-testing-mcp/), where data/ and a local .env (if
# any, dev-only) live — not the monorepo root.
_COMPONENT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_COMPONENT_ROOT / ".env")

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .tools.seo_core import (
    check_title, check_meta_description, check_h1,
    check_heading_hierarchy, check_keywords, check_canonical,
    check_schema, check_og_twitter, get_title_meta,
)
from .tools.content import (
    check_word_count, check_publish_date,
    check_broken_links, check_sentences, get_page_text,
)
from .tools.brand_guide import check_brand_guide, parse_brand_guide, brand_guide_manual_notes
from .tools.gemini_checks import (
    check_grammar, add_site_dictionary_term, generate_recommendations,
)
from .tools.geo import check_geo_accuracy, add_city
from .tools.technical import (
    check_robots_txt, check_llms_txt, check_xml_sitemap,
    check_html_sitemap_footer, check_url_hygiene, check_nap,
    check_page_speed, check_caching,
    check_noindex, check_redirect, check_url_batch, check_fetch_reliability,
)
from .tools.page_elements import (
    check_images, check_links, check_backlink, check_expected_links,
    check_internal_redirects, check_google_maps, check_youtube,
    check_faq, check_toc,
)
from .tools.sheets import (
    read_workbook_rows, list_workbook_months, get_month_rows, get_spreadsheet_title,
    read_brand_guide_tab, read_client_details,
)
from .tools.render_report import render as _render_html
from .report_tokens import create_report_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastMCP auto-enables the mcp SDK's DNS-rebinding Host-header check whenever
# its `host` setting looks like a loopback address (the default). That check
# only knows "127.0.0.1"/"localhost" as valid hosts, so on Cloud Run — where
# the real Host header is the service's *.run.app hostname — every request
# gets rejected with 421 "Invalid Host header" before it reaches our code.
# This service is already gated by Cloud Run IAM (--no-allow-unauthenticated
# + the agent's ID token), which is what DNS-rebinding protection is a stand-in
# for when there's no such auth layer, so it's redundant here.
mcp = FastMCP(
    "vdigital-testing-tools",
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# RULES_LOCAL_PATH overrides this for a non-editable `pip install` (e.g.
# inside the Docker image), where __file__ resolves into site-packages
# rather than this component's own source tree, so _COMPONENT_ROOT no
# longer points at a real data/ directory — see the Dockerfile, which sets
# it to the data/ copied alongside the installed package.
_LOCAL_RULES_FILE = Path(os.environ.get("RULES_LOCAL_PATH", _COMPONENT_ROOT / "data" / "rules.json"))


def _load_rules_data() -> dict:
    gcs_uri = os.environ.get("RULES_GCS_URI")
    if gcs_uri:
        from google.cloud import storage
        bucket_name, blob_path = gcs_uri.removeprefix("gs://").split("/", 1)
        client = storage.Client()
        return json.loads(client.bucket(bucket_name).blob(blob_path).download_as_text())

    with open(_LOCAL_RULES_FILE) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Rules catalog
# ---------------------------------------------------------------------------

@mcp.tool()
def list_rule_categories() -> dict:
    """List available QA categories and their optimization names.

    Returns a dict keyed by category name with description and the list of
    optimization names within that category. Use get_rules() to get auto_checks
    and guided_questions for individual optimizations.
    """
    data = _load_rules_data()
    return {
        name: {
            "description": cat["description"],
            "optimizations": list(cat["optimizations"].keys()),
        }
        for name, cat in data["categories"].items()
    }


@mcp.tool()
def get_rules(category: Optional[str] = None) -> dict:
    """Retrieve optimization rules including auto_checks and guided_questions.

    Each optimization entry has:
      auto_checks: list of MCP tool names to call for automated checks
      guided_questions: list of manual verification questions for the QA report

    Args:
        category: Category name (e.g. "Core SEO"). Returns all categories if omitted.
    """
    data = _load_rules_data()
    cats = data["categories"]
    if category:
        if category not in cats:
            return {"error": f"Category '{category}' not found. Available: {list(cats.keys())}"}
        return {category: cats[category]["optimizations"]}
    return {name: cat["optimizations"] for name, cat in cats.items()}


# ---------------------------------------------------------------------------
# Opt-note dispatch
# ---------------------------------------------------------------------------

_CORE_OPT = ["Title Tag", "Meta Description", "H1 Tag", "H2 / H3 / H4 Tags"]
_DEEP_OPT = _CORE_OPT + ["Schema Markup", "Internal Linking & Anchor Text", "Image Alt Text & Optimization"]

# Keyword fragments → optimization names (for terms that don't appear verbatim in opt names)
_KEYWORD_ALIASES: dict[str, list[str]] = {
    "schema":         ["Schema Markup"],
    "sitemap":        ["XML Sitemaps"],
    "h1":             ["H1 Tag"],
    "h2":             ["H2 / H3 / H4 Tags"],
    "h3":             ["H2 / H3 / H4 Tags"],
    "alt text":       ["Image Alt Text & Optimization"],
    "alt tag":        ["Image Alt Text & Optimization"],
    "missing alt":    ["Image Alt Text & Optimization"],
    "phone":          ["Internal Linking & Anchor Text"],
    "cta":            ["Internal Linking & Anchor Text"],
    "call to action": ["Internal Linking & Anchor Text"],
    "action link":    ["Internal Linking & Anchor Text"],
    "grammar":        ["Grammar, Syntax & Polish"],
    "proofread":      ["Grammar, Syntax & Polish"],
    "llm":            _DEEP_OPT,
    "multi-platform": _DEEP_OPT,
    "deep touch":     _DEEP_OPT,
}


def _all_optimizations() -> dict:
    data = _load_rules_data()
    result = {}
    for cat in data["categories"].values():
        result.update(cat["optimizations"])
    return result


@mcp.tool()
def resolve_checks_for_opt_note(opt_note: str) -> dict:
    """Return the auto_checks and guided_questions for an opt note string.

    Matches the opt_note text against the VDS optimization catalog to find which
    optimizations were performed, then returns the union of their auto_checks and
    guided_questions. The agent should call each tool listed in auto_checks and
    include guided_questions as the manual_checklist in the report.

    Args:
        opt_note: The 'What Is Planned / Has Been Done?' cell value from the workbook.

    Returns:
        {
          "matched_optimizations": [...],
          "auto_checks": [...],       # deduplicated list of MCP tool names to call
          "guided_questions": [...]   # deduplicated list of manual checklist items
        }
    """
    text = opt_note.lower()
    all_opts = _all_optimizations()

    names: list[str] = []

    # Expand shorthands first
    if "deep opt" in text:
        names += _DEEP_OPT
    elif "core opt" in text:
        names += _CORE_OPT

    # Keyword aliases (terms that don't appear verbatim in optimization names)
    for keyword, mapped in _KEYWORD_ALIASES.items():
        if keyword in text:
            for n in mapped:
                if n not in names:
                    names.append(n)

    # Match any optimization name that appears as a substring of the opt note
    for name in all_opts:
        if name.lower() in text and name not in names:
            names.append(name)

    # Fallback: unrecognized opt note → run core title/meta/H1
    if not names:
        names = _CORE_OPT[:3]

    seen_checks: set[str] = set()
    seen_questions: set[str] = set()
    auto_checks: list[str] = []
    guided_questions: list[str] = []

    for name in names:
        opt = all_opts.get(name, {})
        for c in opt.get("auto_checks", []):
            if c not in seen_checks:
                seen_checks.add(c)
                auto_checks.append(c)
        for q in opt.get("guided_questions", []):
            if q not in seen_questions:
                seen_questions.add(q)
                guided_questions.append(q)

    return {
        "matched_optimizations": names,
        "auto_checks": auto_checks,
        "guided_questions": guided_questions,
    }


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

def _clean_spreadsheet_id(spreadsheet_id: str) -> str:
    """Strip a full Sheets URL down to the bare document ID, or pass a
    bare ID (possibly with stray whitespace/slashes) through unchanged —
    lets callers paste a full URL, a bare ID, or an ID with a trailing
    slash indifferently."""
    import re as _re
    m = _re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", spreadsheet_id)
    return m.group(1) if m else spreadsheet_id.strip().strip("/")


@mcp.tool()
def workbook_list_months(spreadsheet_id: str) -> list:
    """List the distinct month/year values in the On-Page sheet.

    Args:
        spreadsheet_id: The Sheets document ID (bare ID or full URL).

    Returns:
        List of month/year strings in the order they appear, e.g.
        ["August 2025", "October 2025", "November 2025"].
    """
    return list_workbook_months(_clean_spreadsheet_id(spreadsheet_id))


@mcp.tool()
def workbook_get_month_rows(spreadsheet_id: str, month_year: str) -> list:
    """Return cleaned, agent-ready rows from the On-Page sheet for a given month/year.

    Filters by month/year (fuzzy — handles typos and date-formatted cells),
    skips separator and non-URL rows, and returns structured dicts with all
    fields needed to run QA checks.

    Args:
        spreadsheet_id: The Sheets document ID (bare ID or full URL).
        month_year: Month and year to filter to, e.g. "June 2026".

    Returns:
        List of dicts with keys: url, keyword, geo_city, geo_state, opt_note,
        optimization_focus, old_title, new_title, old_meta, new_meta,
        old_h1, new_h1, visual_qa, redirection.
    """
    return get_month_rows(_clean_spreadsheet_id(spreadsheet_id), month_year)


@mcp.tool()
def workbook_get_title(spreadsheet_id: str) -> str:
    """Return the workbook's own title from Sheets metadata.

    Args:
        spreadsheet_id: The Sheets document ID (bare ID or full URL).
    """
    return get_spreadsheet_title(_clean_spreadsheet_id(spreadsheet_id))


@mcp.tool()
def workbook_get_brand_guide(spreadsheet_id: str) -> dict:
    """Return the workbook's Brand Guide tab, parsed into structured JSON,
    ready to pass straight to content_check_brand_guide's brand_guide
    argument.

    Returns an empty-structure dict (all fields blank) if the workbook has
    no Brand Guide tab.

    Args:
        spreadsheet_id: The Sheets document ID (bare ID or full URL).
    """
    return parse_brand_guide(read_brand_guide_tab(_clean_spreadsheet_id(spreadsheet_id)))


@mcp.tool()
def workbook_get_client_details(spreadsheet_id: str) -> dict:
    """Return the workbook's Client Details tab: business name and main
    website URL — a deterministic source for the report's client label,
    instead of guessing from the workbook/attachment title.

    Returns {"client": "", "website": ""} if the workbook has no Client
    Details tab or the expected labels aren't found.

    Args:
        spreadsheet_id: The Sheets document ID (bare ID or full URL).
    """
    return read_client_details(_clean_spreadsheet_id(spreadsheet_id))


@mcp.tool()
def get_sheet_rows(
    spreadsheet_id: str,
    sheet: str = "On-Page",
    header_row: int = 1,
) -> list:
    """Read rows from a Google Sheet as a list of dicts keyed by column headers.

    Args:
        spreadsheet_id: The Sheets document ID (from its URL).
        sheet: Sheet name or 0-based index (default "On-Page").
        header_row: Row number (1-based) that contains column headers.
    """
    index = int(sheet) if sheet.isdigit() else sheet
    return read_workbook_rows(_clean_spreadsheet_id(spreadsheet_id), sheet=index, header_row=header_row)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "")).strip("-").lower()


@mcp.tool()
def generate_report(results: dict, output_path: str = "/tmp/qa_result.html") -> str:
    """Render QA results to a styled HTML report.

    When GCS_REPORT_BUCKET is set the report is uploaded to Cloud Storage
    under a unique object name (timestamp + client/month + a short random
    suffix, NOT output_path's filename — every report used to land at the
    same object and silently overwrite the last one) and a short
    /reports/{token} share link (seo-testing-agent) is returned. Otherwise
    the HTML is written to output_path and that path is returned.

    The link is short on purpose — NOT a raw ~700-character signed GCS URL.
    Confirmed live: a signed URL relayed verbatim through Gemini's chat
    reply came back missing 15 hex characters, breaking the signature
    (SignatureDoesNotMatch on click) even though signing itself succeeded.
    A short opaque token the model can reproduce reliably sidesteps that —
    the token resolves to a freshly-signed URL server-side, at redirect
    time, in seo-testing-agent's /reports/{token} route. Mirrors
    seo-workbook's identical fix for the same problem (see
    shared/seo_workbook_common/storage/report_tokens.py and
    seo-workbook-agent/routers/reports_router.py) — duplicated rather than
    imported, since this package deliberately has no shared/ dependency.

    Args:
        results: Dict with keys month, client, urls (list of per-URL result
                 objects), and optionally brand_guide_notes (list of
                 guide-level reminder strings — voice/tone, writing rules,
                 imaging — rendered once at the top of the report, since
                 they apply to every page rather than one specific url).
                 Each url entry has: url, verdict, opt_note, checks, key_issues, recommended_fixes.
        output_path: Local fallback path (default /tmp/qa_result.html).

    Returns:
        A short report share link (production) or the local file path
        (development).
    """
    html = _render_html(results)

    bucket_name = os.environ.get("GCS_REPORT_BUCKET")
    if bucket_name:
        mongo_uri = os.environ.get("MONGO_URI")
        agent_public_url = os.environ.get("AGENT_PUBLIC_URL")
        if not mongo_uri:
            raise ValueError("MONGO_URI is not configured — required to mint report share links")
        if not agent_public_url:
            raise ValueError("AGENT_PUBLIC_URL is not configured — required to mint report share links")

        from google.cloud import storage as gcs
        client = gcs.Client()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        client_slug = _slug(results.get("client")) or "report"
        month_slug = _slug(results.get("month"))
        blob_name = f"qa-reports/{client_slug}-{month_slug}-{stamp}-{uuid.uuid4().hex[:8]}.html"
        blob = client.bucket(bucket_name).blob(blob_name)
        blob.upload_from_string(html, content_type="text/html")

        from pymongo import MongoClient
        mongo_database = os.environ.get("MONGO_DATABASE", "seo_testing")
        mongo_collection_name = os.environ.get("MONGO_REPORT_TOKENS_COLLECTION", "report_tokens")
        tokens_collection = MongoClient(mongo_uri)[mongo_database][mongo_collection_name]
        token = create_report_token(tokens_collection, bucket_name, blob_name)
        return f"{agent_public_url.rstrip('/')}/reports/{token}"

    with open(output_path, "w") as f:
        f.write(html)
    return output_path


# ---------------------------------------------------------------------------
# SEO checks
# ---------------------------------------------------------------------------

@mcp.tool()
async def seo_check_title(url: str, expected: Optional[str] = None) -> list:
    """Check the <title> tag — existence, length, optional comparison to expected value."""
    return await check_title(url, expected)


@mcp.tool()
async def seo_check_meta_description(url: str, expected: Optional[str] = None) -> list:
    """Check meta description — length, CTA presence, optional comparison."""
    return await check_meta_description(url, expected)


@mcp.tool()
async def seo_check_h1(url: str, expected: Optional[str] = None) -> list:
    """Check H1 tag — count, length, optional comparison."""
    return await check_h1(url, expected)


@mcp.tool()
async def seo_check_headings(
    url: str,
    expected_headings: Optional[str] = None,
    old_headings: Optional[str] = None,
) -> list:
    """Check heading hierarchy (H1–H4).

    expected_headings / old_headings: multi-line format:
      <H1> Title here
      <H2> Subtitle here
    """
    return await check_heading_hierarchy(url, expected_headings, old_headings)


@mcp.tool()
async def seo_check_keywords(
    url: str,
    primary_keyword: str,
    secondary_keywords: Optional[str] = None,
    doc_text: Optional[str] = None,
) -> list:
    """Check keyword presence in title, H1, meta, and body.

    Args:
        primary_keyword: Focus keyword for the page.
        secondary_keywords: Comma/newline-separated secondary keywords.
        doc_text: Optional doc text to verify keyword appears in source doc as well.
    """
    return await check_keywords(url, primary_keyword, secondary_keywords, doc_text)


@mcp.tool()
async def seo_check_canonical(url: str) -> list:
    """Check canonical tag — existence, self-canonical, staging leak."""
    return await check_canonical(url)


@mcp.tool()
async def seo_check_schema(url: str) -> list:
    """Detect JSON-LD schema types and flag missing schemas."""
    return await check_schema(url)


@mcp.tool()
async def seo_check_og_twitter(url: str) -> list:
    """Check Open Graph and Twitter/X meta tags."""
    return await check_og_twitter(url)


@mcp.tool()
async def seo_get_title_meta(url: str) -> dict:
    """Fetch a page's raw <title> text and meta description (not a pass/fail
    check) — for callers doing cross-page comparisons like duplicate-title
    detection across a batch."""
    return await get_title_meta(url)


# ---------------------------------------------------------------------------
# Content checks
# ---------------------------------------------------------------------------

@mcp.tool()
def content_parse_brand_guide(brand_guide_text: str) -> dict:
    """Parse raw brand guide text (copied from the workbook, or pasted in
    chat) into structured JSON, ready for content_check_brand_guide.

    Args:
        brand_guide_text: Raw brand guide text.

    Returns:
        Dict with keys: branding, cta_urls, cta_phones, voice_tone,
        negative_words, writing_rules, imaging, geo, excluded_cities, raw.
    """
    return parse_brand_guide(brand_guide_text)


@mcp.tool()
async def content_check_brand_guide(url: str, brand_guide: dict) -> list:
    """Check live page against a client brand guide.

    Args:
        url: Page to check.
        brand_guide: Structured guide dict from content_parse_brand_guide
            or workbook_get_brand_guide.
    """
    return await check_brand_guide(url, brand_guide)


@mcp.tool()
async def content_generate_recommendations(url: str, checks: list) -> dict:
    """Get a short key_issues/recommended_fixes summary for one page from
    its own failing/warning checks — page-specific and small by design (see
    generate_recommendations). Returns empty strings without an LLM call
    when there's nothing to flag.

    Args:
        url: The page these checks are for.
        checks: This page's own checks list (as returned by the check_* tools) —
            not the whole batch's results.
    """
    return await generate_recommendations(url, checks)


@mcp.tool()
def content_get_brand_guide_notes(brand_guide: dict) -> list:
    """Guide-level manual-review reminders (voice/tone, writing rules,
    imaging) — call once per batch, not once per URL, since they don't
    vary by page.

    Args:
        brand_guide: Structured guide dict from content_parse_brand_guide
            or workbook_get_brand_guide.
    """
    return brand_guide_manual_notes(brand_guide)


@mcp.tool()
async def content_check_word_count(url: str) -> list:
    """Count words in the main content area."""
    return await check_word_count(url)


@mcp.tool()
async def content_check_publish_date(url: str, expected_date: Optional[str] = None) -> list:
    """Find publish date on the page and optionally compare to expected value.

    Args:
        expected_date: Expected date string from workbook (e.g. "06/15/2025").
    """
    return await check_publish_date(url, expected_date)


@mcp.tool()
async def content_check_broken_links(url: str) -> list:
    """Check content-area links for broken URLs (4xx/5xx)."""
    return await check_broken_links(url)


@mcp.tool()
async def content_get_text(url: str, max_chars: int = 4000) -> dict:
    """Fetch a page's main-content text for analysis (not a pass/fail check).

    Args:
        max_chars: Truncate returned text to this many characters.
    """
    return await get_page_text(url, max_chars)


@mcp.tool()
async def content_check_grammar(url: str) -> list:
    """Gemini-based spelling/grammar/repetitive-sentence-starter review of a page's content."""
    return await check_grammar(url)


@mcp.tool()
async def dictionary_add_term(domain: str, term: str) -> dict:
    """Add a brand name, event name, or other proper noun to a domain's
    grammar-check dictionary, so content_check_grammar stops flagging it
    as a spelling error on that site. Use when a grammar check result
    flags something that's actually correct for the client (e.g. a brand
    or event name).

    Args:
        domain: The site's domain, e.g. "example.com" (www. is stripped automatically).
        term: The word or phrase to whitelist, e.g. "Tacolandia".
    """
    return await add_site_dictionary_term(domain, term)


@mcp.tool()
async def content_check_sentences(
    url: str,
    old_sentences: list,
    new_sentences: list,
) -> list:
    """Verify old sentences are removed and new sentences are live.

    Args:
        old_sentences: List of old sentence fragments to confirm removed.
        new_sentences: List of new sentence fragments to confirm present.
    """
    return await check_sentences(url, old_sentences, new_sentences)


# ---------------------------------------------------------------------------
# Geo checks
# ---------------------------------------------------------------------------

@mcp.tool()
async def geo_check_accuracy(
    url: str,
    geo_city: str,
    geo_state: str = "",
    threshold_miles: int = 100,
    allowed_counties: Optional[list] = None,
    excluded_cities: Optional[list] = None,
    allowlist_cities: Optional[list] = None,
) -> list:
    """Scan page for city mentions outside the client's geo target area.

    Args:
        geo_city: Target city name, full state name, or 2-letter state abbr for state-wide clients.
        geo_state: 2-letter state code (disambiguates same-name cities).
        threshold_miles: Flag cities farther than this distance (default 100).
        allowed_counties: County names exempt from flagging (e.g. ["Butler County, OH"]).
        excluded_cities: Cities client CANNOT service — always flagged regardless of distance.
        allowlist_cities: Additional in-area cities to whitelist (format "City, ST").
    """
    return await check_geo_accuracy(
        url, geo_city, geo_state, threshold_miles,
        allowed_counties, excluded_cities, allowlist_cities,
    )


@mcp.tool()
async def geo_add_city(
    city: str,
    state: str,
    lat: float,
    lng: float,
    county: str = "",
    population: int = 0,
) -> dict:
    """Add or update a city in the cities database geo_check_accuracy uses.
    Use when a geo check flags a real city as out-of-range/out-of-state
    because it's missing from the database.

    Args:
        city: City name, e.g. "Springfield".
        state: 2-letter state code, e.g. "OH".
        lat: Latitude in decimal degrees.
        lng: Longitude in decimal degrees.
        county: County name (optional — used for county-exemption matching).
        population: Population (optional — used for mention-plausibility filtering).
    """
    return await add_city(city, state, lat, lng, county, population)


# ---------------------------------------------------------------------------
# Technical checks
# ---------------------------------------------------------------------------

@mcp.tool()
async def tech_check_robots_txt(domain: str) -> list:
    """Check robots.txt — existence, sitemap reference, AI crawler directives.

    Args:
        domain: Full domain URL (e.g. https://example.com).
    """
    return await check_robots_txt(domain)


@mcp.tool()
async def tech_check_llms_txt(domain: str) -> list:
    """Check llms.txt — existence and structure.

    Args:
        domain: Full domain URL (e.g. https://example.com).
    """
    return await check_llms_txt(domain)


@mcp.tool()
async def tech_check_xml_sitemap(domain: str) -> list:
    """Check XML sitemap — reachability via robots.txt and common paths.

    Args:
        domain: Full domain URL (e.g. https://example.com).
    """
    return await check_xml_sitemap(domain)


@mcp.tool()
async def tech_check_html_sitemap_footer(domain: str) -> list:
    """Check homepage footer for a sitemap link."""
    return await check_html_sitemap_footer(domain)


@mcp.tool()
def tech_check_url_hygiene(url: str) -> list:
    """Check URL — lowercase, hyphens, no staging domain."""
    return check_url_hygiene(url)


@mcp.tool()
async def tech_check_nap(url: str) -> list:
    """Check Name/Address/Phone signals on the page."""
    return await check_nap(url)


@mcp.tool()
async def tech_check_page_speed(url: str) -> list:
    """Run a PageSpeed Insights mobile check."""
    return await check_page_speed(url)


@mcp.tool()
async def tech_check_caching(url: str) -> list:
    """Detect caching plugin signals from response headers."""
    return await check_caching(url)


@mcp.tool()
async def tech_check_noindex(url: str) -> list:
    """Check for a noindex meta robots directive — fails if found on a live optimised page."""
    return await check_noindex(url)


@mcp.tool()
async def tech_check_redirect(url: str) -> list:
    """Follow a URL and report its HTTP status and redirect chain.

    Use this to verify that an old URL (from the 'Redirection?' workbook column)
    redirects cleanly to the new destination, or that a newly created page returns 200.
    """
    return await check_redirect(url)


@mcp.tool()
async def tech_check_url_batch(urls: list) -> list:
    """Check a list of URLs and report HTTP status for each (max 30).

    Designed for the 'Fixed 404s & Broken URLs' workflow — pass the previously
    broken URLs to confirm they now all return 200.

    Args:
        urls: List of URL strings to check.
    """
    return await check_url_batch(urls)


@mcp.tool()
async def tech_check_fetch_reliability(url: str) -> list:
    """Flag when the page fetch looks bot-gated (stripped <head> despite a
    real, substantial body) — a signal that other checks' results for this
    same URL (Title Tag, Meta Description, H1, Schema, keywords) may be
    unreliable rather than a genuine content problem."""
    return await check_fetch_reliability(url)


# ---------------------------------------------------------------------------
# Page element checks
# ---------------------------------------------------------------------------

@mcp.tool()
async def elements_check_images(url: str) -> list:
    """Check image alt text coverage and modern format usage (WebP/AVIF)."""
    return await check_images(url)


@mcp.tool()
async def elements_check_links(url: str) -> list:
    """Check internal links, external links, anchor text, phone links, and CTAs."""
    return await check_links(url)


@mcp.tool()
async def elements_check_backlink(referring_url: str, expected_target_url: str) -> list:
    """Verify a referring page links to the expected target URL.

    Args:
        referring_url: The page that should contain the backlink.
        expected_target_url: The URL that should be linked to.
    """
    return await check_backlink(referring_url, expected_target_url)


@mcp.tool()
async def elements_check_expected_links(url: str, expected_links: list) -> list:
    """Verify specific target URLs are actually linked from this page —
    e.g. "Internal link here to https://..." mentions parsed out of an
    opt_note's own text (see check_orchestrator.py). Distinct from
    elements_check_links (generic link-quality scan, no specific expected
    destination) and elements_check_backlink (checks a DIFFERENT page
    links back to this one).

    Args:
        url: Page that should contain the links.
        expected_links: Target URLs that should be linked from this page.
    """
    return await check_expected_links(url, expected_links)


@mcp.tool()
async def elements_check_internal_redirects(url: str) -> list:
    """Sample up to 15 internal links and flag any that are redirects (3xx)."""
    return await check_internal_redirects(url)


@mcp.tool()
async def elements_check_google_maps(url: str) -> list:
    """Check for a Google Maps iframe and verify it uses a GBP link."""
    return await check_google_maps(url)


@mcp.tool()
async def elements_check_youtube(url: str) -> list:
    """Check for a YouTube embed and corresponding VideoObject schema."""
    return await check_youtube(url)


@mcp.tool()
async def elements_check_faq(url: str) -> list:
    """Check for a FAQ section and FAQPage schema."""
    return await check_faq(url)


@mcp.tool()
async def elements_check_toc(url: str) -> list:
    """Check for a table of contents with valid anchor targets."""
    return await check_toc(url)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@mcp.tool()
async def send_notification(
    subject: str,
    body: str,
    webhook_url: Optional[str] = None,
) -> dict:
    """Send a notification with review findings via webhook.

    Args:
        subject: Short summary of the notification.
        body: Full details of the findings.
        webhook_url: Optional override. Falls back to NOTIFICATION_WEBHOOK_URL env var.
    """
    import httpx
    target = webhook_url or os.environ.get("NOTIFICATION_WEBHOOK_URL")
    if not target:
        return {"status": "skipped", "reason": "no webhook URL configured"}

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(target, json={"subject": subject, "body": body})

    return {"status": "sent", "http_status": response.status_code}


def main() -> None:
    # Cloud Run injects PORT and expects the process to bind 0.0.0.0:$PORT.
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(mcp.sse_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
