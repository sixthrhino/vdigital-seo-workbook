"""Tests for mcp-server/tools/page_elements.py"""

import httpx
import pytest
import respx

from seo_testing_mcp.tools.page_elements import (
    check_images,
    check_links,
    check_backlink,
    check_expected_links,
    check_internal_redirects,
    check_faq,
    check_toc,
)
from conftest import make_html, ld_json

URL = "https://example.com/page"
DOMAIN = "https://example.com"


def _mock(html, url=URL, status=200, headers=None):
    respx.get(url).mock(
        return_value=httpx.Response(status, text=html, headers=headers or {})
    )


def statuses(results):
    return {r["label"]: r["status"] for r in results}


# ---------------------------------------------------------------------------
# check_images
# ---------------------------------------------------------------------------

class TestCheckImages:
    @respx.mock
    async def test_pass_all_alt_webp(self):
        body = (
            '<img src="hero.webp" alt="Expert spine care Phoenix" width="800" height="600">'
            '<img src="team.webp" alt="Sonoran Spine team" width="400" height="300">'
        )
        _mock(make_html(body=body))
        results = await check_images(URL)
        alt = next((r for r in results if "Alt Text" in r["label"]), None)
        assert alt and alt["status"] == "pass"

    @respx.mock
    async def test_fail_missing_alt(self):
        body = '<img src="photo.jpg">'
        _mock(make_html(body=body))
        results = await check_images(URL)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_warn_generic_alt(self):
        body = '<img src="photo.jpg" alt="image">'
        _mock(make_html(body=body))
        results = await check_images(URL)
        assert any(r["status"] in ("warn", "fail") for r in results)

    @respx.mock
    async def test_fail_legacy_format(self):
        body = '<img src="photo.jpg" alt="Spine clinic Phoenix">'
        _mock(make_html(body=body))
        results = await check_images(URL)
        format_result = next((r for r in results if "Format" in r["label"]), None)
        assert format_result is not None and format_result["status"] in ("warn", "fail")

    @respx.mock
    async def test_pass_imagify_header(self):
        body = '<img src="hero.webp" alt="Clinic exterior">'
        _mock(
            make_html(body=body),
            headers={"x-imagify-optimization": "optimized"},
        )
        results = await check_images(URL)
        imagify = next((r for r in results if "Imagify" in r["label"]), None)
        assert imagify and imagify["status"] == "pass"

    @respx.mock
    async def test_info_no_imagify_header(self):
        body = '<img src="hero.webp" alt="Clinic exterior">'
        _mock(make_html(body=body))
        results = await check_images(URL)
        imagify = next((r for r in results if "Imagify" in r["label"]), None)
        assert imagify and imagify["status"] == "info"


# ---------------------------------------------------------------------------
# check_links
# ---------------------------------------------------------------------------

class TestCheckLinks:
    @respx.mock
    async def test_pass_proper_anchor_text(self):
        body = (
            '<a href="/about">About our spine clinic</a>'
            '<a href="https://example.com/contact">Contact us today</a>'
        )
        _mock(make_html(body=body))
        results = await check_links(URL)
        assert not any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_warn_generic_anchor(self):
        body = '<a href="/services">click here</a>'
        _mock(make_html(body=body))
        results = await check_links(URL)
        assert any(r["status"] in ("warn", "fail") for r in results)

    @respx.mock
    async def test_pass_phone_link(self):
        body = '<a href="tel:+16025551234">(602) 555-1234</a>'
        _mock(make_html(body=body))
        results = await check_links(URL)
        phone = next((r for r in results if "Phone" in r["label"]), None)
        assert phone and phone["status"] == "pass"

    @respx.mock
    async def test_warn_phone_in_non_tel_link(self):
        # Phone number in an <a> tag with href=/contact should warn
        body = '<a href="/contact">Call us at 6025551234</a>'
        _mock(make_html(body=body))
        results = await check_links(URL)
        phone = next((r for r in results if "Phone" in r["label"]), None)
        assert phone and phone["status"] in ("warn", "fail")

    @respx.mock
    async def test_footer_nav_header_external_links_excluded(self):
        # External links living only in site-wide chrome (footer social
        # icons, nav utility links) shouldn't count against a page's own
        # target=_blank content check.
        body = (
            '<header><a href="https://facebook.com/x">Facebook</a></header>'
            '<nav><a href="https://partner.example.org">Partner</a></nav>'
            '<p>Real content with <a href="https://example.com/about">a link</a>.</p>'
            '<footer><a href="https://linkedin.com/x">LinkedIn</a></footer>'
        )
        _mock(make_html(body=body))
        results = await check_links(URL)
        ext = next((r for r in results if "External Links" in r["label"]), None)
        assert ext is None


# ---------------------------------------------------------------------------
# check_backlink
# ---------------------------------------------------------------------------

class TestCheckBacklink:
    @respx.mock
    async def test_pass_link_found(self):
        referring = "https://example.com/blog/post"
        target = "https://example.com/services/spine-care"
        body = f'<p>Read more about <a href="{target}">spine care services</a>.</p>'
        respx.get(target).mock(return_value=httpx.Response(200, text="OK"))
        respx.get(referring).mock(return_value=httpx.Response(200, text=make_html(body=body)))
        results = await check_backlink(referring, target)
        assert any(r["status"] == "pass" for r in results)

    @respx.mock
    async def test_fail_link_not_found(self):
        referring = "https://example.com/blog/post"
        target = "https://example.com/services/spine-care"
        body = "<p>No link to spine care here.</p>"
        respx.get(target).mock(return_value=httpx.Response(200, text="OK"))
        respx.get(referring).mock(return_value=httpx.Response(200, text=make_html(body=body)))
        results = await check_backlink(referring, target)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_fail_page_load_error(self):
        referring = "https://deadlink.example.com/post"
        target = "https://example.com/services"
        respx.get(target).mock(return_value=httpx.Response(200, text="OK"))
        respx.get(referring).mock(return_value=httpx.Response(404))
        results = await check_backlink(referring, target)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_rejects_unsafe_target_url(self):
        # No respx route registered for the internal address — if it were
        # actually fetched, respx would raise instead of just failing cleanly.
        results = await check_backlink("https://example.com/post", "http://169.254.169.254/")
        assert results[0]["status"] == "fail"
        assert "unsafe" in results[0]["detail"].lower()

    @respx.mock
    async def test_rejects_unsafe_referring_url(self):
        results = await check_backlink("http://10.0.0.5/internal", "https://example.com/services")
        assert results[0]["status"] == "fail"
        assert "unsafe" in results[0]["detail"].lower()


# ---------------------------------------------------------------------------
# check_expected_links
# ---------------------------------------------------------------------------

class TestCheckExpectedLinks:
    @respx.mock
    async def test_pass_when_link_present(self):
        body = '<p>See our <a href="https://example.com/faqs/">FAQs</a>.</p>'
        _mock(make_html(body=body))
        results = await check_expected_links(URL, ["https://example.com/faqs/"])
        assert results[0]["status"] == "pass"

    @respx.mock
    async def test_fail_when_link_missing(self):
        _mock(make_html(body="<p>Nothing relevant here.</p>"))
        results = await check_expected_links(URL, ["https://example.com/faqs/"])
        assert results[0]["status"] == "fail"

    @respx.mock
    async def test_matches_despite_scheme_and_trailing_slash_differences(self):
        # Live href is http + no trailing slash; expected is https + trailing
        # slash — same normalization used for brand guide CTA URLs.
        body = '<p><a href="http://example.com/faqs">FAQs</a></p>'
        _mock(make_html(body=body))
        results = await check_expected_links(URL, ["https://example.com/faqs/"])
        assert results[0]["status"] == "pass"

    @respx.mock
    async def test_relative_href_resolved_against_page_url(self):
        body = '<p><a href="/faqs/">FAQs</a></p>'
        _mock(make_html(body=body))
        results = await check_expected_links(URL, ["https://example.com/faqs/"])
        assert results[0]["status"] == "pass"

    @respx.mock
    async def test_multiple_expected_links_each_get_their_own_result(self):
        body = '<p><a href="https://example.com/faqs/">FAQs</a></p>'
        _mock(make_html(body=body))
        results = await check_expected_links(URL, [
            "https://example.com/faqs/", "https://example.com/continued-education/",
        ])
        assert len(results) == 2
        statuses = {r["label"]: r["status"] for r in results}
        assert statuses['Expected Link: "https://example.com/faqs/"'] == "pass"
        assert statuses['Expected Link: "https://example.com/continued-education/"'] == "fail"

    @respx.mock
    async def test_fail_page_load_error(self):
        respx.get(URL).mock(side_effect=httpx.ConnectError("Connection refused"))
        results = await check_expected_links(URL, ["https://example.com/faqs/"])
        assert results[0]["status"] == "fail"
        assert results[0]["label"] == "Page Load"

    async def test_empty_expected_links_returns_no_results(self):
        results = await check_expected_links(URL, [])
        assert results == []


# ---------------------------------------------------------------------------
# check_internal_redirects
# ---------------------------------------------------------------------------

class TestCheckInternalRedirects:
    @respx.mock
    async def test_skips_protocol_relative_link_to_internal_address(self):
        # "//169.254.169.254/..." satisfies the same-domain filter's
        # `href.startswith("/")` check while actually resolving to a
        # different host — a real bypass of the intended same-domain
        # restriction. No respx route is registered for it, so if it were
        # actually fetched, respx would raise instead of just skipping it.
        body = (
            '<p><a href="/page-a">a</a></p>'
            '<p><a href="//169.254.169.254/steal">bad</a></p>'
        )
        _mock(make_html(body=body))
        respx.head(f"{DOMAIN}/page-a").mock(return_value=httpx.Response(200))
        results = await check_internal_redirects(URL)
        # No redirects flagged — the safe link returned 200, the unsafe one
        # was skipped rather than fetched.
        assert not any(r.get("status") == "fail" for r in results)


# ---------------------------------------------------------------------------
# check_faq
# ---------------------------------------------------------------------------

class TestCheckFaq:
    @respx.mock
    async def test_pass_faq_heading_and_schema(self):
        schema = ld_json({
            "@type": "FAQPage",
            "@context": "https://schema.org",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "What is a spine surgeon?",
                    "acceptedAnswer": {"@type": "Answer", "text": "A specialist."},
                }
            ],
        })
        body = (
            "<h2>Frequently Asked Questions</h2>"
            "<h3>What is a spine surgeon?</h3>"
            "<p>A specialist.</p>"
        )
        _mock(make_html(head=schema, body=body))
        results = await check_faq(URL)
        assert any(r["status"] == "pass" for r in results)

    @respx.mock
    async def test_info_no_faq(self):
        _mock(make_html(body="<p>No FAQ content.</p>"))
        results = await check_faq(URL)
        assert any(r["status"] == "info" for r in results)

    @respx.mock
    async def test_warn_faq_heading_no_schema(self):
        body = (
            "<h2>Frequently Asked Questions</h2>"
            "<h3>What do spine surgeons do?</h3>"
        )
        _mock(make_html(body=body))
        results = await check_faq(URL)
        assert any(r["status"] in ("warn", "fail") for r in results)


# ---------------------------------------------------------------------------
# check_toc
# ---------------------------------------------------------------------------

class TestCheckToc:
    @respx.mock
    async def test_pass_toc_with_valid_anchors(self):
        body = (
            '<nav class="toc">'
            '  <ul>'
            '    <li><a href="#intro">Introduction</a></li>'
            '    <li><a href="#section-1">Section 1</a></li>'
            '    <li><a href="#section-2">Section 2</a></li>'
            '  </ul>'
            '</nav>'
            '<h2 id="intro">Introduction</h2><p>Intro content.</p>'
            '<h2 id="section-1">Section 1</h2><p>Content here.</p>'
            '<h2 id="section-2">Section 2</h2><p>More content.</p>'
        )
        _mock(make_html(body=body))
        results = await check_toc(URL)
        assert any(r["status"] == "pass" for r in results)

    @respx.mock
    async def test_fail_broken_anchor(self):
        body = (
            '<nav class="toc">'
            '  <ul>'
            '    <li><a href="#intro">Introduction</a></li>'
            '    <li><a href="#section-1">Section 1</a></li>'
            '    <li><a href="#nonexistent">Missing Section</a></li>'
            '  </ul>'
            '</nav>'
            '<h2 id="intro">Introduction</h2>'
            '<h2 id="section-1">Section 1</h2>'
        )
        _mock(make_html(body=body))
        results = await check_toc(URL)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_info_no_toc(self):
        _mock(make_html(body="<p>No table of contents here.</p>"))
        results = await check_toc(URL)
        assert any(r["status"] == "info" for r in results)
