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

    def test_wires_mcp_toolset_and_the_plan_review_tool(self):
        agent = agent_module.create_agent()
        assert len(agent.tools) == 2
        assert isinstance(agent.tools[0], McpToolset)
        assert agent.tools[1] is agent_module.review_plan_against_live_site

    def test_mcp_toolset_points_at_configured_server(self, monkeypatch):
        monkeypatch.setattr(agent_module.settings, "mcp_server_url", "https://mcp.internal.example")
        agent = agent_module.create_agent()
        toolset = agent.tools[0]
        assert toolset._connection_params.url == "https://mcp.internal.example/sse"

    def test_instruction_covers_both_operating_modes(self):
        agent = agent_module.create_agent()
        assert "Mode A" in agent.instruction
        assert "Mode B" in agent.instruction
        assert "review_plan_against_live_site" in agent.instruction
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


class TestWorkbookMcpAudience:
    def test_strips_mcp_suffix(self, monkeypatch):
        monkeypatch.setattr(agent_module.settings, "workbook_mcp_url", "https://workbook-mcp.example.com/mcp")
        assert agent_module._workbook_mcp_audience() == "https://workbook-mcp.example.com"

    def test_leaves_url_without_mcp_suffix_unchanged(self, monkeypatch):
        monkeypatch.setattr(agent_module.settings, "workbook_mcp_url", "https://workbook-mcp.example.com")
        assert agent_module._workbook_mcp_audience() == "https://workbook-mcp.example.com"


class TestReviewPlanAgainstLiveSite:
    async def test_happy_path_returns_summary_and_report_link(self, monkeypatch):
        session_doc = {"pages": [{"url": "https://example.com/a", "touchpoints": []}]}

        async def fake_fetch(client, month, url, headers):
            assert client == "Acme"
            assert month == "2026-06"
            return session_doc

        monkeypatch.setattr(agent_module, "fetch_plan_session", fake_fetch)

        url_results = [{"url": "https://example.com/a", "verdict": "PASS", "checks": [],
                         "manual_checklist": [], "key_issues": "", "recommended_fixes": ""}]

        async def fake_run_batch(rows, mcp_url, auth_headers):
            return url_results, []

        monkeypatch.setattr(agent_module.check_orchestrator, "run_batch", fake_run_batch)

        async def fake_submit_report(client, month_year, results, mcp_url, auth_headers, brand_guide_notes=None):
            return "https://storage.googleapis.com/signed-url"

        monkeypatch.setattr(agent_module.check_orchestrator, "submit_report", fake_submit_report)

        result = await agent_module.review_plan_against_live_site("Acme", "2026-06")

        assert "https://storage.googleapis.com/signed-url" in result
        assert "All 1 page(s) passed." in result

    async def test_reports_fail_count(self, monkeypatch):
        session_doc = {"pages": [{"url": "https://example.com/a", "touchpoints": []}]}

        async def fake_fetch(*a):
            return session_doc

        monkeypatch.setattr(agent_module, "fetch_plan_session", fake_fetch)

        url_results = [{"url": "https://example.com/a", "verdict": "FAIL", "checks": [],
                         "manual_checklist": [], "key_issues": "", "recommended_fixes": ""}]

        async def fake_run_batch(rows, mcp_url, auth_headers):
            return url_results, []

        monkeypatch.setattr(agent_module.check_orchestrator, "run_batch", fake_run_batch)

        async def fake_submit_report(client, month_year, results, mcp_url, auth_headers, brand_guide_notes=None):
            return "https://storage.googleapis.com/signed-url"

        monkeypatch.setattr(agent_module.check_orchestrator, "submit_report", fake_submit_report)

        result = await agent_module.review_plan_against_live_site("Acme", "2026-06")

        assert "0 passed, 1 need attention." in result

    async def test_no_plan_found_returns_friendly_message(self, monkeypatch):
        async def fake_fetch(*a):
            raise RuntimeError("No session found for client='Acme', month='2026-06'")

        monkeypatch.setattr(agent_module, "fetch_plan_session", fake_fetch)

        result = await agent_module.review_plan_against_live_site("Acme", "2026-06")

        assert "Couldn't find a recorded plan" in result
        assert "Acme" in result

    async def test_no_pages_returns_friendly_message(self, monkeypatch):
        async def fake_fetch(*a):
            return {"pages": []}

        monkeypatch.setattr(agent_module, "fetch_plan_session", fake_fetch)

        result = await agent_module.review_plan_against_live_site("Acme", "2026-06")

        assert "no pages recorded" in result

    async def test_run_batch_failure_returns_friendly_message(self, monkeypatch):
        session_doc = {"pages": [{"url": "https://example.com/a", "touchpoints": []}]}

        async def fake_fetch(*a):
            return session_doc

        monkeypatch.setattr(agent_module, "fetch_plan_session", fake_fetch)

        async def failing_run_batch(rows, mcp_url, auth_headers):
            raise RuntimeError("mcp-server unreachable")

        monkeypatch.setattr(agent_module.check_orchestrator, "run_batch", failing_run_batch)

        result = await agent_module.review_plan_against_live_site("Acme", "2026-06")

        assert "Error running checks" in result
        assert "mcp-server unreachable" in result

    async def test_submit_report_failure_returns_friendly_message(self, monkeypatch):
        session_doc = {"pages": [{"url": "https://example.com/a", "touchpoints": []}]}

        async def fake_fetch(*a):
            return session_doc

        monkeypatch.setattr(agent_module, "fetch_plan_session", fake_fetch)

        async def fake_run_batch(rows, mcp_url, auth_headers):
            return [], []

        monkeypatch.setattr(agent_module.check_orchestrator, "run_batch", fake_run_batch)

        async def failing_submit_report(client, month_year, results, mcp_url, auth_headers, brand_guide_notes=None):
            raise RuntimeError("signing failed")

        monkeypatch.setattr(agent_module.check_orchestrator, "submit_report", failing_submit_report)

        result = await agent_module.review_plan_against_live_site("Acme", "2026-06")

        assert "report generation failed" in result
        assert "signing failed" in result
