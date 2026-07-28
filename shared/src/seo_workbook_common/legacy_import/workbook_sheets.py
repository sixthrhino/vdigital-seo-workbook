from __future__ import annotations

import re
from typing import Any

# Adapted from vdigital-testing-agent's mcp-server/tools/sheets.py — that
# project already solved reading a live client's legacy workbook robustly
# (fuzzy month/year parsing, tolerant column-name aliases across different
# clients' copies of the template). Re-implemented here against this
# system's own month format ("YYYY-MM") and field names rather than
# imported directly, since the two repos don't share a dependency.

_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

_SPREADSHEET_ID_IN_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def extract_spreadsheet_id(url_or_id: str) -> str:
    """Accept either a bare spreadsheet id or a full Google Sheets share
    URL (e.g. "https://docs.google.com/spreadsheets/d/{id}/edit#gid=0")
    and return just the id — so callers can paste whatever they were
    given (a shared link) rather than having to manually dig the id back
    out of it first.
    """
    value = url_or_id.strip()
    match = _SPREADSHEET_ID_IN_URL_RE.search(value)
    return match.group(1) if match else value


_SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _parse_month_year(text: str) -> str | None:
    """Parse a workbook's month/year cell into "YYYY-MM", tolerating the
    same messy real-world formats the source project handles: "August
    2025", "10/1/2025", "2025-10-01" — or None if unparseable (separator
    rows, "Month Year" header repeats, blanks, "Make Good", etc.).
    """
    text = str(text).strip()
    if not text or text.lower() == "month year":
        return None

    match = re.search(r"([a-zA-Z]{3,})\s+(\d{4})", text)
    if match:
        month = _MONTH_MAP.get(match.group(1).lower()[:3])
        if month:
            return f"{match.group(2)}-{month}"

    match = re.match(r"(\d{1,2})/\d{0,2}/?(\d{4})", text)
    if match:
        month_num = int(match.group(1))
        if 1 <= month_num <= 12:
            return f"{match.group(2)}-{month_num:02d}"

    match = re.match(r"(\d{4})-(\d{2})-\d{2}", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"

    return None


def _is_url(text: str) -> bool:
    return bool(text) and str(text).strip().lower().startswith("http")


def _normalize_sheet_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _find_worksheet(workbook: Any, name: str) -> Any:
    """Look up a worksheet by name, tolerating decorative characters
    clients add to make a tab stand out (e.g. "**On-Page" instead of
    "On-Page")."""
    import gspread

    try:
        return workbook.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        target = _normalize_sheet_name(name)
        for ws in workbook.worksheets():
            if _normalize_sheet_name(ws.title) == target:
                return ws
        raise


def build_sheets_client() -> Any:
    """Construct a real gspread client using Application Default
    Credentials — the same auth approach as this package's other Sheets
    integration (seo_workbook_common.output.sheets_writer.build_sheets_service),
    just via gspread instead of the raw googleapiclient discovery client
    since gspread's worksheet/row APIs are a much better fit for reading an
    arbitrary client's workbook than hand-rolling range reads.

    The target spreadsheet must be shared (view access is enough) with
    whatever identity these credentials resolve to — a GCP service
    account's Sheets/Drive access is granted the same way a person's would
    be, not through IAM roles.
    """
    import gspread
    import google.auth

    credentials, _ = google.auth.default(scopes=_SCOPES)
    return gspread.authorize(credentials)


def _get_col(row: dict, *names: str) -> str:
    """First non-empty value among column-name aliases — different
    clients' copies of the workbook template use different exact header
    text for the same field (e.g. "Old H1" vs "Old Headers")."""
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return ""


def _read_on_page_rows(sheets_client: Any, spreadsheet_id: str) -> list[dict]:
    workbook = sheets_client.open_by_key(spreadsheet_id)
    worksheet = _find_worksheet(workbook, "On-Page")
    return worksheet.get_all_records(head=4, default_blank="")


def list_workbook_months(sheets_client: Any, spreadsheet_id: str) -> list[str]:
    """Distinct "YYYY-MM" months found in the workbook's On-Page tab, in
    the order they first appear.
    """
    rows = _read_on_page_rows(sheets_client, spreadsheet_id)
    seen: set[str] = set()
    months: list[str] = []
    for row in rows:
        raw = str(_get_col(row, "Mo - Yr", "Month Year")).strip()
        month = _parse_month_year(raw)
        if month and month not in seen:
            seen.add(month)
            months.append(month)
    return months


def get_month_rows(sheets_client: Any, spreadsheet_id: str, month: str) -> list[dict]:
    """Cleaned row dicts for one "YYYY-MM" month from the workbook's
    On-Page tab — skips separator/non-URL rows, one dict per real page
    optimization row. Each dict has: url, keyword_raw, geo, opt_note,
    old_title, new_title, old_meta, new_meta, old_h1, new_h1.
    """
    rows = _read_on_page_rows(sheets_client, spreadsheet_id)
    results = []
    for row in rows:
        raw_month = str(_get_col(row, "Mo - Yr", "Month Year")).strip()
        if _parse_month_year(raw_month) != month:
            continue

        url = str(row.get("Optimization / URL", "")).strip()
        if not _is_url(url):
            continue

        results.append(
            {
                "url": url,
                "keyword_raw": str(_get_col(row, "Keyword / Volume", "Keyword")).strip(),
                "geo": str(row.get("Target Geo", "")).strip(),
                "opt_note": str(_get_col(row, "What Is Planned / Has Been Done?", "Optimizations")).strip(),
                "old_title": str(row.get("Old Title Tag", "")).strip(),
                "new_title": str(row.get("New Title Tag", "")).strip(),
                "old_meta": str(row.get("Old Meta Description", "")).strip(),
                "new_meta": str(row.get("New Meta Description", "")).strip(),
                "old_h1": str(_get_col(row, "Old H1", "Old Headers")).strip(),
                "new_h1": str(_get_col(row, "New H1", "New Headers")).strip(),
            }
        )
    return results
