"""Tests for plan_session_source.py — converting a PlanSession (as fetched
from seo-workbook-mcp's find_session) into the flat row shape Mode B's check
pipeline (check_orchestrator) expects."""

import json

import pytest

import seo_testing_agent.check_orchestrator as check_orchestrator
import seo_testing_agent.plan_session_source as pss


# ---------------------------------------------------------------------------
# _split_geo
# ---------------------------------------------------------------------------

class TestSplitGeo:
    def test_splits_city_and_state(self):
        assert pss._split_geo("Denver, CO") == ("Denver", "CO")

    def test_no_comma_passes_through_as_city(self):
        assert pss._split_geo("Colorado") == ("Colorado", "")

    def test_multi_location_phrase_passes_through_unsplit(self):
        assert pss._split_geo("Multiple Locations") == ("Multiple Locations", "")

    def test_empty_string(self):
        assert pss._split_geo("") == ("", "")

    def test_none_treated_as_empty(self):
        assert pss._split_geo(None) == ("", "")


# ---------------------------------------------------------------------------
# page_optimization_names
# ---------------------------------------------------------------------------

class TestPageOptimizationNames:
    def test_maps_known_touchpoints_to_catalog_names(self):
        page = {"touchpoints": [
            {"touchpoint_id": "title_tag", "items": []},
            {"touchpoint_id": "meta_description", "items": []},
        ]}
        assert pss.page_optimization_names(page) == "Title Tag; Meta Description"

    def test_unknown_touchpoint_contributes_nothing(self):
        page = {"touchpoints": [{"touchpoint_id": "not_a_real_touchpoint", "items": []}]}
        assert pss.page_optimization_names(page) == ""

    def test_touchpoint_with_no_testing_catalog_equivalent_contributes_nothing(self):
        page = {"touchpoints": [{"touchpoint_id": "trust_signals_testimonials_reviews_etc", "items": []}]}
        assert pss.page_optimization_names(page) == ""

    def test_no_touchpoints_returns_empty_string(self):
        assert pss.page_optimization_names({"touchpoints": []}) == ""

    def test_names_are_deduplicated(self):
        # Both internal-linking touchpoints map to the same catalog name.
        page = {"touchpoints": [
            {"touchpoint_id": "internal_linking_to_target_page", "items": []},
            {"touchpoint_id": "internal_linking_to_other_pages_homepage", "items": []},
        ]}
        assert pss.page_optimization_names(page) == "Internal Linking & Anchor Text"


# ---------------------------------------------------------------------------
# page_to_row
# ---------------------------------------------------------------------------

class TestPageToRow:
    def test_bare_page_with_no_touchpoints(self):
        page = {"url": "https://example.com/a", "touchpoints": []}
        row = pss.page_to_row(page)
        assert row["url"] == "https://example.com/a"
        assert row["keyword"] == ""
        assert row["geo_city"] == ""
        assert row["geo_state"] == ""
        assert row["new_title"] == ""
        assert row["new_meta"] == ""
        assert row["new_h1"] == ""
        assert row["redirection"] == ""
        assert row["opt_note"] == ""

    def test_title_tag_touchpoint_populates_new_title(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "title_tag", "items": [{"new_value": "New Title", "primary_keyword": "widgets"}]},
        ]}
        row = pss.page_to_row(page)
        assert row["new_title"] == "New Title"
        assert "Title Tag" in row["opt_note"]

    def test_meta_description_touchpoint_populates_new_meta(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "meta_description", "items": [{"new_value": "New meta", "cta": "Call now"}]},
        ]}
        row = pss.page_to_row(page)
        assert row["new_meta"] == "New meta"

    def test_h1_tag_touchpoint_populates_new_h1(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "h1_tag", "items": [{"new_value": "New H1", "primary_keyword": "widgets"}]},
        ]}
        row = pss.page_to_row(page)
        assert row["new_h1"] == "New H1"

    def test_keyword_target_populates_keyword(self):
        page = {"url": "https://example.com/a", "touchpoints": [],
                "keyword_target": {"keyword": "back pain", "search_volume": 100}}
        row = pss.page_to_row(page)
        assert row["keyword"] == "back pain"

    def test_geo_is_split_into_city_and_state(self):
        page = {"url": "https://example.com/a", "touchpoints": [], "geo": "Phoenix, AZ"}
        row = pss.page_to_row(page)
        assert row["geo_city"] == "Phoenix"
        assert row["geo_state"] == "AZ"

    def test_heading_touchpoint_produces_inline_heading_opt_note(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "h2_h3_h4_tags", "items": [
                {"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"},
                {"old_tag": "h4", "new_tag": "h3", "heading_text": "How to use your GI benefits"},
            ]},
        ]}
        row = pss.page_to_row(page)
        assert "<H3> Common Career Paths" in row["opt_note"]
        assert "<H3> How to use your GI benefits" in row["opt_note"]

    def test_heading_touchpoint_populates_old_headings_index_aligned_with_opt_note(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "h2_h3_h4_tags", "items": [
                {"old_tag": "h2", "new_tag": "h3", "heading_text": "Checking Over Your Trailer"},
                {"new_tag": "h3", "heading_text": "Emergency Equipment"},
            ]},
        ]}
        row = pss.page_to_row(page)
        # Item 2 has no old_tag — falls back to its own new_tag as a no-op
        # placeholder rather than leaving a gap that would misalign the
        # index-based pairing check_heading_hierarchy relies on.
        assert row["old_headings"] == (
            "<H2> Checking Over Your Trailer\n<H3> Emergency Equipment"
        )

    def test_no_heading_touchpoint_leaves_old_headings_empty(self):
        page = {"url": "https://example.com/a", "touchpoints": []}
        row = pss.page_to_row(page)
        assert row["old_headings"] == ""

    def test_internal_linking_touchpoint_produces_internal_link_opt_note(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "internal_linking_to_target_page", "items": [
                {"anchor_text": "our FAQ page", "target_url": "https://example.com/faqs/"},
            ]},
        ]}
        row = pss.page_to_row(page)
        assert "Internal link to https://example.com/faqs/" in row["opt_note"]

    def test_optimizations_touchpoint_note_is_folded_into_opt_note(self):
        # The free-text "optimizations" touchpoint (legacy import, or
        # record_page_from_text's notes: field) has no touchpoint_id to
        # dispatch a check from directly — but checks_for_row's own
        # heading/link extraction already parses this exact free-text
        # shape, so folding it into opt_note is enough to make those
        # checks fire without any new dispatch logic.
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "optimizations", "items": [
                {"note": "Make Headers below an <H2> tag\n<H3> Checking Over Your Trailer\n<H3> Emergency Equipment"},
            ]},
        ]}
        row = pss.page_to_row(page)
        assert "<H3> Checking Over Your Trailer" in row["opt_note"]
        assert "<H3> Emergency Equipment" in row["opt_note"]

    def test_optimizations_touchpoint_headings_dispatch_seo_check_headings(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "optimizations", "items": [
                {"note": "<H3> Checking Over Your Trailer\n<H3> Emergency Equipment"},
            ]},
        ]}
        row = pss.page_to_row(page)
        calls = check_orchestrator.checks_for_row(row, [])
        heading_calls = [kw for name, kw in calls if name == "seo_check_headings"]
        assert len(heading_calls) == 1
        assert heading_calls[0]["expected_headings"] == (
            "<H3> Checking Over Your Trailer\n<H3> Emergency Equipment"
        )
        # No h2_h3_h4_tags touchpoint here (free text only) — nothing
        # populates old_headings, so it's correctly left out entirely
        # rather than passed through empty.
        assert "old_headings" not in heading_calls[0]

    def test_heading_touchpoint_dispatches_seo_check_headings_with_old_headings(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "h2_h3_h4_tags", "items": [
                {"old_tag": "h2", "new_tag": "h3", "heading_text": "Checking Over Your Trailer"},
                {"new_tag": "h3", "heading_text": "Emergency Equipment"},
            ]},
        ]}
        row = pss.page_to_row(page)
        calls = check_orchestrator.checks_for_row(row, [])
        heading_calls = [kw for name, kw in calls if name == "seo_check_headings"]
        assert len(heading_calls) == 1
        assert heading_calls[0]["expected_headings"] == (
            "<H3> Checking Over Your Trailer\n<H3> Emergency Equipment"
        )
        assert heading_calls[0]["old_headings"] == (
            "<H2> Checking Over Your Trailer\n<H3> Emergency Equipment"
        )

    def test_optimizations_touchpoint_internal_links_dispatch_expected_links_check(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "optimizations", "items": [
                {"note": "Internal link here to https://example.com/faqs/"},
            ]},
        ]}
        row = pss.page_to_row(page)
        calls = check_orchestrator.checks_for_row(row, [])
        assert ("elements_check_expected_links", {
            "url": "https://example.com/a",
            "expected_links": ["https://example.com/faqs/"],
        }) in calls

    def test_url_changes_redirection_populates_redirection_from_old_value(self):
        page = {"url": "https://example.com/new", "touchpoints": [
            {"touchpoint_id": "url_changes_redirection", "items": [
                {"old_value": "https://example.com/old"},
            ]},
        ]}
        row = pss.page_to_row(page)
        assert row["redirection"] == "https://example.com/old"

    def test_url_changes_redirection_falls_back_to_old_url_key(self):
        page = {"url": "https://example.com/new", "touchpoints": [
            {"touchpoint_id": "url_changes_redirection", "items": [
                {"old_url": "https://example.com/old"},
            ]},
        ]}
        row = pss.page_to_row(page)
        assert row["redirection"] == "https://example.com/old"

    def test_opt_note_combines_names_and_structured_extras(self):
        page = {"url": "https://example.com/a", "touchpoints": [
            {"touchpoint_id": "title_tag", "items": [{"new_value": "New Title", "primary_keyword": "widgets"}]},
            {"touchpoint_id": "h2_h3_h4_tags", "items": [
                {"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"},
            ]},
        ]}
        row = pss.page_to_row(page)
        assert "Title Tag" in row["opt_note"]
        assert "H2 / H3 / H4 Tags" in row["opt_note"]
        assert "<H3> Common Career Paths" in row["opt_note"]


class TestSessionToRows:
    def test_converts_every_page(self):
        session = {"pages": [
            {"url": "https://example.com/a", "touchpoints": []},
            {"url": "https://example.com/b", "touchpoints": []},
        ]}
        rows = pss.session_to_rows(session)
        assert [r["url"] for r in rows] == ["https://example.com/a", "https://example.com/b"]

    def test_no_pages_returns_empty_list(self):
        assert pss.session_to_rows({"pages": []}) == []


# ---------------------------------------------------------------------------
# fetch_plan_session
# ---------------------------------------------------------------------------

class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, *, structured=None, text=None, is_error=False):
        self.isError = is_error
        self.structuredContent = structured
        self.content = [FakeContent(text)] if text is not None else []


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def initialize(self):
        pass

    async def call_tool(self, name, kwargs):
        self.calls.append((name, kwargs))
        return self._response


def _patch_streamable_client(monkeypatch, session):
    class FakeStreamableClient:
        async def __aenter__(self):
            return ("read", "write", lambda: None)

        async def __aexit__(self, *a):
            return False

    class FakeClientSession:
        def __init__(self, read, write):
            assert (read, write) == ("read", "write")

        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(pss, "streamablehttp_client", lambda url, headers=None: FakeStreamableClient())
    monkeypatch.setattr(pss, "ClientSession", FakeClientSession)


class TestFetchPlanSession:
    async def test_returns_structured_content(self, monkeypatch):
        session_doc = {"session_id": "acme-2026-06", "client": "Acme", "month": "2026-06", "pages": []}
        session = FakeSession(FakeResult(structured=session_doc))
        _patch_streamable_client(monkeypatch, session)

        result = await pss.fetch_plan_session("Acme", "2026-06", "https://workbook-mcp.example.com/mcp", None)

        assert result == session_doc
        assert session.calls == [("find_session", {"client": "Acme", "month": "2026-06"})]

    async def test_falls_back_to_json_text_block(self, monkeypatch):
        session_doc = {"session_id": "acme-2026-06", "client": "Acme", "month": "2026-06", "pages": []}
        session = FakeSession(FakeResult(text=json.dumps(session_doc)))
        _patch_streamable_client(monkeypatch, session)

        result = await pss.fetch_plan_session("Acme", "2026-06", "https://workbook-mcp.example.com/mcp", None)

        assert result == session_doc

    async def test_raises_on_tool_error(self, monkeypatch):
        session = FakeSession(FakeResult(is_error=True, text="No session found for client='Acme', month='2026-06'"))
        _patch_streamable_client(monkeypatch, session)

        with pytest.raises(RuntimeError, match="No session found"):
            await pss.fetch_plan_session("Acme", "2026-06", "https://workbook-mcp.example.com/mcp", None)
