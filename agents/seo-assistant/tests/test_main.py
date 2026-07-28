from starlette.testclient import TestClient

from seo_assistant.main import app


def test_run_endpoint_503s_when_run_api_key_not_configured():
    # Reusing seo_workbook_agent's http_router as-is means its existing
    # fail-closed behavior applies here too — confirms the whole app
    # wiring (create_app + injected AgentCore) actually works end to end,
    # not just that build_orchestrator() succeeds in isolation.
    client = TestClient(app)
    response = client.post("/run", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 503
    assert response.json()["detail"] == "RUN_API_KEY is not configured"
