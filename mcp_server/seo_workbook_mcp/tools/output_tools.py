from __future__ import annotations

from typing import Any, Callable

from fastmcp import FastMCP
from seo_workbook_common.best_practices import BestPracticeCatalog
from seo_workbook_common.output import (
    build_sheets_service,
    build_storage_client,
    iam_signing_credentials,
    render_summary_html,
    session_to_rows,
    upload_html_report,
    write_rows_to_sheet,
)

from ..config import McpSettings
from ..session_store import SessionStore


def _touchpoint_name(catalog: BestPracticeCatalog, touchpoint_id: str) -> str:
    try:
        return catalog.get(touchpoint_id).name
    except KeyError:
        return touchpoint_id


def register(
    mcp: FastMCP,
    catalog: BestPracticeCatalog,
    store: SessionStore,
    settings: McpSettings,
    sheets_client_factory: Callable[[], Any] = build_sheets_service,
    storage_client_factory: Callable[[], Any] = build_storage_client,
    signing_credentials_factory: Callable[[], tuple[str, str]] = iam_signing_credentials,
) -> None:
    @mcp.tool()
    def render_session_report(session_id: str) -> dict:
        """Render the current state of a session (draft or finalized) as a
        styled HTML report, upload it to the reports bucket, and return a
        signed link valid for 7 days. There's no server-side PDF generation
        — if a PDF is wanted, open the link in a browser and print to PDF
        from there (the report's print stylesheet is tuned for this).
        """
        if not settings.reports_bucket:
            raise ValueError("reports_bucket is not configured (SEO_WORKBOOK_REPORTS_BUCKET)")

        session = store.get(session_id)
        html = render_summary_html(session, catalog=catalog)
        filename = f"{session.client}-{session.month}-seo-plan.html".replace(" ", "-")
        storage_client = storage_client_factory()
        service_account_email, access_token = signing_credentials_factory()
        report_url = upload_html_report(
            storage_client,
            settings.reports_bucket,
            filename,
            html,
            service_account_email=service_account_email,
            access_token=access_token,
        )
        return {"filename": filename, "report_url": report_url}

    @mcp.tool()
    def export_session_to_sheet(session_id: str, spreadsheet_id: str, sheet_range: str = "A1") -> dict:
        """Write a session's data as rows into an existing Google Sheet —
        one row per page/touchpoint/item, matching render_session_report's
        level of detail. Requires the caller's Google credentials to already
        have edit access to the target spreadsheet.
        """
        session = store.get(session_id)
        rows = session_to_rows(session, touchpoint_name=lambda tp_id: _touchpoint_name(catalog, tp_id))
        sheets_service = sheets_client_factory()
        result = write_rows_to_sheet(sheets_service, spreadsheet_id, rows, sheet_range=sheet_range)
        return {"rows_written": len(rows) - 1, "spreadsheet_id": spreadsheet_id, "result": result}
