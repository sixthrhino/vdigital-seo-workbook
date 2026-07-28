"""
Tests for sheets.py pure helper functions (no Google API calls).
"""

import pytest
import gspread.exceptions

import seo_testing_mcp.tools.sheets as sheets_module
from seo_testing_mcp.tools.sheets import (
    _parse_month_year, _clean_keyword, _is_url,
    _normalize_sheet_name, _find_worksheet, _get_col,
    get_spreadsheet_title, _rows_to_brand_guide_text, read_brand_guide_tab,
    _extract_client_details, read_client_details,
)


class TestParseMonthYear:
    # Standard "Month YYYY" format
    def test_full_month_name(self):
        assert _parse_month_year("August 2025") == (8, 2025)

    def test_full_month_name_october(self):
        assert _parse_month_year("October 2024") == (10, 2024)

    def test_full_month_name_december(self):
        assert _parse_month_year("December 2026") == (12, 2026)

    def test_full_month_name_january(self):
        assert _parse_month_year("January 2025") == (1, 2025)

    # Typos (workbook cells often have misspellings)
    def test_typo_november(self):
        assert _parse_month_year("Novemeber 2025") == (11, 2025)

    def test_typo_february(self):
        assert _parse_month_year("Feburary 2025") == (2, 2025)

    # Date-like formats
    def test_slash_date_with_day(self):
        assert _parse_month_year("10/1/2025") == (10, 2025)

    def test_slash_date_no_day(self):
        assert _parse_month_year("6/2026") == (6, 2026)

    def test_iso_date(self):
        assert _parse_month_year("2025-10-01") == (10, 2025)

    def test_iso_date_march(self):
        assert _parse_month_year("2024-03-15") == (3, 2024)

    # Separator / empty rows
    def test_separator_row_returns_none(self):
        assert _parse_month_year("Month Year") is None

    def test_empty_string_returns_none(self):
        assert _parse_month_year("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_month_year("   ") is None

    def test_garbage_returns_none(self):
        assert _parse_month_year("N/A") is None

    def test_numeric_only_returns_none(self):
        assert _parse_month_year("2025") is None


class TestCleanKeyword:
    def test_strips_volume_with_k_suffix(self):
        assert _clean_keyword("plumber austin (2.5k)") == "plumber austin"

    def test_strips_numeric_volume(self):
        assert _clean_keyword("car accident lawyer (170)") == "car accident lawyer"

    def test_no_change_when_no_parens(self):
        assert _clean_keyword("roofing contractor") == "roofing contractor"

    def test_preserves_parens_mid_keyword(self):
        assert _clean_keyword("lawyer (car accident) services (50)") == "lawyer (car accident) services"

    def test_empty_string(self):
        assert _clean_keyword("") == ""

    def test_strips_extra_whitespace(self):
        assert _clean_keyword("  dentist near me  (400)  ") == "dentist near me"

    def test_strips_bare_volume_kd_suffix(self):
        assert _clean_keyword("furnace repair 194000/KD 0") == "furnace repair"

    def test_strips_bare_volume_kd_with_comma_and_lowercase(self):
        assert _clean_keyword("furnace repair 194,000/kd 45") == "furnace repair"

    def test_strips_parenthesized_volume_kd(self):
        assert _clean_keyword("furnace repair (194000/KD 0)") == "furnace repair"

    def test_strips_bare_volume_kd_with_k_suffix(self):
        assert _clean_keyword("furnace repair 1.4K/KD 12") == "furnace repair"


class TestIsUrl:
    def test_https_url(self):
        assert _is_url("https://example.com/page") is True

    def test_http_url(self):
        assert _is_url("http://example.com/") is True

    def test_empty_string_is_not_url(self):
        assert _is_url("") is False

    def test_plain_text_is_not_url(self):
        assert _is_url("just some text") is False

    def test_none_is_not_url(self):
        assert _is_url(None) is False

    def test_leading_whitespace_still_matches(self):
        assert _is_url("  https://example.com") is True


class TestNormalizeSheetName:
    def test_strips_asterisks(self):
        assert _normalize_sheet_name("**On-Page") == "onpage"

    def test_plain_name_matches_decorated_one(self):
        assert _normalize_sheet_name("On-Page") == _normalize_sheet_name("**On-Page")

    def test_case_insensitive(self):
        assert _normalize_sheet_name("ON-PAGE") == _normalize_sheet_name("on-page")

    def test_strips_underscores_and_whitespace(self):
        assert _normalize_sheet_name("On_Page ") == "onpage"


class _FakeWorksheet:
    def __init__(self, title):
        self.title = title


class _FakeWorkbook:
    def __init__(self, titles):
        self._titles = titles

    def worksheet(self, name):
        for title in self._titles:
            if title == name:
                return _FakeWorksheet(title)
        raise gspread.exceptions.WorksheetNotFound(name)

    def worksheets(self):
        return [_FakeWorksheet(t) for t in self._titles]


class TestFindWorksheet:
    def test_exact_match_returned_directly(self):
        wb = _FakeWorkbook(["On-Page", "Summary"])
        ws = _find_worksheet(wb, "On-Page")
        assert ws.title == "On-Page"

    def test_falls_back_to_normalized_match_for_decorated_tab_name(self):
        wb = _FakeWorkbook(["**On-Page", "Summary"])
        ws = _find_worksheet(wb, "On-Page")
        assert ws.title == "**On-Page"

    def test_raises_when_no_match_at_all(self):
        wb = _FakeWorkbook(["Summary"])
        with pytest.raises(gspread.exceptions.WorksheetNotFound):
            _find_worksheet(wb, "On-Page")


class TestGetCol:
    def test_returns_first_matching_alias(self):
        assert _get_col({"Month Year": "August 2025"}, "Mo - Yr", "Month Year") == "August 2025"

    def test_prefers_earlier_alias_when_both_present(self):
        row = {"Mo - Yr": "August 2025", "Month Year": "September 2025"}
        assert _get_col(row, "Mo - Yr", "Month Year") == "August 2025"

    def test_skips_empty_values_for_earlier_alias(self):
        row = {"Mo - Yr": "", "Month Year": "August 2025"}
        assert _get_col(row, "Mo - Yr", "Month Year") == "August 2025"

    def test_returns_empty_string_when_no_alias_matches(self):
        assert _get_col({"Other": "x"}, "Mo - Yr", "Month Year") == ""


class TestGetSpreadsheetTitle:
    def test_returns_title_from_workbook(self, monkeypatch):
        class _FakeSpreadsheet:
            title = "IEC Rocky Mountain (main) | Organic SEO Workbook"

        class _FakeClient:
            def open_by_key(self, spreadsheet_id):
                assert spreadsheet_id == "sheet-id-123"
                return _FakeSpreadsheet()

        monkeypatch.setattr(sheets_module, "_client", lambda: _FakeClient())

        assert get_spreadsheet_title("sheet-id-123") == "IEC Rocky Mountain (main) | Organic SEO Workbook"


class TestRowsToBrandGuideText:
    def test_joins_row_cells_with_tabs(self):
        rows = [["Branding", "Acme Corp"], ["CTA", "https://example.com"]]
        assert _rows_to_brand_guide_text(rows) == "Branding\tAcme Corp\nCTA\thttps://example.com"

    def test_trims_trailing_empty_cells(self):
        rows = [["Branding", "Acme Corp", "", ""]]
        assert _rows_to_brand_guide_text(rows) == "Branding\tAcme Corp"

    def test_skips_fully_empty_rows(self):
        rows = [["Branding", "Acme Corp"], ["", "", ""], ["CTA", "https://example.com"]]
        assert _rows_to_brand_guide_text(rows) == "Branding\tAcme Corp\nCTA\thttps://example.com"

    def test_empty_input_returns_empty_string(self):
        assert _rows_to_brand_guide_text([]) == ""


class _FakeBrandGuideWorksheet:
    def __init__(self, title, rows):
        self.title = title
        self._rows = rows

    def get_all_values(self):
        return self._rows


class _FakeBrandGuideWorkbook:
    def __init__(self, sheets: dict):
        self._sheets = sheets

    def worksheet(self, name):
        if name in self._sheets:
            return _FakeBrandGuideWorksheet(name, self._sheets[name])
        raise gspread.exceptions.WorksheetNotFound(name)

    def worksheets(self):
        return [_FakeBrandGuideWorksheet(t, r) for t, r in self._sheets.items()]


class TestReadBrandGuideTab:
    def test_reads_and_joins_brand_guide_tab(self, monkeypatch):
        wb = _FakeBrandGuideWorkbook({
            "Brand Guide": [["Branding", "Acme Corp"], ["CTA", "https://example.com"]],
        })

        class _FakeClient:
            def open_by_key(self, spreadsheet_id):
                return wb

        monkeypatch.setattr(sheets_module, "_client", lambda: _FakeClient())

        result = read_brand_guide_tab("sheet-id-123")
        assert result == "Branding\tAcme Corp\nCTA\thttps://example.com"

    def test_returns_empty_string_when_tab_missing(self, monkeypatch):
        wb = _FakeBrandGuideWorkbook({"On-Page": [["a"]]})

        class _FakeClient:
            def open_by_key(self, spreadsheet_id):
                return wb

        monkeypatch.setattr(sheets_module, "_client", lambda: _FakeClient())

        assert read_brand_guide_tab("sheet-id-123") == ""


class TestExtractClientDetails:
    def test_reads_business_name_and_website(self):
        rows = [
            ["Client Business Name", "Sonoran Spine"],
            ["Website URL", "https://www.sonoranspine.com/"],
        ]
        assert _extract_client_details(rows) == {
            "client": "Sonoran Spine", "website": "https://www.sonoranspine.com/",
        }

    def test_accepts_label_aliases(self):
        assert _extract_client_details([["Client Name", "Acme Corp"]])["client"] == "Acme Corp"
        assert _extract_client_details([["Business Name", "Acme Corp"]])["client"] == "Acme Corp"
        assert _extract_client_details([["Website", "https://acme.com"]])["website"] == "https://acme.com"

    def test_label_matching_is_case_insensitive_and_strips_colon(self):
        rows = [["client business name:", "Acme Corp"]]
        assert _extract_client_details(rows)["client"] == "Acme Corp"

    def test_ignores_unrecognized_labels(self):
        rows = [["Package Level", "Standard"], ["Project Start Date", "2023-10-01"]]
        assert _extract_client_details(rows) == {"client": "", "website": ""}

    def test_skips_short_rows(self):
        assert _extract_client_details([["Client Business Name"], []]) == {"client": "", "website": ""}

    def test_empty_input_returns_blank_structure(self):
        assert _extract_client_details([]) == {"client": "", "website": ""}


class TestReadClientDetails:
    def test_reads_client_details_tab(self, monkeypatch):
        wb = _FakeBrandGuideWorkbook({
            "Client Details": [["Client Business Name", "Sonoran Spine"],
                                ["Website URL", "https://www.sonoranspine.com/"]],
        })

        class _FakeClient:
            def open_by_key(self, spreadsheet_id):
                return wb

        monkeypatch.setattr(sheets_module, "_client", lambda: _FakeClient())

        assert read_client_details("sheet-id-123") == {
            "client": "Sonoran Spine", "website": "https://www.sonoranspine.com/",
        }

    def test_returns_blank_structure_when_tab_missing(self, monkeypatch):
        wb = _FakeBrandGuideWorkbook({"On-Page": [["a"]]})

        class _FakeClient:
            def open_by_key(self, spreadsheet_id):
                return wb

        monkeypatch.setattr(sheets_module, "_client", lambda: _FakeClient())

        assert read_client_details("sheet-id-123") == {"client": "", "website": ""}
