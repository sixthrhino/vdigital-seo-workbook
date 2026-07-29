from __future__ import annotations

from typing import Any, Callable

from seo_workbook_common.output import upload_html
from seo_workbook_common.storage import create_report_token


def upload_report_and_get_link(
    html: str,
    filename: str,
    *,
    reports_bucket: str,
    agent_public_url: str,
    storage_client_factory: Callable[[], Any],
    get_report_tokens_collection: Callable[[], Any],
) -> str:
    """Upload a rendered report's HTML to the reports bucket and return a
    short share link — shared by every report format (render_session_report,
    render_session_table_report, and import_legacy_workbook's post-import
    link) so the upload/token/link-shape logic lives in exactly one place.

    The link is a short redirect (agent-service's /reports/{token} route),
    not the raw signed GCS URL — that URL is ~400 characters and a real
    source of transcription errors when reproduced verbatim in a chat
    reply, so it's resolved server-side instead.
    """
    storage_client = storage_client_factory()
    upload_html(storage_client, reports_bucket, filename, html)

    tokens_collection = get_report_tokens_collection()
    token = create_report_token(tokens_collection, reports_bucket, filename)

    return f"{agent_public_url.rstrip('/')}/reports/{token}"
