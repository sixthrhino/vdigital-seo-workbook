from __future__ import annotations

import datetime
import secrets
from typing import Any

_DEFAULT_EXPIRATION = datetime.timedelta(days=7)


def create_report_token(
    collection: Any,
    bucket_name: str,
    blob_name: str,
    expiration: datetime.timedelta = _DEFAULT_EXPIRATION,
) -> str:
    """Create a random, unguessable token mapping to a GCS object and store
    it in Mongo, so seo-testing-agent's /reports/{token} redirect route can
    resolve it back to a freshly-signed URL later.

    Returning this short token instead of a ~700-character signed URL
    directly is the point: that length is a real source of corruption when
    an LLM has to reproduce it verbatim in a chat reply (confirmed live —
    a signed URL relayed through Gemini came back 15 hex characters short,
    breaking the signature). Mirrors
    shared/seo_workbook_common/storage/report_tokens.py's identical
    seo-workbook fix for the same underlying problem — duplicated rather
    than imported, since seo-testing-agent deliberately has no dependency
    on the shared package (see the repo root CLAUDE.md).

    `collection` is a pymongo Collection (or any test double implementing
    `.insert_one(document)`) — injected rather than constructed here so
    this stays unit-testable without a real MongoDB connection.
    """
    token = secrets.token_urlsafe(24)
    now = datetime.datetime.now(datetime.timezone.utc)
    collection.insert_one(
        {
            "_id": token,
            "bucket_name": bucket_name,
            "blob_name": blob_name,
            "created_at": now.isoformat(),
            "expires_at": (now + expiration).isoformat(),
        }
    )
    return token
