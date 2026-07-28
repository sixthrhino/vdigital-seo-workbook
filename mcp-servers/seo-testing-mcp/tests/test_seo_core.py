"""Tests for mcp-server/tools/seo_core.py"""

import httpx
import pytest
import respx

from seo_testing_mcp.tools.seo_core import (
    check_title,
    check_meta_description,
    check_h1,
    check_heading_hierarchy,
    check_keywords,
    check_canonical,
    check_schema,
    check_og_twitter,
    get_title_meta,
    _normalize_for_exact_match,
    _normalize_kw,
)
from conftest import make_html, meta_desc, canonical, ld_json

URL = "https://example.com/page"


def _mock(html, url=URL, status=200):
    respx.get(url).mock(return_value=httpx.Response(status, text=html))


def statuses(results):
    return {r["label"]: r["status"] for r in results}


# ---------------------------------------------------------------------------
# _normalize_for_exact_match / _normalize_kw — pure helpers
# ---------------------------------------------------------------------------

class TestNormalizeForExactMatch:
    def test_curly_apostrophe_normalized_to_straight(self):
        assert _normalize_for_exact_match("Women’s Health") == "Women's Health"

    def test_curly_double_quotes_normalized(self):
        assert _normalize_for_exact_match("“Best” Clinic") == '"Best" Clinic'

    def test_en_and_em_dash_normalized_to_hyphen(self):
        assert _normalize_for_exact_match("Mon–Fri") == "Mon-Fri"
        assert _normalize_for_exact_match("Wait—what") == "Wait-what"

    def test_non_breaking_space_normalized(self):
        assert _normalize_for_exact_match("Back Pain") == "Back Pain"

    def test_collapses_and_strips_whitespace(self):
        assert _normalize_for_exact_match("  a   b  ") == "a b"

    def test_empty_string_returns_empty(self):
        assert _normalize_for_exact_match("") == ""


class TestNormalizeKw:
    def test_ampersand_normalized_to_and(self):
        assert _normalize_kw("Butler & Montgomery") == "butler and montgomery"

    def test_hyphen_normalized_to_space(self):
        assert _normalize_kw("Roll-Up Door") == "roll up door"

    def test_unicode_hyphen_variants_normalized(self):
        for dash in ("‐", "‑", "‒", "–", "—", "−"):
            assert _normalize_kw(f"Roll{dash}Up") == "roll up"

    def test_collapses_doubled_spaces_from_substitution(self):
        assert _normalize_kw("Roll - Up") == "roll up"


# ---------------------------------------------------------------------------
# check_title
# ---------------------------------------------------------------------------

class TestCheckTitle:
    @respx.mock
    async def test_pass_exact_match(self):
        _mock(make_html(head="<title>Spine Specialist Phoenix | Sonoran Spine</title>"))
        results = await check_title(URL, expected="Spine Specialist Phoenix | Sonoran Spine")
        assert statuses(results)["Title Tag"] == "pass"

    @respx.mock
    async def test_pass_fuzzy_match(self):
        _mock(make_html(head="<title>Spine Specialists Phoenix | Sonoran Spine</title>"))
        results = await check_title(URL, expected="Spine Specialist Phoenix | Sonoran Spine")
        assert statuses(results)["Title Tag"] == "pass"

    @respx.mock
    async def test_fail_missing(self):
        _mock(make_html())
        results = await check_title(URL)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_fail_mismatch(self):
        _mock(make_html(head="<title>Wrong Title | Brand</title>"))
        results = await check_title(URL, expected="Right Title | Brand")
        assert statuses(results)["Title Tag"] == "fail"

    @respx.mock
    async def test_warn_length_over_60(self):
        long = "A" * 65 + " | Brand"
        _mock(make_html(head=f"<title>{long}</title>"))
        results = await check_title(URL)
        assert statuses(results)["Title Length"] == "warn"

    @respx.mock
    async def test_pass_length_under_60(self):
        _mock(make_html(head="<title>Short Title | Brand</title>"))
        results = await check_title(URL)
        assert statuses(results)["Title Length"] == "pass"

    @respx.mock
    async def test_info_no_expected(self):
        _mock(make_html(head="<title>Some Title</title>"))
        results = await check_title(URL)
        assert statuses(results)["Title Tag"] == "info"

    @respx.mock
    async def test_info_detail_explains_no_update_was_planned(self):
        # INFO here means "nothing to compare against" (no New Title Tag in
        # the workbook), not "this matched" — the detail text should say so
        # rather than relying on the reader knowing what INFO vs pass means.
        _mock(make_html(head="<title>Some Title</title>"))
        results = await check_title(URL)
        detail = next(r["detail"] for r in results if r["label"] == "Title Tag")
        assert detail == 'No new title planned — live: "Some Title"'

    @respx.mock
    async def test_pass_when_only_difference_is_smart_quotes(self):
        # A CMS converting a straight apostrophe to curly (or vice versa)
        # between the workbook and the live page shouldn't read as a
        # mismatch. The live title has enough extra content (brand suffix)
        # appended that the fuzzy-ratio path alone wouldn't pass this —
        # it specifically exercises the "expected text present, extra
        # content added" substring-containment path, which used to compare
        # raw text and would have failed on the quote-style difference alone.
        _mock(make_html(head="<title>Women’s Health Clinic | Sonoran Spine Institute</title>"))
        results = await check_title(URL, expected="Women's Health Clinic")
        assert statuses(results)["Title Tag"] == "pass"

class TestCheckMetaDescription:
    @respx.mock
    async def test_fail_missing(self):
        _mock(make_html())
        results = await check_meta_description(URL)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_warn_too_short(self):
        _mock(make_html(head=meta_desc("Short.")))
        results = await check_meta_description(URL)
        assert statuses(results)["Meta Length"] == "warn"

    @respx.mock
    async def test_warn_too_long(self):
        _mock(make_html(head=meta_desc("X" * 165)))
        results = await check_meta_description(URL)
        assert statuses(results)["Meta Length"] == "warn"

    @respx.mock
    async def test_pass_length(self):
        _mock(make_html(head=meta_desc("X" * 140)))
        results = await check_meta_description(URL)
        assert statuses(results)["Meta Length"] == "pass"

    @respx.mock
    async def test_pass_cta_detected(self):
        _mock(make_html(head=meta_desc("Contact us today for expert spine care in Phoenix, AZ.")))
        results = await check_meta_description(URL)
        assert statuses(results)["Meta CTA"] == "pass"

    @respx.mock
    async def test_warn_no_cta(self):
        _mock(make_html(head=meta_desc("X" * 130)))
        results = await check_meta_description(URL)
        assert statuses(results)["Meta CTA"] == "warn"

    @respx.mock
    async def test_pass_expected_match(self):
        content = "Need a spine specialist in Phoenix? Sonoran Spine treats back pain."
        _mock(make_html(head=meta_desc(content)))
        results = await check_meta_description(URL, expected=content)
        assert statuses(results)["Meta Description"] == "pass"

    @respx.mock
    async def test_fail_expected_mismatch(self):
        _mock(make_html(head=meta_desc("Completely different meta.")))
        results = await check_meta_description(URL, expected="Expected meta that is not here at all.")
        assert statuses(results)["Meta Description"] == "fail"

    @respx.mock
    async def test_info_detail_explains_no_update_was_planned(self):
        _mock(make_html(head=meta_desc("Some live meta description.")))
        results = await check_meta_description(URL)
        detail = next(r["detail"] for r in results if r["label"] == "Meta Description")
        assert detail == 'No new meta description planned — live: "Some live meta description."'

    @respx.mock
    async def test_pass_when_only_difference_is_smart_quotes(self):
        # Same reasoning as check_title's equivalent test — enough extra
        # content appended that this exercises the substring-containment
        # path specifically, not just fuzzy-ratio tolerance.
        live = "Visit Sonoran Spine’s Phoenix clinic today for expert spine care and same-week appointments."
        _mock(make_html(head=meta_desc(live)))
        results = await check_meta_description(URL, expected="Visit Sonoran Spine's Phoenix clinic today")
        assert statuses(results)["Meta Description"] == "pass"


# ---------------------------------------------------------------------------
# get_title_meta
# ---------------------------------------------------------------------------

class TestGetTitleMeta:
    @respx.mock
    async def test_returns_raw_title_and_meta(self):
        html = make_html(head="<title>Spine Specialist Phoenix</title>" + meta_desc("Expert spine care in Phoenix."))
        _mock(html)
        result = await get_title_meta(URL)
        assert result == {
            "url": URL,
            "title": "Spine Specialist Phoenix",
            "meta_description": "Expert spine care in Phoenix.",
            "error": None,
        }

    @respx.mock
    async def test_missing_title_and_meta_return_empty_strings(self):
        _mock(make_html())
        result = await get_title_meta(URL)
        assert result["title"] == ""
        assert result["meta_description"] == ""
        assert result["error"] is None

    @respx.mock
    async def test_page_load_error(self):
        respx.get(URL).mock(side_effect=httpx.ConnectError("Connection refused"))
        result = await get_title_meta(URL)
        assert result["title"] == ""
        assert result["meta_description"] == ""
        assert result["error"]


# ---------------------------------------------------------------------------
# check_h1
# ---------------------------------------------------------------------------

class TestCheckH1:
    @respx.mock
    async def test_pass_exact(self):
        _mock(make_html(body="<h1>Expert Spine Care in Phoenix</h1>"))
        results = await check_h1(URL, expected="Expert Spine Care in Phoenix")
        assert statuses(results)["H1 Tag"] == "pass"

    @respx.mock
    async def test_fail_missing(self):
        _mock(make_html(body="<p>No heading here</p>"))
        results = await check_h1(URL)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_warn_multiple(self):
        _mock(make_html(body="<h1>First</h1><h1>Second</h1>"))
        results = await check_h1(URL)
        assert statuses(results)["H1 Count"] == "warn"

    @respx.mock
    async def test_pass_single(self):
        _mock(make_html(body="<h1>Just One</h1>"))
        results = await check_h1(URL)
        assert statuses(results)["H1 Count"] == "pass"

    @respx.mock
    async def test_fail_mismatch(self):
        _mock(make_html(body="<h1>Wrong Heading</h1>"))
        results = await check_h1(URL, expected="Expected Heading Here")
        assert statuses(results)["H1 Tag"] == "fail"

    @respx.mock
    async def test_info_detail_explains_no_update_was_planned(self):
        _mock(make_html(body="<h1>Some Live H1</h1>"))
        results = await check_h1(URL)
        detail = next(r["detail"] for r in results if r["label"] == "H1 Tag")
        assert detail == 'No new H1 planned — live: "Some Live H1"'

    @respx.mock
    async def test_pass_when_only_difference_is_smart_quotes(self):
        # Extra trailing content pushes this past fuzzy-ratio tolerance, so
        # it specifically exercises the substring-containment fallback.
        _mock(make_html(body="<h1>Women’s Health Clinic — Now Accepting New Patients</h1>"))
        results = await check_h1(URL, expected="Women's Health Clinic")
        assert statuses(results)["H1 Tag"] == "pass"


# ---------------------------------------------------------------------------
# check_heading_hierarchy
# ---------------------------------------------------------------------------

class TestCheckHeadingHierarchy:
    @respx.mock
    async def test_pass_no_skips(self):
        _mock(make_html(body="<h1>T</h1><h2>A</h2><h3>B</h3><h2>C</h2>"))
        results = await check_heading_hierarchy(URL)
        assert statuses(results)["Hierarchy"] == "pass"

    @respx.mock
    async def test_warn_skipped_level(self):
        _mock(make_html(body="<h1>Title</h1><h3>Skipped H2</h3>"))
        results = await check_heading_hierarchy(URL)
        assert statuses(results)["Hierarchy"] == "warn"

    @respx.mock
    async def test_new_heading_confirmed(self):
        body = "<h1>New H1</h1><h2>New H2</h2>"
        _mock(make_html(body=body))
        results = await check_heading_hierarchy(
            URL,
            expected_headings="<H1> New H1\n<H2> New H2",
            old_headings="<H1> Old H1\n<H2> Old H2",
        )
        assert any(r["status"] == "pass" and "Match" in r["label"] for r in results)

    @respx.mock
    async def test_old_heading_still_live_fails(self):
        body = "<h1>Old H1</h1>"
        _mock(make_html(body=body))
        results = await check_heading_hierarchy(
            URL,
            expected_headings="<H1> New H1",
            old_headings="<H1> Old H1",
        )
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_match_is_case_insensitive(self):
        _mock(make_html(body="<h1>T</h1><h3>COMMON CAREER PATHS</h3>"))
        results = await check_heading_hierarchy(URL, expected_headings="<H3> common career paths")
        assert any(r["status"] == "pass" and "H3" in r["label"] for r in results)

    @respx.mock
    async def test_match_ignores_trailing_punctuation(self):
        _mock(make_html(body="<h1>T</h1><h3>How to use your GI benefits</h3>"))
        results = await check_heading_hierarchy(URL, expected_headings="<H3> How to use your GI benefits:")
        assert any(r["status"] == "pass" and "H3" in r["label"] for r in results)

    @respx.mock
    async def test_match_ignores_symbol_and_curly_apostrophe_differences(self):
        _mock(make_html(body="<h1>T</h1><h3>Use your GI Bill benefits</h3>"))
        results = await check_heading_hierarchy(
            URL, expected_headings="<H3> Use your GI Bill® benefits"
        )
        assert any(r["status"] == "pass" and "H3" in r["label"] for r in results)

    @respx.mock
    async def test_match_still_fails_when_wording_differs(self):
        _mock(make_html(body="<h1>T</h1><h3>Totally Different Heading</h3>"))
        results = await check_heading_hierarchy(URL, expected_headings="<H3> Common Career Paths")
        assert any(r["status"] == "fail" and "H3" in r["label"] for r in results)


# ---------------------------------------------------------------------------
# check_keywords
# ---------------------------------------------------------------------------

class TestCheckKeywords:
    @respx.mock
    async def test_pass_in_title(self):
        _mock(make_html(head="<title>Spine Specialist Phoenix | Brand</title>"))
        results = await check_keywords(URL, primary_keyword="spine specialist")
        assert statuses(results)["Primary Keyword"] == "pass"

    @respx.mock
    async def test_fail_not_found(self):
        _mock(make_html(head="<title>Unrelated Page</title>", body="<p>Some content.</p>"))
        results = await check_keywords(URL, primary_keyword="missing keyword phrase")
        assert statuses(results)["Primary Keyword"] == "fail"

    @respx.mock
    async def test_warn_fuzzy_match_when_words_split_by_filler(self):
        # "spine" and "specialists" appear in order but with "care" inserted
        # between them — a fuzzy (order-preserved, filler-tolerant) match,
        # not an exact contiguous phrase, so it's a warn rather than a
        # silent pass — still real evidence, just not confirmed word-for-word.
        _mock(make_html(head="<title>Phoenix Spine Care Specialists in AZ</title>"))
        results = await check_keywords(URL, primary_keyword="spine specialist")
        assert statuses(results)["Primary Keyword"] == "warn"

    @respx.mock
    async def test_pass_exact_plural_tolerant_phrase(self):
        _mock(make_html(head="<title>Phoenix Spine Specialists in AZ</title>"))
        results = await check_keywords(URL, primary_keyword="spine specialist")
        assert statuses(results)["Primary Keyword"] == "pass"

    @respx.mock
    async def test_secondary_keywords(self):
        # secondary keywords need to be in title/H1/meta to pass
        body = "<h1>Treatment for Herniated Disc and Spinal Stenosis in Phoenix</h1>"
        _mock(make_html(head="<title>Spine Care</title>", body=body))
        results = await check_keywords(
            URL,
            primary_keyword="spine care",
            secondary_keywords="herniated disc, spinal stenosis",
        )
        secondary = [r for r in results if "Secondary" in r["label"]]
        assert len(secondary) == 2
        assert all(r["status"] == "pass" for r in secondary)

    @respx.mock
    async def test_strips_volume_annotation(self):
        _mock(make_html(head="<title>Spine Specialist Phoenix | Brand</title>"))
        results = await check_keywords(URL, primary_keyword="spine specialist (2.5k)")
        assert statuses(results)["Primary Keyword"] == "pass"

    @respx.mock
    async def test_pass_hyphen_vs_space_keyword_variant(self):
        # Live title has a space where the keyword has a hyphen — without
        # connector normalization, tokenizing "roll-up" on \w+ drops the
        # hyphen but then requires literal whitespace between the words,
        # so this fell through to a bag-of-words proximity match at best.
        _mock(make_html(head="<title>American Roll Up Door Co.</title>"))
        results = await check_keywords(URL, primary_keyword="Roll-Up Door")
        assert statuses(results)["Primary Keyword"] == "pass"

    @respx.mock
    async def test_pass_ampersand_vs_and_keyword_variant(self):
        _mock(make_html(head="<title>Butler and Montgomery County Roofing</title>"))
        results = await check_keywords(URL, primary_keyword="Butler & Montgomery")
        assert statuses(results)["Primary Keyword"] == "pass"

    @respx.mock
    async def test_footer_only_mention_does_not_count_as_found_in_body(self):
        # A keyword sitting only in nav/header/footer boilerplate isn't
        # genuine on-page content — body_text_excluding_chrome strips those
        # before the scan, so this should read as not found anywhere rather
        # than a false "found in body" pass.
        body = "<footer>Spine Specialist Phoenix</footer><p>Unrelated content.</p>"
        _mock(make_html(head="<title>Unrelated Page</title>", body=body))
        results = await check_keywords(URL, primary_keyword="spine specialist")
        result = next(r for r in results if r["label"] == "Primary Keyword")
        assert result["status"] == "fail"
        assert "Not found anywhere" in result["detail"]

    @respx.mock
    async def test_pass_ies_plural_variant(self):
        # "company" <-> "companies" — real English morphology, not just a
        # trailing "s" (the old bag-of-words fallback handled this by luck
        # at best; the exact-phrase tier now handles it directly).
        _mock(make_html(head="<title>Best Moving Companies in Phoenix</title>"))
        results = await check_keywords(URL, primary_keyword="moving company")
        assert statuses(results)["Primary Keyword"] == "pass"

    @respx.mock
    async def test_pass_es_plural_variant(self):
        _mock(make_html(head="<title>Storage Boxes For Rent</title>"))
        results = await check_keywords(URL, primary_keyword="storage box")
        assert statuses(results)["Primary Keyword"] == "pass"

    @respx.mock
    async def test_fuzzy_match_detail_includes_matched_snippet(self):
        _mock(make_html(head="<title>Phoenix Spine Care Specialists in AZ</title>"))
        results = await check_keywords(URL, primary_keyword="spine specialist")
        detail = next(r for r in results if r["label"] == "Primary Keyword")["detail"]
        assert "spine care specialists" in detail.lower()

    @respx.mock
    async def test_warn_loose_match_all_words_present_but_reordered(self):
        # "budget" only appears in the body, "renovation" only in the title
        # — no location has both words even loosely adjacent, but every
        # significant word is present somewhere, so this is real (weak)
        # evidence rather than a true miss.
        body = "<p>Ask about our budget-friendly financing options today.</p>"
        _mock(make_html(head="<title>Home Renovation Experts</title>", body=body))
        results = await check_keywords(URL, primary_keyword="budget renovation")
        result = next(r for r in results if r["label"] == "Primary Keyword")
        assert result["status"] == "warn"
        assert "reordered/reworded" in result["detail"]

    @respx.mock
    async def test_warn_loose_match_reports_missing_words(self):
        # 3 of 4 significant words present (within the 1-missing tolerance
        # for a 4-word phrase) — still surfaced as a warn, naming what's
        # missing, rather than either a silent pass or a hard fail.
        _mock(make_html(head="<title>Custom Kitchen Renovation Experts</title>"))
        results = await check_keywords(URL, primary_keyword="affordable custom kitchen renovation")
        result = next(r for r in results if r["label"] == "Primary Keyword")
        assert result["status"] == "warn"
        assert "missing: affordable" in result["detail"]

    @respx.mock
    async def test_fail_when_no_tier_matches(self):
        _mock(make_html(head="<title>Completely Unrelated Content</title>"))
        results = await check_keywords(URL, primary_keyword="emergency plumbing repair")
        assert statuses(results)["Primary Keyword"] == "fail"

    @respx.mock
    async def test_slash_alternative_matches_on_either_half(self):
        # "/" is "this OR that" shorthand — the person's name showing up on
        # its own should count as a match without requiring the brand name
        # to appear glued to it as one literal phrase.
        _mock(make_html(head="<title>Meet Stephen Roller | Our Team</title>"))
        results = await check_keywords(
            URL, primary_keyword="Stephen Roller / Primary Health Solutions",
        )
        result = next(r for r in results if r["label"] == "Primary Keyword")
        assert result["status"] == "pass"
        assert "Stephen Roller" in result["detail"]

    @respx.mock
    async def test_body_found_note_when_also_in_body(self):
        body = "<p>Our roll-up doors are the best in the business.</p>"
        _mock(make_html(head="<title>American Roll Up Door Co.</title>", body=body))
        results = await check_keywords(URL, primary_keyword="Roll-Up Door")
        result = next(r for r in results if r["label"] == "Primary Keyword")
        assert "Also in body." in result["detail"]


# ---------------------------------------------------------------------------
# check_canonical
# ---------------------------------------------------------------------------

class TestCheckCanonical:
    @respx.mock
    async def test_pass_self_canonical(self):
        _mock(make_html(head=canonical(URL)))
        results = await check_canonical(URL)
        assert statuses(results)["Canonical"] == "pass"

    @respx.mock
    async def test_fail_missing(self):
        _mock(make_html())
        results = await check_canonical(URL)
        assert any(r["status"] == "fail" for r in results)

    @respx.mock
    async def test_warn_points_elsewhere(self):
        _mock(make_html(head=canonical("https://example.com/other-page")))
        results = await check_canonical(URL)
        assert statuses(results)["Canonical"] == "warn"

    @respx.mock
    async def test_fail_staging_url(self):
        _mock(make_html(head=canonical("https://staging.example.com/page")))
        results = await check_canonical(URL)
        assert statuses(results)["Canonical Staging"] == "fail"


# ---------------------------------------------------------------------------
# check_schema
# ---------------------------------------------------------------------------

class TestCheckSchema:
    @respx.mock
    async def test_pass_local_business(self):
        script = ld_json({"@type": "LocalBusiness", "@context": "https://schema.org", "name": "Clinic"})
        _mock(make_html(head=script))
        results = await check_schema(URL)
        assert statuses(results)["Schema Types"] == "pass"

    @respx.mock
    async def test_warn_missing(self):
        _mock(make_html())
        results = await check_schema(URL)
        assert any(r["status"] == "warn" for r in results)

    @respx.mock
    async def test_faq_schema_flagged(self):
        schema = {"@type": "FAQPage", "@context": "https://schema.org", "mainEntity": []}
        _mock(make_html(head=ld_json(schema)))
        results = await check_schema(URL)
        assert any(r["label"] == "FAQPage Schema" and r["status"] == "pass" for r in results)

    @respx.mock
    async def test_blog_schema_detected(self):
        schema = {"@type": "BlogPosting", "@context": "https://schema.org", "headline": "Post"}
        blog_url = "https://example.com/blog/post"
        respx.get(blog_url).mock(return_value=httpx.Response(200, text=make_html(head=ld_json(schema))))
        results = await check_schema(blog_url)
        assert any(r["label"] == "Blog Schema" and r["status"] == "pass" for r in results)

    @respx.mock
    async def test_completeness_flags_missing_required_properties(self):
        schema = {"@type": "LocalBusiness", "@context": "https://schema.org", "name": "Clinic"}
        _mock(make_html(head=ld_json(schema)))
        results = await check_schema(URL)
        by_label = statuses(results)
        assert by_label["LocalBusiness Schema Completeness"] == "fail"
        detail = next(r["detail"] for r in results if r["label"] == "LocalBusiness Schema Completeness")
        assert "address" in detail and "telephone" in detail
        assert "name" not in detail.split(":")[1]

    @respx.mock
    async def test_completeness_passes_when_all_required_properties_present(self):
        schema = {
            "@type": "LocalBusiness", "@context": "https://schema.org",
            "name": "Clinic", "address": "123 Main St", "telephone": "555-1234",
        }
        _mock(make_html(head=ld_json(schema)))
        results = await check_schema(URL)
        assert "LocalBusiness Schema Completeness" not in statuses(results)

    @respx.mock
    async def test_completeness_flags_faq_with_empty_main_entity(self):
        schema = {"@type": "FAQPage", "@context": "https://schema.org", "mainEntity": []}
        _mock(make_html(head=ld_json(schema)))
        results = await check_schema(URL)
        assert statuses(results)["FAQPage Schema Completeness"] == "fail"

    @respx.mock
    async def test_completeness_flags_blog_missing_date_and_author(self):
        schema = {"@type": "BlogPosting", "@context": "https://schema.org", "headline": "Post"}
        blog_url = "https://example.com/blog/post"
        respx.get(blog_url).mock(return_value=httpx.Response(200, text=make_html(head=ld_json(schema))))
        results = await check_schema(blog_url)
        detail = next(r["detail"] for r in results if r["label"] == "BlogPosting Schema Completeness")
        assert "datePublished" in detail and "author" in detail

    @respx.mock
    async def test_completeness_ignores_unrecognized_type(self):
        schema = {"@type": "SomethingUnknown", "@context": "https://schema.org"}
        _mock(make_html(head=ld_json(schema)))
        results = await check_schema(URL)
        assert not any(r["label"].endswith("Schema Completeness") for r in results)


# ---------------------------------------------------------------------------
# check_og_twitter
# ---------------------------------------------------------------------------

class TestCheckOgTwitter:
    @respx.mock
    async def test_pass_all_tags(self):
        tags = (
            '<meta property="og:title" content="Page">'
            '<meta property="og:description" content="Desc">'
            '<meta property="og:image" content="https://example.com/img.jpg">'
            '<meta name="twitter:card" content="summary">'
            '<meta name="twitter:title" content="Page">'
        )
        _mock(make_html(head=tags))
        results = await check_og_twitter(URL)
        assert all(r["status"] == "pass" for r in results)

    @respx.mock
    async def test_warn_missing_og(self):
        _mock(make_html())
        results = await check_og_twitter(URL)
        assert statuses(results)["Open Graph"] == "warn"

    @respx.mock
    async def test_warn_missing_twitter(self):
        tags = (
            '<meta property="og:title" content="P">'
            '<meta property="og:description" content="D">'
            '<meta property="og:image" content="https://x.com/i.jpg">'
        )
        _mock(make_html(head=tags))
        results = await check_og_twitter(URL)
        assert statuses(results)["Twitter/X Tags"] == "warn"
