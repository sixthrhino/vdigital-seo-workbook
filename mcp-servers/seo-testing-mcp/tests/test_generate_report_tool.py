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

from datetime import timedelta

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


class _FakeBaseCredentials:
    service_account_email = "test-sa@example.iam.gserviceaccount.com"

    def refresh(self, request):
        pass


class _FakeSigningCredentials:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class TestGenerateReportGCS:
    @pytest.fixture(autouse=True)
    def fake_signing(self, monkeypatch):
        # generate_signed_url needs credentials that can sign, which Cloud
        # Run's default metadata-server credentials can't do locally — the
        # real code wraps them in self-impersonated credentials to route
        # signing through the IAM API instead. Stub both layers so tests
        # don't need real GCP credentials.
        monkeypatch.setattr("google.auth.default", lambda: (_FakeBaseCredentials(), "test-project"))
        monkeypatch.setattr(
            "google.auth.impersonated_credentials.Credentials",
            lambda **kwargs: _FakeSigningCredentials(**kwargs),
        )

    def test_uploads_with_unique_name_and_returns_signed_url(self, monkeypatch):
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
        assert result == f"https://storage.googleapis.com/signed/{id(blob)}"
        assert blob.signed_url_kwargs["version"] == "v4"
        assert blob.signed_url_kwargs["method"] == "GET"
        assert blob.signed_url_kwargs["expiration"] == timedelta(days=7)
        assert isinstance(blob.signed_url_kwargs["credentials"], _FakeSigningCredentials)
        assert blob.signed_url_kwargs["credentials"].kwargs["target_principal"] == \
            "test-sa@example.iam.gserviceaccount.com"

    def test_two_reports_get_distinct_object_names(self, monkeypatch):
        monkeypatch.setenv("GCS_REPORT_BUCKET", "vdigital-500922-qa-reports")
        bucket = _FakeBucket()
        monkeypatch.setattr("google.cloud.storage.Client", lambda: _FakeClient(bucket))

        generate_report(RESULTS, output_path="/tmp/qa_result.html")
        generate_report(RESULTS, output_path="/tmp/qa_result.html")

        assert len(bucket.blobs) == 2

    def test_missing_client_and_month_still_produce_a_valid_name(self, monkeypatch):
        monkeypatch.setenv("GCS_REPORT_BUCKET", "vdigital-500922-qa-reports")
        bucket = _FakeBucket()
        monkeypatch.setattr("google.cloud.storage.Client", lambda: _FakeClient(bucket))

        generate_report({"urls": []}, output_path="/tmp/qa_result.html")

        blob_name = next(iter(bucket.blobs))
        assert blob_name.startswith("qa-reports/report-")
