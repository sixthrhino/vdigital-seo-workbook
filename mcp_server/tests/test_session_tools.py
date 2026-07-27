from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from seo_workbook_mcp.app import create_app

from conftest import CSV_PATH
from seo_workbook_mcp.config import McpSettings


async def test_start_session_creates_draft_session(mcp_app):
    async with Client(mcp_app) as client:
        result = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
    assert result.data["session_id"] == "kyz-2026-06"
    assert result.data["status"] == "draft"
    assert result.data["pages"] == []


async def test_start_session_duplicate_raises(mcp_app):
    async with Client(mcp_app) as client:
        await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        with pytest.raises(ToolError):
            await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})


async def test_add_page_appends_to_session(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        page = await client.call_tool(
            "add_page", {"session_id": session.data["session_id"], "url": "https://kyz.com/service-a/"}
        )
        assert page.data["url"] == "https://kyz.com/service-a/"
        assert page.data["touchpoints"] == []

        state = await client.call_tool("get_session", {"session_id": session.data["session_id"]})
        assert len(state.data["pages"]) == 1


async def test_add_page_to_unknown_session_raises(mcp_app):
    async with Client(mcp_app) as client:
        with pytest.raises(ToolError):
            await client.call_tool("add_page", {"session_id": "does-not-exist", "url": "https://kyz.com/"})


async def test_set_page_targeting_parses_keyword_shorthand(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "IECRM", "month": "2026-01"})
        session_id = session.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://iecrm.org/faqs/"})

        page = await client.call_tool(
            "set_page_targeting",
            {
                "session_id": session_id,
                "url": "https://iecrm.org/faqs/",
                "keyword": "IEC rocky mountain (100)",
                "geo": "Northglenn, CO",
            },
        )
        assert page.data["keyword_target"] == {"keyword": "IEC rocky mountain", "search_volume": 100}
        assert page.data["geo"] == "Northglenn, CO"


async def test_set_page_targeting_missing_page_raises(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        with pytest.raises(ToolError):
            await client.call_tool(
                "set_page_targeting",
                {"session_id": session.data["session_id"], "url": "https://kyz.com/never-added/", "geo": "Denver"},
            )


async def test_record_touchpoint_single_item_validates(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})

        result = await client.call_tool(
            "record_touchpoint",
            {
                "session_id": session_id,
                "url": "https://kyz.com/a/",
                "touchpoint_id": "title_tag",
                "items": [{"new_value": "Auto Insurance in Scottsdale", "primary_keyword": "auto insurance"}],
            },
        )
        assert result.data["category"] == "Core"
        assert result.data["validation"]["passed"] is True


async def test_record_touchpoint_reports_validation_failure(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})

        result = await client.call_tool(
            "record_touchpoint",
            {
                "session_id": session_id,
                "url": "https://kyz.com/a/",
                "touchpoint_id": "title_tag",
                "items": [{"new_value": "x" * 61}],
            },
        )
        assert result.data["validation"]["passed"] is False
        assert any("60" in m for m in result.data["validation"]["messages"])


async def test_record_touchpoint_multi_item_heading_changes(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "IECRM", "month": "2026-06"})
        session_id = session.data["session_id"]
        await client.call_tool(
            "add_page", {"session_id": session_id, "url": "https://iecrm.org/veteran-benefits/"}
        )

        result = await client.call_tool(
            "record_touchpoint",
            {
                "session_id": session_id,
                "url": "https://iecrm.org/veteran-benefits/",
                "touchpoint_id": "h2_h3_h4_tags",
                "items": [
                    {"old_tag": "h4", "new_tag": "h3", "heading_text": "Common Career Paths"},
                    {"old_tag": "h4", "new_tag": "h3", "heading_text": "How to use your GI benefits"},
                ],
            },
        )
        assert len(result.data["items"]) == 2
        assert result.data["validation"]["passed"] is True


async def test_record_touchpoint_replaces_previous_answer(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})

        common_args = {
            "session_id": session_id,
            "url": "https://kyz.com/a/",
            "touchpoint_id": "title_tag",
        }
        await client.call_tool(
            "record_touchpoint",
            {**common_args, "items": [{"new_value": "First Title", "primary_keyword": "x"}]},
        )
        await client.call_tool(
            "record_touchpoint",
            {**common_args, "items": [{"new_value": "Second Title", "primary_keyword": "y"}]},
        )

        state = await client.call_tool("get_session", {"session_id": session_id})
        page = state.data["pages"][0]
        title_touchpoints = [tp for tp in page["touchpoints"] if tp["touchpoint_id"] == "title_tag"]
        assert len(title_touchpoints) == 1
        assert title_touchpoints[0]["items"][0]["new_value"] == "Second Title"


async def test_record_touchpoint_unknown_touchpoint_raises(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})
        with pytest.raises(ToolError):
            await client.call_tool(
                "record_touchpoint",
                {
                    "session_id": session_id,
                    "url": "https://kyz.com/a/",
                    "touchpoint_id": "not_a_real_touchpoint",
                    "items": [{"new_value": "x"}],
                },
            )


async def test_list_open_questions_reflects_progress(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})

        open_before = await client.call_tool("list_open_questions", {"session_id": session_id})
        assert len(open_before.data) == 1

        await client.call_tool(
            "record_touchpoint",
            {
                "session_id": session_id,
                "url": "https://kyz.com/a/",
                "touchpoint_id": "title_tag",
                "items": [{"new_value": "Good Title", "primary_keyword": "insurance"}],
            },
        )
        open_after = await client.call_tool("list_open_questions", {"session_id": session_id})
        assert open_after.data == []


async def test_finalize_session_requires_completion(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})
        with pytest.raises(ToolError):
            await client.call_tool("finalize_session", {"session_id": session_id})


async def test_finalize_session_succeeds_once_complete(mcp_app, fake_mongo_collection):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})
        await client.call_tool(
            "record_touchpoint",
            {
                "session_id": session_id,
                "url": "https://kyz.com/a/",
                "touchpoint_id": "title_tag",
                "items": [{"new_value": "Good Title", "primary_keyword": "insurance"}],
            },
        )
        result = await client.call_tool("finalize_session", {"session_id": session_id})
        assert result.data["status"] == "finalized"
        assert result.data["finalized_at"] is not None

    # Persisted to Mongo (the fake collection) as the system of record.
    saved = fake_mongo_collection.documents[session_id]
    assert saved["status"] == "finalized"
    assert saved["client"] == "KYZ"


async def test_start_session_requires_mongo_configured():
    # Every mutating tool persists to Mongo now (not just finalize_session),
    # so a misconfigured deployment fails on the very first call instead of
    # only surfacing at the end of a long conversation.
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH))  # mongo_uri left empty
    app = create_app(settings)
    async with Client(app) as client:
        with pytest.raises(ToolError, match="mongo_uri is not configured"):
            await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})


async def test_draft_session_is_persisted_to_mongo_before_finalizing(mcp_app, fake_mongo_collection):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})
        await client.call_tool(
            "record_touchpoint",
            {
                "session_id": session_id,
                "url": "https://kyz.com/a/",
                "touchpoint_id": "title_tag",
                "items": [{"new_value": "Good Title", "primary_keyword": "insurance"}],
            },
        )

    # Persisted incrementally as a draft — recoverable even before finalizing.
    saved = fake_mongo_collection.documents[session_id]
    assert saved["status"] == "draft"
    assert saved["finalized_at"] is None
    assert saved["pages"][0]["touchpoints"][0]["touchpoint_id"] == "title_tag"
