from seo_workbook_common.legacy_import.converter import build_session_from_rows
from seo_workbook_common.models.plan_session import SessionStatus


def test_build_session_from_rows_sets_session_identity():
    rows = [
        {
            "url": "https://kyz.com/a/",
            "keyword_raw": "auto insurance (500)",
            "geo": "Scottsdale, AZ",
            "opt_note": "Core Optimizations: Title Tag, Meta Description.",
            "old_title": "Old Title",
            "new_title": "New Title",
            "old_meta": "",
            "new_meta": "",
            "old_h1": "",
            "new_h1": "",
        }
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    assert session.session_id == "kyz-2025-10"
    assert session.client == "KYZ"
    assert session.month == "2025-10"
    assert session.status == SessionStatus.FINALIZED


def test_build_session_from_rows_creates_title_tag_touchpoint_with_old_new_and_keyword():
    rows = [
        {
            "url": "https://kyz.com/a/",
            "keyword_raw": "auto insurance (500)",
            "geo": "",
            "opt_note": "",
            "old_title": "Old Title",
            "new_title": "New Title",
            "old_meta": "",
            "new_meta": "",
            "old_h1": "",
            "new_h1": "",
        }
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    page = session.pages[0]
    touchpoint = page.get_touchpoint("title_tag")
    assert touchpoint is not None
    assert touchpoint.items[0] == {
        "new_value": "New Title",
        "old_value": "Old Title",
        "primary_keyword": "auto insurance",
    }
    assert page.keyword_target.keyword == "auto insurance"
    assert page.keyword_target.search_volume == 500


def test_build_session_from_rows_skips_unchanged_or_placeholder_values():
    rows = [
        {
            "url": "https://kyz.com/a/",
            "keyword_raw": "",
            "geo": "",
            "opt_note": "",
            "old_title": "Same Title",
            "new_title": "Same Title",  # unchanged
            "old_meta": "N/A",
            "new_meta": "N/A",  # placeholder
            "old_h1": "Old H1",
            "new_h1": "No changes",  # legacy sheet's "nothing changed" marker
        }
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    page = session.pages[0]
    assert page.get_touchpoint("title_tag") is None
    assert page.get_touchpoint("meta_description") is None
    assert page.get_touchpoint("h1_tag") is None


def test_build_session_from_rows_preserves_opt_note_as_optimizations_touchpoint():
    rows = [
        {
            "url": "https://kyz.com/a/",
            "keyword_raw": "",
            "geo": "",
            "opt_note": "Deep Optimizations: added internal link to homepage.",
            "old_title": "",
            "new_title": "",
            "old_meta": "",
            "new_meta": "",
            "old_h1": "",
            "new_h1": "",
        }
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    page = session.pages[0]
    notes = page.get_touchpoint("optimizations")
    assert notes is not None
    assert notes.category == "Optimizations"
    assert notes.items[0]["note"] == "Deep Optimizations: added internal link to homepage."
    assert notes.validation.passed is True


def test_build_session_from_rows_preserves_opt_note_line_breaks():
    # Line breaks are meaningful structure in the source cell (paragraph/
    # section breaks) — needed to correctly read the note back, not
    # incidental formatting to collapse away.
    rows = [
        {
            "url": "https://kyz.com/a/",
            "keyword_raw": "",
            "geo": "",
            "opt_note": (
                "Core Optimizations: Schema Markup.\n\n"
                "Some general notes here.\n\n"
                "More notes on a second line.\n"
            ),
            "old_title": "",
            "new_title": "",
            "old_meta": "",
            "new_meta": "",
            "old_h1": "",
            "new_h1": "",
        }
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    notes = session.pages[0].get_touchpoint("optimizations")
    assert notes.items[0]["note"] == (
        "Core Optimizations: Schema Markup\n\n"
        "Some general notes here.\n\n"
        "More notes on a second line."
    )


def test_build_session_from_rows_promotes_bracket_marker_headings_to_a_real_touchpoint():
    # "<H#> heading text" is the one shape reliable enough to parse without
    # fabricating structure (old_tag is never stated, but it's optional —
    # see validators.py) — promoted to a real h2_h3_h4_tags touchpoint
    # instead of staying free text.
    rows = [
        {
            "url": "https://www.northtexastrailers.com/blog/trailer-springs/",
            "keyword_raw": "",
            "geo": "",
            "opt_note": (
                "Should not be a blog post.\n"
                "Core Optimizations: Title Tag, Meta Description, H1.\n\n"
                "Make Headers below an <H2> tag\n\n"
                "<H3> Checking Over Your Trailer\n"
                "<H3> Emergency Equipment"
            ),
            "old_title": "", "new_title": "", "old_meta": "", "new_meta": "",
            "old_h1": "", "new_h1": "",
        }
    ]
    session = build_session_from_rows("North Texas Trailers", "2026-07", rows)
    page = session.pages[0]

    headings = page.get_touchpoint("h2_h3_h4_tags")
    assert headings is not None
    assert headings.category == "Deep"
    assert headings.items == [
        {"new_tag": "h3", "heading_text": "Checking Over Your Trailer"},
        {"new_tag": "h3", "heading_text": "Emergency Equipment"},
    ]
    assert headings.validation.passed is True

    notes = page.get_touchpoint("optimizations")
    assert "<H3>" not in notes.items[0]["note"]
    assert "Should not be a blog post." in notes.items[0]["note"]
    assert "Make Headers below an <H2> tag" in notes.items[0]["note"]
    # Redundant with the dedicated Title/Meta/H1 columns — stripped from
    # the *stored* note itself, not just filtered when a report renders it.
    assert "Core Optimizations" not in notes.items[0]["note"]


def test_build_session_from_rows_promotes_change_to_prose_with_old_and_new_tag():
    # "Change <H#> heading text to an <H#> tag." states the old level too
    # (unlike the bare "<H#> text" shape), so old_tag is populated here.
    rows = [
        {
            "url": "https://example.com/trailer-suspension/",
            "keyword_raw": "", "geo": "",
            "opt_note": (
                "Core Optimizations: Title Tag, Meta Description, H1.\n\n"
                "Change <H1> Signs to Look for and How to Maintain Your Trailer Suspension "
                "to an <H2> tag.\n\n"
                "Change <H3> What is Trailer Suspension? to an <H2> tag."
            ),
            "old_title": "", "new_title": "", "old_meta": "", "new_meta": "",
            "old_h1": "", "new_h1": "",
        }
    ]
    session = build_session_from_rows("Test", "2026-07", rows)
    page = session.pages[0]

    headings = page.get_touchpoint("h2_h3_h4_tags")
    assert headings.items == [
        {"old_tag": "h1", "new_tag": "h2", "heading_text": "Signs to Look for and How to Maintain Your Trailer Suspension"},
        {"old_tag": "h3", "new_tag": "h2", "heading_text": "What is Trailer Suspension?"},
    ]

    from seo_workbook_common.validators import validate_touchpoint
    assert validate_touchpoint("h2_h3_h4_tags", headings.items).passed is True

    # Nothing non-redundant survived the Core Optimizations sentence, and
    # both "Change ..." sentences were fully extracted — no optimizations
    # touchpoint left at all.
    assert page.get_touchpoint("optimizations") is None


def test_build_session_from_rows_heading_items_pass_real_validation():
    from seo_workbook_common.validators import validate_touchpoint

    rows = [
        {
            "url": "https://kyz.com/a/", "keyword_raw": "", "geo": "",
            "opt_note": "<H3> Why Choose Us?",
            "old_title": "", "new_title": "", "old_meta": "", "new_meta": "",
            "old_h1": "", "new_h1": "",
        }
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    headings = session.pages[0].get_touchpoint("h2_h3_h4_tags")
    assert validate_touchpoint("h2_h3_h4_tags", headings.items).passed is True


def test_build_session_from_rows_no_optimizations_touchpoint_when_note_is_only_headings():
    rows = [
        {
            "url": "https://kyz.com/a/", "keyword_raw": "", "geo": "",
            "opt_note": "<H3> Why Choose Us?\n<H3> Our Services",
            "old_title": "", "new_title": "", "old_meta": "", "new_meta": "",
            "old_h1": "", "new_h1": "",
        }
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    page = session.pages[0]
    assert page.get_touchpoint("h2_h3_h4_tags") is not None
    assert page.get_touchpoint("optimizations") is None


def test_build_session_from_rows_note_with_no_bracket_headings_is_untouched():
    rows = [
        {
            "url": "https://kyz.com/a/", "keyword_raw": "", "geo": "",
            "opt_note": "Change H1: Old Heading to an H2: tag.",
            "old_title": "", "new_title": "", "old_meta": "", "new_meta": "",
            "old_h1": "", "new_h1": "",
        }
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    page = session.pages[0]
    assert page.get_touchpoint("h2_h3_h4_tags") is None
    notes = page.get_touchpoint("optimizations")
    assert notes.items[0]["note"] == "Change H1: Old Heading to an H2: tag."


def test_build_session_from_rows_collapses_repeated_blank_lines_to_one():
    rows = [
        {
            "url": "https://kyz.com/a/",
            "keyword_raw": "",
            "geo": "",
            "opt_note": "First line.\n\n\n\nSecond line.",
            "old_title": "", "new_title": "", "old_meta": "", "new_meta": "",
            "old_h1": "", "new_h1": "",
        }
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    notes = session.pages[0].get_touchpoint("optimizations")
    assert notes.items[0]["note"] == "First line.\n\nSecond line."


def test_build_session_from_rows_merges_rows_for_the_same_url():
    rows = [
        {
            "url": "https://kyz.com/a/",
            "keyword_raw": "",
            "geo": "",
            "opt_note": "",
            "old_title": "Old Title",
            "new_title": "New Title",
            "old_meta": "",
            "new_meta": "",
            "old_h1": "",
            "new_h1": "",
        },
        {
            "url": "https://kyz.com/a/",
            "keyword_raw": "",
            "geo": "",
            "opt_note": "",
            "old_title": "",
            "new_title": "",
            "old_meta": "Old Meta",
            "new_meta": "New Meta " * 20,
            "old_h1": "",
            "new_h1": "",
        },
    ]
    session = build_session_from_rows("KYZ", "2025-10", rows)
    assert len(session.pages) == 1
    page = session.pages[0]
    assert page.get_touchpoint("title_tag") is not None
    assert page.get_touchpoint("meta_description") is not None
