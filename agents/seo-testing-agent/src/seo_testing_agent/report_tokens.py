from __future__ import annotations

import datetime
from typing import Any


def lookup_report_token(collection: Any, token: str) -> dict[str, str] | None:
    """Resolve a report token (minted by seo-testing-mcp's generate_report —
    see its own report_tokens.py) to {"bucket_name":, "blob_name":}, or None
    if the token is unknown or has expired.

    Mirrors shared/seo_workbook_common/storage/report_tokens.py's identical
    function — duplicated rather than imported, since this package
    deliberately has no dependency on the shared package (see the repo
    root CLAUDE.md).

    `collection` is a pymongo Collection (or any test double implementing
    `.find_one(filter)`) — injected for testability without a real
    MongoDB connection.
    """
    document = collection.find_one({"_id": token})
    if document is None:
        return None

    expires_at = datetime.datetime.fromisoformat(document["expires_at"])
    if datetime.datetime.now(datetime.timezone.utc) > expires_at:
        return None

    return {"bucket_name": document["bucket_name"], "blob_name": document["blob_name"]}
