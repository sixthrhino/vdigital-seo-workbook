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
    it in Mongo, so agent-service's /reports/{token} redirect route can
    resolve it back to a freshly-signed URL later.

    Returning this short token instead of a ~400-character signed URL
    directly is the point — that length is a real source of transcription
    errors when a model has to reproduce it verbatim in a chat reply.

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


def lookup_report_token(collection: Any, token: str) -> dict[str, str] | None:
    """Resolve a report token to {"bucket_name":, "blob_name":}, or None if
    the token is unknown or has expired.

    `collection` is a pymongo Collection (or any test double implementing
    `.find_one(filter)`) — injected for the same testability reason as
    create_report_token.
    """
    document = collection.find_one({"_id": token})
    if document is None:
        return None

    expires_at = datetime.datetime.fromisoformat(document["expires_at"])
    if datetime.datetime.now(datetime.timezone.utc) > expires_at:
        return None

    return {"bucket_name": document["bucket_name"], "blob_name": document["blob_name"]}
