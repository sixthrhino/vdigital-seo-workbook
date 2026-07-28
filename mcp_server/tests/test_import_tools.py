from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from seo_workbook_mcp.app import create_app

from conftest import CSV_PATH, FakeMongoCollection
from seo_workbook_mcp.config import McpSettings

_SAMPLE_RECORDS = [
    {"Month Year": "September 2025", "Optimization / URL": "[Focus: authority]"},
    {
        "Month Year": "September 2025",
        "Optimization / URL": "https://kyz.com/a/",
        "Keyword / Volume": "auto insurance (500)",
        "Target Geo": "Scottsdale, AZ",
        "What Is Planned / Has Been Done?": "Core Optimizations: Title Tag.",
        "Old Title Tag": "Old Title",
        "New Title Tag": "New Title",
    },
    {
        "Month Year": "October 2025",
        "Optimization / URL": "https://kyz.com/b/",
        "Old Meta Description": "Old Meta",
        "New Meta Description": "New Meta " * 20,
    },
    {"Month Year": "November 2025", "Optimization / URL": "[Focus: authority]"},
]


class _FakeWorksheet:
    def __init__(self, records):
        self._records = records

    def get_all_records(self, head=1, default_blank=""):
        return self._records


class _FakeWorkbook:
    def __init__(self, worksheet):
        self._worksheet = worksheet

    def worksheet(self, name):
        return self._worksheet


class _FakeSheetsClient:
    def __init__(self, records=_SAMPLE_RECORDS):
        self._worksheet = _FakeWorksheet(records)

    def open_by_key(self, spreadsheet_id):
        return _FakeWorkbook(self._worksheet)


@pytest.fixture
def import_app():
    fake_mongo_collection = FakeMongoCollection()
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH), mongo_uri="mongodb://fake-uri")
    app = create_app(
        settings,
        mongo_collection_factory=lambda: fake_mongo_collection,
        workbook_sheets_client_factory=lambda: _FakeSheetsClient(),
    )
    return app, fake_mongo_collection


async def test_import_legacy_workbook_imports_every_month_found(import_app):
    app, fake_mongo_collection = import_app
    async with Client(app) as client:
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ"}
        )

    assert sorted(result.data["imported"]) == ["kyz-2025-09", "kyz-2025-10"]
    assert result.data["skipped"] == []
    assert "no page rows found for 2025-11" in result.data["warnings"][0]

    sept = fake_mongo_collection.documents["kyz-2025-09"]
    assert sept["status"] == "finalized"
    assert sept["pages"][0]["url"] == "https://kyz.com/a/"
    assert sept["pages"][0]["touchpoints"][0]["touchpoint_id"] == "title_tag"

    oct_ = fake_mongo_collection.documents["kyz-2025-10"]
    assert oct_["pages"][0]["touchpoints"][0]["touchpoint_id"] == "meta_description"


async def test_import_legacy_workbook_filters_to_a_single_month(import_app):
    app, fake_mongo_collection = import_app
    async with Client(app) as client:
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ", "month": "2025-09"}
        )

    assert result.data["imported"] == ["kyz-2025-09"]
    assert "kyz-2025-10" not in fake_mongo_collection.documents


async def test_import_legacy_workbook_skips_a_month_that_already_has_a_session(import_app):
    app, fake_mongo_collection = import_app
    async with Client(app) as client:
        await client.call_tool("start_session", {"client": "KYZ", "month": "2025-09"})
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ"}
        )

    assert "kyz-2025-09" in result.data["skipped"]
    assert result.data["imported"] == ["kyz-2025-10"]
    # The live session (empty, from start_session) must not have been
    # overwritten by the import.
    assert fake_mongo_collection.documents["kyz-2025-09"]["pages"] == []


async def test_import_legacy_workbook_requires_mongo_configured():
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH))  # mongo_uri left empty
    app = create_app(settings, workbook_sheets_client_factory=lambda: _FakeSheetsClient())
    async with Client(app) as client:
        with pytest.raises(ToolError, match="mongo_uri is not configured"):
            await client.call_tool("import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ"})
