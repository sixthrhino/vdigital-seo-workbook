import pytest

from seo_workbook_common.page_capture import parse_page_capture


def test_parses_all_fields():
    text = (
        "url: https://example.com/service-a/\n"
        "keyword: auto insurance (500)\n"
        "geo: Scottsdale, AZ\n"
        "title: Old Title Tag -> New Title Tag\n"
        "meta: Old meta description -> New meta description\n"
        "cta: Get a Quote\n"
        "h1: Old H1 -> New H1\n"
        "notes: Added internal link to homepage."
    )
    result = parse_page_capture(text)
    assert result.url == "https://example.com/service-a/"
    assert result.keyword == "auto insurance (500)"
    assert result.geo == "Scottsdale, AZ"
    assert result.title_old == "Old Title Tag"
    assert result.title_new == "New Title Tag"
    assert result.meta_old == "Old meta description"
    assert result.meta_new == "New meta description"
    assert result.cta == "Get a Quote"
    assert result.h1_old == "Old H1"
    assert result.h1_new == "New H1"
    assert result.notes == "Added internal link to homepage."


def test_missing_url_raises():
    with pytest.raises(ValueError, match="Missing \"url:\" line"):
        parse_page_capture("title: New Title Only")


def test_url_only_leaves_everything_else_none():
    result = parse_page_capture("url: https://example.com/a/")
    assert result.url == "https://example.com/a/"
    assert result.keyword is None
    assert result.geo is None
    assert result.title_old is None
    assert result.title_new is None
    assert result.meta_old is None
    assert result.meta_new is None
    assert result.cta is None
    assert result.h1_old is None
    assert result.h1_new is None
    assert result.notes is None


def test_title_with_no_arrow_is_new_value_only():
    result = parse_page_capture("url: https://example.com/a/\ntitle: New Title Only")
    assert result.title_old is None
    assert result.title_new == "New Title Only"


def test_unicode_arrow_also_works():
    result = parse_page_capture("url: https://example.com/a/\ntitle: Old Title → New Title")
    assert result.title_old == "Old Title"
    assert result.title_new == "New Title"


def test_does_not_split_on_bare_to_inside_title_text():
    # "to" is a common English word inside real titles/metas — must not be
    # treated as an old/new separator the way "->" is.
    result = parse_page_capture("url: https://example.com/a/\ntitle: Guide to Trailer Maintenance")
    assert result.title_old is None
    assert result.title_new == "Guide to Trailer Maintenance"


def test_notes_preserves_its_own_line_breaks():
    text = (
        "url: https://example.com/a/\n"
        "notes: First line.\n"
        "\n"
        "Second paragraph.\n"
        "Third line."
    )
    result = parse_page_capture(text)
    assert result.notes == "First line.\n\nSecond paragraph.\nThird line."


def test_notes_must_be_last_everything_after_it_is_captured():
    text = "url: https://example.com/a/\nnotes: some note\nkeyword: this looks like a label but isn't parsed as one"
    result = parse_page_capture(text)
    assert result.notes == "some note\nkeyword: this looks like a label but isn't parsed as one"


def test_label_aliases_are_case_insensitive_and_tolerant():
    text = (
        "URL: https://example.com/a/\n"
        "Primary Keyword: widgets\n"
        "Target Geo: Dallas, TX\n"
        "Title Tag: Old -> New\n"
        "Meta Description: Old meta -> New meta\n"
        "H1 Tag: Old H1 -> New H1"
    )
    result = parse_page_capture(text)
    assert result.keyword == "widgets"
    assert result.geo == "Dallas, TX"
    assert result.title_new == "New"
    assert result.meta_new == "New meta"
    assert result.h1_new == "New H1"


def test_unrecognized_lines_are_ignored():
    text = "url: https://example.com/a/\nthis is just a stray line\ntitle: Old -> New"
    result = parse_page_capture(text)
    assert result.title_new == "New"


def test_arrow_with_empty_old_side_treated_as_new_only():
    # "-> New Title" (nothing before the arrow) shouldn't produce a blank
    # old_value — same as omitting the arrow entirely.
    result = parse_page_capture("url: https://example.com/a/\ntitle: -> New Title")
    assert result.title_old is None
    assert result.title_new == "New Title"


def test_no_headings_label_leaves_heading_items_empty():
    result = parse_page_capture("url: https://example.com/a/")
    assert result.heading_items == []


def test_headings_block_parses_old_and_new_tag_lines():
    text = (
        "url: https://example.com/a/\n"
        "headings: H2 -> H3: Checking Over Your Trailer\n"
        "H3: Emergency Equipment"
    )
    result = parse_page_capture(text)
    assert result.heading_items == [
        {"old_tag": "h2", "new_tag": "h3", "heading_text": "Checking Over Your Trailer"},
        {"new_tag": "h3", "heading_text": "Emergency Equipment"},
    ]


def test_headings_unicode_arrow_also_works():
    result = parse_page_capture("url: https://example.com/a/\nheadings: H2 → H3: Checking Over Your Trailer")
    assert result.heading_items == [{"old_tag": "h2", "new_tag": "h3", "heading_text": "Checking Over Your Trailer"}]


def test_headings_block_stops_at_the_next_recognized_label():
    # headings doesn't have to be the last label — unlike notes, it should
    # stop consuming lines once another recognized label line shows up.
    text = (
        "url: https://example.com/a/\n"
        "headings: H3: Emergency Equipment\n"
        "notes: Some free-form note."
    )
    result = parse_page_capture(text)
    assert result.heading_items == [{"new_tag": "h3", "heading_text": "Emergency Equipment"}]
    assert result.notes == "Some free-form note."


def test_headings_block_can_come_before_or_after_other_single_line_labels():
    text = (
        "url: https://example.com/a/\n"
        "headings: H3: Emergency Equipment\n"
        "title: Old Title -> New Title"
    )
    result = parse_page_capture(text)
    assert result.heading_items == [{"new_tag": "h3", "heading_text": "Emergency Equipment"}]
    assert result.title_new == "New Title"


def test_headings_block_skips_blank_lines_without_terminating():
    text = (
        "url: https://example.com/a/\n"
        "headings: H2 -> H3: Checking Over Your Trailer\n"
        "\n"
        "H3: Emergency Equipment"
    )
    result = parse_page_capture(text)
    assert result.heading_items == [
        {"old_tag": "h2", "new_tag": "h3", "heading_text": "Checking Over Your Trailer"},
        {"new_tag": "h3", "heading_text": "Emergency Equipment"},
    ]


def test_headings_block_ignores_unrecognized_lines_within_it():
    text = (
        "url: https://example.com/a/\n"
        "headings: not a heading line at all\n"
        "H3: Emergency Equipment"
    )
    result = parse_page_capture(text)
    assert result.heading_items == [{"new_tag": "h3", "heading_text": "Emergency Equipment"}]
