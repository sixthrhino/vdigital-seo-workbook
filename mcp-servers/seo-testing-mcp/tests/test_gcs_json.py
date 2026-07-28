"""Tests for mcp-server/tools/gcs_json.py — shared GCS-or-local JSON
read/write for editable data files (uscities.json, site_dictionaries.json).
"""
import json

from pathlib import Path

from seo_testing_mcp.tools.gcs_json import read_json, write_json


class _FakeBlob:
    def __init__(self, text=None):
        self._text = text
        self.uploaded = None

    def download_as_text(self):
        return self._text

    def upload_from_string(self, content, content_type=None):
        self.uploaded = (content, content_type)


class _FakeBucket:
    def __init__(self, blob):
        self._blob = blob

    def blob(self, name):
        self.requested_name = name
        return self._blob


class _FakeClient:
    def __init__(self, bucket):
        self._bucket = bucket

    def bucket(self, name):
        self.requested_bucket = name
        return self._bucket


class TestReadJson:
    def test_reads_from_gcs_when_uri_given(self, monkeypatch):
        blob = _FakeBlob(text=json.dumps({"example.com": ["Foo"]}))
        bucket = _FakeBucket(blob)
        monkeypatch.setattr("google.cloud.storage.Client", lambda: _FakeClient(bucket))

        result = read_json("gs://my-bucket/qa-data/site_dictionaries.json", Path("/nonexistent/local.json"))

        assert result == {"example.com": ["Foo"]}
        assert bucket.requested_name == "qa-data/site_dictionaries.json"

    def test_falls_back_to_local_file_when_no_uri(self, tmp_path):
        local_file = tmp_path / "data.json"
        local_file.write_text(json.dumps({"a": 1}))

        result = read_json(None, local_file)

        assert result == {"a": 1}

    def test_returns_empty_dict_when_neither_available(self, tmp_path):
        result = read_json(None, tmp_path / "missing.json")
        assert result == {}

    def test_empty_string_uri_treated_as_no_uri(self, tmp_path):
        local_file = tmp_path / "data.json"
        local_file.write_text(json.dumps({"a": 1}))

        result = read_json("", local_file)

        assert result == {"a": 1}


class TestWriteJson:
    def test_uploads_json_to_the_right_blob(self, monkeypatch):
        blob = _FakeBlob()
        bucket = _FakeBucket(blob)
        monkeypatch.setattr("google.cloud.storage.Client", lambda: _FakeClient(bucket))

        write_json("gs://my-bucket/qa-data/uscities.json", {"phoenix": [{"city": "Phoenix"}]})

        assert bucket.requested_name == "qa-data/uscities.json"
        content, content_type = blob.uploaded
        assert json.loads(content) == {"phoenix": [{"city": "Phoenix"}]}
        assert content_type == "application/json"
