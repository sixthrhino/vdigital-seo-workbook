from __future__ import annotations

from typing import Any, Callable

from fastmcp import FastMCP
from seo_workbook_common.best_practices import BestPracticeCatalog
from seo_workbook_common.output import (
    build_sheets_service,
    build_storage_client,
    render_page_table_html,
    render_summary_html,
    session_to_rows,
    write_rows_to_sheet,
)
from seo_workbook_common.storage import build_mongo_collection

from ..config import McpSettings
from ..session_store import SessionStore
from .report_upload import upload_report_and_get_link


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
    report_tokens_collection_factory: Callable[[], Any] | None = None,
) -> None:
    def _default_report_tokens_collection_factory() -> Any:
        return build_mongo_collection(settings.mongo_uri, settings.mongo_database, settings.mongo_report_tokens_collection)

    get_report_tokens_collection = report_tokens_collection_factory or _default_report_tokens_collection_factory

    @mcp.tool()
    def render_session_report(session_id: str) -> dict:
        """Render the current state of a session (draft or finalized) as a
        detailed, per-touchpoint HTML report, upload it to the reports
        bucket, and return a short share link (valid for 7 days). There's
        no server-side PDF generation — if a PDF is wanted, open the link
        in a browser and print to PDF from there (the report's print
        stylesheet is tuned for this).

        For a compact, one-row-per-URL table view instead (closer to the
        legacy workbook's layout), see render_session_table_report — the
        two are independent files, generate whichever (or both) the
        specialist wants.

        Safe to call again any time the specialist wants to regenerate or
        refresh a report (e.g. after recording more touchpoints) — it
        always re-renders from the session's current state and overwrites
        the same file rather than creating a new one, so previously shared
        links keep working but now show the newly regenerated content
        instead of what was there before. There's no way to recover the
        prior version once you regenerate.

        The link is a short redirect (agent-service's /reports/{token}
        route), not the raw signed GCS URL — that URL is ~400 characters
        and a real source of transcription errors when reproduced verbatim
        in a chat reply, so it's resolved server-side instead.
        """
        if not settings.reports_bucket:
            raise ValueError("reports_bucket is not configured (SEO_WORKBOOK_REPORTS_BUCKET)")
        if not settings.agent_public_url:
            raise ValueError("agent_public_url is not configured (SEO_WORKBOOK_AGENT_PUBLIC_URL)")
        if not settings.mongo_uri:
            raise ValueError("mongo_uri is not configured (SEO_WORKBOOK_MONGO_URI)")

        session = store.get(session_id)
        html = render_summary_html(session, catalog=catalog)
        filename = f"{session.client}-{session.month}-seo-plan.html".replace(" ", "-")

        report_url = upload_report_and_get_link(
            html, filename,
            reports_bucket=settings.reports_bucket, agent_public_url=settings.agent_public_url,
            storage_client_factory=storage_client_factory, get_report_tokens_collection=get_report_tokens_collection,
        )
        return {"filename": filename, "report_url": report_url}

    @mcp.tool()
    def render_session_table_report(session_id: str) -> dict:
        """Render a session as a compact, one-row-per-URL table (URL,
        Keyword, Geo, Optimizations, and old/new Title/Meta Description/H1
        columns) — closer to the legacy workbook's row layout than
        render_session_report's per-touchpoint breakdown, for quickly
        scanning a whole month's planned changes across every page at once.
        Use this when the specialist wants to review/compare pages side by
        side rather than read a detailed per-touchpoint write-up; use
        render_session_report for the latter. Both can be generated for the
        same session — they're independent files, not alternatives that
        replace each other.

        Same upload/link/regeneration behavior as render_session_report:
        uploads to the reports bucket and returns a short share link (valid
        for 7 days), safe to call again any time to refresh it in place.
        """
        if not settings.reports_bucket:
            raise ValueError("reports_bucket is not configured (SEO_WORKBOOK_REPORTS_BUCKET)")
        if not settings.agent_public_url:
            raise ValueError("agent_public_url is not configured (SEO_WORKBOOK_AGENT_PUBLIC_URL)")
        if not settings.mongo_uri:
            raise ValueError("mongo_uri is not configured (SEO_WORKBOOK_MONGO_URI)")

        session = store.get(session_id)
        html = render_page_table_html(session, catalog=catalog)
        filename = f"{session.client}-{session.month}-seo-plan-table.html".replace(" ", "-")

        report_url = upload_report_and_get_link(
            html, filename,
            reports_bucket=settings.reports_bucket, agent_public_url=settings.agent_public_url,
            storage_client_factory=storage_client_factory, get_report_tokens_collection=get_report_tokens_collection,
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
