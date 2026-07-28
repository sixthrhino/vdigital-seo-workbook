"""Tests for mcp-server/tools/technical.py"""

import httpx
import pytest
import respx

from seo_testing_mcp.tools.technical import (
    check_url_hygiene,
    check_robots_txt,
    check_llms_txt,
    check_xml_sitemap,
    check_caching,
    check_noindex,
    check_redirect,
    check_url_batch,
    check_fetch_reliability,
    _parse_robots_groups,
    _bot_blocked_by_robots,
)
from conftest import make_html

DOMAIN = "https://example.com"
URL = "https://example.com/services/page"


def statuses(results):
    return {r["label"]: r["status"] for r in results}


# ---------------------------------------------------------------------------
# check_url_hygiene — synchronous, no HTTP
# ---------------------------------------------------------------------------

class TestCheckUrlHygiene:
    def test_pass_clean_url(self):
        results = check_url_hygiene("https://example.com/services/spine-care")
        assert all(r["status"] == "pass" for r in results)

    def test_fail_uppercase(self):
        results = check_url_hygiene("https://example.com/Services/Spine-Care")
        assert any(r["status"] == "fail" for r in results)

    def test_fail_underscore(self):
        results = check_url_hygiene("https://example.com/services/spine_care")
        assert any(r["status"] == "fail" for r in results)

    def test_fail_staging_domain(self):
        results = check_url_hygiene("https://staging.example.com/services/spine-care")
        assert any(r["status"] == "fail" for r in results)

    def test_pass_subdirectory_url(self):
        results = check_url_hygiene("https://example.com/services/spine-care/phoenix")
        assert all(r["status"] == "pass" for r in results)

    def test_fail_dev_domain(self):
        results = check_url_hygiene("https://dev.example.com/services/spine-care")
        assert any(r["status"] == "fail" for r in results)


# ---------------------------------------------------------------------------
# check_robots_txt
# ---------------------------------------------------------------------------

class TestCheckRobotsTxt:
    @respx.mock
    async def test_pass_exists_with_sitemap(self):
        robots = "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml"
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
        results = await check_robots_txt(DOMAIN)
        assert any(r["label"] == "robots.txt" and r["status"] == "pass" for r in results)
        assert any(r["label"] == "Sitemap in robots.txt" and r["status"] == "pass" for r in results)

    @respx.mock
    async def test_fail_missing_robots(self):
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(404))
        results = await check_robots_txt(DOMAIN)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_warn_no_sitemap_in_robots(self):
        robots = "User-agent: *\nDisallow: /wp-admin/"
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
        results = await check_robots_txt(DOMAIN)
        assert any(r["label"] == "Sitemap in robots.txt" and r["status"] == "warn" for r in results)

    @respx.mock
    async def test_warn_gpt_bot_allowed(self):
        robots = (
            "User-agent: *\nAllow: /\n"
            "Sitemap: https://example.com/sitemap.xml"
        )
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
        results = await check_robots_txt(DOMAIN)
        ai_result = next(
            (r for r in results if "GPTBot" in r.get("label", "") or "AI" in r.get("label", "")),
            None,
        )
        # AI bot control line should be present (warn or pass depending on content)
        assert ai_result is not None

    @respx.mock
    async def test_ai_crawler_blocked_by_wildcard_fails(self):
        # Regression guard for the bug the old substring-based check had:
        # GPTBot's name appearing in robots.txt doesn't mean it's allowed —
        # here the wildcard group blocks everything, GPTBot included.
        robots = "User-agent: *\nDisallow: /"
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
        results = await check_robots_txt(DOMAIN)
        ai_result = next(r for r in results if r["label"] == "AI Crawler Access")
        assert ai_result["status"] == "fail"
        assert "GPTBot" in ai_result["detail"]

    @respx.mock
    async def test_ai_crawler_explicitly_blocked_fails_even_if_wildcard_allows(self):
        robots = "User-agent: *\nAllow: /\n\nUser-agent: GPTBot\nDisallow: /"
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
        results = await check_robots_txt(DOMAIN)
        ai_result = next(r for r in results if r["label"] == "AI Crawler Access")
        assert ai_result["status"] == "fail"
        assert "GPTBot" in ai_result["detail"]
        assert "OpenAI" in ai_result["detail"]

    @respx.mock
    async def test_ai_crawler_explicitly_allowed_overrides_wildcard_block(self):
        # Specific group beats wildcard per the robots standard — GPTBot has
        # its own Allow, so it isn't blocked even though * disallows root
        # for everything else (including every other AI crawler, which
        # correctly still shows up as blocked).
        robots = "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /"
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
        results = await check_robots_txt(DOMAIN)
        ai_result = next(r for r in results if r["label"] == "AI Crawler Access")
        assert ai_result["status"] == "fail"  # other AI crawlers are genuinely still blocked
        assert "GPTBot" not in ai_result["detail"]
        assert "ClaudeBot" in ai_result["detail"]

    @respx.mock
    async def test_no_ai_crawlers_named_passes_via_wildcard(self):
        robots = "User-agent: *\nAllow: /"
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
        results = await check_robots_txt(DOMAIN)
        ai_result = next(r for r in results if r["label"] == "AI Crawler Access")
        assert ai_result["status"] == "pass"
        assert "wildcard" in ai_result["detail"].lower()

    @respx.mock
    async def test_disallow_subpath_does_not_block_ai_crawlers(self):
        robots = "User-agent: *\nDisallow: /wp-admin/"
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(200, text=robots))
        results = await check_robots_txt(DOMAIN)
        ai_result = next(r for r in results if r["label"] == "AI Crawler Access")
        assert ai_result["status"] == "pass"


class TestParseRobotsGroups:
    def test_single_wildcard_group(self):
        groups = _parse_robots_groups("User-agent: *\nDisallow: /admin/")
        assert groups == [({"*"}, [("disallow", "/admin/")])]

    def test_multiple_groups(self):
        groups = _parse_robots_groups(
            "User-agent: *\nAllow: /\n\nUser-agent: GPTBot\nDisallow: /"
        )
        assert ({"*"}, [("allow", "/")]) in groups
        assert ({"gptbot"}, [("disallow", "/")]) in groups

    def test_consecutive_user_agent_lines_share_one_group(self):
        groups = _parse_robots_groups(
            "User-agent: GPTBot\nUser-agent: ClaudeBot\nDisallow: /"
        )
        assert groups == [({"gptbot", "claudebot"}, [("disallow", "/")])]

    def test_comments_and_blank_lines_ignored(self):
        groups = _parse_robots_groups(
            "# comment\nUser-agent: *\n\n# another comment\nDisallow: /admin/\n"
        )
        assert groups == [({"*"}, [("disallow", "/admin/")])]

    def test_empty_text_returns_no_groups(self):
        assert _parse_robots_groups("") == []


class TestBotBlockedByRobots:
    def test_no_matching_group_means_full_access(self):
        groups = _parse_robots_groups("User-agent: SomeOtherBot\nDisallow: /")
        assert _bot_blocked_by_robots("GPTBot", groups) is False

    def test_wildcard_disallow_root_blocks(self):
        groups = _parse_robots_groups("User-agent: *\nDisallow: /")
        assert _bot_blocked_by_robots("GPTBot", groups) is True

    def test_wildcard_disallow_subpath_does_not_block(self):
        groups = _parse_robots_groups("User-agent: *\nDisallow: /admin/")
        assert _bot_blocked_by_robots("GPTBot", groups) is False

    def test_specific_group_overrides_wildcard_block(self):
        groups = _parse_robots_groups(
            "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /"
        )
        assert _bot_blocked_by_robots("GPTBot", groups) is False

    def test_specific_group_overrides_wildcard_allow(self):
        groups = _parse_robots_groups(
            "User-agent: *\nAllow: /\n\nUser-agent: GPTBot\nDisallow: /"
        )
        assert _bot_blocked_by_robots("GPTBot", groups) is True

    def test_case_insensitive_bot_token_match(self):
        groups = _parse_robots_groups("User-agent: gptbot\nDisallow: /")
        assert _bot_blocked_by_robots("GPTBot", groups) is True

    def test_allow_root_alongside_disallow_root_does_not_block(self):
        # An explicit Allow: / in the same group overrides a Disallow: / —
        # unusual robots.txt authoring but should resolve to "not blocked."
        groups = _parse_robots_groups("User-agent: GPTBot\nDisallow: /\nAllow: /")
        assert _bot_blocked_by_robots("GPTBot", groups) is False


# ---------------------------------------------------------------------------
# check_caching
# ---------------------------------------------------------------------------

class TestCheckCaching:
    @respx.mock
    async def test_pass_wp_rocket(self):
        respx.get(URL).mock(return_value=httpx.Response(
            200,
            text=make_html(),
            headers={"x-cache": "HIT", "x-rocket-nginx-serving-static": "1"},
        ))
        results = await check_caching(URL)
        assert any(r["label"] == "Caching" and r["status"] == "pass" for r in results)

    @respx.mock
    async def test_pass_w3_total_cache(self):
        respx.get(URL).mock(return_value=httpx.Response(
            200,
            text=make_html(),
            headers={"x3-powered-by": "W3 Total Cache/2.8.0"},
        ))
        results = await check_caching(URL)
        assert any(r["label"] == "Caching" and r["status"] == "pass" for r in results)

    @respx.mock
    async def test_warn_no_cache_signals(self):
        respx.get(URL).mock(return_value=httpx.Response(
            200,
            text=make_html(),
            headers={"content-type": "text/html"},
        ))
        results = await check_caching(URL)
        assert any(r["status"] == "warn" for r in results)


# ---------------------------------------------------------------------------
# check_noindex
# ---------------------------------------------------------------------------

class TestCheckNoindex:
    @respx.mock
    async def test_pass_no_noindex(self):
        respx.get(URL).mock(return_value=httpx.Response(200, text=make_html()))
        results = await check_noindex(URL)
        assert any(r["status"] == "pass" for r in results)

    @respx.mock
    async def test_fail_noindex_present(self):
        head = '<meta name="robots" content="noindex, nofollow">'
        respx.get(URL).mock(return_value=httpx.Response(200, text=make_html(head=head)))
        results = await check_noindex(URL)
        assert any(r["label"] == "Noindex" and r["status"] == "fail" for r in results)

    @respx.mock
    async def test_pass_index_follow(self):
        head = '<meta name="robots" content="index, follow">'
        respx.get(URL).mock(return_value=httpx.Response(200, text=make_html(head=head)))
        results = await check_noindex(URL)
        assert any(r["status"] == "pass" for r in results)

    @respx.mock
    async def test_fail_noindex_only(self):
        head = '<meta name="robots" content="noindex">'
        respx.get(URL).mock(return_value=httpx.Response(200, text=make_html(head=head)))
        results = await check_noindex(URL)
        assert any(r["status"] == "fail" for r in results)


# ---------------------------------------------------------------------------
# check_redirect
# ---------------------------------------------------------------------------

class TestCheckRedirect:
    @respx.mock
    async def test_pass_direct_200(self):
        respx.get(URL).mock(return_value=httpx.Response(200, text="OK"))
        results = await check_redirect(URL)
        assert results[0]["status"] == "pass"

    @respx.mock
    async def test_pass_single_hop(self):
        old_url = "https://example.com/old-page"
        new_url = "https://example.com/new-page"
        respx.get(old_url).mock(
            return_value=httpx.Response(301, headers={"location": new_url})
        )
        respx.get(new_url).mock(return_value=httpx.Response(200, text="OK"))
        results = await check_redirect(old_url)
        assert results[0]["status"] == "pass"
        assert "1 hop" in results[0]["detail"]

    @respx.mock
    async def test_warn_chain(self):
        a = "https://example.com/a"
        b = "https://example.com/b"
        c = "https://example.com/c"
        respx.get(a).mock(return_value=httpx.Response(301, headers={"location": b}))
        respx.get(b).mock(return_value=httpx.Response(301, headers={"location": c}))
        respx.get(c).mock(return_value=httpx.Response(200, text="OK"))
        results = await check_redirect(a)
        assert results[0]["status"] == "warn"

    @respx.mock
    async def test_warn_bot_block(self):
        respx.get(URL).mock(return_value=httpx.Response(403))
        results = await check_redirect(URL)
        assert results[0]["status"] == "warn"

    @respx.mock
    async def test_fail_404(self):
        respx.get(URL).mock(return_value=httpx.Response(404))
        results = await check_redirect(URL)
        assert results[0]["status"] == "fail"


# ---------------------------------------------------------------------------
# check_url_batch
# ---------------------------------------------------------------------------

class TestCheckUrlBatch:
    @respx.mock
    async def test_pass_all_200(self):
        urls = ["https://example.com/a", "https://example.com/b"]
        for u in urls:
            respx.get(u).mock(return_value=httpx.Response(200))
        results = await check_url_batch(urls)
        assert all(r["status"] == "pass" for r in results)

    @respx.mock
    async def test_fail_on_404(self):
        urls = ["https://example.com/good", "https://example.com/gone"]
        respx.get("https://example.com/good").mock(return_value=httpx.Response(200))
        respx.get("https://example.com/gone").mock(return_value=httpx.Response(404))
        results = await check_url_batch(urls)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_warn_redirect(self):
        url = "https://example.com/old"
        target = "https://example.com/new"
        respx.get(url).mock(return_value=httpx.Response(301, headers={"location": target}))
        respx.get(target).mock(return_value=httpx.Response(200))
        results = await check_url_batch([url])
        assert any(r["status"] == "warn" for r in results)

    async def test_empty_input_warns(self):
        results = await check_url_batch([])
        assert any(r["status"] == "warn" for r in results)

    @respx.mock
    async def test_note_appended_for_over_30(self):
        urls = [f"https://example.com/{i}" for i in range(35)]
        for u in urls[:30]:
            respx.get(u).mock(return_value=httpx.Response(200))
        results = await check_url_batch(urls)
        assert any(r["label"] == "Note" for r in results)


# ---------------------------------------------------------------------------
# SSRF protection — no respx route registered for the unsafe target, so if
# assert_safe_url didn't actually fire first, respx's "all requests must be
# mocked" default would raise and fail the test.
# ---------------------------------------------------------------------------

class TestSSRFProtection:
    @respx.mock
    async def test_robots_txt_rejects_metadata_ip(self):
        results = await check_robots_txt("http://169.254.169.254")
        assert results[0]["status"] == "fail"
        assert "unsafe" in results[0]["detail"].lower()

    @respx.mock
    async def test_llms_txt_rejects_private_ip(self):
        results = await check_llms_txt("http://10.0.0.5")
        assert results[0]["status"] == "fail"
        assert "unsafe" in results[0]["detail"].lower()

    @respx.mock
    async def test_xml_sitemap_rejects_metadata_ip(self):
        results = await check_xml_sitemap("http://169.254.169.254")
        assert results[0]["status"] == "fail"
        assert "unsafe" in results[0]["detail"].lower()

    @respx.mock
    async def test_xml_sitemap_skips_unsafe_sitemap_url_in_robots(self):
        # robots.txt (attacker/site-controlled content) points the Sitemap:
        # line at an internal address — must not be followed.
        respx.get(f"{DOMAIN}/robots.txt").mock(return_value=httpx.Response(
            200, text="User-agent: *\nSitemap: http://169.254.169.254/steal-me.xml"
        ))
        for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml", "/wp-sitemap.xml"):
            respx.get(f"{DOMAIN}{path}").mock(return_value=httpx.Response(404))
        results = await check_xml_sitemap(DOMAIN)
        assert results[0]["status"] == "fail"

    @respx.mock
    async def test_redirect_rejects_loopback(self):
        results = await check_redirect("http://127.0.0.1/admin")
        assert results[0]["status"] == "fail"
        assert "unsafe" in results[0]["detail"].lower()

    @respx.mock
    async def test_url_batch_rejects_unsafe_entries_but_checks_the_rest(self):
        urls = ["http://169.254.169.254/", "https://example.com/good"]
        respx.get("https://example.com/good").mock(return_value=httpx.Response(200))
        results = await check_url_batch(urls)
        assert results[0]["status"] == "fail"
        assert "unsafe" in results[0]["detail"].lower()
        assert results[1]["status"] == "pass"


# ---------------------------------------------------------------------------
# check_fetch_reliability
# ---------------------------------------------------------------------------

class TestCheckFetchReliability:
    @respx.mock
    async def test_flags_suspicious_response(self):
        words = " ".join(["word"] * 150)
        stripped_html = f"<html><head></head><body>{words}</body></html>"
        respx.get(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200, text=stripped_html)
        )
        results = await check_fetch_reliability(URL)
        assert len(results) == 1
        assert results[0]["label"] == "Suspicious Response"
        assert results[0]["status"] == "warn"

    @respx.mock
    async def test_clean_response_returns_no_results(self):
        respx.get(URL).mock(return_value=httpx.Response(
            200, text=make_html(head="<title>Real Title</title>")
        ))
        results = await check_fetch_reliability(URL)
        assert results == []

    @respx.mock
    async def test_load_error_returns_no_results(self):
        # A genuine load failure is already reported by whichever check
        # ran first (Page Load fail) — this check shouldn't double it up.
        respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
        results = await check_fetch_reliability(URL)
        assert results == []
