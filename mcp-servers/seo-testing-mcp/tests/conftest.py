

import pytest
from seo_testing_mcp.tools import fetcher, gemini_checks
from seo_testing_mcp.tools.fetcher import clear_cache


@pytest.fixture(autouse=True)
def reset_fetch_cache():
    """Clear the in-process URL cache before and after every test."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture(autouse=True)
def no_real_retry_delay(monkeypatch):
    """fetch_parsed sleeps between retry attempts when a mocked response
    looks bot-gated (see fetcher._looks_suspicious) — e.g. a test body with
    100+ words and no <title>/meta/h1, which plenty of tests do incidentally
    without meaning to exercise retry behavior at all. Zero it out so no
    test pays real wall-clock time for that unless it explicitly restores it."""
    monkeypatch.setattr(fetcher, "_RETRY_DELAY_SECONDS", 0)


@pytest.fixture(autouse=True)
def no_real_throttle_delay(monkeypatch):
    """Same reasoning as no_real_retry_delay, for the per-domain request
    throttle — several tests fetch more than one URL on the same domain
    (e.g. robots.txt then a sitemap) without meaning to exercise throttling."""
    monkeypatch.setattr(fetcher, "_REQUEST_DELAY_SECONDS", 0)


@pytest.fixture(autouse=True)
def no_real_gemini_retry_delay(monkeypatch):
    """Same reasoning as no_real_retry_delay, for gemini_checks.py's Gemini
    429/5xx retry backoff (_generate_content_with_retry)."""
    monkeypatch.setattr(gemini_checks, "_GEMINI_BACKOFF_BASE_SECONDS", 0)


# ---------------------------------------------------------------------------
# Shared HTML builders
# ---------------------------------------------------------------------------

def make_html(*, head="", body=""):
    """Minimal HTML page with optional head/body content."""
    return f"<html><head>{head}</head><body>{body}</body></html>"


def meta_desc(content):
    return f'<meta name="description" content="{content}">'


def canonical(href):
    return f'<link rel="canonical" href="{href}">'


def ld_json(data: dict | list):
    import json
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'
