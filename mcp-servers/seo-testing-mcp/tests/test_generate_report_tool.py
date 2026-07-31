"""Tests for the generate_report MCP tool in mcp-server/main.py.

Covers the local-file fallback and the GCS upload path. The GCS path has had
two live bugs fixed here:
  1. blob.make_public() (legacy per-object ACL) was called against a
     uniform-bucket-level-access bucket, which rejects that API outright.
  2. Every report reused output_path's filename as the GCS object name, so
     every single report — for every client, every month — silently
     overwrote the same object. Fixed by generating a unique name per call
     and switching to a signed URL instead of a public bucket ACL.
"""

import pytest

from seo_testing_mcp.app import generate_report

RESULTS = {
    "month": "June 2026", "client": "Test Client",
    "urls": [{
        "url": "https://example.com", "verdict": "PASS", "opt_note": "",
        "checks": [{"label": "Title Tag", "status": "pass", "detail": "ok"}],
    }],
}


class TestGenerateReportLocal:
    def test_writes_html_to_output_path(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GCS_REPORT_BUCKET", raising=False)
        out = tmp_path / "report.html"

        result = generate_report(RESULTS, output_path=str(out))

        assert result == str(out)
        assert out.read_text().startswith("<!DOCTYPE html>")


class _FakeBlob:
    def __init__(self):
        self.uploaded = None
        self.make_public_called = False
        self.signed_url_kwargs = None

    def upload_from_string(self, content, content_type=None):
        self.uploaded = (content, content_type)

    def make_public(self):
        self.make_public_called = True

    def generate_signed_url(self, **kwargs):
        self.signed_url_kwargs = kwargs
        return f"https://storage.googleapis.com/signed/{id(self)}"


class _FakeBucket:
    def __init__(self):
        self.blobs = {}

    def blob(self, name):
        b = _FakeBlob()
        self.blobs[name] = b
        return b


class _FakeClient:
    def __init__(self, bucket):
        self._bucket = bucket

    def bucket(self, name):
        assert name == "vdigital-500922-qa-reports"
        return self._bucket


class _FakeReportTokensCollection:
    def __init__(self):
        self.documents = {}

    def insert_one(self, document):
        self.documents[document["_id"]] = document


class TestGenerateReportGCS:
    @pytest.fixture(autouse=True)
    def fake_mongo(self, monkeypatch):
        # generate_report now stores a token in Mongo and returns a short
        # /reports/{token} link (seo-testing-agent) instead of signing a
        # URL itself — see report_tokens.py and the module docstring on why
        # (a raw signed URL is a real source of corruption when an LLM has
        # to relay it verbatim; confirmed live).
        self.tokens_collection = _FakeReportTokensCollection()

        class _FakeDb:
            def __getitem__(_self, collection_name):
                return self.tokens_collection

        class _FakeMongoClient:
            def __init__(_self, uri):
                _self.uri = uri

            def __getitem__(_self, database):
                return _FakeDb()

        monkeypatch.setattr("pymongo.MongoClient", _FakeMongoClient)
        monkeypatch.setenv("MONGO_URI", "mongodb://fake-uri")
        monkeypatch.setenv("AGENT_PUBLIC_URL", "https://seo-testing-agent.example.com")

    def test_uploads_with_unique_name_and_returns_a_short_report_link(self, monkeypatch):
        monkeypatch.setenv("GCS_REPORT_BUCKET", "vdigital-500922-qa-reports")
        bucket = _FakeBucket()
        monkeypatch.setattr("google.cloud.storage.Client", lambda: _FakeClient(bucket))

        result = generate_report(RESULTS, output_path="/tmp/qa_result.html")

        assert len(bucket.blobs) == 1
        blob_name, blob = next(iter(bucket.blobs.items()))
        assert blob_name.startswith("qa-reports/test-client-june-2026-")
        assert blob_name.endswith(".html")
        assert blob.uploaded[1] == "text/html"
        assert blob.make_public_called is False

        assert result.startswith("https://seo-testing-agent.example.com/reports/")
        token = result.rsplit("/", 1)[-1]
        stored = self.tokens_collection.documents[token]
        assert stored["bucket_name"] == "vdigital-500922-qa-reports"
        assert stored["blob_name"] == blob_name

    def test_two_reports_get_distinct_object_names_and_tokens(self, monkeypatch):
        monkeypatch.setenv("GCS_REPORT_BUCKET", "vdigital-500922-qa-reports")
        bucket = _FakeBucket()
        monkeypatch.setattr("google.cloud.storage.Client", lambda: _FakeClient(bucket))

        first = generate_report(RESULTS, output_path="/tmp/qa_result.html")
        second = generate_report(RESULTS, output_path="/tmp/qa_result.html")

        assert len(bucket.blobs) == 2
        assert first != second

    def test_missing_client_and_month_still_produce_a_valid_name(self, monkeypatch):
        monkeypatch.setenv("GCS_REPORT_BUCKET", "vdigital-500922-qa-reports")
        bucket = _FakeBucket()
        monkeypatch.setattr("google.cloud.storage.Client", lambda: _FakeClient(bucket))

        generate_report({"urls": []}, output_path="/tmp/qa_result.html")

        blob_name = next(iter(bucket.blobs))
        assert blob_name.startswith("qa-reports/report-")

    def test_missing_mongo_uri_raises_clear_error(self, monkeypatch):
        monkeypatch.setenv("GCS_REPORT_BUCKET", "vdigital-500922-qa-reports")
        monkeypatch.delenv("MONGO_URI", raising=False)
        bucket = _FakeBucket()
        monkeypatch.setattr("google.cloud.storage.Client", lambda: _FakeClient(bucket))

        with pytest.raises(ValueError, match="MONGO_URI"):
            generate_report(RESULTS, output_path="/tmp/qa_result.html")

    def test_missing_agent_public_url_raises_clear_error(self, monkeypatch):
        monkeypatch.setenv("GCS_REPORT_BUCKET", "vdigital-500922-qa-reports")
        monkeypatch.delenv("AGENT_PUBLIC_URL", raising=False)
        bucket = _FakeBucket()
        monkeypatch.setattr("google.cloud.storage.Client", lambda: _FakeClient(bucket))

        with pytest.raises(ValueError, match="AGENT_PUBLIC_URL"):
            generate_report(RESULTS, output_path="/tmp/qa_result.html")
