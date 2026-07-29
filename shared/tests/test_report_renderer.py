from pathlib import Path

import pytest

from seo_workbook_common.best_practices.loader import load_catalog
from seo_workbook_common.keywords import parse_keyword_target
from seo_workbook_common.models.plan_session import PlanSession, TouchpointAnswer, ValidationResult
from seo_workbook_common.output.report_renderer import render_page_table_html, render_summary_html

CSV_PATH = Path(__file__).resolve().parents[2] / "mcp-servers" / "seo-workbook-mcp" / "data" / "organic_qa_checklist.csv"


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
    # Headings render as an old/new pair (two rows), not a single arrow
    # line — see format_old_new.
    html = render_summary_html(sample_session)
    assert "Old: &lt;H4&gt; Common Career Paths" in html
    assert "New: &lt;H3&gt; Common Career Paths" in html
    assert "Old: &lt;H4&gt; How to use your GI benefits" in html
    assert "New: &lt;H3&gt; How to use your GI benefits" in html


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


def test_render_summary_html_shows_ops_standards_status_column_as_met(sample_session):
    html = render_summary_html(sample_session)
    assert "Ops Standards Status" in html
    assert "Met" in html


def test_render_summary_html_shows_ops_standards_status_column_as_missed_on_failed_validation():
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
    assert "Missed" in html
    assert "title exceeds 60 characters" not in html


def test_render_summary_html_includes_print_media_rule(sample_session):
    # Delivery is: open the report link in a browser, print to PDF from
    # there — no server-side PDF generation — so the print stylesheet is
    # part of the contract, not just cosmetic.
    html = render_summary_html(sample_session)
    assert "@media print" in html


# ---------------------------------------------------------------------------
# render_page_table_html — compact one-row-per-URL table view
# ---------------------------------------------------------------------------

def test_render_page_table_html_includes_client_and_url(sample_session):
    html = render_page_table_html(sample_session)
    assert "KYZ" in html
    assert "https://kyz.com/service-a/" in html
    assert "https://kyz.com/service-b/" in html


def test_render_page_table_html_shows_keyword_with_volume_and_geo(sample_session):
    html = render_page_table_html(sample_session)
    assert "auto insurance (500)" in html
    assert "Scottsdale, AZ" in html


def test_render_page_table_html_shows_title_as_old_new_pair():
    session = PlanSession(session_id="kyz-2026-06", client="KYZ", month="2026-06")
    page = session.add_page("https://kyz.com/a/")
    page.touchpoints.append(
        TouchpointAnswer(
            touchpoint_id="title_tag",
            category="Core",
            items=[{"new_value": "New Title", "old_value": "Old Title", "primary_keyword": "x"}],
            validation=ValidationResult(passed=True),
        )
    )
    html = render_page_table_html(session)
    assert "Old Title" in html
    assert "New Title" in html


def test_render_page_table_html_shows_placeholder_for_untouched_title(sample_session):
    # service-b has no touchpoints at all — Title/Meta/H1 columns should
    # show a placeholder, not blow up or leave a blank cell indistinguishable
    # from an empty string.
    html = render_page_table_html(sample_session)
    assert "—" in html


def test_render_page_table_html_lists_other_touchpoints_in_optimizations_column(sample_session):
    # sample_session's page has an h2_h3_h4_tags touchpoint — it should show
    # up in the Optimizations column, not get its own dedicated column.
    catalog = load_catalog(CSV_PATH)
    html = render_page_table_html(sample_session, catalog=catalog)
    assert "H2 / H3 / H4 tags" in html


def test_render_page_table_html_optimizations_column_excludes_title_meta_h1():
    session = PlanSession(session_id="kyz-2026-06", client="KYZ", month="2026-06")
    page = session.add_page("https://kyz.com/a/")
    page.touchpoints.append(
        TouchpointAnswer(
            touchpoint_id="title_tag", category="Core",
            items=[{"new_value": "New Title", "primary_keyword": "x"}],
            validation=ValidationResult(passed=True),
        )
    )
    catalog = load_catalog(CSV_PATH)
    html = render_page_table_html(session, catalog=catalog)
    # Optimizations column for this page should read "—" (nothing besides
    # title_tag was recorded), even though "Title Tag" appears elsewhere in
    # the Title column itself.
    assert "—" in html


def test_render_page_table_html_no_pages_shows_empty_state():
    session = PlanSession(session_id="kyz-2026-06", client="KYZ", month="2026-06")
    html = render_page_table_html(session)
    assert "No pages recorded yet" in html


def test_render_page_table_html_includes_print_media_rule(sample_session):
    html = render_page_table_html(sample_session)
    assert "@media print" in html
