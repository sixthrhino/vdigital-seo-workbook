import datetime

from seo_workbook_common.output.gcs_uploader import upload_html_report


class _FakeBlob:
    def __init__(self):
        self.uploaded_content = None
        self.uploaded_content_type = None
        self.generate_signed_url_calls = []

    def upload_from_string(self, content, content_type=None):
        self.uploaded_content = content
        self.uploaded_content_type = content_type

    def generate_signed_url(self, *, version, expiration, method):
        self.generate_signed_url_calls.append({"version": version, "expiration": expiration, "method": method})
        return "https://storage.googleapis.com/fake-bucket/fake-blob?signed=1"


class _FakeBucket:
    def __init__(self):
        self.blobs: dict[str, _FakeBlob] = {}
        self.requested_bucket_name = None

    def blob(self, blob_name):
        blob = _FakeBlob()
        self.blobs[blob_name] = blob
        return blob


class _FakeStorageClient:
    def __init__(self):
        self.bucket_instance = _FakeBucket()
        self.requested_bucket_name = None

    def bucket(self, bucket_name):
        self.requested_bucket_name = bucket_name
        return self.bucket_instance


def test_upload_html_report_uploads_content_and_returns_signed_url():
    client = _FakeStorageClient()

    url = upload_html_report(client, "my-bucket", "kyz-2026-06.html", "<html>hi</html>")

    assert url == "https://storage.googleapis.com/fake-bucket/fake-blob?signed=1"
    assert client.requested_bucket_name == "my-bucket"

    blob = client.bucket_instance.blobs["kyz-2026-06.html"]
    assert blob.uploaded_content == "<html>hi</html>"
    assert blob.uploaded_content_type == "text/html; charset=utf-8"


def test_upload_html_report_uses_v4_signing_with_default_week_long_expiration():
    client = _FakeStorageClient()

    upload_html_report(client, "my-bucket", "report.html", "<html></html>")

    blob = client.bucket_instance.blobs["report.html"]
    call = blob.generate_signed_url_calls[0]
    assert call["version"] == "v4"
    assert call["method"] == "GET"
    assert call["expiration"] == datetime.timedelta(days=7)


def test_upload_html_report_respects_custom_expiration():
    client = _FakeStorageClient()

    upload_html_report(client, "my-bucket", "report.html", "<html></html>", expiration=datetime.timedelta(hours=1))

    blob = client.bucket_instance.blobs["report.html"]
    assert blob.generate_signed_url_calls[0]["expiration"] == datetime.timedelta(hours=1)
