import pytest

from seo_workbook_common.legacy_import.workbook_sheets import (
    _get_col,
    _parse_month_year,
    get_month_rows,
    list_workbook_months,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("September 2025", "2025-09"),
        ("Novemeber 2025", "2025-11"),  # common typo — first-3-letters matching tolerates it
        ("June 2026", "2026-06"),
        ("10/1/2025", "2025-10"),
        ("2025-10-01", "2025-10"),
        ("", None),
        ("Month Year", None),
        ("Make Good", None),
        ("[Focus: authority]", None),
    ],
)
def test_parse_month_year(raw, expected):
    assert _parse_month_year(raw) == expected


def test_get_col_returns_first_non_empty_alias():
    row = {"Old H1": "", "Old Headers": "Fallback Value"}
    assert _get_col(row, "Old H1", "Old Headers") == "Fallback Value"


def test_get_col_returns_empty_string_when_no_alias_present():
    assert _get_col({}, "Old H1", "Old Headers") == ""


class _FakeWorksheet:
    def __init__(self, records):
        self._records = records

    def get_all_records(self, head=1, default_blank=""):
        return self._records


class _FakeWorkbook:
    def __init__(self, worksheet):
        self._worksheet = worksheet

    def worksheet(self, name):
        return self._worksheet


class _FakeSheetsClient:
    def __init__(self, worksheet):
        self._worksheet = worksheet

    def open_by_key(self, spreadsheet_id):
        return _FakeWorkbook(self._worksheet)


_SAMPLE_RECORDS = [
    {"Month Year": "September 2025", "Optimization / URL": "[Focus: authority]"},
    {
        "Month Year": "September 2025",
        "Optimization / URL": "https://kyz.com/a/",
        "Keyword / Volume": "auto insurance (500)",
        "Target Geo": "Scottsdale, AZ",
        "What Is Planned / Has Been Done?": "Core Optimizations: Title Tag.",
        "Old Title Tag": "Old Title",
        "New Title Tag": "New Title",
    },
    {
        "Month Year": "October 2025",
        "Optimization / URL": "https://kyz.com/b/",
    },
]


def test_list_workbook_months_returns_distinct_months_in_order():
    client = _FakeSheetsClient(_FakeWorksheet(_SAMPLE_RECORDS))
    assert list_workbook_months(client, "sheet-id") == ["2025-09", "2025-10"]


def test_get_month_rows_filters_to_the_requested_month_and_skips_non_url_rows():
    client = _FakeSheetsClient(_FakeWorksheet(_SAMPLE_RECORDS))
    rows = get_month_rows(client, "sheet-id", "2025-09")
    assert len(rows) == 1
    assert rows[0]["url"] == "https://kyz.com/a/"
    assert rows[0]["keyword_raw"] == "auto insurance (500)"
    assert rows[0]["geo"] == "Scottsdale, AZ"
    assert rows[0]["old_title"] == "Old Title"
    assert rows[0]["new_title"] == "New Title"


def test_get_month_rows_returns_empty_for_a_month_not_in_the_sheet():
    client = _FakeSheetsClient(_FakeWorksheet(_SAMPLE_RECORDS))
    assert get_month_rows(client, "sheet-id", "2026-01") == []
