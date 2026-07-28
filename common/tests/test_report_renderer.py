from pathlib import Path

import pytest

from seo_workbook_common.best_practices.loader import load_catalog
from seo_workbook_common.keywords import parse_keyword_target
from seo_workbook_common.models.plan_session import PlanSession, TouchpointAnswer, ValidationResult
from seo_workbook_common.output.report_renderer import render_summary_html

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "organic_qa_checklist.csv"


@pytest.fixture
def sample_session() -> PlanSession:
    session = PlanSession(session_id="kyz-2026-06", client="KYZ", month="2026-06")
    page = session.add_page("https://kyz.com/service-a/")
    page.keyword_target = parse_keyword_target("auto insurance (500)")
    page.geo = "Scottsdale, AZ"
    page.touchpoints.append(
        TouchpointAnswer(
            touchpoint_id="title_tag",
            category="Core",
            items=[{"new_value": "Auto Insurance in Scottsdale", "primary_keyword": "auto insurance"}],
            validation=ValidationResult(passed=True, messages=[]),
        )
    )
    page.touchpoints.append(
        TouchpointAnswer(
            touchpoint_id="h2_h3_h4_tags",
            category="Deep",
            items=[
                {"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"},
                {"old_tag": "h4", "new_tag": "h3", "heading_text": "How to use your GI benefits"},
            ],
            validation=ValidationResult(passed=True, messages=[]),
        )
    )
    session.add_page("https://kyz.com/service-b/")  # left empty on purpose
    return session


def test_render_summary_html_includes_client_and_pages(sample_session):
    html = render_summary_html(sample_session)
    assert "KYZ" in html
    assert "https://kyz.com/service-a/" in html
    assert "https://kyz.com/service-b/" in html
    assert "No optimizations recorded yet" in html


def test_render_summary_html_shows_keyword_and_geo(sample_session):
    html = render_summary_html(sample_session)
    assert "auto insurance" in html
    assert "vol. 500" in html
    assert "Scottsdale, AZ" in html


def test_render_summary_html_shows_multi_item_touchpoint_entries(sample_session):
    html = render_summary_html(sample_session)
    assert "H4 → H3: Common Career Paths" in html
    assert "H4 → H3: How to use your GI benefits" in html


def test_render_summary_html_uses_touchpoint_names_from_catalog(sample_session):
    catalog = load_catalog(CSV_PATH)
    html = render_summary_html(sample_session, catalog=catalog)
    assert "Title Tag" in html


def test_render_summary_html_without_catalog_falls_back_to_touchpoint_id(sample_session):
    html = render_summary_html(sample_session, catalog=None)
    assert "title_tag" in html


def test_render_summary_html_shows_a_human_readable_month(sample_session):
    # session_id ("kyz-2026-06") legitimately contains the raw "2026-06"
    # substring in the footer, so this only checks the human-readable form
    # is present — not that the raw form is absent everywhere.
    html = render_summary_html(sample_session)
    assert "June 2026" in html


def test_render_summary_html_shows_ops_standards_meet_column_as_yes(sample_session):
    html = render_summary_html(sample_session)
    assert "Ops Standards Meet" in html
    assert "Yes" in html


def test_render_summary_html_shows_ops_standards_meet_column_as_no_on_failed_validation():
    session = PlanSession(session_id="kyz-2026-06", client="KYZ", month="2026-06")
    page = session.add_page("https://kyz.com/service-a/")
    page.touchpoints.append(
        TouchpointAnswer(
            touchpoint_id="title_tag",
            category="Core",
            items=[{"new_value": "x" * 100}],
            validation=ValidationResult(passed=False, messages=["title exceeds 60 characters"]),
        )
    )
    html = render_summary_html(session)
    assert "No" in html
    assert "title exceeds 60 characters" not in html


def test_render_summary_html_includes_print_media_rule(sample_session):
    # Delivery is: open the report link in a browser, print to PDF from
    # there — no server-side PDF generation — so the print stylesheet is
    # part of the contract, not just cosmetic.
    html = render_summary_html(sample_session)
    assert "@media print" in html
