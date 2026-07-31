"""Tests for page_fetch.fetch_current_page_values — the page-update
dialog's live-page fetch, used to hint at a page's current title/meta
description/H1 (see dialog_cards.build_url_entry_dialog)."""

import httpx
import pytest
import respx

from seo_workbook_agent.page_fetch import fetch_current_page_values

_HTML = """
<html>
<head>
  <title>Existing Title Tag</title>
  <meta name="description" content="Existing meta description.">
</head>
<body>
  <h1>Existing H1</h1>
</body>
</html>
"""


@respx.mock
async def test_fetch_current_page_values_parses_title_meta_and_h1():
    respx.get("https://example.com/a/").mock(return_value=httpx.Response(200, text=_HTML))
    result = await fetch_current_page_values("https://example.com/a/")
    assert result == {
        "title": "Existing Title Tag",
        "meta_description": "Existing meta description.",
        "h1": "Existing H1",
    }


@respx.mock
async def test_fetch_current_page_values_handles_missing_tags():
    respx.get("https://example.com/a/").mock(return_value=httpx.Response(200, text="<html><body></body></html>"))
    result = await fetch_current_page_values("https://example.com/a/")
    assert result == {"title": "", "meta_description": "", "h1": ""}


@respx.mock
async def test_fetch_current_page_values_returns_empty_on_404():
    respx.get("https://example.com/missing/").mock(return_value=httpx.Response(404))
    result = await fetch_current_page_values("https://example.com/missing/")
    assert result == {"title": "", "meta_description": "", "h1": ""}


@respx.mock
async def test_fetch_current_page_values_returns_empty_on_timeout():
    respx.get("https://example.com/a/").mock(side_effect=httpx.ConnectTimeout("timed out"))
    result = await fetch_current_page_values("https://example.com/a/")
    assert result == {"title": "", "meta_description": "", "h1": ""}


async def test_fetch_current_page_values_returns_empty_for_an_unsafe_url():
    result = await fetch_current_page_values("http://169.254.169.254/latest/meta-data/")
    assert result == {"title": "", "meta_description": "", "h1": ""}


async def test_fetch_current_page_values_returns_empty_for_a_non_http_scheme():
    result = await fetch_current_page_values("file:///etc/passwd")
    assert result == {"title": "", "meta_description": "", "h1": ""}
