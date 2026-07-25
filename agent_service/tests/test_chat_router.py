from unittest.mock import patch

from fastapi.testclient import TestClient

from seo_workbook_agent.chat_auth import ChatAuthError
from seo_workbook_agent.config import get_agent_settings
from seo_workbook_agent.main import create_app

from conftest import StubAgentCore

VERIFY_PATH = "seo_workbook_agent.routers.chat_router.verify_chat_bearer_token"


def _client(monkeypatch, chat_audience: str | None = "https://agent.example.com/chat"):
    if chat_audience is None:
        monkeypatch.delenv("SEO_WORKBOOK_CHAT_AUDIENCE", raising=False)
    else:
        monkeypatch.setenv("SEO_WORKBOOK_CHAT_AUDIENCE", chat_audience)
    get_agent_settings.cache_clear()

    stub = StubAgentCore()
    app = create_app(agent_core=stub)
    return TestClient(app), stub


def test_chat_rejects_missing_auth(monkeypatch):
    client, _ = _client(monkeypatch)
    response = client.post("/chat", json={"type": "MESSAGE", "message": {"text": "hi"}})
    assert response.status_code == 401


def test_chat_503_when_audience_not_configured(monkeypatch):
    client, _ = _client(monkeypatch, chat_audience=None)
    response = client.post(
        "/chat", headers={"Authorization": "Bearer x"}, json={"type": "MESSAGE", "message": {"text": "hi"}}
    )
    assert response.status_code == 503


def test_chat_accepts_verified_message_and_replies(monkeypatch):
    client, stub = _client(monkeypatch)
    with patch(VERIFY_PATH):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer faketoken"},
            json={
                "type": "MESSAGE",
                "message": {"text": "create the plan for June"},
                "space": {"name": "spaces/AAAA"},
                "user": {"name": "users/123"},
            },
        )
    assert response.status_code == 200
    assert response.json() == {"text": "stub reply"}
    assert stub.calls == [("users/123", "spaces/AAAA", "create the plan for June")]


def test_chat_rejects_invalid_token(monkeypatch):
    client, stub = _client(monkeypatch)
    with patch(VERIFY_PATH, side_effect=ChatAuthError("bad token")):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer bad"},
            json={"type": "MESSAGE", "message": {"text": "hi"}},
        )
    assert response.status_code == 401
    assert stub.calls == []


def test_chat_ignores_non_message_events(monkeypatch):
    client, stub = _client(monkeypatch)
    with patch(VERIFY_PATH):
        response = client.post(
            "/chat", headers={"Authorization": "Bearer faketoken"}, json={"type": "ADDED_TO_SPACE"}
        )
    assert response.status_code == 200
    assert response.json() == {"text": ""}
    assert stub.calls == []


def test_chat_ignores_blank_message_text(monkeypatch):
    client, stub = _client(monkeypatch)
    with patch(VERIFY_PATH):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer faketoken"},
            json={"type": "MESSAGE", "message": {"text": "   "}, "space": {"name": "spaces/AAAA"}},
        )
    assert response.status_code == 200
    assert response.json() == {"text": ""}
    assert stub.calls == []
