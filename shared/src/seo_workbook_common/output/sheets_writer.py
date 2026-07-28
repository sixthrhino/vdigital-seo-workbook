from __future__ import annotations

from typing import Any, Callable

from ..models.plan_session import PlanSession
from .formatting import format_item

HEADER_ROW = [
    "Month",
    "Client",
    "URL",
    "Keyword",
    "Search Volume",
    "Geo",
    "Category",
    "Touchpoint",
    "Detail",
    "Validation",
]


def session_to_rows(session: PlanSession, touchpoint_name: Callable[[str], str] | None = None) -> list[list[str]]:
    """Deterministically flatten a PlanSession into spreadsheet rows — one
    row per (page, touchpoint, item), so multi-instance touchpoints (several
    headings changed, several links added) land as separate rows instead of
    collapsing back into one ambiguous cell like the legacy workbook did.
    """
    resolve_name = touchpoint_name or (lambda tp_id: tp_id)
    rows: list[list[str]] = [HEADER_ROW]

    for page in session.pages:
        keyword = page.keyword_target.keyword if page.keyword_target else ""
        volume = (
            str(page.keyword_target.search_volume)
            if page.keyword_target and page.keyword_target.search_volume is not None
            else ""
        )
        geo = page.geo or ""
        base = [session.month, session.client, page.url, keyword, volume, geo]

        if not page.touchpoints:
            rows.append([*base, "", "", "(no optimizations recorded yet)", ""])
            continue

        for tp in page.touchpoints:
            validation = "Passed" if tp.validation.passed else "; ".join(tp.validation.messages)
            name = resolve_name(tp.touchpoint_id)
            if not tp.items:
                rows.append([*base, tp.category, name, "", validation])
                continue
            for item in tp.items:
                detail = format_item(item, tp.touchpoint_id)
                rows.append([*base, tp.category, name, detail, validation])

    return rows


def write_rows_to_sheet(
    sheets_service: Any,
    spreadsheet_id: str,
    rows: list[list[str]],
    sheet_range: str = "A1",
) -> dict[str, Any]:
    """Write pre-built rows to a Google Sheet.

    `sheets_service` is a googleapiclient Sheets API resource (or any test
    double implementing the same `.spreadsheets().values().update(...)
    .execute()` chain) — injected rather than constructed here so this stays
    unit-testable without real Google credentials. Use build_sheets_service()
    to get a real one.
    """
    request = sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=sheet_range,
        valueInputOption="RAW",
        body={"values": rows},
    )
    return request.execute()


def build_sheets_service() -> Any:
    """Construct a real Sheets API client using Application Default
    Credentials. Not exercised in unit tests — see write_rows_to_sheet's
    injectable `sheets_service` param for the testable seam.
    """
    import google.auth
    from googleapiclient.discovery import build

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=credentials)
