"""
Google Sheets reader.

Auth priority:
  1. GOOGLE_SERVICE_ACCOUNT_JSON env var  (JSON string — good for Cloud Run secrets)
  2. GOOGLE_SERVICE_ACCOUNT_FILE env var  (path to .json file — good for local dev)
  3. Application Default Credentials      (automatic in Cloud Run with correct SA)
"""

from __future__ import annotations

import json
import os
import re

import gspread
from google.auth import default as google_auth_default
from google.oauth2.service_account import Credentials

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_month_year(text: str) -> tuple[int, int] | None:
    """Return (month, year) from various formats, or None if unparseable."""
    text = str(text).strip()
    if not text or text.lower() == "month year":
        return None
    # "August 2025", "Novemeber 2025", "June 2026"
    m = re.search(r"([a-zA-Z]{3,})\s+(\d{4})", text)
    if m:
        month = _MONTH_MAP.get(m.group(1).lower()[:3])
        if month:
            return (month, int(m.group(2)))
    # "10/1/2025", "6/2026"
    m = re.match(r"(\d{1,2})/\d{0,2}/?(\d{4})", text)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    # "2025-10-01"
    m = re.match(r"(\d{4})-(\d{2})-\d{2}", text)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    return None


_VOLUME_KD_RE = re.compile(r"\s*\(?[\d,]+(?:\.\d+)?[kKmM]?\s*/\s*kd\s*\d+\)?\s*$", re.I)


def _clean_keyword(kw: str) -> str:
    """Strip trailing volume/difficulty markers — parenthesized like
    '(2.5k)'/'(170)', or bare like '194000/KD 0' (some workbooks paste
    volume/KD straight from the keyword-research tool with no parens)."""
    kw = _VOLUME_KD_RE.sub("", str(kw))
    kw = re.sub(r"\s*\([^)]*\)\s*$", "", kw)
    return kw.strip()


def _is_url(text: str) -> bool:
    return bool(text) and str(text).strip().startswith("http")


_SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _client() -> gspread.Client:
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

    if sa_json:
        info = json.loads(sa_json)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
    elif sa_file and os.path.exists(sa_file):
        creds = Credentials.from_service_account_file(sa_file, scopes=_SCOPES)
    else:
        creds, _ = google_auth_default(scopes=_SCOPES)

    return gspread.authorize(creds)


def _normalize_sheet_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _find_worksheet(wb, name: str):
    """Look up a worksheet by name, tolerating decorative characters clients
    add to make a tab stand out (seen in the wild: "**On-Page" instead of
    "On-Page") — falls back to a normalized-name match only if the exact
    name isn't found, so the common case is unaffected."""
    try:
        return wb.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        target = _normalize_sheet_name(name)
        for ws in wb.worksheets():
            if _normalize_sheet_name(ws.title) == target:
                return ws
        raise


def _rows_to_brand_guide_text(rows: list[list[str]]) -> str:
    """Reconstruct a sheet's raw cell grid as tab-separated label/value
    text — the same shape a user gets copy-pasting the tab by hand, which
    content.py's parse_brand_guide() is built to consume."""
    lines = []
    for row in rows:
        row = list(row)
        while row and row[-1] == "":
            row.pop()
        if row:
            lines.append("\t".join(row))
    return "\n".join(lines)


def read_brand_guide_tab(spreadsheet_id: str, sheet: str = "Brand Guide") -> str:
    """Read the workbook's Brand Guide tab and reconstruct it as tab-separated
    label/value text, ready to hand to content.py's parse_brand_guide().

    Returns "" if the workbook has no Brand Guide tab — not every client
    workbook has one, and callers should treat that the same as a tab
    nobody has filled in yet rather than an error.
    """
    gc = _client()
    wb = gc.open_by_key(spreadsheet_id)
    try:
        ws = _find_worksheet(wb, sheet)
    except gspread.exceptions.WorksheetNotFound:
        return ""
    return _rows_to_brand_guide_text(ws.get_all_values())


_CLIENT_NAME_LABELS = {"client business name", "client name", "business name"}
_WEBSITE_LABELS = {"website url", "website"}


def _extract_client_details(rows: list[list[str]]) -> dict:
    result = {"client": "", "website": ""}
    for row in rows:
        if len(row) < 2:
            continue
        label = row[0].strip().lower().rstrip(":")
        value = row[1].strip()
        if not value:
            continue
        if label in _CLIENT_NAME_LABELS:
            result["client"] = value
        elif label in _WEBSITE_LABELS:
            result["website"] = value
    return result


def read_client_details(spreadsheet_id: str, sheet: str = "Client Details") -> dict:
    """Read the workbook's Client Details tab for the client's business name
    and main website URL — a deterministic source for generate_report's
    client label, instead of guessing from the workbook/attachment title.

    Returns {"client": "", "website": ""} if the tab is missing or doesn't
    have the expected labels — not every client workbook has this tab.
    """
    gc = _client()
    wb = gc.open_by_key(spreadsheet_id)
    try:
        ws = _find_worksheet(wb, sheet)
    except gspread.exceptions.WorksheetNotFound:
        return {"client": "", "website": ""}
    return _extract_client_details(ws.get_all_values())


def get_spreadsheet_title(spreadsheet_id: str) -> str:
    """Return the workbook's own title from Sheets metadata.

    Used when a Sheets link is shared directly in Chat — that attachment
    shape doesn't reliably carry a display name the way an uploaded file
    does, so the title is fetched from the authoritative source instead of
    guessing at Chat's attachment payload fields.
    """
    gc = _client()
    return gc.open_by_key(spreadsheet_id).title


def read_workbook_rows(
    spreadsheet_id: str,
    sheet: str | int = 0,
    header_row: int = 1,
) -> list[dict]:
    """Read all rows from a Google Sheets worksheet.

    Args:
        spreadsheet_id: The ID from the spreadsheet URL
                        (between /d/ and /edit in the URL).
        sheet: Worksheet name (e.g. "Sheet6") or 0-based index (e.g. 5 for Sheet 6).
        header_row: Row number (1-based) that contains column headers.

    Returns:
        List of dicts — one per data row — keyed by the header values.
        Empty cells are represented as empty strings.
    """
    gc = _client()
    wb = gc.open_by_key(spreadsheet_id)

    if isinstance(sheet, int):
        ws = wb.get_worksheet(sheet)
    else:
        ws = _find_worksheet(wb, sheet)

    rows = ws.get_all_records(head=header_row, default_blank="")
    return rows


def _get_col(row: dict, *names: str) -> str:
    """Return the first non-empty value among any of the given column-name
    aliases. Different client workbooks use different exact header text for
    the same field — seen in the wild: "Month Year" vs "Mo - Yr", and
    "Old Headers"/"New Headers" vs "Old H1"/"New H1"."""
    for name in names:
        val = row.get(name)
        if val not in (None, ""):
            return val
    return ""


def list_workbook_months(spreadsheet_id: str) -> list[str]:
    """Return the distinct month/year values found in the On-Page sheet.

    Reads the month/year column (column A, header row 4 — "Mo - Yr" or
    "Month Year" depending on the workbook) and returns unique
    non-separator entries in the order they appear.
    """
    rows = read_workbook_rows(spreadsheet_id, sheet="On-Page", header_row=4)
    seen: set[str] = set()
    months: list[str] = []
    for row in rows:
        val = str(_get_col(row, "Mo - Yr", "Month Year")).strip()
        if not val or val.lower() == "month year":
            continue
        parsed = _parse_month_year(val)
        if parsed and val not in seen:
            seen.add(val)
            months.append(val)
    return months


def get_month_rows(spreadsheet_id: str, month_year: str) -> list[dict]:
    """Return cleaned, agent-ready row dicts for a specific month/year.

    Reads the On-Page sheet, filters to rows matching month_year (fuzzy:
    handles typos, date-formatted cells, various string formats), skips
    separator and non-URL rows, and returns a structured list with all
    fields the agent needs to run checks.

    Each returned dict has:
        url, keyword, geo_city, geo_state, opt_note, optimization_focus,
        old_title, new_title, old_meta, new_meta, old_h1, new_h1,
        visual_qa (bool), redirection
    """
    target = _parse_month_year(month_year)
    if target is None:
        raise ValueError(f"Could not parse month/year from: {month_year!r}")

    rows = read_workbook_rows(spreadsheet_id, sheet="On-Page", header_row=4)
    results = []
    for row in rows:
        mo_yr = str(_get_col(row, "Mo - Yr", "Month Year")).strip()
        if not mo_yr or mo_yr.lower() == "month year":
            continue
        if _parse_month_year(mo_yr) != target:
            continue

        url = str(row.get("Optimization / URL", "")).strip()
        if not _is_url(url):
            continue

        geo_raw = str(row.get("Target Geo", "")).strip()
        if "," in geo_raw:
            geo_city, geo_state = [p.strip() for p in geo_raw.split(",", 1)]
        else:
            geo_city, geo_state = geo_raw, ""

        visual_raw = str(row.get("Front End Visual QA", "")).strip().lower()

        results.append({
            "url": url,
            "keyword": _clean_keyword(row.get("Keyword / Volume", "")),
            "geo_city": geo_city,
            "geo_state": geo_state,
            "opt_note": str(row.get("What Is Planned / Has Been Done?", "")).strip(),
            "optimization_focus": str(row.get("Optimization Focus", "")).strip(),
            "old_title": str(row.get("Old Title Tag", "")).strip(),
            "new_title": str(row.get("New Title Tag", "")).strip(),
            "old_meta": str(row.get("Old Meta Description", "")).strip(),
            "new_meta": str(row.get("New Meta Description", "")).strip(),
            "old_h1": str(_get_col(row, "Old H1", "Old Headers")).strip(),
            "new_h1": str(_get_col(row, "New H1", "New Headers")).strip(),
            "visual_qa": visual_raw in ("true", "yes", "1"),
            "redirection": str(row.get("Redirection?", "")).strip(),
        })
    return results
