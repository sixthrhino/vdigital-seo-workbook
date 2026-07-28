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


def test_build_session_from_rows_preserves_opt_note_as_legacy_notes():
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
    notes = page.get_touchpoint("legacy_notes")
    assert notes is not None
    assert notes.items[0]["note"] == "Deep Optimizations: added internal link to homepage."
    assert notes.validation.passed is True


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
