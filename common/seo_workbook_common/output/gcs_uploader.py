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
    service_account_email: str | None = None,
    access_token: str | None = None,
) -> str:
    """Upload a rendered HTML report to GCS and return a time-limited signed
    URL — open it in a browser and print to PDF if a PDF is actually needed,
    rather than generating one server-side.

    `storage_client` is a google.cloud.storage.Client (or any test double
    implementing `.bucket(name).blob(name)` with `.upload_from_string` and
    `.generate_signed_url`) — injected rather than constructed here so this
    stays unit-testable without real GCS credentials. Use
    build_storage_client() to get a real one.

    `service_account_email`/`access_token`: Cloud Run's attached service
    account has no private key, so `generate_signed_url` can't sign locally
    — passing these tells it to sign via the IAM Credentials API's signBlob
    method instead (needs roles/iam.serviceAccountTokenCreator on itself;
    see deploy.sh's setup_project). Get them from iam_signing_credentials().
    Omit both for a locally-run key-file scenario, where local signing works
    as-is.
    """
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(html, content_type="text/html; charset=utf-8")

    signing_kwargs: dict[str, Any] = {}
    if service_account_email and access_token:
        signing_kwargs["service_account_email"] = service_account_email
        signing_kwargs["access_token"] = access_token

    return blob.generate_signed_url(version="v4", expiration=expiration, method="GET", **signing_kwargs)


def iam_signing_credentials() -> tuple[str, str]:
    """Fetch (service_account_email, access_token) for the current
    Application Default Credentials, for use with upload_html_report's IAM
    signing path.

    Not exercised in unit tests — see upload_html_report's injectable
    signing params for the testable seam.
    """
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default()
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.service_account_email, credentials.token


def build_storage_client() -> Any:
    """Construct a real GCS client using Application Default Credentials.

    Not exercised in unit tests — see upload_html_report's injectable
    `storage_client` param for the testable seam.
    """
    from google.cloud import storage

    return storage.Client()
