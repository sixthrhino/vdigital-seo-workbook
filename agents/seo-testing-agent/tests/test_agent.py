"""Tests for agent/agent.py"""

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset

import seo_testing_agent.agent as agent_module


class TestCreateAgent:
    def test_returns_llm_agent_with_expected_identity(self):
        agent = agent_module.create_agent()
        assert isinstance(agent, LlmAgent)
        assert agent.name == "web_content_reviewer"
        assert agent.model == agent_module.settings.agent_model

    def test_wires_a_single_mcp_toolset(self):
        agent = agent_module.create_agent()
        assert len(agent.tools) == 1
        assert isinstance(agent.tools[0], McpToolset)

    def test_mcp_toolset_points_at_configured_server(self, monkeypatch):
        monkeypatch.setattr(agent_module.settings, "mcp_server_url", "https://mcp.internal.example")
        agent = agent_module.create_agent()
        toolset = agent.tools[0]
        assert toolset._connection_params.url == "https://mcp.internal.example/sse"

    def test_instruction_covers_both_operating_modes(self):
        agent = agent_module.create_agent()
        assert "Mode A" in agent.instruction
        assert "Mode B" in agent.instruction
        assert "resolve_checks_for_opt_note" in agent.instruction
        assert "generate_report" in agent.instruction

    def test_no_auth_header_in_development(self, monkeypatch):
        monkeypatch.setattr(agent_module.settings, "environment", "development")
        agent = agent_module.create_agent()
        assert agent.tools[0]._connection_params.headers is None

    def test_production_attaches_bearer_id_token_for_mcp_server(self, monkeypatch):
        monkeypatch.setattr(agent_module.settings, "environment", "production")
        monkeypatch.setattr(agent_module.settings, "mcp_server_url", "https://mcp.example.run.app")

        def fake_fetch_id_token(request, audience):
            assert audience == "https://mcp.example.run.app"
            return "fake-id-token"

        monkeypatch.setattr(
            "google.oauth2.id_token.fetch_id_token", fake_fetch_id_token
        )

        agent = agent_module.create_agent()
        headers = agent.tools[0]._connection_params.headers
        assert headers == {"Authorization": "Bearer fake-id-token"}
