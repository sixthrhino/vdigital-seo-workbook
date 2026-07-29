from seo_workbook_common.output.formatting import (
    format_item,
    format_month,
    format_old_new,
    format_optimizations_note,
)


def test_title_tag_shows_new_and_old_and_keyword():
    result = format_item(
        {"new_value": "New Title", "old_value": "Old Title", "primary_keyword": "insurance"}, "title_tag"
    )
    assert "New: New Title" in result
    assert "(was: Old Title)" in result
    assert "Keyword: insurance" in result


def test_meta_description_shows_cta():
    result = format_item({"new_value": "Great meta", "cta": "Get a Quote"}, "meta_description")
    assert "CTA: Get a Quote" in result


def test_heading_change_shows_arrow():
    result = format_item({"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"}, "h2_h3_h4_tags")
    assert result == "H4 → H3: Common Career Paths"


def test_internal_link_shows_anchor_and_target():
    result = format_item(
        {"anchor_text": "our FAQs", "target_url": "https://iecrm.org/faqs/"},
        "internal_linking_to_other_pages_homepage",
    )
    assert result == '"our FAQs" → https://iecrm.org/faqs/'


def test_canonical_tag_format():
    result = format_item({"new_value": "https://kyz.com/blog/"}, "canonical_tags")
    assert result == "Canonical → https://kyz.com/blog/"


def test_image_alt_text_format():
    result = format_item({"new_value": "Golden Retriever puppy", "old_value": "dog"}, "image_alt_text")
    assert "Alt text: Golden Retriever puppy" in result
    assert "(was: dog)" in result


def test_unknown_touchpoint_falls_back_to_key_value_join():
    result = format_item({"b": "2", "a": "1"}, "geo_keywords")
    assert result == "a: 1; b: 2"


def test_optimizations_touchpoint_shows_the_reformatted_note():
    result = format_item({"note": "Core Optimizations: Schema Markup, Internal Linking"}, "optimizations")
    assert result == "Core Optimizations: Schema Markup, Internal Linking"


class TestFormatOptimizationsNote:
    def test_empty_note_returns_empty_list(self):
        assert format_optimizations_note("") == []
        assert format_optimizations_note("   ") == []

    def test_core_optimizations_extracted_as_its_own_line(self):
        # Schema Markup isn't redundant with any dedicated column, so it's
        # kept — unlike Title Tag/Meta Description/H1 (see the next tests).
        result = format_optimizations_note("Core Optimizations: Title Tag, Meta Description, H1, Schema Markup.")
        assert result == ["Core Optimizations: Schema Markup"]

    def test_core_optimizations_line_dropped_when_only_redundant_touchpoints_named(self):
        # Title Tag/Meta Description/H1 already get their own dedicated
        # old/new display everywhere this note is shown — naming them
        # again here is pure repetition, so the whole line is dropped
        # rather than kept as a content-free "Core Optimizations:" label.
        result = format_optimizations_note("Core Optimizations: Title Tag, Meta Description, H1.")
        assert result == []

    def test_real_world_example_with_preamble_and_two_heading_levels(self):
        # Verbatim (whitespace-collapsed, as actually stored — see
        # converter.py) real North Texas Trailers example.
        note = (
            "Should not be a blog post. Core Optimizations: TItle Tag, Meta Description, H1. "
            "Make Headers below an <H2> tag <H3> Why Choose North Texas Trailers? "
            "Make Headers below an <H3> tag <H4> Expertise You Can Depend On: "
            "<H4> Quality Parts, Every Time: <H4> Transparent Communication: <H4> Timely Execution:"
        )
        result = format_optimizations_note(note)
        assert result == [
            "Should not be a blog post.",
            "H3: Why Choose North Texas Trailers?",
            "H4: Expertise You Can Depend On",
            "H4: Quality Parts, Every Time",
            "H4: Transparent Communication",
            "H4: Timely Execution",
        ]

    def test_real_world_example_with_no_preamble_and_one_heading_level(self):
        note = (
            "Core Optimizations: TItle Tag, Meta Description, H1. "
            "Make Headers below an <H2> tag "
            "<H3> Checking Over Your Trailer <H3> Equipment That Connects the Trailer and Vehicle "
            "<H3> Emergency Equipment <H3> Wait Time and Braking <H3> Cargo Weight and Distribution "
            "<H3> Route Restrictions <H3> Get Your Trailer From North Texas Trailers"
        )
        result = format_optimizations_note(note)
        assert result == [
            "H3: Checking Over Your Trailer",
            "H3: Equipment That Connects the Trailer and Vehicle",
            "H3: Emergency Equipment",
            "H3: Wait Time and Braking",
            "H3: Cargo Weight and Distribution",
            "H3: Route Restrictions",
            "H3: Get Your Trailer From North Texas Trailers",
        ]

    def test_tolerates_instruction_phrasing_with_no_an_or_tag_word(self):
        # Real messier variant seen live: "Make Headers below <H2>" with
        # neither "an" nor a trailing "tag" word.
        note = "Core Optimizations: Schema Markup. Make Headers below <H2> <H3> Understanding Trailer Springs <H3> Conclusion"
        result = format_optimizations_note(note)
        assert result == [
            "Core Optimizations: Schema Markup",
            "H3: Understanding Trailer Springs",
            "H3: Conclusion",
        ]

    def test_multiline_input_with_real_newlines_parses_the_same_way(self):
        note = (
            "Core Optimizations: Title Tag, Meta Description, H1.\n\n"
            "Make Headers below an <H2> tag\n\n"
            "<H3> Checking Over Your Trailer\n"
            "<H3> Emergency Equipment\n"
        )
        result = format_optimizations_note(note)
        assert result == [
            "H3: Checking Over Your Trailer",
            "H3: Emergency Equipment",
        ]

    def test_plain_text_with_no_recognized_pattern_passes_through_unchanged(self):
        assert format_optimizations_note("Added internal link to homepage.") == [
            "Added internal link to homepage."
        ]

    def test_heading_only_note_with_no_core_optimizations_line(self):
        note = "<H2> First Section <H2> Second Section"
        assert format_optimizations_note(note) == ["H2: First Section", "H2: Second Section"]


def test_format_month_renders_full_month_name_and_year():
    assert format_month("2026-06") == "June 2026"


def test_format_month_falls_back_to_raw_value_on_bad_input():
    assert format_month("not-a-month") == "not-a-month"


def test_format_old_new_splits_title_tag_into_old_and_new():
    result = format_old_new({"new_value": "New Title", "old_value": "Old Title"}, "title_tag")
    assert result == ("Old Title", "New Title")


def test_format_old_new_title_tag_with_no_old_value_shows_placeholder():
    result = format_old_new({"new_value": "New Title"}, "title_tag")
    assert result == ("—", "New Title")


def test_format_old_new_meta_description():
    result = format_old_new({"new_value": "New meta", "old_value": "Old meta"}, "meta_description")
    assert result == ("Old meta", "New meta")


def test_format_old_new_h1_tag():
    result = format_old_new({"new_value": "New H1", "old_value": "Old H1"}, "h1_tag")
    assert result == ("Old H1", "New H1")


def test_format_old_new_image_alt_text():
    result = format_old_new({"new_value": "Golden Retriever puppy", "old_value": "dog"}, "image_alt_text")
    assert result == ("dog", "Golden Retriever puppy")


def test_format_old_new_heading_change_shows_same_text_at_both_levels():
    result = format_old_new(
        {"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"}, "h2_h3_h4_tags"
    )
    assert result == ("<H4> Common Career Paths", "<H3> Common Career Paths")


def test_format_old_new_returns_none_for_internal_links():
    result = format_old_new(
        {"anchor_text": "our FAQs", "target_url": "https://iecrm.org/faqs/"},
        "internal_linking_to_other_pages_homepage",
    )
    assert result is None


def test_format_old_new_returns_none_for_canonical_tags():
    assert format_old_new({"new_value": "https://kyz.com/blog/"}, "canonical_tags") is None


def test_format_old_new_returns_none_for_unknown_touchpoint():
    assert format_old_new({"a": "1"}, "geo_keywords") is None
