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


def test_heading_change_with_no_old_tag_shows_just_the_target_level():
    result = format_item({"new_tag": "h3", "heading_text": "Common Career Paths"}, "h2_h3_h4_tags")
    assert result == "H3: Common Career Paths"


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

    def test_single_line_note_with_no_embedded_breaks_stays_one_entry(self):
        # Deliberately NOT parsed into per-heading lines — real historical
        # notes use too many different phrasings (bracket markers, "Change
        # H1: ... to an H2: tag." prose, and surely others) to reconstruct
        # reliably; trying produced garbled output on some of them. Only
        # the Core Optimizations summary is special-cased; a note with no
        # line breaks of its own stays as one entry.
        note = (
            "Core Optimizations: Title Tag, Meta Description, H1. "
            "Change H1: Signs to Look for and How to Maintain Your Trailer Suspension to an "
            "H2: tag. Change H3: What is Trailer Suspension? to an H2: tag."
        )
        result = format_optimizations_note(note)
        assert result == [
            "Change H1: Signs to Look for and How to Maintain Your Trailer Suspension to an "
            "H2: tag. Change H3: What is Trailer Suspension? to an H2: tag."
        ]

    def test_line_breaks_in_the_note_are_preserved_as_separate_entries(self):
        # The note's own line breaks are meaningful section/paragraph
        # structure — needed to correctly read it back — not incidental
        # formatting, so each one becomes its own display entry rather
        # than being collapsed into one run-on paragraph. Matches what
        # legacy_import.converter._normalize_note actually stores.
        note = (
            "Should not be a blog post.\n"
            "Core Optimizations: TItle Tag, Meta Description, H1.\n\n"
            "Make Headers below an <H2> tag\n\n"
            "<H3> Why Choose North Texas Trailers?\n\n"
            "Make Headers below an <H3> tag\n\n"
            "<H4> Expertise You Can Depend On:\n"
            "<H4> Quality Parts, Every Time:\n"
            "<H4> Transparent Communication:\n"
            "<H4> Timely Execution:"
        )
        result = format_optimizations_note(note)
        assert result == [
            "Should not be a blog post.",
            "Make Headers below an <H2> tag",
            "<H3> Why Choose North Texas Trailers?",
            "Make Headers below an <H3> tag",
            "<H4> Expertise You Can Depend On:",
            "<H4> Quality Parts, Every Time:",
            "<H4> Transparent Communication:",
            "<H4> Timely Execution:",
        ]

    def test_blank_lines_are_dropped_not_kept_as_empty_entries(self):
        note = "First line.\n\n\nSecond line."
        assert format_optimizations_note(note) == ["First line.", "Second line."]

    def test_plain_text_with_no_core_optimizations_line_passes_through_unchanged(self):
        assert format_optimizations_note("Added internal link to homepage.") == [
            "Added internal link to homepage."
        ]

    def test_multiple_core_optimizations_sentences_are_all_recognized(self):
        # A note can legitimately contain more than one "Core
        # Optimizations:" sentence (e.g. two update blocks recorded in one
        # cell) — every occurrence is found and reduced/dropped, not just
        # the first.
        note = "Core Optimizations: Schema Markup. Some notes. Core Optimizations: Internal Linking. More notes."
        result = format_optimizations_note(note)
        assert result == [
            "Core Optimizations: Schema Markup",
            "Core Optimizations: Internal Linking",
            "Some notes. More notes.",
        ]


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


def test_format_old_new_heading_change_with_no_old_tag_shows_placeholder_for_old():
    result = format_old_new({"new_tag": "h3", "heading_text": "Common Career Paths"}, "h2_h3_h4_tags")
    assert result == ("—", "<H3> Common Career Paths")


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
