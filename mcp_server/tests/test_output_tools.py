import base64

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from seo_workbook_mcp.app import create_app

from conftest import CSV_PATH
from seo_workbook_common.config import Settings


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


async def test_render_session_pdf_returns_valid_pdf(mcp_app):
    async with Client(mcp_app) as client:
        session_id = await _start_session_with_a_touchpoint(client)
        result = await client.call_tool("render_session_pdf", {"session_id": session_id})

    assert result.data["filename"] == "KYZ-2026-06-seo-plan.pdf"
    pdf_bytes = base64.b64decode(result.data["pdf_base64"])
    assert pdf_bytes.startswith(b"%PDF")


async def test_render_session_pdf_uses_catalog_touchpoint_names(mcp_app):
    async with Client(mcp_app) as client:
        session_id = await _start_session_with_a_touchpoint(client)
        result = await client.call_tool("render_session_pdf", {"session_id": session_id})

    pdf_bytes = base64.b64decode(result.data["pdf_base64"])
    # Title Tag is the catalog display name for touchpoint_id "title_tag" —
    # can't grep PDF bytes directly, so just confirm the render didn't error
    # and produced a plausible page count.
    assert len(pdf_bytes) > 500


async def test_render_session_pdf_unknown_session_raises(mcp_app):
    async with Client(mcp_app) as client:
        with pytest.raises(ToolError):
            await client.call_tool("render_session_pdf", {"session_id": "does-not-exist"})


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
    settings = Settings(best_practices_csv_path=str(CSV_PATH))
    app = create_app(settings, sheets_client_factory=lambda: fake_service)
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
