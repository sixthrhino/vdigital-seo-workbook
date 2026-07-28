from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest


async def test_list_touchpoints_returns_all(mcp_app):
    async with Client(mcp_app) as client:
        result = await client.call_tool("list_touchpoints", {})
    assert len(result.data) == 33
    assert {"touchpoint_id", "name", "category", "search_tactic", "description"} <= result.data[0].keys()


async def test_list_touchpoints_filters_by_category(mcp_app):
    async with Client(mcp_app) as client:
        result = await client.call_tool("list_touchpoints", {"category": "Core"})
    ids = {tp["touchpoint_id"] for tp in result.data}
    assert ids == {"h1_tag", "meta_description", "title_tag"}


async def test_get_touchpoint_detail_returns_qa_guidelines(mcp_app):
    async with Client(mcp_app) as client:
        result = await client.call_tool("get_touchpoint_detail", {"touchpoint_id": "title_tag"})
    assert result.data["touchpoint_id"] == "title_tag"
    assert len(result.data["qa_guidelines"]) == 4
    assert result.data["implementation_notes"]


async def test_get_touchpoint_detail_unknown_id_raises(mcp_app):
    async with Client(mcp_app) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_touchpoint_detail", {"touchpoint_id": "not_a_real_touchpoint"})
