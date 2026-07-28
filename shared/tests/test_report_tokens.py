import datetime

from seo_workbook_common.storage.report_tokens import create_report_token, lookup_report_token


class _FakeCollection:
    def __init__(self):
        self.documents: dict[str, dict] = {}

    def insert_one(self, document):
        self.documents[document["_id"]] = document

    def find_one(self, filter):
        return self.documents.get(filter["_id"])


def test_create_report_token_returns_a_random_url_safe_token():
    collection = _FakeCollection()

    token_a = create_report_token(collection, "my-bucket", "a.html")
    token_b = create_report_token(collection, "my-bucket", "b.html")

    assert token_a != token_b
    assert len(token_a) > 20


def test_lookup_report_token_resolves_a_valid_token():
    collection = _FakeCollection()
    token = create_report_token(collection, "my-bucket", "report.html")

    result = lookup_report_token(collection, token)

    assert result == {"bucket_name": "my-bucket", "blob_name": "report.html"}


def test_lookup_report_token_returns_none_for_unknown_token():
    collection = _FakeCollection()
    assert lookup_report_token(collection, "does-not-exist") is None


def test_lookup_report_token_returns_none_once_expired():
    collection = _FakeCollection()
    token = create_report_token(collection, "my-bucket", "report.html", expiration=datetime.timedelta(seconds=-1))

    assert lookup_report_token(collection, token) is None
