import pytest

from seo_workbook_common.legacy_import.workbook_sheets import (
    _get_col,
    _parse_month_year,
    extract_spreadsheet_id,
    get_month_rows,
    list_workbook_months,
    read_client_details,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1AbCdEfGhIjKlMnOpQrStUvWxYz", "1AbCdEfGhIjKlMnOpQrStUvWxYz"),
        (
            "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit#gid=0",
            "1AbCdEfGhIjKlMnOpQrStUvWxYz",
        ),
        (
            "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit?usp=sharing",
            "1AbCdEfGhIjKlMnOpQrStUvWxYz",
        ),
        ("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz", "1AbCdEfGhIjKlMnOpQrStUvWxYz"),
        ("  1AbCdEfGhIjKlMnOpQrStUvWxYz  ", "1AbCdEfGhIjKlMnOpQrStUvWxYz"),
    ],
)
def test_extract_spreadsheet_id(raw, expected):
    assert extract_spreadsheet_id(raw) == expected


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
    def __init__(self, records=None, values=None, title="On-Page"):
        self._records = records or []
        self._values = values or []
        self.title = title

    def get_all_records(self, head=1, default_blank=""):
        return self._records

    def get_all_values(self):
        return self._values


class _FakeWorkbook:
    def __init__(self, worksheet, extra_worksheets: dict | None = None):
        self._worksheet = worksheet
        self._extra = extra_worksheets or {}

    def worksheet(self, name):
        import gspread

        if name == self._worksheet.title:
            return self._worksheet
        if name in self._extra:
            return self._extra[name]
        raise gspread.exceptions.WorksheetNotFound(name)

    def worksheets(self):
        return [self._worksheet, *self._extra.values()]


class _FakeSheetsClient:
    def __init__(self, worksheet, extra_worksheets: dict | None = None):
        self._worksheet = worksheet
        self._extra = extra_worksheets or {}

    def open_by_key(self, spreadsheet_id):
        return _FakeWorkbook(self._worksheet, self._extra)


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


def test_read_client_details_returns_business_name_and_website():
    client_details_ws = _FakeWorksheet(
        values=[["Client Business Name", "Dynamic Dibs"], ["Website URL", "https://dynamicdrips.com"]],
        title="Client Details",
    )
    client = _FakeSheetsClient(
        _FakeWorksheet(_SAMPLE_RECORDS), extra_worksheets={"Client Details": client_details_ws}
    )
    result = read_client_details(client, "sheet-id")
    assert result == {"client": "Dynamic Dibs", "details": {"website": "https://dynamicdrips.com"}}


def test_read_client_details_missing_tab_returns_empty_dict():
    client = _FakeSheetsClient(_FakeWorksheet(_SAMPLE_RECORDS))
    assert read_client_details(client, "sheet-id") == {"client": "", "details": {}}


def test_read_client_details_blank_value_is_ignored():
    client_details_ws = _FakeWorksheet(
        values=[["Client Business Name", ""]], title="Client Details",
    )
    client = _FakeSheetsClient(
        _FakeWorksheet(_SAMPLE_RECORDS), extra_worksheets={"Client Details": client_details_ws}
    )
    assert read_client_details(client, "sheet-id") == {"client": "", "details": {}}


def test_read_client_details_tolerates_alternate_labels():
    client_details_ws = _FakeWorksheet(
        values=[["Business Name", "Sonoran Spine"]], title="Client Details",
    )
    client = _FakeSheetsClient(
        _FakeWorksheet(_SAMPLE_RECORDS), extra_worksheets={"Client Details": client_details_ws}
    )
    result = read_client_details(client, "sheet-id")
    assert result["client"] == "Sonoran Spine"


def test_read_client_details_captures_allowlisted_account_metadata():
    client_details_ws = _FakeWorksheet(
        values=[
            ["Client Business Name", "Dynamic Dibs"],
            ["Website URL", "https://dynamicdrips.com"],
            ["Package Level", "Growth"],
            ["Project Start Date", "2026-01-15"],
            ["Other Services?", "PPC"],
            ["Account Manager", "Kevin L"],
            ["Project Manager", "PM"],
            ["SEO Strategist", "Kevin L"],
            ["Content Strategist", "CS"],
            ["Link Builder", "LB"],
        ],
        title="Client Details",
    )
    client = _FakeSheetsClient(
        _FakeWorksheet(_SAMPLE_RECORDS), extra_worksheets={"Client Details": client_details_ws}
    )
    result = read_client_details(client, "sheet-id")
    assert result["details"] == {
        "website": "https://dynamicdrips.com",
        "package_level": "Growth",
        "project_start_date": "2026-01-15",
        "other_services": "PPC",
        "account_manager": "Kevin L",
        "project_manager": "PM",
        "seo_strategist": "Kevin L",
        "content_strategist": "CS",
        "link_builder": "LB",
    }


def test_read_client_details_never_captures_login_credential_rows():
    # The tab is labeled "DO NOT PUT CREDENTIALS HERE" but a workbook could
    # ignore that — these rows must never be read into client_details
    # regardless, since it ends up in MongoDB and potentially a shared
    # report link.
    client_details_ws = _FakeWorksheet(
        values=[
            ["Client Business Name", "Dynamic Dibs"],
            ["Website Login URL", "https://dynamicdrips.com/wp-admin"],
            ["Website Username", "admin"],
            ["Website Password", "hunter2"],
        ],
        title="Client Details",
    )
    client = _FakeSheetsClient(
        _FakeWorksheet(_SAMPLE_RECORDS), extra_worksheets={"Client Details": client_details_ws}
    )
    result = read_client_details(client, "sheet-id")
    assert result["details"] == {}
