import pytest

from seo_workbook_common.keywords import parse_keyword_target
from seo_workbook_common.models.plan_session import PlanSession, TouchpointAnswer, ValidationResult
from seo_workbook_common.output.sheets_writer import HEADER_ROW, session_to_rows, write_rows_to_sheet


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
            validation=ValidationResult(passed=False, messages=["item 2: heading_text is required"]),
        )
    )
    session.add_page("https://kyz.com/service-b/")  # left empty on purpose
    return session


def test_session_to_rows_starts_with_header(sample_session):
    rows = session_to_rows(sample_session)
    assert rows[0] == HEADER_ROW


def test_session_to_rows_one_row_per_item(sample_session):
    rows = session_to_rows(sample_session)
    # 1 title_tag row + 2 heading rows (one per item) + 1 empty-page row = 4 data rows
    assert len(rows) == 1 + 4


def test_session_to_rows_splits_multi_item_touchpoint_into_separate_rows(sample_session):
    rows = session_to_rows(sample_session)
    heading_rows = [r for r in rows if r[7] == "h2_h3_h4_tags"]
    assert len(heading_rows) == 2
    details = {r[8] for r in heading_rows}
    assert details == {"H4 → H3: Common Career Paths", "H4 → H3: How to use your GI benefits"}


def test_session_to_rows_includes_keyword_geo_and_validation(sample_session):
    rows = session_to_rows(sample_session)
    title_row = next(r for r in rows if r[7] == "title_tag")
    assert title_row[3] == "auto insurance"  # Keyword
    assert title_row[4] == "500"  # Search Volume
    assert title_row[5] == "Scottsdale, AZ"  # Geo
    assert title_row[9] == "Passed"  # Validation

    failed_heading_row = next(r for r in rows if r[7] == "h2_h3_h4_tags")
    assert "heading_text is required" in failed_heading_row[9]


def test_session_to_rows_flags_pages_with_no_touchpoints(sample_session):
    rows = session_to_rows(sample_session)
    empty_page_row = next(r for r in rows if r[2] == "https://kyz.com/service-b/")
    assert "no optimizations recorded" in empty_page_row[8]


def test_session_to_rows_uses_touchpoint_name_resolver(sample_session):
    rows = session_to_rows(sample_session, touchpoint_name=lambda tp_id: tp_id.upper())
    title_row = next(r for r in rows if r[2] == "https://kyz.com/service-a/" and "TITLE_TAG" in r)
    assert title_row[7] == "TITLE_TAG"


class _FakeValues:
    def __init__(self):
        self.update_calls = []

    def update(self, *, spreadsheetId, range, valueInputOption, body):
        self.update_calls.append(
            {"spreadsheetId": spreadsheetId, "range": range, "valueInputOption": valueInputOption, "body": body}
        )
        return _FakeRequest()


class _FakeRequest:
    def execute(self):
        return {"updatedCells": 42}


class _FakeSpreadsheets:
    def __init__(self):
        self.values_resource = _FakeValues()

    def values(self):
        return self.values_resource


class _FakeSheetsService:
    def __init__(self):
        self.spreadsheets_resource = _FakeSpreadsheets()

    def spreadsheets(self):
        return self.spreadsheets_resource


def test_write_rows_to_sheet_calls_update_with_expected_shape(sample_session):
    rows = session_to_rows(sample_session)
    fake_service = _FakeSheetsService()

    result = write_rows_to_sheet(fake_service, "sheet-123", rows)

    assert result == {"updatedCells": 42}
    call = fake_service.spreadsheets_resource.values_resource.update_calls[0]
    assert call["spreadsheetId"] == "sheet-123"
    assert call["range"] == "A1"
    assert call["valueInputOption"] == "RAW"
    assert call["body"] == {"values": rows}
