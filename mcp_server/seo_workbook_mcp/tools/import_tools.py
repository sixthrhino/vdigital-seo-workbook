from __future__ import annotations

from typing import Any, Callable

from fastmcp import FastMCP
from seo_workbook_common.best_practices.loader import slugify
from seo_workbook_common.legacy_import import build_session_from_rows, build_sheets_client, get_month_rows, list_workbook_months
from seo_workbook_common.storage import build_mongo_collection, save_session

from ..config import McpSettings
from ..session_store import SessionNotFoundError, SessionStore


def register(
    mcp: FastMCP,
    store: SessionStore,
    settings: McpSettings,
    mongo_collection_factory: Callable[[], Any] | None = None,
    workbook_sheets_client_factory: Callable[[], Any] = build_sheets_client,
) -> None:
    def _default_mongo_collection_factory() -> Any:
        return build_mongo_collection(settings.mongo_uri, settings.mongo_database, settings.mongo_collection)

    get_mongo_collection = mongo_collection_factory or _default_mongo_collection_factory

    @mcp.tool()
    def import_legacy_workbook(spreadsheet_id: str, client: str, month: str | None = None) -> dict:
        """Import a client's legacy SEO workbook (a Google Sheet with an
        "On-Page" tab) into this system, creating one finalized session per
        month found — so its history becomes available through
        find_session/render_session_report/export_session_to_sheet like any
        other client's data.

        `spreadsheet_id` is the id from the sheet's URL (between /d/ and
        /edit). The spreadsheet must already be shared (view access is
        enough) with this server's service account — a service account's
        Sheets access is granted the same way a person's would be, not
        through IAM roles, so sharing it is a one-time manual step outside
        this tool.

        `month` limits the import to one "YYYY-MM" month; omit it to import
        every month found in the sheet in one call.

        Never overwrites an existing session: any month that already has a
        record (draft or finalized, from a prior import or from live
        conversational work) is skipped and reported separately, so
        re-running this after fixing a typo in the sheet is always safe.

        Only the explicit Old/New Title Tag, Meta Description, and H1
        columns become real touchpoints — the free-text "what was done"
        column is preserved verbatim as a single legacy_notes touchpoint
        per page rather than parsed into fabricated structure. See
        seo_workbook_common.legacy_import.converter for the full rationale.
        """
        if not settings.mongo_uri:
            raise ValueError("mongo_uri is not configured (SEO_WORKBOOK_MONGO_URI)")

        sheets_client = workbook_sheets_client_factory()
        months = [month] if month else list_workbook_months(sheets_client, spreadsheet_id)

        imported: list[str] = []
        skipped: list[str] = []
        warnings: list[str] = []

        for target_month in months:
            session_id = f"{slugify(client)}-{target_month}"
            try:
                store.get(session_id)
            except SessionNotFoundError:
                pass
            else:
                skipped.append(session_id)
                continue

            rows = get_month_rows(sheets_client, spreadsheet_id, target_month)
            if not rows:
                warnings.append(f"no page rows found for {target_month}")
                continue

            session = build_session_from_rows(client, target_month, rows)
            store.create(session)
            save_session(get_mongo_collection(), session)
            imported.append(session_id)

        return {"imported": imported, "skipped": skipped, "warnings": warnings}
