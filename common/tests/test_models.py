import pytest
from pydantic import ValidationError

from seo_workbook_common.models.plan_session import (
    PlanSession,
    SessionStatus,
    TouchpointAnswer,
    ValidationResult,
)
from seo_workbook_common.keywords import parse_keyword_target


def _make_session(**overrides) -> PlanSession:
    defaults = {"session_id": "kyz-2026-06", "client": "KYZ", "month": "2026-06"}
    defaults.update(overrides)
    return PlanSession(**defaults)


def test_create_session_defaults_to_draft():
    session = _make_session()
    assert session.status == SessionStatus.DRAFT
    assert session.pages == []


def test_add_page_appends_and_returns_it():
    session = _make_session()
    page = session.add_page("https://kyz.com/service-a/")
    assert session.pages == [page]
    assert page.url == "https://kyz.com/service-a/"
    assert page.keyword_target is None
    assert page.geo is None


def test_page_keyword_target_and_geo_are_settable():
    session = _make_session()
    page = session.add_page("https://iecrm.org/locations/cheyenne-wyoming/")
    page.keyword_target = parse_keyword_target("electrician apprenticeship (25k)")
    page.geo = "Cheyenne, WY"

    assert page.keyword_target.keyword == "electrician apprenticeship"
    assert page.keyword_target.search_volume == 25000
    assert page.geo == "Cheyenne, WY"


def test_add_duplicate_page_raises():
    session = _make_session()
    session.add_page("https://kyz.com/a/")
    with pytest.raises(ValueError):
        session.add_page("https://kyz.com/a/")


def test_get_page_returns_none_when_missing():
    session = _make_session()
    assert session.get_page("https://kyz.com/nope/") is None


@pytest.mark.parametrize("bad_month", ["June 2026", "2026-13", "2026-6", "26-06"])
def test_invalid_month_format_rejected(bad_month: str):
    with pytest.raises(ValidationError):
        _make_session(month=bad_month)


def test_open_questions_flags_pages_with_no_touchpoints():
    session = _make_session()
    session.add_page("https://kyz.com/a/")
    issues = session.open_questions()
    assert len(issues) == 1
    assert "no optimizations recorded" in issues[0]


def test_open_questions_flags_failed_validation():
    session = _make_session()
    page = session.add_page("https://kyz.com/a/")
    page.touchpoints.append(
        TouchpointAnswer(
            touchpoint_id="title_tag",
            category="Core",
            items=[{"new_value": "x"}],
            validation=ValidationResult(passed=False, messages=["too short"]),
        )
    )
    issues = session.open_questions()
    assert len(issues) == 1
    assert "title_tag" in issues[0]
    assert "too short" in issues[0]


def test_touchpoint_answer_supports_multiple_items():
    # Four heading promotions + two internal links bundled in one legacy
    # cell now become six clean, independently-checkable items across two
    # touchpoints instead of one ambiguous free-text blob.
    headings = TouchpointAnswer(
        touchpoint_id="h2_h3_h4_tags",
        category="Deep",
        items=[
            {"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"},
            {"old_tag": "h4", "new_tag": "h3", "heading_text": "How to use your GI benefits"},
        ],
    )
    assert len(headings.items) == 2

    links = TouchpointAnswer(
        touchpoint_id="internal_linking_to_other_pages_homepage",
        category="Deep",
        items=[
            {"anchor_text": "our FAQs", "target_url": "https://iecrm.org/faqs/"},
            {"anchor_text": "continuing education", "target_url": "https://iecrm.org/continued-education/"},
        ],
    )
    assert len(links.items) == 2


def test_is_complete_true_only_when_every_page_and_touchpoint_pass():
    session = _make_session()
    page = session.add_page("https://kyz.com/a/")
    assert not session.is_complete()

    page.touchpoints.append(
        TouchpointAnswer(
            touchpoint_id="title_tag",
            category="Core",
            items=[{"new_value": "Good Title", "primary_keyword": "insurance"}],
            validation=ValidationResult(passed=True, messages=[]),
        )
    )
    assert session.is_complete()


def test_round_trip_json_serialization():
    session = _make_session()
    page = session.add_page("https://kyz.com/a/")
    page.keyword_target = parse_keyword_target("insurance quotes (500)")
    page.geo = "Denver, CO"
    restored = PlanSession.model_validate_json(session.model_dump_json())
    assert restored == session
