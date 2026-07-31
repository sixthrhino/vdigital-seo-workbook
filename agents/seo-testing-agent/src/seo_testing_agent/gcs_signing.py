from __future__ import annotations

import datetime
from typing import Any

_DEFAULT_EXPIRATION = datetime.timedelta(days=7)


def generate_report_url(
    storage_client: Any,
    bucket_name: str,
    blob_name: str,
    expiration: datetime.timedelta = _DEFAULT_EXPIRATION,
    service_account_email: str | None = None,
    access_token: str | None = None,
) -> str:
    """Generate a time-limited signed URL for an already-uploaded report.

    `service_account_email`/`access_token`: Cloud Run's attached service
    account has no private key, so `generate_signed_url` can't sign
    locally — passing these tells it to sign via the IAM Credentials API's
    signBlob method instead (needs roles/iam.serviceAccountTokenCreator on
    itself; already granted to this project's shared compute service
    account by deploy.sh's setup_project). Get them from
    iam_signing_credentials(). Omit both for a locally-run key-file
    scenario, where local signing works as-is.

    Mirrors shared/seo_workbook_common/output/gcs_uploader.py's identical
    function — duplicated rather than imported, since this package
    deliberately has no dependency on the shared package (see the repo
    root CLAUDE.md).
    """
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    signing_kwargs: dict[str, Any] = {}
    if service_account_email and access_token:
        signing_kwargs["service_account_email"] = service_account_email
        signing_kwargs["access_token"] = access_token

    return blob.generate_signed_url(version="v4", expiration=expiration, method="GET", **signing_kwargs)


def iam_signing_credentials() -> tuple[str, str]:
    """Fetch (service_account_email, access_token) for the current
    Application Default Credentials, for use with generate_report_url's IAM
    signing path.

    Not exercised in unit tests — see generate_report_url's injectable
    signing params for the testable seam.
    """
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default()
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.service_account_email, credentials.token


def build_storage_client() -> Any:
    """Construct a real GCS client using Application Default Credentials.

    Not exercised in unit tests — see generate_report_url's injectable
    `storage_client` param for the testable seam.
    """
    from google.cloud import storage

    return storage.Client()


def build_mongo_collection(uri: str, database: str, collection_name: str) -> Any:
    """Construct a real MongoDB collection handle.

    Not exercised in unit tests — see the /reports/{token} route's
    injectable collection factory for the testable seam.
    """
    from pymongo import MongoClient

    client = MongoClient(uri)
    return client[database][collection_name]
