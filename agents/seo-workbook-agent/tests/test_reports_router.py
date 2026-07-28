from fastapi.testclient import TestClient

from seo_workbook_agent.config import get_agent_settings
from seo_workbook_agent.main import create_app

from conftest import StubAgentCore


class _FakeReportTokensCollection:
    def __init__(self):
        self.documents: dict[str, dict] = {}

    def insert_one(self, document):
        self.documents[document["_id"]] = document

    def find_one(self, filter):
        return self.documents.get(filter["_id"])


class _FakeBucket:
    def __init__(self, bucket_name):
        self.bucket_name = bucket_name

    def blob(self, blob_name):
        return _FakeBlob(self.bucket_name, blob_name)


class _FakeBlob:
    def __init__(self, bucket_name, blob_name):
        self.bucket_name = bucket_name
        self.blob_name = blob_name

    def generate_signed_url(self, *, version, expiration, method, service_account_email=None, access_token=None):
        return f"https://storage.googleapis.com/{self.bucket_name}/{self.blob_name}?signed=1"


class _FakeStorageClient:
    def bucket(self, bucket_name):
        return _FakeBucket(bucket_name)


def _client(monkeypatch, *, mongo_uri="mongodb://fake-uri", reports_bucket="test-bucket"):
    if mongo_uri is None:
        monkeypatch.delenv("SEO_WORKBOOK_MONGO_URI", raising=False)
    else:
        monkeypatch.setenv("SEO_WORKBOOK_MONGO_URI", mongo_uri)
    if reports_bucket is None:
        monkeypatch.delenv("SEO_WORKBOOK_REPORTS_BUCKET", raising=False)
    else:
        monkeypatch.setenv("SEO_WORKBOOK_REPORTS_BUCKET", reports_bucket)
    get_agent_settings.cache_clear()

    tokens_collection = _FakeReportTokensCollection()
    app = create_app(
        agent_core=StubAgentCore(),
        storage_client_factory=lambda: _FakeStorageClient(),
        signing_credentials_factory=lambda: ("fake@test.iam.gserviceaccount.com", "fake-access-token"),
        report_tokens_collection_factory=lambda: tokens_collection,
    )
    return TestClient(app), tokens_collection


def test_reports_redirects_to_a_freshly_signed_url(monkeypatch):
    client, tokens_collection = _client(monkeypatch)
    tokens_collection.documents["good-token"] = {
        "_id": "good-token",
        "bucket_name": "test-bucket",
        "blob_name": "client-2026-06-seo-plan.html",
        "expires_at": "2999-01-01T00:00:00+00:00",
    }

    response = client.get("/reports/good-token", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://storage.googleapis.com/test-bucket/client-2026-06-seo-plan.html?signed=1"


def test_reports_404s_for_unknown_token(monkeypatch):
    client, _ = _client(monkeypatch)
    response = client.get("/reports/does-not-exist", follow_redirects=False)
    assert response.status_code == 404


def test_reports_404s_for_expired_token(monkeypatch):
    client, tokens_collection = _client(monkeypatch)
    tokens_collection.documents["old-token"] = {
        "_id": "old-token",
        "bucket_name": "test-bucket",
        "blob_name": "old-report.html",
        "expires_at": "2000-01-01T00:00:00+00:00",
    }
    response = client.get("/reports/old-token", follow_redirects=False)
    assert response.status_code == 404


def test_reports_503_when_storage_not_configured(monkeypatch):
    client, _ = _client(monkeypatch, mongo_uri=None)
    response = client.get("/reports/anything", follow_redirects=False)
    assert response.status_code == 503
