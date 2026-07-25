from pathlib import Path

import pytest

from seo_workbook_mcp.app import create_app
from seo_workbook_mcp.config import McpSettings

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "organic_qa_checklist.csv"


class FakeMongoCollection:
    """Minimal in-memory stand-in for a pymongo Collection — just enough of
    `replace_one`'s upsert-by-filter behavior for finalize_session's tests.
    """

    def __init__(self):
        self.documents: dict[str, dict] = {}

    def replace_one(self, filter, replacement, upsert=False):
        self.documents[filter["_id"]] = replacement


@pytest.fixture
def fake_mongo_collection():
    return FakeMongoCollection()


@pytest.fixture
def mcp_app(fake_mongo_collection):
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH), mongo_uri="mongodb://fake-uri")
    return create_app(settings, mongo_collection_factory=lambda: fake_mongo_collection)
