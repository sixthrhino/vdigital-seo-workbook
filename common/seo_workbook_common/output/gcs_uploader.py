from __future__ import annotations

import datetime
from typing import Any

_DEFAULT_EXPIRATION = datetime.timedelta(days=7)


def upload_html_report(
    storage_client: Any,
    bucket_name: str,
    blob_name: str,
    html: str,
    expiration: datetime.timedelta = _DEFAULT_EXPIRATION,
) -> str:
    """Upload a rendered HTML report to GCS and return a time-limited signed
    URL — open it in a browser and print to PDF if a PDF is actually needed,
    rather than generating one server-side.

    `storage_client` is a google.cloud.storage.Client (or any test double
    implementing `.bucket(name).blob(name)` with `.upload_from_string` and
    `.generate_signed_url`) — injected rather than constructed here so this
    stays unit-testable without real GCS credentials. Use
    build_storage_client() to get a real one.
    """
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(html, content_type="text/html; charset=utf-8")
    return blob.generate_signed_url(version="v4", expiration=expiration, method="GET")


def build_storage_client() -> Any:
    """Construct a real GCS client using Application Default Credentials.

    Signed URL generation on Cloud Run's attached service account requires
    that identity to also hold roles/iam.serviceAccountTokenCreator on
    itself (no private key is available locally to sign with otherwise) —
    see deploy.sh's setup_project.

    Not exercised in unit tests — see upload_html_report's injectable
    `storage_client` param for the testable seam.
    """
    from google.cloud import storage

    return storage.Client()
