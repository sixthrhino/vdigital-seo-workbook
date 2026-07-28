from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from seo_workbook_common.output.gcs_uploader import generate_report_url
from seo_workbook_common.storage import lookup_report_token

from ..config import get_agent_settings

router = APIRouter()


@router.get("/reports/{token}")
def get_report(token: str, request: Request) -> RedirectResponse:
    """Resolve a short report share-token (minted by mcp-server's
    render_session_report) into a freshly-signed GCS URL and redirect
    there.

    A plain (non-async) route — FastAPI runs it in a worker thread, so the
    blocking Mongo/GCS calls here don't block the event loop.

    Exists because the alternative — the model reproducing a ~400-char
    signed URL verbatim in a chat reply — is a real source of
    transcription errors; the short link is what actually goes in front of
    the model instead.
    """
    settings = get_agent_settings()
    if not settings.mongo_uri or not settings.reports_bucket:
        raise HTTPException(status_code=503, detail="Report storage is not configured")

    tokens_collection = request.app.state.report_tokens_collection_factory()
    record = lookup_report_token(tokens_collection, token)
    if record is None:
        raise HTTPException(status_code=404, detail="Report link not found or expired")

    storage_client = request.app.state.storage_client_factory()
    service_account_email, access_token = request.app.state.signing_credentials_factory()
    url = generate_report_url(
        storage_client,
        record["bucket_name"],
        record["blob_name"],
        service_account_email=service_account_email,
        access_token=access_token,
    )
    return RedirectResponse(url=url, status_code=302)
