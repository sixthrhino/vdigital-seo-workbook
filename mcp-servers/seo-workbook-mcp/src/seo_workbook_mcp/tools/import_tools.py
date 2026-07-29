from __future__ import annotations

from typing import Any, Callable

from fastmcp import FastMCP
from seo_workbook_common.best_practices import BestPracticeCatalog
from seo_workbook_common.best_practices.loader import slugify
from seo_workbook_common.legacy_import import (
    build_session_from_rows,
    build_sheets_client,
    extract_spreadsheet_id,
    get_month_rows,
    list_workbook_months,
    read_client_details,
)
from seo_workbook_common.output import build_storage_client, render_page_table_html
from seo_workbook_common.storage import build_mongo_collection, save_session

from ..config import McpSettings
from ..session_store import SessionNotFoundError, SessionStore
from .report_upload import upload_report_and_get_link


def register(
    mcp: FastMCP,
    catalog: BestPracticeCatalog,
    store: SessionStore,
    settings: McpSettings,
    mongo_collection_factory: Callable[[], Any] | None = None,
    workbook_sheets_client_factory: Callable[[], Any] = build_sheets_client,
    storage_client_factory: Callable[[], Any] = build_storage_client,
    report_tokens_collection_factory: Callable[[], Any] | None = None,
) -> None:
    def _default_mongo_collection_factory() -> Any:
        return build_mongo_collection(settings.mongo_uri, settings.mongo_database, settings.mongo_collection)

    def _default_report_tokens_collection_factory() -> Any:
        return build_mongo_collection(settings.mongo_uri, settings.mongo_database, settings.mongo_report_tokens_collection)

    get_mongo_collection = mongo_collection_factory or _default_mongo_collection_factory
    get_report_tokens_collection = report_tokens_collection_factory or _default_report_tokens_collection_factory

    @mcp.tool()
    def import_legacy_workbook(spreadsheet_id: str, client: str, month: str | None = None) -> dict:
        """Import a client's legacy SEO workbook (a Google Sheet with an
        "On-Page" tab) into this system, creating one finalized session per
        month found — so its history becomes available through
        find_session/render_session_report/export_session_to_sheet like any
        other client's data.

        `spreadsheet_id` accepts either the bare id from the sheet's URL
        (between /d/ and /edit) or the full share URL/link as-is — whatever
        the specialist pasted, no need to dig the id out of it first.

        `client` is a fallback only — if the workbook has a Client Details
        tab with a Client Business Name filled in, that's used instead
        (it's the authoritative source, not whatever the specialist typed
        when asking for the import). The result's "client" field always
        says which name was actually used. Every session created also gets
        a client_details property populated from that same tab — a fixed
        allowlist only (website, package level, account/project manager,
        SEO/content strategist, link builder), never anything login/
        credential-shaped even if the workbook has that filled in despite
        the tab's own "don't put credentials here" labeling.

        The spreadsheet must already be shared (view access is enough)
        with this server's service account — a service account's Sheets
        access is granted the same way a person's would be, not through
        IAM roles, so sharing it is a one-time manual step outside this
        tool.

        `month` limits the import to one "YYYY-MM" month; omit it to import
        every month found in the sheet in one call.

        Never overwrites an existing session: any month that already has a
        record (draft or finalized, from a prior import or from live
        conversational work) is skipped and reported separately, so
        re-running this after fixing a typo in the sheet is always safe.

        Only the explicit Old/New Title Tag, Meta Description, and H1
        columns become real touchpoints — the free-text "what was done"
        column is preserved verbatim as a single "optimizations" touchpoint
        per page rather than parsed into fabricated structure. See
        seo_workbook_common.legacy_import.converter for the full rationale.

        Every newly imported month also gets a table-format report
        generated immediately (see render_session_table_report) — its link
        is returned in "table_reports" so it can be shared right away
        without a separate follow-up call. Skipped/already-existing months
        don't get a new link generated; call render_session_table_report
        directly for those if needed. If reports_bucket/agent_public_url
        aren't configured, "table_reports" is empty and a warning explains
        why — the import itself still succeeds.
        """
        if not settings.mongo_uri:
            raise ValueError("mongo_uri is not configured (SEO_WORKBOOK_MONGO_URI)")

        spreadsheet_id = extract_spreadsheet_id(spreadsheet_id)
        sheets_client = workbook_sheets_client_factory()

        details = read_client_details(sheets_client, spreadsheet_id)
        resolved_client = details.get("client") or client
        client_details = details.get("details") or {}

        months = [month] if month else list_workbook_months(sheets_client, spreadsheet_id)

        imported: list[str] = []
        skipped: list[str] = []
        warnings: list[str] = []
        table_reports: dict[str, str] = {}
        can_generate_reports = bool(settings.reports_bucket and settings.agent_public_url)

        for target_month in months:
            session_id = f"{slugify(resolved_client)}-{target_month}"
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

            session = build_session_from_rows(resolved_client, target_month, rows)
            session.client_details = client_details
            store.create(session)
            save_session(get_mongo_collection(), session)
            imported.append(session_id)

            if can_generate_reports:
                html = render_page_table_html(session, catalog=catalog)
                filename = f"{session.client}-{session.month}-seo-plan-table.html".replace(" ", "-")
                table_reports[session_id] = upload_report_and_get_link(
                    html, filename,
                    reports_bucket=settings.reports_bucket, agent_public_url=settings.agent_public_url,
                    storage_client_factory=storage_client_factory,
                    get_report_tokens_collection=get_report_tokens_collection,
                )

        if imported and not can_generate_reports:
            warnings.append(
                "table_reports not generated: reports_bucket/agent_public_url not configured "
                "(SEO_WORKBOOK_REPORTS_BUCKET / SEO_WORKBOOK_AGENT_PUBLIC_URL)"
            )

        return {
            "client": resolved_client,
            "imported": imported,
            "skipped": skipped,
            "warnings": warnings,
            "table_reports": table_reports,
        }
