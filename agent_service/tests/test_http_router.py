from fastapi.testclient import TestClient

from seo_workbook_agent.config import get_agent_settings
from seo_workbook_agent.main import create_app

from conftest import StubAgentCore


def _client(monkeypatch, run_api_key: str | None = "secret123"):
    if run_api_key is None:
        monkeypatch.delenv("SEO_WORKBOOK_RUN_API_KEY", raising=False)
    else:
        monkeypatch.setenv("SEO_WORKBOOK_RUN_API_KEY", run_api_key)
    get_agent_settings.cache_clear()

    stub = StubAgentCore()
    app = create_app(agent_core=stub)
    return TestClient(app), stub


def test_run_requires_api_key(monkeypatch):
    client, _ = _client(monkeypatch)
    response = client.post("/run", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 401


def test_run_rejects_wrong_api_key(monkeypatch):
    client, _ = _client(monkeypatch)
    response = client.post("/run", json={"session_id": "s1", "message": "hi"}, headers={"X-Api-Key": "wrong"})
    assert response.status_code == 401


def test_run_succeeds_with_correct_key(monkeypatch):
    client, stub = _client(monkeypatch)
    response = client.post(
        "/run",
        json={"session_id": "s1", "message": "hi", "user_id": "u1"},
        headers={"X-Api-Key": "secret123"},
    )
    assert response.status_code == 200
    assert response.json() == {"reply": "stub reply"}
    assert stub.calls == [("u1", "s1", "hi")]


def test_run_defaults_user_id_when_omitted(monkeypatch):
    client, stub = _client(monkeypatch)
    client.post("/run", json={"session_id": "s1", "message": "hi"}, headers={"X-Api-Key": "secret123"})
    assert stub.calls == [("default-user", "s1", "hi")]


def test_run_503_when_key_not_configured(monkeypatch):
    client, _ = _client(monkeypatch, run_api_key=None)
    response = client.post("/run", json={"session_id": "s1", "message": "hi"}, headers={"X-Api-Key": "anything"})
    assert response.status_code == 503
