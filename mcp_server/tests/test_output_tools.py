from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from seo_workbook_mcp.app import create_app

from conftest import CSV_PATH, FakeMongoCollection
from seo_workbook_mcp.config import McpSettings


async def _start_session_with_a_touchpoint(client, client_name="KYZ", month="2026-06"):
    session = await client.call_tool("start_session", {"client": client_name, "month": month})
    session_id = session.data["session_id"]
    await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})
    await client.call_tool(
        "record_touchpoint",
        {
            "session_id": session_id,
            "url": "https://kyz.com/a/",
            "touchpoint_id": "title_tag",
            "items": [{"new_value": "Auto Insurance in Scottsdale", "primary_keyword": "auto insurance"}],
        },
    )
    return session_id


class _FakeBlob:
    def __init__(self):
        self.uploaded_content = None
        self.generate_signed_url_calls = []

    def upload_from_string(self, content, content_type=None):
        self.uploaded_content = content

    def generate_signed_url(self, *, version, expiration, method, service_account_email=None, access_token=None):
        self.generate_signed_url_calls.append(
            {"service_account_email": service_account_email, "access_token": access_token}
        )
        return "https://storage.googleapis.com/fake-bucket/fake-report.html?signed=1"


class _FakeBucket:
    def __init__(self):
        self.blobs: dict[str, _FakeBlob] = {}

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


@pytest.fixture
def mcp_app_with_fake_storage():
    fake_client = _FakeStorageClient()
    settings = McpSettings(
        best_practices_csv_path=str(CSV_PATH), reports_bucket="test-reports-bucket", mongo_uri="mongodb://fake-uri"
    )
    app = create_app(
        settings,
        storage_client_factory=lambda: fake_client,
        signing_credentials_factory=lambda: ("fake@test.iam.gserviceaccount.com", "fake-access-token"),
        mongo_collection_factory=lambda: FakeMongoCollection(),
    )
    return app, fake_client


async def test_render_session_report_uploads_and_returns_signed_url(mcp_app_with_fake_storage):
    app, fake_client = mcp_app_with_fake_storage
    async with Client(app) as client:
        session_id = await _start_session_with_a_touchpoint(client)
        result = await client.call_tool("render_session_report", {"session_id": session_id})

    assert result.data["filename"] == "KYZ-2026-06-seo-plan.html"
    assert result.data["report_url"] == "https://storage.googleapis.com/fake-bucket/fake-report.html?signed=1"
    assert fake_client.requested_bucket_name == "test-reports-bucket"

    blob = fake_client.bucket_instance.blobs["KYZ-2026-06-seo-plan.html"]
    assert "KYZ" in blob.uploaded_content
    assert "Title Tag" in blob.uploaded_content  # resolved via catalog, not the raw touchpoint_id

    # Cloud Run's attached service account has no private key — signing
    # must go through the IAM API instead, which needs these two explicitly.
    signing_call = blob.generate_signed_url_calls[0]
    assert signing_call["service_account_email"] == "fake@test.iam.gserviceaccount.com"
    assert signing_call["access_token"] == "fake-access-token"


async def test_render_session_report_unknown_session_raises(mcp_app_with_fake_storage):
    app, _ = mcp_app_with_fake_storage
    async with Client(app) as client:
        with pytest.raises(ToolError):
            await client.call_tool("render_session_report", {"session_id": "does-not-exist"})


async def test_render_session_report_requires_reports_bucket_configured(mcp_app):
    # mcp_app fixture has no reports_bucket set — should fail clearly rather
    # than attempting a real GCS call.
    async with Client(mcp_app) as client:
        session_id = await _start_session_with_a_touchpoint(client)
        with pytest.raises(ToolError, match="reports_bucket is not configured"):
            await client.call_tool("render_session_report", {"session_id": session_id})


class _FakeValues:
    def __init__(self):
        self.update_calls = []

    def update(self, *, spreadsheetId, range, valueInputOption, body):
        self.update_calls.append(
            {"spreadsheetId": spreadsheetId, "range": range, "valueInputOption": valueInputOption, "body": body}
        )
        return _FakeRequest()


class _FakeRequest:
    def execute(self):
        return {"updatedCells": 99}


class _FakeSpreadsheets:
    def __init__(self):
        self.values_resource = _FakeValues()

    def values(self):
        return self.values_resource


class _FakeSheetsService:
    def __init__(self):
        self.spreadsheets_resource = _FakeSpreadsheets()

    def spreadsheets(self):
        return self.spreadsheets_resource


@pytest.fixture
def mcp_app_with_fake_sheets():
    fake_service = _FakeSheetsService()
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH), mongo_uri="mongodb://fake-uri")
    app = create_app(
        settings,
        sheets_client_factory=lambda: fake_service,
        mongo_collection_factory=lambda: FakeMongoCollection(),
    )
    return app, fake_service


async def test_export_session_to_sheet_writes_expected_rows(mcp_app_with_fake_sheets):
    app, fake_service = mcp_app_with_fake_sheets
    async with Client(app) as client:
        session_id = await _start_session_with_a_touchpoint(client)
        result = await client.call_tool(
            "export_session_to_sheet", {"session_id": session_id, "spreadsheet_id": "sheet-abc"}
        )

    assert result.data == {"rows_written": 1, "spreadsheet_id": "sheet-abc", "result": {"updatedCells": 99}}
    call = fake_service.spreadsheets_resource.values_resource.update_calls[0]
    assert call["spreadsheetId"] == "sheet-abc"
    assert call["range"] == "A1"
    header, data_row = call["body"]["values"]
    assert header[0] == "Month"
    assert data_row[2] == "https://kyz.com/a/"
    assert data_row[7] == "Title Tag"  # resolved via catalog, not the raw touchpoint_id


async def test_export_session_to_sheet_unknown_session_raises(mcp_app_with_fake_sheets):
    app, _ = mcp_app_with_fake_sheets
    async with Client(app) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "export_session_to_sheet", {"session_id": "does-not-exist", "spreadsheet_id": "sheet-abc"}
            )
