"""Tests for agent/check_orchestrator.py — direct MCP-client check execution
that bypasses the LLM tool-calling loop entirely."""

import asyncio
import json

import pytest

import seo_testing_agent.check_orchestrator as co


# ---------------------------------------------------------------------------
# checks_for_row — deterministic dispatch logic
# ---------------------------------------------------------------------------

class TestChecksForRow:
    def test_bare_auto_check_gets_only_url(self):
        row = {"url": "https://example.com/a"}
        calls = co.checks_for_row(row, ["seo_check_canonical"])
        assert ("seo_check_canonical", {"url": "https://example.com/a"}) in calls

    def test_title_check_gets_expected_when_new_title_populated(self):
        row = {"url": "https://example.com/a", "new_title": "New Title"}
        calls = co.checks_for_row(row, ["seo_check_title"])
        assert ("seo_check_title", {"url": "https://example.com/a", "expected": "New Title"}) in calls

    def test_title_check_omits_expected_when_new_title_empty(self):
        row = {"url": "https://example.com/a", "new_title": ""}
        calls = co.checks_for_row(row, ["seo_check_title"])
        assert ("seo_check_title", {"url": "https://example.com/a"}) in calls

    def test_fetch_reliability_check_always_runs(self):
        # Unconditional — not gated by auto_checks/opt_note like everything
        # else here, since a bot-gated fetch can silently poison any check
        # for this URL regardless of what was actually planned this month.
        row = {"url": "https://example.com/a"}
        calls = co.checks_for_row(row, [])
        assert ("tech_check_fetch_reliability", {"url": "https://example.com/a"}) in calls

    def test_meta_and_h1_expected_kwargs(self):
        row = {"url": "https://example.com/a", "new_meta": "New meta", "new_h1": "New H1"}
        calls = co.checks_for_row(row, ["seo_check_meta_description", "seo_check_h1"])
        assert ("seo_check_meta_description", {"url": row["url"], "expected": "New meta"}) in calls
        assert ("seo_check_h1", {"url": row["url"], "expected": "New H1"}) in calls

    def test_keyword_adds_seo_check_keywords(self):
        row = {"url": "https://example.com/a", "keyword": "back pain"}
        calls = co.checks_for_row(row, [])
        assert ("seo_check_keywords", {"url": row["url"], "primary_keyword": "back pain"}) in calls

    def test_keyword_na_is_skipped(self):
        row = {"url": "https://example.com/a", "keyword": "N/A"}
        calls = co.checks_for_row(row, [])
        assert not any(name == "seo_check_keywords" for name, _ in calls)

    def test_empty_keyword_is_skipped(self):
        row = {"url": "https://example.com/a", "keyword": ""}
        calls = co.checks_for_row(row, [])
        assert not any(name == "seo_check_keywords" for name, _ in calls)

    def test_geo_city_adds_geo_check_accuracy(self):
        row = {"url": "https://example.com/a", "geo_city": "Phoenix", "geo_state": "AZ"}
        calls = co.checks_for_row(row, [])
        assert ("geo_check_accuracy", {"url": row["url"], "geo_city": "Phoenix", "geo_state": "AZ"}) in calls

    def test_no_geo_city_skips_geo_check(self):
        row = {"url": "https://example.com/a"}
        calls = co.checks_for_row(row, [])
        assert not any(name == "geo_check_accuracy" for name, _ in calls)

    def test_redirection_checks_the_old_url_not_row_url(self):
        row = {"url": "https://example.com/new", "redirection": "https://example.com/old"}
        calls = co.checks_for_row(row, [])
        assert ("tech_check_redirect", {"url": "https://example.com/old"}) in calls

    def test_blog_url_adds_schema_check(self):
        row = {"url": "https://example.com/blog/post"}
        calls = co.checks_for_row(row, [])
        assert ("seo_check_schema", {"url": row["url"]}) in calls

    def test_blog_url_does_not_duplicate_schema_already_in_auto_checks(self):
        row = {"url": "https://example.com/blog/post"}
        calls = co.checks_for_row(row, ["seo_check_schema"])
        assert sum(1 for name, _ in calls if name == "seo_check_schema") == 1

    def test_non_blog_url_skips_schema_check(self):
        row = {"url": "https://example.com/page"}
        calls = co.checks_for_row(row, [])
        assert not any(name == "seo_check_schema" for name, _ in calls)

    def test_excluded_cities_passed_when_geo_city_present(self):
        row = {"url": "https://example.com/a", "geo_city": "Phoenix", "geo_state": "AZ"}
        guide = {"raw": "x", "excluded_cities": ["Chicago"]}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert ("geo_check_accuracy", {
            "url": row["url"], "geo_city": "Phoenix", "geo_state": "AZ",
            "excluded_cities": ["Chicago"],
        }) in calls

    def test_excluded_cities_trigger_geo_check_even_without_row_geo_city(self):
        # A row with no geo target of its own should still get scanned for
        # "client cannot service" cities parsed from the brand guide.
        row = {"url": "https://example.com/a"}
        guide = {"raw": "x", "excluded_cities": ["Chicago"]}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert ("geo_check_accuracy", {
            "url": row["url"], "geo_city": "", "geo_state": "",
            "excluded_cities": ["Chicago"],
        }) in calls

    def test_no_geo_check_when_no_geo_city_and_no_excluded_cities(self):
        row = {"url": "https://example.com/a"}
        guide = {"raw": "x", "excluded_cities": []}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert not any(name == "geo_check_accuracy" for name, _ in calls)

    def test_multi_city_geo_guide_dispatches_target_market_check_for_nationwide_row(self):
        # A row with no geo_city of its own (nationwide/multi-market client)
        # previously got no geo check at all — the brand guide's multi-city
        # Geo Targeting list should now be dispatched as the allowlist.
        row = {"url": "https://example.com/a"}
        guide = {"raw": "x", "geo": ["Tempe, AZ", "Gilbert, AZ", "Queen Creek, AZ"]}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert ("geo_check_accuracy", {
            "url": row["url"], "geo_city": "", "geo_state": "",
            "allowlist_cities": ["Tempe, AZ", "Gilbert, AZ", "Queen Creek, AZ"],
        }) in calls

    def test_multi_city_geo_guide_passed_as_extra_allowlist_alongside_row_geo_city(self):
        row = {"url": "https://example.com/a", "geo_city": "Tempe", "geo_state": "AZ"}
        guide = {"raw": "x", "geo": ["Tempe, AZ", "Gilbert, AZ"]}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert ("geo_check_accuracy", {
            "url": row["url"], "geo_city": "Tempe", "geo_state": "AZ",
            "allowlist_cities": ["Tempe, AZ", "Gilbert, AZ"],
        }) in calls

    def test_single_city_geo_guide_is_not_treated_as_an_allowlist(self):
        # A single-city guide is redundant with the row's own geo_city and
        # shouldn't be passed as allowlist_cities.
        row = {"url": "https://example.com/a", "geo_city": "Tempe", "geo_state": "AZ"}
        guide = {"raw": "x", "geo": ["Tempe, AZ"]}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert ("geo_check_accuracy", {
            "url": row["url"], "geo_city": "Tempe", "geo_state": "AZ",
        }) in calls

    def test_single_city_geo_guide_and_no_row_geo_city_skips_geo_check(self):
        row = {"url": "https://example.com/a"}
        guide = {"raw": "x", "geo": ["Tempe, AZ"]}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert not any(name == "geo_check_accuracy" for name, _ in calls)

    def test_multiple_locations_target_geo_skips_geo_check(self):
        # Real workbooks sometimes put the literal phrase "Multiple Locations"
        # in the Target Geo column instead of a real city — should be treated
        # as no single geo target, not looked up as an unrecognized city.
        row = {"url": "https://example.com/a", "geo_city": "Multiple Locations", "geo_state": ""}
        calls = co.checks_for_row(row, [])
        assert not any(name == "geo_check_accuracy" for name, _ in calls)

    def test_multi_location_phrasing_variants_skip_geo_check(self):
        for geo_city in ["Multi-location", "Multi location", "multiple locations"]:
            row = {"url": "https://example.com/a", "geo_city": geo_city, "geo_state": ""}
            calls = co.checks_for_row(row, [])
            assert not any(name == "geo_check_accuracy" for name, _ in calls), geo_city

    def test_multiple_locations_overrides_excluded_cities_and_allowlist(self):
        # "Skip entirely" means no geo_check_accuracy call at all for this
        # row, even when the brand guide would otherwise have triggered one
        # via excluded_cities or a multi-city allowlist.
        row = {"url": "https://example.com/a", "geo_city": "Multiple Locations", "geo_state": ""}
        guide = {"raw": "x", "excluded_cities": ["Chicago"],
                 "geo": ["Tempe, AZ", "Gilbert, AZ"]}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert not any(name == "geo_check_accuracy" for name, _ in calls)

    def test_brand_guide_check_added_when_guide_provided(self):
        row = {"url": "https://example.com/a"}
        guide = {"raw": "Branding\tAcme Corp", "branding": ["Acme Corp"]}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert ("content_check_brand_guide",
                {"url": "https://example.com/a", "brand_guide": guide}) in calls

    def test_brand_guide_check_omitted_when_no_guide(self):
        row = {"url": "https://example.com/a"}
        calls = co.checks_for_row(row, [])
        assert not any(name == "content_check_brand_guide" for name, _ in calls)

    def test_brand_guide_check_omitted_when_raw_is_blank(self):
        row = {"url": "https://example.com/a"}
        guide = {"raw": "   "}
        calls = co.checks_for_row(row, [], brand_guide=guide)
        assert not any(name == "content_check_brand_guide" for name, _ in calls)

    def test_inline_headings_in_opt_note_dispatch_seo_check_headings(self):
        row = {"url": "https://example.com/a", "opt_note": (
            "Make the <H4> headers below into <H3> headers\n"
            "Common Career Paths\n"
            "Starting out, you should seek employment as an electrician's apprentice, and we can help!\n"
        )}
        calls = co.checks_for_row(row, [])
        headings_calls = [kw for name, kw in calls if name == "seo_check_headings"]
        assert len(headings_calls) == 1
        assert headings_calls[0]["expected_headings"] == (
            "<H3> Common Career Paths\n"
            "<H3> Starting out, you should seek employment as an electrician's apprentice, and we can help!"
        )

    def test_inline_headings_replace_rather_than_duplicate_auto_checks_entry(self):
        # "h3" in the opt_note also triggers seo_check_headings via
        # resolve_checks_for_opt_note's keyword alias — the inline-text
        # version should replace that generic call, not run alongside it.
        row = {"url": "https://example.com/a", "opt_note": "<H4> Common Career Paths"}
        calls = co.checks_for_row(row, ["seo_check_headings"])
        assert sum(1 for name, _ in calls if name == "seo_check_headings") == 1
        kwargs = next(kw for name, kw in calls if name == "seo_check_headings")
        assert "expected_headings" in kwargs

    def test_no_inline_headings_leaves_auto_checks_entry_untouched(self):
        row = {"url": "https://example.com/a", "opt_note": "Update the H2 tags for clarity"}
        calls = co.checks_for_row(row, ["seo_check_headings"])
        assert ("seo_check_headings", {"url": "https://example.com/a"}) in calls

    def test_internal_link_mentions_dispatch_expected_links_check(self):
        row = {"url": "https://example.com/a", "opt_note": (
            "Internal link here to https://iecrm.org/faqs/\n\n"
            "Internal link here to https://iecrm.org/continued-education/"
        )}
        calls = co.checks_for_row(row, [])
        assert ("elements_check_expected_links", {
            "url": "https://example.com/a",
            "expected_links": ["https://iecrm.org/faqs/", "https://iecrm.org/continued-education/"],
        }) in calls

    def test_no_internal_link_mentions_skips_expected_links_check(self):
        row = {"url": "https://example.com/a", "opt_note": "Update the meta description"}
        calls = co.checks_for_row(row, [])
        assert not any(name == "elements_check_expected_links" for name, _ in calls)


class TestExtractInlineHeadings:
    def test_extracts_only_lines_starting_with_heading_marker(self):
        note = (
            "Make headers below <H3> tags\n"
            "<H4> Common Career Paths\n"
            "<H4> How to use your GI benefits:\n"
        )
        result = co._extract_inline_headings(note)
        assert result == "<H4> Common Career Paths\n<H4> How to use your GI benefits:"

    def test_no_heading_markers_returns_empty_string(self):
        assert co._extract_inline_headings("Update the meta description") == ""

    def test_empty_opt_note_returns_empty_string(self):
        assert co._extract_inline_headings("") == ""

    def test_case_insensitive_marker(self):
        result = co._extract_inline_headings("<h2> lowercase marker works too")
        assert result == "<h2> lowercase marker works too"

    def test_real_world_instruction_plus_plain_text_list(self):
        # The actual phrasing/shape client workbooks use: one instruction
        # line naming the target level, then the plain (unmarked) heading
        # text itself — ended by the blank line before "Internal link".
        note = (
            "Make the <H4> headers below into <H3> headers\n"
            "Common Career Paths\n"
            "Starting out, you should seek employment as an electrician's apprentice, and we can help!\n"
            "Veteran students may use their GI Bill® benefits for:\n"
            "How to use your GI benefits:\n"
            "\n"
            "Internal link here to https://iecrm.org/faqs/\n"
            "\n"
            "Internal link here to https://iecrm.org/continued-education/"
        )
        result = co._extract_inline_headings(note)
        assert result == (
            "<H3> Common Career Paths\n"
            "<H3> Starting out, you should seek employment as an electrician's apprentice, and we can help!\n"
            "<H3> Veteran students may use their GI Bill® benefits for:\n"
            "<H3> How to use your GI benefits:"
        )

    def test_to_wording_without_headers_word(self):
        note = "Change the <H4> below to <H3>\nCommon Career Paths"
        result = co._extract_inline_headings(note)
        assert result == "<H3> Common Career Paths"

    def test_instruction_with_no_following_text_returns_empty(self):
        assert co._extract_inline_headings("Make the <H4> headers below into <H3> headers") == ""


class TestExtractInternalLinks:
    def test_extracts_url_after_internal_link_phrase(self):
        note = "Internal link here to https://iecrm.org/faqs/"
        assert co._extract_internal_links(note) == ["https://iecrm.org/faqs/"]

    def test_extracts_multiple_mentions(self):
        note = (
            "Internal link here to https://iecrm.org/faqs/\n\n"
            "Internal link here to https://iecrm.org/continued-education/"
        )
        assert co._extract_internal_links(note) == [
            "https://iecrm.org/faqs/", "https://iecrm.org/continued-education/",
        ]

    def test_no_mention_returns_empty_list(self):
        assert co._extract_internal_links("Update the meta description") == []

    def test_trailing_punctuation_stripped(self):
        note = "Internal link here to https://iecrm.org/faqs/."
        assert co._extract_internal_links(note) == ["https://iecrm.org/faqs/"]

    def test_case_insensitive_phrase(self):
        note = "INTERNAL LINK to https://iecrm.org/faqs/"
        assert co._extract_internal_links(note) == ["https://iecrm.org/faqs/"]


# ---------------------------------------------------------------------------
# _call_tool — CallToolResult extraction
# ---------------------------------------------------------------------------

class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, *, structured=None, text=None, texts=None, is_error=False):
        self.isError = is_error
        self.structuredContent = structured
        if texts is not None:
            self.content = [FakeContent(t) for t in texts]
        else:
            self.content = [FakeContent(text)] if text is not None else []


class FakeSession:
    def __init__(self, responses: dict):
        self._responses = responses
        self.calls = []

    async def call_tool(self, name, kwargs):
        self.calls.append((name, kwargs))
        response = self._responses[name]
        # supports a callable(kwargs) -> FakeResult for tools called once
        # per row with different arguments (e.g. seo_get_title_meta), where
        # a single fixed response for the whole test wouldn't do.
        return response(kwargs) if callable(response) else response


class TestCallTool:
    async def test_prefers_structured_content(self):
        session = FakeSession({"resolve_checks_for_opt_note": FakeResult(structured={"auto_checks": ["x"]})})
        result = await co._call_tool(session, asyncio.Semaphore(1), "resolve_checks_for_opt_note", opt_note="x")
        assert result == {"auto_checks": ["x"]}

    async def test_falls_back_to_json_text_content(self):
        session = FakeSession({"seo_check_title": FakeResult(text=json.dumps([{"label": "Title Tag", "status": "pass"}]))})
        result = await co._call_tool(session, asyncio.Semaphore(1), "seo_check_title", url="https://example.com")
        assert result == [{"label": "Title Tag", "status": "pass"}]

    async def test_error_result_becomes_fail_entry(self):
        session = FakeSession({"seo_check_title": FakeResult(is_error=True, text="boom")})
        result = await co._call_tool(session, asyncio.Semaphore(1), "seo_check_title", url="https://example.com")
        assert result == [{"label": "seo_check_title", "status": "fail", "detail": "Tool call failed: boom"}]


class TestExtractResult:
    # Real production bug, caught live: workbook_list_months (a bare
    # list[str]) comes back with structuredContent=None and one *separate*
    # raw-text content block per string, not one block holding a JSON array —
    # unlike the list[dict] check results, which reliably populate
    # structuredContent. json.loads on just content[0] crashed with
    # JSONDecodeError the moment the sheet actually had real month strings.
    def test_prefers_structured_content(self):
        result = FakeResult(structured=["August 2025", "September 2025"])
        assert co._extract_result(result) == ["August 2025", "September 2025"]

    def test_single_json_blob_block(self):
        result = FakeResult(text=json.dumps([{"label": "x", "status": "pass"}]))
        assert co._extract_result(result) == [{"label": "x", "status": "pass"}]

    def test_single_bare_string_block(self):
        result = FakeResult(text="August 2025")
        assert co._extract_result(result) == "August 2025"

    def test_multiple_bare_string_blocks(self):
        result = FakeResult(texts=["August 2025", "September 2025", "June 2026"])
        assert co._extract_result(result) == ["August 2025", "September 2025", "June 2026"]

    def test_multiple_json_blocks(self):
        result = FakeResult(texts=[json.dumps({"url": "a"}), json.dumps({"url": "b"})])
        assert co._extract_result(result) == [{"url": "a"}, {"url": "b"}]

    def test_no_content_returns_empty_list(self):
        result = FakeResult()
        assert co._extract_result(result) == []


# ---------------------------------------------------------------------------
# _run_row / run_batch
# ---------------------------------------------------------------------------

class TestRunRow:
    async def test_aggregates_checks_and_computes_verdict(self):
        row = {"url": "https://example.com/a", "opt_note": "Title Tag"}
        session = FakeSession({
            "resolve_checks_for_opt_note": FakeResult(structured={
                "auto_checks": ["seo_check_title"],
                "guided_questions": ["Is it unique?"],
            }),
            "seo_check_title": FakeResult(structured=None, text=json.dumps(
                [{"label": "Title Tag", "status": "fail", "detail": "too short"}]
            )),
            "tech_check_fetch_reliability": FakeResult(structured=[]),
            "content_generate_recommendations": FakeResult(structured={
                "key_issues": "1. Title too short", "recommended_fixes": "1. Lengthen the title",
            }),
        })
        result = await co._run_row(session, asyncio.Semaphore(4), row)

        assert result["url"] == "https://example.com/a"
        assert result["verdict"] == "FAIL"
        assert result["checks"] == [{"label": "Title Tag", "status": "fail", "detail": "too short"}]
        assert result["manual_checklist"] == ["Is it unique?"]
        assert result["key_issues"] == "1. Title too short"
        assert result["recommended_fixes"] == "1. Lengthen the title"
        assert ("content_generate_recommendations",
                {"url": "https://example.com/a", "checks": result["checks"]}) in session.calls

    async def test_all_pass_gives_pass_verdict(self):
        row = {"url": "https://example.com/a", "opt_note": ""}
        session = FakeSession({
            "resolve_checks_for_opt_note": FakeResult(structured={"auto_checks": ["seo_check_canonical"], "guided_questions": []}),
            "seo_check_canonical": FakeResult(text=json.dumps([{"label": "Canonical", "status": "pass"}])),
            "tech_check_fetch_reliability": FakeResult(structured=[]),
            "content_generate_recommendations": FakeResult(structured={"key_issues": "", "recommended_fixes": ""}),
        })
        result = await co._run_row(session, asyncio.Semaphore(4), row)
        assert result["verdict"] == "PASS"
        assert result["key_issues"] == ""
        assert result["recommended_fixes"] == ""


class TestGrammarCheckDispatch:
    # content_check_grammar is now a real MCP tool (mcp-server owns the
    # Gemini call), so it's dispatched exactly like every other auto_check —
    # no special-casing here anymore.
    async def test_run_row_dispatches_grammar_check_like_any_other_tool(self):
        row = {"url": "https://example.com/a", "opt_note": "Grammar review"}
        session = FakeSession({
            "resolve_checks_for_opt_note": FakeResult(structured={
                "auto_checks": ["content_check_grammar"],
                "guided_questions": [],
            }),
            "content_check_grammar": FakeResult(structured=[
                {"label": "Grammar & Syntax", "status": "fail", "detail": "Misspelled word"},
            ]),
            "tech_check_fetch_reliability": FakeResult(structured=[]),
            "content_generate_recommendations": FakeResult(structured={"key_issues": "", "recommended_fixes": ""}),
        })

        result = await co._run_row(session, asyncio.Semaphore(4), row)

        assert result["checks"] == [{"label": "Grammar & Syntax", "status": "fail", "detail": "Misspelled word"}]
        assert result["verdict"] == "FAIL"
        assert ("content_check_grammar", {"url": "https://example.com/a"}) in session.calls


class TestGroupDuplicates:
    def test_no_duplicates_returns_empty(self):
        assert co._group_duplicates([(0, "a"), (1, "b")]) == {}

    def test_finds_group_sharing_a_value(self):
        groups = co._group_duplicates([(0, "a"), (1, "a"), (2, "b")])
        assert groups == {"a": [0, 1]}

    def test_empty_values_are_excluded(self):
        assert co._group_duplicates([(0, ""), (1, "")]) == {}


class TestRowKeyword:
    def test_returns_stripped_keyword(self):
        assert co._row_keyword({"keyword": "  back pain  "}) == "back pain"

    def test_na_returns_empty(self):
        assert co._row_keyword({"keyword": "N/A"}) == ""
        assert co._row_keyword({"keyword": "n/a"}) == ""

    def test_missing_keyword_returns_empty(self):
        assert co._row_keyword({}) == ""


def _base_result(url: str) -> dict:
    return {"url": url, "opt_note": "", "verdict": "PASS", "checks": [], "manual_checklist": []}


def _title_meta_session(mapping: dict[str, dict]) -> FakeSession:
    return FakeSession({"seo_get_title_meta": lambda kwargs: FakeResult(structured=mapping[kwargs["url"]])})


class TestApplyBatchChecks:
    async def test_duplicate_titles_flag_both_rows(self):
        rows = [
            {"url": "https://example.com/a", "keyword": ""},
            {"url": "https://example.com/b", "keyword": ""},
        ]
        results = [_base_result(r["url"]) for r in rows]
        session = _title_meta_session({
            "https://example.com/a": {"title": "Same Title", "meta_description": "Meta A"},
            "https://example.com/b": {"title": " same title ", "meta_description": "Meta B"},
        })

        await co._apply_batch_checks(session, asyncio.Semaphore(4), rows, results)

        for r in results:
            assert r["verdict"] == "FAIL"
            labels = [c["label"] for c in r["checks"]]
            assert co._DUPLICATE_TITLE_LABEL in labels
            assert co._DUPLICATE_META_LABEL not in labels
        assert "example.com/b" in results[0]["checks"][0]["detail"]
        assert "example.com/a" in results[1]["checks"][0]["detail"]

    async def test_duplicate_meta_flags_both_rows(self):
        rows = [
            {"url": "https://example.com/a", "keyword": ""},
            {"url": "https://example.com/b", "keyword": ""},
        ]
        results = [_base_result(r["url"]) for r in rows]
        session = _title_meta_session({
            "https://example.com/a": {"title": "Title A", "meta_description": "Same meta."},
            "https://example.com/b": {"title": "Title B", "meta_description": "Same meta."},
        })

        await co._apply_batch_checks(session, asyncio.Semaphore(4), rows, results)

        for r in results:
            assert r["verdict"] == "FAIL"
            assert any(c["label"] == co._DUPLICATE_META_LABEL for c in r["checks"])

    async def test_keyword_cannibalization_flags_both_rows(self):
        rows = [
            {"url": "https://example.com/a", "keyword": "back pain"},
            {"url": "https://example.com/b", "keyword": "Back Pain"},
        ]
        results = [_base_result(r["url"]) for r in rows]
        session = _title_meta_session({
            "https://example.com/a": {"title": "A", "meta_description": "MA"},
            "https://example.com/b": {"title": "B", "meta_description": "MB"},
        })

        await co._apply_batch_checks(session, asyncio.Semaphore(4), rows, results)

        for r in results:
            assert r["verdict"] == "FAIL"
            issue = next(c for c in r["checks"] if c["label"] == co._KEYWORD_CANNIBALIZATION_LABEL)
            assert "back pain" in issue["detail"].lower()

    async def test_na_keywords_are_not_flagged_as_duplicates(self):
        rows = [
            {"url": "https://example.com/a", "keyword": "N/A"},
            {"url": "https://example.com/b", "keyword": "n/a"},
        ]
        results = [_base_result(r["url"]) for r in rows]
        session = _title_meta_session({
            "https://example.com/a": {"title": "A", "meta_description": "MA"},
            "https://example.com/b": {"title": "B", "meta_description": "MB"},
        })

        await co._apply_batch_checks(session, asyncio.Semaphore(4), rows, results)

        for r in results:
            assert r["verdict"] == "PASS"
            assert r["checks"] == []

    async def test_unique_pages_are_untouched(self):
        rows = [
            {"url": "https://example.com/a", "keyword": "back pain"},
            {"url": "https://example.com/b", "keyword": "neck pain"},
        ]
        results = [_base_result(r["url"]) for r in rows]
        session = _title_meta_session({
            "https://example.com/a": {"title": "Title A", "meta_description": "Meta A"},
            "https://example.com/b": {"title": "Title B", "meta_description": "Meta B"},
        })

        await co._apply_batch_checks(session, asyncio.Semaphore(4), rows, results)

        for r in results:
            assert r["verdict"] == "PASS"
            assert r["checks"] == []

    async def test_single_row_batch_skips_without_calling_the_session(self):
        rows = [{"url": "https://example.com/a", "keyword": "back pain"}]
        results = [_base_result(rows[0]["url"])]
        session = _title_meta_session({})

        await co._apply_batch_checks(session, asyncio.Semaphore(4), rows, results)

        assert session.calls == []
        assert results[0]["checks"] == []

    async def test_existing_row_checks_and_verdict_are_preserved_alongside_new_ones(self):
        rows = [
            {"url": "https://example.com/a", "keyword": ""},
            {"url": "https://example.com/b", "keyword": ""},
        ]
        results = [_base_result(r["url"]) for r in rows]
        results[0]["checks"] = [{"label": "Title Tag", "status": "pass", "detail": "ok"}]
        session = _title_meta_session({
            "https://example.com/a": {"title": "Same Title", "meta_description": "Meta A"},
            "https://example.com/b": {"title": "Same Title", "meta_description": "Meta B"},
        })

        await co._apply_batch_checks(session, asyncio.Semaphore(4), rows, results)

        assert {"label": "Title Tag", "status": "pass", "detail": "ok"} in results[0]["checks"]
        assert any(c["label"] == co._DUPLICATE_TITLE_LABEL for c in results[0]["checks"])


def _patch_mcp_session(monkeypatch, session) -> list[bool]:
    """Patch co.sse_client/co.ClientSession so any code path that opens an
    MCP session gets the given fake `session` back. Returns a list that
    gets [True] appended once session.initialize() is awaited."""
    initialized: list[bool] = []

    async def fake_initialize():
        initialized.append(True)

    session.initialize = fake_initialize

    class FakeSseClient:
        async def __aenter__(self):
            return ("read", "write")

        async def __aexit__(self, *a):
            return False

    class FakeClientSession:
        def __init__(self, read, write):
            assert (read, write) == ("read", "write")

        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(co, "sse_client", lambda url, headers=None: FakeSseClient())
    monkeypatch.setattr(co, "ClientSession", FakeClientSession)
    return initialized


class TestGetBrandGuideNotes:
    async def test_returns_notes_from_the_tool(self):
        guide = {"raw": "Voice & Tone\tFriendly", "voice_tone": ["Friendly"]}
        session = FakeSession({
            "content_get_brand_guide_notes": FakeResult(structured=["Voice & Tone — should be: Friendly"]),
        })

        notes = await co._get_brand_guide_notes(session, asyncio.Semaphore(4), guide)

        assert notes == ["Voice & Tone — should be: Friendly"]
        assert session.calls == [("content_get_brand_guide_notes", {"brand_guide": guide})]

    async def test_skipped_when_no_brand_guide(self):
        session = FakeSession({})

        notes = await co._get_brand_guide_notes(session, asyncio.Semaphore(4), None)

        assert notes == []
        assert session.calls == []

    async def test_skipped_when_guide_raw_is_blank(self):
        session = FakeSession({})

        notes = await co._get_brand_guide_notes(session, asyncio.Semaphore(4), {"raw": "   "})

        assert notes == []
        assert session.calls == []

    async def test_no_notes_returned_gives_empty_list(self):
        guide = {"raw": "Branding\tAcme Corp"}
        session = FakeSession({"content_get_brand_guide_notes": FakeResult(structured=[])})

        notes = await co._get_brand_guide_notes(session, asyncio.Semaphore(4), guide)

        assert notes == []


class TestRunBatch:
    async def test_runs_all_rows_and_initializes_session(self, monkeypatch):
        rows = [
            {"url": "https://example.com/a", "opt_note": ""},
            {"url": "https://example.com/b", "opt_note": ""},
        ]

        responses = {
            "resolve_checks_for_opt_note": FakeResult(structured={"auto_checks": [], "guided_questions": []}),
            "seo_get_title_meta": lambda kwargs: FakeResult(structured={
                "url": kwargs["url"], "title": kwargs["url"], "meta_description": kwargs["url"], "error": None,
            }),
            "tech_check_fetch_reliability": FakeResult(structured=[]),
            "content_generate_recommendations": FakeResult(structured={"key_issues": "", "recommended_fixes": ""}),
        }
        session = FakeSession(responses)
        initialized = _patch_mcp_session(monkeypatch, session)

        results, brand_guide_notes = await co.run_batch(rows, "https://mcp.example.com", {"Authorization": "Bearer x"})

        assert initialized == [True]
        assert [r["url"] for r in results] == ["https://example.com/a", "https://example.com/b"]
        assert all(r["verdict"] == "PASS" for r in results)
        assert brand_guide_notes == []

    async def test_brand_guide_threads_into_each_row(self, monkeypatch):
        rows = [{"url": "https://example.com/a", "opt_note": ""}]
        guide = {"raw": "Branding\tAcme Corp", "branding": ["Acme Corp"]}

        responses = {
            "resolve_checks_for_opt_note": FakeResult(structured={"auto_checks": [], "guided_questions": []}),
            "tech_check_fetch_reliability": FakeResult(structured=[]),
            "content_check_brand_guide": FakeResult(structured=[]),
            "content_get_brand_guide_notes": FakeResult(structured=[]),
            "content_generate_recommendations": FakeResult(structured={"key_issues": "", "recommended_fixes": ""}),
        }
        session = FakeSession(responses)
        _patch_mcp_session(monkeypatch, session)

        await co.run_batch(rows, "https://mcp.example.com", None, brand_guide=guide)

        assert ("content_check_brand_guide",
                {"url": "https://example.com/a", "brand_guide": guide}) in session.calls

    async def test_returns_brand_guide_notes_alongside_results(self, monkeypatch):
        rows = [{"url": "https://example.com/a", "opt_note": ""}]
        guide = {"raw": "Voice & Tone\tFriendly", "voice_tone": ["Friendly"]}

        responses = {
            "resolve_checks_for_opt_note": FakeResult(structured={"auto_checks": [], "guided_questions": []}),
            "tech_check_fetch_reliability": FakeResult(structured=[]),
            "content_check_brand_guide": FakeResult(structured=[]),
            "content_get_brand_guide_notes": FakeResult(structured=["Voice & Tone — should be: Friendly"]),
            "content_generate_recommendations": FakeResult(structured={"key_issues": "", "recommended_fixes": ""}),
        }
        session = FakeSession(responses)
        _patch_mcp_session(monkeypatch, session)

        results, brand_guide_notes = await co.run_batch(rows, "https://mcp.example.com", None, brand_guide=guide)

        assert brand_guide_notes == ["Voice & Tone — should be: Friendly"]
        # Not attached to any row's manual_checklist anymore — it's a
        # separate top-level report section instead.
        assert results[0]["manual_checklist"] == []

    async def test_unwraps_taskgroup_exception(self, monkeypatch):
        # Real production case: a session dropped mid-batch (e.g. the
        # instance holding it recycled) surfaces as anyio's TaskGroup
        # wrapping the real error in a BaseExceptionGroup — run_batch must
        # unwrap it like every other function in this module does, or
        # callers only ever see the useless "unhandled errors in a
        # TaskGroup (1 sub-exception)" instead of the real cause.
        class RaisingSession:
            async def initialize(self):
                pass

            async def call_tool(self, name, kwargs):
                raise RuntimeError("404 Not Found for url .../messages/?session_id=abc")

        class FakeSseClient:
            async def __aenter__(self):
                return ("read", "write")

            async def __aexit__(self, exc_type, exc, tb):
                if exc is not None:
                    raise BaseExceptionGroup("unhandled errors in a TaskGroup", [exc])
                return False

        class FakeClientSession:
            def __init__(self, read, write):
                pass

            async def __aenter__(self):
                return RaisingSession()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(co, "sse_client", lambda url, headers=None: FakeSseClient())
        monkeypatch.setattr(co, "ClientSession", FakeClientSession)

        rows = [{"url": "https://example.com/a", "opt_note": ""}]
        with pytest.raises(RuntimeError, match=r"404 Not Found for url \.\.\./messages/\?session_id=abc"):
            await co.run_batch(rows, "https://mcp.example.com", None)


class TestFlattenExceptionMessage:
    def test_returns_str_of_plain_exception(self):
        assert co._flatten_exception_message(RuntimeError("boom")) == "boom"

    def test_unwraps_single_level_group(self):
        group = BaseExceptionGroup("eg", [RuntimeError("inner")])
        assert co._flatten_exception_message(group) == "inner"

    def test_unwraps_nested_groups(self):
        inner_group = BaseExceptionGroup("inner-eg", [ValueError("deepest")])
        outer_group = BaseExceptionGroup("outer-eg", [inner_group])
        assert co._flatten_exception_message(outer_group) == "deepest"


class TestSheetsLookups:
    async def test_list_workbook_months_returns_list(self, monkeypatch):
        session = FakeSession({
            "workbook_list_months": FakeResult(structured=["August 2025", "September 2025"]),
        })
        _patch_mcp_session(monkeypatch, session)

        result = await co.list_workbook_months("sheet-id-123", "https://mcp.example.com", None)

        assert result == ["August 2025", "September 2025"]

    async def test_list_workbook_months_handles_one_block_per_month(self, monkeypatch):
        # The real shape hit live against an actual client workbook:
        # structuredContent=None, one raw-text content block per month
        # string rather than a single JSON-array blob.
        session = FakeSession({
            "workbook_list_months": FakeResult(texts=["August 2025", "September 2025", "June 2026"]),
        })
        _patch_mcp_session(monkeypatch, session)

        result = await co.list_workbook_months("sheet-id-123", "https://mcp.example.com", None)

        assert result == ["August 2025", "September 2025", "June 2026"]
        assert session.calls == [("workbook_list_months", {"spreadsheet_id": "sheet-id-123"})]

    async def test_list_workbook_months_raises_on_tool_error(self, monkeypatch):
        session = FakeSession({"workbook_list_months": FakeResult(is_error=True, text="permission denied")})
        _patch_mcp_session(monkeypatch, session)

        with pytest.raises(RuntimeError, match="permission denied"):
            await co.list_workbook_months("sheet-id-123", "https://mcp.example.com", None)

    async def test_get_workbook_month_rows_raises_on_tool_error(self, monkeypatch):
        session = FakeSession({"workbook_get_month_rows": FakeResult(is_error=True, text="not found")})
        _patch_mcp_session(monkeypatch, session)

        with pytest.raises(RuntimeError, match="not found"):
            await co.get_workbook_month_rows("sheet-id-123", "August 2025", "https://mcp.example.com", None)

    async def test_list_workbook_months_unwraps_taskgroup_exception(self, monkeypatch):
        # anyio's TaskGroup (used internally by sse_client/ClientSession) wraps
        # any exception raised inside the `async with` body in a
        # BaseExceptionGroup on the way out — simulate that real behavior here
        # rather than the plain-propagation _patch_mcp_session harness above.
        class RaisingSession:
            async def initialize(self):
                pass

            async def call_tool(self, name, kwargs):
                raise RuntimeError("Error executing tool workbook_list_months: On-Page")

        class FakeSseClient:
            async def __aenter__(self):
                return ("read", "write")

            async def __aexit__(self, exc_type, exc, tb):
                if exc is not None:
                    raise BaseExceptionGroup("unhandled errors in a TaskGroup", [exc])
                return False

        class FakeClientSession:
            def __init__(self, read, write):
                pass

            async def __aenter__(self):
                return RaisingSession()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(co, "sse_client", lambda url, headers=None: FakeSseClient())
        monkeypatch.setattr(co, "ClientSession", FakeClientSession)

        with pytest.raises(RuntimeError, match="Error executing tool workbook_list_months: On-Page"):
            await co.list_workbook_months("sheet-id-123", "https://mcp.example.com", None)

    async def test_get_workbook_month_rows_returns_list(self, monkeypatch):
        session = FakeSession({
            "workbook_get_month_rows": FakeResult(structured=[{"url": "https://example.com/a"}]),
        })
        _patch_mcp_session(monkeypatch, session)

        result = await co.get_workbook_month_rows("sheet-id-123", "August 2025", "https://mcp.example.com", None)

        assert result == [{"url": "https://example.com/a"}]
        assert session.calls == [
            ("workbook_get_month_rows", {"spreadsheet_id": "sheet-id-123", "month_year": "August 2025"}),
        ]

    async def test_get_workbook_title_returns_string(self, monkeypatch):
        session = FakeSession({
            "workbook_get_title": FakeResult(text="IEC Rocky Mountain (main) | Organic SEO Workbook"),
        })
        _patch_mcp_session(monkeypatch, session)

        result = await co.get_workbook_title("sheet-id-123", "https://mcp.example.com", None)

        assert result == "IEC Rocky Mountain (main) | Organic SEO Workbook"
        assert session.calls == [("workbook_get_title", {"spreadsheet_id": "sheet-id-123"})]

    async def test_get_workbook_title_raises_on_tool_error(self, monkeypatch):
        session = FakeSession({"workbook_get_title": FakeResult(is_error=True, text="not found")})
        _patch_mcp_session(monkeypatch, session)

        with pytest.raises(RuntimeError, match="not found"):
            await co.get_workbook_title("sheet-id-123", "https://mcp.example.com", None)

    async def test_get_workbook_brand_guide_returns_structured_dict(self, monkeypatch):
        guide = {"raw": "Branding\tAcme Corp", "branding": ["Acme Corp"]}
        session = FakeSession({
            "workbook_get_brand_guide": FakeResult(structured=guide),
        })
        _patch_mcp_session(monkeypatch, session)

        result = await co.get_workbook_brand_guide("sheet-id-123", "https://mcp.example.com", None)

        assert result == guide
        assert session.calls == [("workbook_get_brand_guide", {"spreadsheet_id": "sheet-id-123"})]

    async def test_get_workbook_brand_guide_raises_on_tool_error(self, monkeypatch):
        session = FakeSession({"workbook_get_brand_guide": FakeResult(is_error=True, text="permission denied")})
        _patch_mcp_session(monkeypatch, session)

        with pytest.raises(RuntimeError, match="permission denied"):
            await co.get_workbook_brand_guide("sheet-id-123", "https://mcp.example.com", None)

    async def test_get_workbook_client_details_returns_structured_dict(self, monkeypatch):
        details = {"client": "Sonoran Spine", "website": "https://www.sonoranspine.com/"}
        session = FakeSession({
            "workbook_get_client_details": FakeResult(structured=details),
        })
        _patch_mcp_session(monkeypatch, session)

        result = await co.get_workbook_client_details("sheet-id-123", "https://mcp.example.com", None)

        assert result == details
        assert session.calls == [("workbook_get_client_details", {"spreadsheet_id": "sheet-id-123"})]

    async def test_get_workbook_client_details_raises_on_tool_error(self, monkeypatch):
        session = FakeSession({"workbook_get_client_details": FakeResult(is_error=True, text="permission denied")})
        _patch_mcp_session(monkeypatch, session)

        with pytest.raises(RuntimeError, match="permission denied"):
            await co.get_workbook_client_details("sheet-id-123", "https://mcp.example.com", None)

    async def test_parse_brand_guide_text_returns_structured_dict(self, monkeypatch):
        guide = {"raw": "Branding\tAcme Corp", "branding": ["Acme Corp"]}
        session = FakeSession({
            "content_parse_brand_guide": FakeResult(structured=guide),
        })
        _patch_mcp_session(monkeypatch, session)

        result = await co.parse_brand_guide_text("Branding\tAcme Corp", "https://mcp.example.com", None)

        assert result == guide
        assert session.calls == [("content_parse_brand_guide", {"brand_guide_text": "Branding\tAcme Corp"})]

    async def test_parse_brand_guide_text_raises_on_tool_error(self, monkeypatch):
        session = FakeSession({"content_parse_brand_guide": FakeResult(is_error=True, text="bad input")})
        _patch_mcp_session(monkeypatch, session)

        with pytest.raises(RuntimeError, match="bad input"):
            await co.parse_brand_guide_text("garbage", "https://mcp.example.com", None)


class TestSubmitReport:
    async def test_assembles_and_submits_results(self, monkeypatch):
        url_results = [{"url": "https://example.com/a", "verdict": "PASS", "checks": [],
                         "manual_checklist": [], "key_issues": "", "recommended_fixes": ""}]
        session = FakeSession({
            "generate_report": FakeResult(text="https://storage.googleapis.com/signed-url"),
        })
        _patch_mcp_session(monkeypatch, session)

        result = await co.submit_report("Acme Corp", "August 2025", url_results,
                                         "https://mcp.example.com", None)

        assert result == "https://storage.googleapis.com/signed-url"
        assert session.calls == [("generate_report", {
            "results": {"month": "August 2025", "client": "Acme Corp", "urls": url_results,
                        "brand_guide_notes": []},
        })]

    async def test_includes_brand_guide_notes_when_given(self, monkeypatch):
        session = FakeSession({
            "generate_report": FakeResult(text="https://storage.googleapis.com/signed-url"),
        })
        _patch_mcp_session(monkeypatch, session)

        await co.submit_report("Acme Corp", "August 2025", [], "https://mcp.example.com", None,
                                brand_guide_notes=["Voice & Tone — should be: Friendly"])

        assert session.calls == [("generate_report", {
            "results": {"month": "August 2025", "client": "Acme Corp", "urls": [],
                        "brand_guide_notes": ["Voice & Tone — should be: Friendly"]},
        })]

    async def test_raises_on_tool_error(self, monkeypatch):
        session = FakeSession({"generate_report": FakeResult(is_error=True, text="upload failed")})
        _patch_mcp_session(monkeypatch, session)

        with pytest.raises(RuntimeError, match="upload failed"):
            await co.submit_report("Acme Corp", "August 2025", [], "https://mcp.example.com", None)
