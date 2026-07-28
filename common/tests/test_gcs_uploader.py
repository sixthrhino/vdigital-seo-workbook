import datetime

from seo_workbook_common.output.gcs_uploader import generate_report_url, upload_html


class _FakeBlob:
    def __init__(self):
        self.uploaded_content = None
        self.uploaded_content_type = None
        self.generate_signed_url_calls = []

    def upload_from_string(self, content, content_type=None):
        self.uploaded_content = content
        self.uploaded_content_type = content_type

    def generate_signed_url(self, *, version, expiration, method, service_account_email=None, access_token=None):
        self.generate_signed_url_calls.append(
            {
                "version": version,
                "expiration": expiration,
                "method": method,
                "service_account_email": service_account_email,
                "access_token": access_token,
            }
        )
        return "https://storage.googleapis.com/fake-bucket/fake-blob?signed=1"


class _FakeBucket:
    def __init__(self):
        self.blobs: dict[str, _FakeBlob] = {}
        self.requested_bucket_name = None

    def blob(self, blob_name):
        blob = self.blobs.setdefault(blob_name, _FakeBlob())
        return blob


class _FakeStorageClient:
    def __init__(self):
        self.bucket_instance = _FakeBucket()
        self.requested_bucket_name = None

    def bucket(self, bucket_name):
        self.requested_bucket_name = bucket_name
        return self.bucket_instance


def test_upload_html_uploads_content():
    client = _FakeStorageClient()

    upload_html(client, "my-bucket", "kyz-2026-06.html", "<html>hi</html>")

    assert client.requested_bucket_name == "my-bucket"
    blob = client.bucket_instance.blobs["kyz-2026-06.html"]
    assert blob.uploaded_content == "<html>hi</html>"
    assert blob.uploaded_content_type == "text/html; charset=utf-8"


def test_generate_report_url_uses_v4_signing_with_default_week_long_expiration():
    client = _FakeStorageClient()

    url = generate_report_url(client, "my-bucket", "report.html")

    assert url == "https://storage.googleapis.com/fake-bucket/fake-blob?signed=1"
    call = client.bucket_instance.blobs["report.html"].generate_signed_url_calls[0]
    assert call["version"] == "v4"
    assert call["method"] == "GET"
    assert call["expiration"] == datetime.timedelta(days=7)


def test_generate_report_url_respects_custom_expiration():
    client = _FakeStorageClient()

    generate_report_url(client, "my-bucket", "report.html", expiration=datetime.timedelta(hours=1))

    call = client.bucket_instance.blobs["report.html"].generate_signed_url_calls[0]
    assert call["expiration"] == datetime.timedelta(hours=1)


def test_generate_report_url_omits_iam_signing_kwargs_by_default():
    # Cloud Run's attached service account has no private key — generate_
    # signed_url must be told to sign via the IAM API by passing these two
    # kwargs explicitly. Omitting both (the default) is only correct for a
    # local key-file scenario, where it should sign without them.
    client = _FakeStorageClient()

    generate_report_url(client, "my-bucket", "report.html")

    call = client.bucket_instance.blobs["report.html"].generate_signed_url_calls[0]
    assert call["service_account_email"] is None
    assert call["access_token"] is None


def test_generate_report_url_passes_iam_signing_credentials_when_given():
    client = _FakeStorageClient()

    generate_report_url(
        client,
        "my-bucket",
        "report.html",
        service_account_email="agent@my-project.iam.gserviceaccount.com",
        access_token="fake-access-token",
    )

    call = client.bucket_instance.blobs["report.html"].generate_signed_url_calls[0]
    assert call["service_account_email"] == "agent@my-project.iam.gserviceaccount.com"
    assert call["access_token"] == "fake-access-token"
