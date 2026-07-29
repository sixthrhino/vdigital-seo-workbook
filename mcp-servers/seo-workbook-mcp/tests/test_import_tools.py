from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from seo_workbook_mcp.app import create_app

from conftest import CSV_PATH, FakeMongoCollection, FakeReportTokensCollection
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
    def __init__(self, records=None, values=None, title="On-Page"):
        self._records = records or []
        self._values = values or []
        self.title = title

    def get_all_records(self, head=1, default_blank=""):
        return self._records

    def get_all_values(self):
        return self._values


class _FakeWorkbook:
    def __init__(self, worksheet, client_details_worksheet=None):
        self._worksheet = worksheet
        self._client_details_worksheet = client_details_worksheet

    def worksheet(self, name):
        import gspread

        if name == self._worksheet.title:
            return self._worksheet
        if name == "Client Details" and self._client_details_worksheet is not None:
            return self._client_details_worksheet
        raise gspread.exceptions.WorksheetNotFound(name)

    def worksheets(self):
        extra = [self._client_details_worksheet] if self._client_details_worksheet else []
        return [self._worksheet, *extra]


class _FakeSheetsClient:
    def __init__(self, records=_SAMPLE_RECORDS, client_details_values=None):
        self._worksheet = _FakeWorksheet(records)
        self._client_details_worksheet = (
            _FakeWorksheet(values=client_details_values, title="Client Details")
            if client_details_values is not None else None
        )
        self.opened_with: list[str] = []

    def open_by_key(self, spreadsheet_id):
        self.opened_with.append(spreadsheet_id)
        return _FakeWorkbook(self._worksheet, self._client_details_worksheet)


@pytest.fixture
def import_app():
    fake_mongo_collection = FakeMongoCollection()
    fake_sheets_client = _FakeSheetsClient()
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH), mongo_uri="mongodb://fake-uri")
    app = create_app(
        settings,
        mongo_collection_factory=lambda: fake_mongo_collection,
        workbook_sheets_client_factory=lambda: fake_sheets_client,
    )
    return app, fake_mongo_collection, fake_sheets_client


async def test_import_legacy_workbook_imports_every_month_found(import_app):
    app, fake_mongo_collection, _ = import_app
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
    app, fake_mongo_collection, _ = import_app
    async with Client(app) as client:
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ", "month": "2025-09"}
        )

    assert result.data["imported"] == ["kyz-2025-09"]
    assert "kyz-2025-10" not in fake_mongo_collection.documents


async def test_import_legacy_workbook_accepts_a_full_share_url(import_app):
    app, _, fake_sheets_client = import_app
    async with Client(app) as client:
        result = await client.call_tool(
            "import_legacy_workbook",
            {
                "spreadsheet_id": "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=0",
                "client": "KYZ",
                "month": "2025-09",
            },
        )

    assert result.data["imported"] == ["kyz-2025-09"]
    # Opened more than once (once for read_client_details, once per
    # months/rows lookup) — every call still uses the extracted bare id,
    # never the raw share URL.
    assert set(fake_sheets_client.opened_with) == {"sheet-id"}


async def test_import_legacy_workbook_skips_a_month_that_already_has_a_session(import_app):
    app, fake_mongo_collection, _ = import_app
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


# ---------------------------------------------------------------------------
# Client name resolution — the workbook's own Client Details tab wins over
# whatever the specialist typed.
# ---------------------------------------------------------------------------

async def test_import_legacy_workbook_prefers_client_details_tab_over_typed_name():
    fake_mongo_collection = FakeMongoCollection()
    fake_sheets_client = _FakeSheetsClient(
        client_details_values=[["Client Business Name", "Dynamic Dibs"]]
    )
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH), mongo_uri="mongodb://fake-uri")
    app = create_app(
        settings,
        mongo_collection_factory=lambda: fake_mongo_collection,
        workbook_sheets_client_factory=lambda: fake_sheets_client,
    )
    async with Client(app) as client:
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "whatever i typed", "month": "2025-09"}
        )

    assert result.data["client"] == "Dynamic Dibs"
    assert result.data["imported"] == ["dynamic_dibs-2025-09"]
    assert "dynamic_dibs-2025-09" in fake_mongo_collection.documents


async def test_import_legacy_workbook_stores_allowlisted_client_details_never_credentials():
    fake_mongo_collection = FakeMongoCollection()
    fake_sheets_client = _FakeSheetsClient(
        client_details_values=[
            ["Client Business Name", "Dynamic Dibs"],
            ["Website URL", "https://dynamicdrips.com"],
            ["Account Manager", "Kevin L"],
            ["Website Login URL", "https://dynamicdrips.com/wp-admin"],
            ["Website Username", "admin"],
            ["Website Password", "hunter2"],
        ]
    )
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH), mongo_uri="mongodb://fake-uri")
    app = create_app(
        settings,
        mongo_collection_factory=lambda: fake_mongo_collection,
        workbook_sheets_client_factory=lambda: fake_sheets_client,
    )
    async with Client(app) as client:
        await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "whatever i typed", "month": "2025-09"}
        )

    stored = fake_mongo_collection.documents["dynamic_dibs-2025-09"]
    assert stored["client_details"] == {
        "website": "https://dynamicdrips.com",
        "account_manager": "Kevin L",
    }
    dumped = str(stored)
    assert "hunter2" not in dumped
    assert "wp-admin" not in dumped
    assert "admin" not in stored["client_details"].values()


async def test_import_legacy_workbook_falls_back_to_typed_name_when_no_client_details_tab(import_app):
    app, _, _ = import_app
    async with Client(app) as client:
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ", "month": "2025-09"}
        )

    assert result.data["client"] == "KYZ"


async def test_import_legacy_workbook_falls_back_to_typed_name_when_business_name_blank():
    fake_sheets_client = _FakeSheetsClient(client_details_values=[["Client Business Name", ""]])
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH), mongo_uri="mongodb://fake-uri")
    app = create_app(
        settings,
        mongo_collection_factory=lambda: FakeMongoCollection(),
        workbook_sheets_client_factory=lambda: fake_sheets_client,
    )
    async with Client(app) as client:
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ", "month": "2025-09"}
        )

    assert result.data["client"] == "KYZ"


# ---------------------------------------------------------------------------
# Immediate table-report link on import
# ---------------------------------------------------------------------------

class _FakeBlob:
    def __init__(self):
        self.uploaded_content = None

    def upload_from_string(self, content, content_type=None):
        self.uploaded_content = content


class _FakeBucket:
    def __init__(self):
        self.blobs: dict[str, "_FakeBlob"] = {}

    def blob(self, blob_name):
        blob = _FakeBlob()
        self.blobs[blob_name] = blob
        return blob


class _FakeStorageClient:
    def __init__(self):
        self.bucket_instance = _FakeBucket()

    def bucket(self, bucket_name):
        return self.bucket_instance


@pytest.fixture
def import_app_with_reports_configured():
    fake_mongo_collection = FakeMongoCollection()
    fake_storage_client = _FakeStorageClient()
    fake_report_tokens = FakeReportTokensCollection()
    fake_sheets_client = _FakeSheetsClient()
    settings = McpSettings(
        best_practices_csv_path=str(CSV_PATH),
        mongo_uri="mongodb://fake-uri",
        reports_bucket="test-reports-bucket",
        agent_public_url="https://agent.example.com",
    )
    app = create_app(
        settings,
        mongo_collection_factory=lambda: fake_mongo_collection,
        workbook_sheets_client_factory=lambda: fake_sheets_client,
        storage_client_factory=lambda: fake_storage_client,
        report_tokens_collection_factory=lambda: fake_report_tokens,
    )
    return app, fake_storage_client, fake_report_tokens


async def test_import_legacy_workbook_generates_a_table_report_link_for_each_imported_month(
    import_app_with_reports_configured,
):
    app, fake_storage_client, _ = import_app_with_reports_configured
    async with Client(app) as client:
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ"}
        )

    assert set(result.data["table_reports"].keys()) == {"kyz-2025-09", "kyz-2025-10"}
    for url in result.data["table_reports"].values():
        assert url.startswith("https://agent.example.com/reports/")
    assert "KYZ-2025-09-seo-plan-table.html" in fake_storage_client.bucket_instance.blobs


async def test_import_legacy_workbook_no_table_report_for_skipped_month(import_app_with_reports_configured):
    app, _, _ = import_app_with_reports_configured
    async with Client(app) as client:
        await client.call_tool("start_session", {"client": "KYZ", "month": "2025-09"})
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ"}
        )

    assert "kyz-2025-09" not in result.data["table_reports"]
    assert "kyz-2025-10" in result.data["table_reports"]


async def test_import_legacy_workbook_warns_when_reports_not_configured(import_app):
    # import_app's settings have no reports_bucket/agent_public_url set.
    app, _, _ = import_app
    async with Client(app) as client:
        result = await client.call_tool(
            "import_legacy_workbook", {"spreadsheet_id": "sheet-id", "client": "KYZ"}
        )

    assert result.data["table_reports"] == {}
    assert any("table_reports not generated" in w for w in result.data["warnings"])
