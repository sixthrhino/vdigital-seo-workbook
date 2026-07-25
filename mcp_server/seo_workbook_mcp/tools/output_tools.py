from __future__ import annotations

import base64
from typing import Any, Callable

from fastmcp import FastMCP
from seo_workbook_common.best_practices import BestPracticeCatalog
from seo_workbook_common.output import build_sheets_service, render_summary_pdf, session_to_rows, write_rows_to_sheet

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
    sheets_client_factory: Callable[[], Any] = build_sheets_service,
) -> None:
    @mcp.tool()
    def render_session_pdf(session_id: str) -> dict:
        """Render the current state of a session (draft or finalized) as a
        PDF summary. Returns base64-encoded PDF bytes and a suggested
        filename — the caller is responsible for delivering it (e.g.
        attaching it in Google Chat, or uploading it and returning a link).
        """
        session = store.get(session_id)
        pdf_bytes = render_summary_pdf(session, catalog=catalog)
        filename = f"{session.client}-{session.month}-seo-plan.pdf".replace(" ", "-")
        return {"filename": filename, "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii")}

    @mcp.tool()
    def export_session_to_sheet(session_id: str, spreadsheet_id: str, sheet_range: str = "A1") -> dict:
        """Write a session's data as rows into an existing Google Sheet —
        one row per page/touchpoint/item, matching render_session_pdf's
        level of detail. Requires the caller's Google credentials to already
        have edit access to the target spreadsheet.
        """
        session = store.get(session_id)
        rows = session_to_rows(session, touchpoint_name=lambda tp_id: _touchpoint_name(catalog, tp_id))
        sheets_service = sheets_client_factory()
        result = write_rows_to_sheet(sheets_service, spreadsheet_id, rows, sheet_range=sheet_range)
        return {"rows_written": len(rows) - 1, "spreadsheet_id": spreadsheet_id, "result": result}
