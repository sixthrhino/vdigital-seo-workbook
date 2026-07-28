"""Shared GCS-or-local JSON read/write for editable data files
(uscities.json, site_dictionaries.json).

Mirrors the pattern mcp-server/main.py's rules.json loading already uses:
read from GCS if a *_GCS_URI env var is set, else fall back to a local
file. The write path only supports GCS — there's no local-file write,
since a local edit on one Cloud Run instance wouldn't propagate to any
other running instance anyway.

No read-modify-write locking: concurrent writes to the same file could
race and one could be lost. Acceptable for how these are actually used
(occasional, human-paced edits via chat), not worth the extra complexity
of GCS generation-precondition retries for that usage pattern.
"""

from __future__ import annotations

import json
from pathlib import Path


def read_json(gcs_uri: str | None, local_path: Path) -> dict:
    if gcs_uri:
        from google.cloud import storage
        bucket_name, blob_path = gcs_uri.removeprefix("gs://").split("/", 1)
        client = storage.Client()
        return json.loads(client.bucket(bucket_name).blob(blob_path).download_as_text())

    if local_path.exists():
        with open(local_path) as f:
            return json.load(f)
    return {}


def write_json(gcs_uri: str, data: dict) -> None:
    from google.cloud import storage
    bucket_name, blob_path = gcs_uri.removeprefix("gs://").split("/", 1)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_path)
    blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")
