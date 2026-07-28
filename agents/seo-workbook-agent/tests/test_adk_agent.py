import pytest

from seo_workbook_agent.adk_agent import build_agent, mcp_audience
from seo_workbook_agent.config import AgentSettings


@pytest.mark.parametrize(
    ("mcp_server_url", "expected_audience"),
    [
        ("https://seo-workbook-mcp-server-xyz.run.app/mcp", "https://seo-workbook-mcp-server-xyz.run.app"),
        ("https://seo-workbook-mcp-server-xyz.run.app", "https://seo-workbook-mcp-server-xyz.run.app"),
        ("http://localhost:8080/mcp", "http://localhost:8080"),
    ],
)
def test_mcp_audience_strips_mcp_suffix(mcp_server_url: str, expected_audience: str):
    assert mcp_audience(mcp_server_url) == expected_audience


def test_build_agent_wires_a_header_provider_onto_the_toolset():
    agent = build_agent(AgentSettings(mcp_server_url="https://example.run.app/mcp"))
    (toolset,) = agent.tools
    assert toolset.header_provider is not None
