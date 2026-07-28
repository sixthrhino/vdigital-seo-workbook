"""Tests for mcp-server/tools/content.py"""

import httpx
import pytest
import respx

from seo_testing_mcp.tools.content import (
    check_word_count,
    check_publish_date,
    check_sentences,
    check_broken_links,
    get_page_text,
)
from conftest import make_html

URL = "https://example.com/article"


def _mock(html, url=URL, status=200):
    respx.get(url).mock(return_value=httpx.Response(status, text=html))


def statuses(results):
    return {r["label"]: r["status"] for r in results}


# ---------------------------------------------------------------------------
# check_word_count
# ---------------------------------------------------------------------------

class TestCheckWordCount:
    @respx.mock
    async def test_pass_normal_count(self):
        words = " ".join(["word"] * 400)
        _mock(make_html(body=f"<article><p>{words}</p></article>"))
        results = await check_word_count(URL)
        assert any(r["label"] == "Word Count" and r["status"] == "pass" for r in results)

    @respx.mock
    async def test_warn_over_limit(self):
        words = " ".join(["word"] * 700)
        _mock(make_html(body=f"<article><p>{words}</p></article>"))
        results = await check_word_count(URL)
        assert any(r["label"] == "Word Count" and r["status"] == "warn" for r in results)

    @respx.mock
    async def test_fail_page_load_error(self):
        respx.get(URL).mock(side_effect=httpx.ConnectError("Connection refused"))
        results = await check_word_count(URL)
        assert any(r["status"] == "fail" for r in results)


# ---------------------------------------------------------------------------
# get_page_text
# ---------------------------------------------------------------------------

class TestGetPageText:
    @respx.mock
    async def test_returns_main_content_text(self):
        _mock(make_html(body="<article><p>Some article body text.</p></article>"))
        result = await get_page_text(URL)
        assert result["url"] == URL
        assert "Some article body text." in result["text"]
        assert result["truncated"] is False
        assert result["error"] is None

    @respx.mock
    async def test_truncates_to_max_chars(self):
        words = " ".join(["word"] * 2000)
        _mock(make_html(body=f"<article><p>{words}</p></article>"))
        result = await get_page_text(URL, max_chars=50)
        assert len(result["text"]) == 50
        assert result["truncated"] is True

    @respx.mock
    async def test_error_on_fetch_failure(self):
        respx.get(URL).mock(side_effect=httpx.ConnectError("Connection refused"))
        result = await get_page_text(URL)
        assert result["text"] == ""
        assert result["error"]


# ---------------------------------------------------------------------------
# check_publish_date
# ---------------------------------------------------------------------------

class TestCheckPublishDate:
    @respx.mock
    async def test_pass_via_time_tag(self):
        body = '<time datetime="2025-06-15">June 15, 2025</time>'
        _mock(make_html(body=body))
        results = await check_publish_date(URL, expected_date="06/15/2025")
        assert statuses(results)["Publish Date"] == "pass"

    @respx.mock
    async def test_pass_via_ld_json(self):
        import json
        schema = json.dumps({"@type": "BlogPosting", "datePublished": "2025-06-15"})
        head = f'<script type="application/ld+json">{schema}</script>'
        _mock(make_html(head=head))
        results = await check_publish_date(URL, expected_date="2025-06-15")
        assert statuses(results)["Publish Date"] == "pass"

    @respx.mock
    async def test_fail_date_mismatch(self):
        body = '<time datetime="2025-06-01">June 1, 2025</time>'
        _mock(make_html(body=body))
        results = await check_publish_date(URL, expected_date="06/15/2025")
        assert statuses(results)["Publish Date"] == "fail"

    @respx.mock
    async def test_warn_no_date_found(self):
        _mock(make_html())
        results = await check_publish_date(URL, expected_date="06/15/2025")
        assert statuses(results)["Publish Date"] == "warn"

    @respx.mock
    async def test_info_no_expected(self):
        body = '<time datetime="2025-06-15">June 15, 2025</time>'
        _mock(make_html(body=body))
        results = await check_publish_date(URL)
        assert statuses(results)["Publish Date"] == "info"

    @respx.mock
    async def test_warn_no_expected_no_date(self):
        _mock(make_html())
        results = await check_publish_date(URL)
        assert statuses(results)["Publish Date"] == "warn"

    @respx.mock
    async def test_pass_via_meta_tag(self):
        head = '<meta property="article:published_time" content="2025-06-15">'
        _mock(make_html(head=head))
        results = await check_publish_date(URL, expected_date="2025-06-15")
        assert statuses(results)["Publish Date"] == "pass"


# ---------------------------------------------------------------------------
# check_sentences
# ---------------------------------------------------------------------------

class TestCheckSentences:
    @respx.mock
    async def test_pass_new_present_old_gone(self):
        body = "<p>The new sentence content is now live on the page.</p>"
        _mock(make_html(body=body))
        results = await check_sentences(
            URL,
            old_sentences=["The old sentence text that was removed"],
            new_sentences=["The new sentence content is now live"],
        )
        assert results[0]["status"] == "pass"

    @respx.mock
    async def test_fail_old_still_present(self):
        body = "<p>The old sentence text that was removed is still here.</p>"
        _mock(make_html(body=body))
        results = await check_sentences(
            URL,
            old_sentences=["The old sentence text that was removed"],
            new_sentences=["The new sentence content"],
        )
        assert results[0]["status"] == "fail"

    @respx.mock
    async def test_warn_new_and_old_both_present(self):
        body = (
            "<p>The old sentence text that was removed.</p>"
            "<p>The new sentence content is now live.</p>"
        )
        _mock(make_html(body=body))
        results = await check_sentences(
            URL,
            old_sentences=["The old sentence text that was removed"],
            new_sentences=["The new sentence content is now live"],
        )
        assert results[0]["status"] == "warn"

    @respx.mock
    async def test_warn_neither_found(self):
        _mock(make_html(body="<p>Unrelated content on the page.</p>"))
        results = await check_sentences(
            URL,
            old_sentences=["The old text"],
            new_sentences=["The new text"],
        )
        assert results[0]["status"] == "warn"

    @respx.mock
    async def test_multiple_pairs_labelled(self):
        body = "<p>new first sentence here. new second sentence here.</p>"
        _mock(make_html(body=body))
        results = await check_sentences(
            URL,
            old_sentences=["old first sentence", "old second sentence"],
            new_sentences=["new first sentence here", "new second sentence here"],
        )
        assert len(results) == 2
        assert results[0]["label"] == "Sentence 1"
        assert results[1]["label"] == "Sentence 2"


# ---------------------------------------------------------------------------
# check_broken_links
# ---------------------------------------------------------------------------

class TestCheckBrokenLinks:
    @respx.mock
    async def test_pass_all_links_ok(self):
        body = '<article><p><a href="https://other.com/page">text</a></p></article>'
        _mock(make_html(body=body))
        respx.head("https://other.com/page").mock(return_value=httpx.Response(200))
        results = await check_broken_links(URL)
        assert any(r["label"] == "Content Links" and r["status"] == "pass" for r in results)

    @respx.mock
    async def test_fail_broken_link(self):
        body = '<article><p><a href="https://dead.com/page">text</a></p></article>'
        _mock(make_html(body=body))
        respx.head("https://dead.com/page").mock(return_value=httpx.Response(404))
        respx.get("https://dead.com/page").mock(return_value=httpx.Response(404))
        results = await check_broken_links(URL)
        assert any(r["label"] == "Broken Link" and r["status"] == "fail" for r in results)

    @respx.mock
    async def test_warn_no_links(self):
        _mock(make_html(body="<article><p>No links here.</p></article>"))
        results = await check_broken_links(URL)
        assert any(r["label"] == "Content Links" and r["status"] == "warn" for r in results)

    @respx.mock
    async def test_skips_link_to_internal_address_without_fetching_it(self):
        # No respx route registered for the metadata IP — if it were actually
        # fetched, respx's "all requests must be mocked" default would raise.
        body = (
            '<article><p><a href="https://other.com/page">ok</a></p>'
            '<p><a href="http://169.254.169.254/computeMetadata/v1/">bad</a></p></article>'
        )
        _mock(make_html(body=body))
        respx.head("https://other.com/page").mock(return_value=httpx.Response(200))
        results = await check_broken_links(URL)
        assert any(r["label"] == "Content Links" and r["status"] == "pass" for r in results)
