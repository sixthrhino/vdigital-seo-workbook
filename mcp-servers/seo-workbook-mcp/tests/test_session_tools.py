from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from seo_workbook_mcp.app import create_app

from conftest import CSV_PATH, FakeMongoCollection
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


async def test_start_session_does_not_overwrite_a_record_that_only_survives_in_mongo():
    # Simulates a restart: the session was created and worked on by one
    # mcp-server process, that process's in-memory SessionStore is gone,
    # and start_session is called again for the same client/month. Without
    # the Mongo-aware duplicate check, this would silently replace_one()
    # over the existing record via _persist(), discarding its pages.
    shared_mongo_collection = FakeMongoCollection()
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH), mongo_uri="mongodb://fake-uri")

    app_before_restart = create_app(settings, mongo_collection_factory=lambda: shared_mongo_collection)
    async with Client(app_before_restart) as client:
        session_id = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session_id.data["session_id"]
        await client.call_tool("add_page", {"session_id": session_id, "url": "https://kyz.com/a/"})

    app_after_restart = create_app(settings, mongo_collection_factory=lambda: shared_mongo_collection)
    async with Client(app_after_restart) as client:
        with pytest.raises(ToolError, match="already exists"):
            await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})

    # The original record — including the page recorded before the
    # "restart" — must be untouched.
    saved = shared_mongo_collection.documents[session_id]
    assert len(saved["pages"]) == 1
    assert saved["pages"][0]["url"] == "https://kyz.com/a/"


async def test_find_session_returns_existing_session_by_client_and_month(mcp_app):
    async with Client(mcp_app) as client:
        await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        await client.call_tool("add_page", {"session_id": "kyz-2026-06", "url": "https://kyz.com/a/"})

        result = await client.call_tool("find_session", {"client": "KYZ", "month": "2026-06"})

    assert result.data["session_id"] == "kyz-2026-06"
    assert len(result.data["pages"]) == 1
    assert result.data["pages"][0]["url"] == "https://kyz.com/a/"


async def test_find_session_unknown_client_month_raises(mcp_app):
    async with Client(mcp_app) as client:
        with pytest.raises(ToolError, match="No session found"):
            await client.call_tool("find_session", {"client": "KYZ", "month": "2026-06"})


async def test_find_session_works_after_a_simulated_restart():
    # The real motivation: asking for a summary of a past month's plan from
    # a brand new conversation, where nothing is in this process's memory —
    # only find_session's Mongo fallback (via SessionStore.get) makes that
    # work at all.
    shared_mongo_collection = FakeMongoCollection()
    settings = McpSettings(best_practices_csv_path=str(CSV_PATH), mongo_uri="mongodb://fake-uri")

    app_before_restart = create_app(settings, mongo_collection_factory=lambda: shared_mongo_collection)
    async with Client(app_before_restart) as client:
        await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        await client.call_tool("add_page", {"session_id": "kyz-2026-06", "url": "https://kyz.com/a/"})
        await client.call_tool(
            "record_touchpoint",
            {
                "session_id": "kyz-2026-06",
                "url": "https://kyz.com/a/",
                "touchpoint_id": "title_tag",
                "items": [{"new_value": "Good Title", "primary_keyword": "insurance"}],
            },
        )
        await client.call_tool("finalize_session", {"session_id": "kyz-2026-06"})

    app_after_restart = create_app(settings, mongo_collection_factory=lambda: shared_mongo_collection)
    async with Client(app_after_restart) as client:
        result = await client.call_tool("find_session", {"client": "KYZ", "month": "2026-06"})

    assert result.data["session_id"] == "kyz-2026-06"
    assert result.data["status"] == "finalized"


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


async def test_record_page_from_text_parses_every_field(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]

        result = await client.call_tool(
            "record_page_from_text",
            {
                "session_id": session_id,
                "text": (
                    "url: https://kyz.com/a/\n"
                    "keyword: auto insurance (500)\n"
                    "geo: Scottsdale, AZ\n"
                    "title: Old Title -> New Title\n"
                    "meta: Old meta -> New meta\n"
                    "cta: Get a Quote\n"
                    "h1: Old H1 -> New H1\n"
                    "notes: Added internal link to homepage."
                ),
            },
        )

        page = result.data
        assert page["url"] == "https://kyz.com/a/"
        assert page["keyword_target"]["keyword"] == "auto insurance"
        assert page["keyword_target"]["search_volume"] == 500
        assert page["geo"] == "Scottsdale, AZ"

        by_id = {tp["touchpoint_id"]: tp for tp in page["touchpoints"]}
        assert by_id["title_tag"]["items"][0] == {
            "new_value": "New Title", "old_value": "Old Title", "primary_keyword": "auto insurance",
        }
        assert by_id["title_tag"]["category"] == "Core"
        assert by_id["meta_description"]["items"][0] == {
            "new_value": "New meta", "old_value": "Old meta", "cta": "Get a Quote",
        }
        assert by_id["h1_tag"]["items"][0] == {
            "new_value": "New H1", "old_value": "Old H1", "primary_keyword": "auto insurance",
        }
        assert by_id["optimizations"]["items"][0] == {"note": "Added internal link to homepage."}
        assert by_id["optimizations"]["category"] == "Optimizations"


async def test_record_page_from_text_adds_the_page_automatically(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]

        await client.call_tool(
            "record_page_from_text",
            {"session_id": session_id, "text": "url: https://kyz.com/a/\ntitle: New Title"},
        )

        state = await client.call_tool("get_session", {"session_id": session_id})
        assert len(state.data["pages"]) == 1
        assert state.data["pages"][0]["url"] == "https://kyz.com/a/"


async def test_record_page_from_text_surfaces_validation_failure(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]

        # meta given with no cta — validate_touchpoint requires one.
        result = await client.call_tool(
            "record_page_from_text",
            {"session_id": session_id, "text": "url: https://kyz.com/a/\nmeta: New meta only, no CTA"},
        )

        meta = next(tp for tp in result.data["touchpoints"] if tp["touchpoint_id"] == "meta_description")
        assert meta["validation"]["passed"] is False
        assert any("cta" in m for m in meta["validation"]["messages"])


async def test_record_page_from_text_only_touches_fields_present_on_recall(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]

        await client.call_tool(
            "record_page_from_text",
            {
                "session_id": session_id,
                "text": "url: https://kyz.com/a/\ntitle: New Title\nkeyword: widgets\nh1: New H1",
            },
        )
        # A follow-up correction only naming title shouldn't wipe out h1.
        result = await client.call_tool(
            "record_page_from_text",
            {"session_id": session_id, "text": "url: https://kyz.com/a/\ntitle: Corrected Title"},
        )

        by_id = {tp["touchpoint_id"]: tp for tp in result.data["touchpoints"]}
        assert by_id["title_tag"]["items"][0]["new_value"] == "Corrected Title"
        assert by_id["h1_tag"]["items"][0]["new_value"] == "New H1"


async def test_record_page_from_text_keyword_autofills_primary_keyword(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]

        result = await client.call_tool(
            "record_page_from_text",
            {
                "session_id": session_id,
                "text": "url: https://kyz.com/a/\nkeyword: back pain\ntitle: New Title\nh1: New H1",
            },
        )

        by_id = {tp["touchpoint_id"]: tp for tp in result.data["touchpoints"]}
        assert by_id["title_tag"]["items"][0]["primary_keyword"] == "back pain"
        assert by_id["h1_tag"]["items"][0]["primary_keyword"] == "back pain"


async def test_record_page_from_text_missing_url_raises(mcp_app):
    async with Client(mcp_app) as client:
        session = await client.call_tool("start_session", {"client": "KYZ", "month": "2026-06"})
        session_id = session.data["session_id"]
        with pytest.raises(ToolError, match="Missing \"url:\" line"):
            await client.call_tool(
                "record_page_from_text", {"session_id": session_id, "text": "title: New Title Only"}
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
