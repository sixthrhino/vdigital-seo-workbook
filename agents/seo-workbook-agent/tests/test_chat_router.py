from unittest.mock import patch

from fastapi.testclient import TestClient

from seo_workbook_agent.chat_auth import ChatAuthError
from seo_workbook_agent.config import get_agent_settings
from seo_workbook_agent.main import create_app

from conftest import StubAgentCore

VERIFY_PATH = "seo_workbook_agent.routers.chat_router.verify_chat_bearer_token"


class _FakeCreateRequest:
    def __init__(self, parent, body):
        self.parent = parent
        self.body = body

    def execute(self):
        return {"name": f"{self.parent}/messages/fake-id"}


class _FakeMessages:
    def __init__(self):
        self.create_calls = []

    def create(self, *, parent, body):
        self.create_calls.append({"parent": parent, "body": body})
        return _FakeCreateRequest(parent, body)


class _FakeSpaces:
    def __init__(self):
        self.messages_resource = _FakeMessages()

    def messages(self):
        return self.messages_resource


class _FakeChatService:
    def __init__(self):
        self.spaces_resource = _FakeSpaces()

    def spaces(self):
        return self.spaces_resource


class _RaisingAgentCore:
    async def handle_turn(self, *, user_id, session_id, message):
        raise RuntimeError("boom")


def _client(monkeypatch, chat_audience: str | None = "https://agent.example.com/chat", agent_core=None):
    if chat_audience is None:
        monkeypatch.delenv("SEO_WORKBOOK_CHAT_AUDIENCE", raising=False)
    else:
        monkeypatch.setenv("SEO_WORKBOOK_CHAT_AUDIENCE", chat_audience)
    get_agent_settings.cache_clear()

    stub = agent_core or StubAgentCore()
    fake_chat_service = _FakeChatService()
    app = create_app(agent_core=stub, chat_client_factory=lambda: fake_chat_service)
    return TestClient(app), stub, fake_chat_service


def test_chat_rejects_missing_auth(monkeypatch):
    client, _, _ = _client(monkeypatch)
    response = client.post("/chat", json={"type": "MESSAGE", "message": {"text": "hi"}})
    assert response.status_code == 401


def test_chat_503_when_audience_not_configured(monkeypatch):
    client, _, _ = _client(monkeypatch, chat_audience=None)
    response = client.post(
        "/chat", headers={"Authorization": "Bearer x"}, json={"type": "MESSAGE", "message": {"text": "hi"}}
    )
    assert response.status_code == 503


def test_chat_acks_immediately_and_posts_reply_asynchronously(monkeypatch):
    client, stub, fake_chat_service = _client(monkeypatch)
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
    # The synchronous ack carries no message content — Chat interprets an
    # empty body as "no synchronous reply, one may follow asynchronously".
    assert response.status_code == 200
    assert response.json() == {}
    assert stub.calls == [("users/123", "spaces/AAAA", "create the plan for June")]

    call = fake_chat_service.spaces_resource.messages_resource.create_calls[0]
    assert call["parent"] == "spaces/AAAA"
    assert call["body"] == {"text": "stub reply"}


def test_chat_converts_markdown_bold_to_chat_markup_before_posting(monkeypatch):
    stub = StubAgentCore(reply="**Primary Keyword:** Custom Clothing")
    client, _, fake_chat_service = _client(monkeypatch, agent_core=stub)
    with patch(VERIFY_PATH):
        client.post(
            "/chat",
            headers={"Authorization": "Bearer faketoken"},
            json={
                "type": "MESSAGE",
                "message": {"text": "summarize"},
                "space": {"name": "spaces/AAAA"},
                "user": {"name": "users/123"},
            },
        )
    call = fake_chat_service.spaces_resource.messages_resource.create_calls[0]
    assert call["body"]["text"] == "*Primary Keyword:* Custom Clothing"


def test_chat_posts_reply_into_the_same_thread_when_present(monkeypatch):
    client, _, fake_chat_service = _client(monkeypatch)
    with patch(VERIFY_PATH):
        client.post(
            "/chat",
            headers={"Authorization": "Bearer faketoken"},
            json={
                "type": "MESSAGE",
                "message": {"text": "yes", "thread": {"name": "spaces/AAAA/threads/BBBB"}},
                "space": {"name": "spaces/AAAA"},
                "user": {"name": "users/123"},
            },
        )
    call = fake_chat_service.spaces_resource.messages_resource.create_calls[0]
    assert call["body"]["thread"] == {"name": "spaces/AAAA/threads/BBBB"}


def test_chat_posts_a_fallback_message_when_the_agent_turn_raises(monkeypatch):
    client, _, fake_chat_service = _client(monkeypatch, agent_core=_RaisingAgentCore())
    with patch(VERIFY_PATH):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer faketoken"},
            json={
                "type": "MESSAGE",
                "message": {"text": "hi"},
                "space": {"name": "spaces/AAAA"},
                "user": {"name": "users/123"},
            },
        )
    # Still acks cleanly even though the background task failed.
    assert response.status_code == 200
    call = fake_chat_service.spaces_resource.messages_resource.create_calls[0]
    assert "went wrong" in call["body"]["text"]


def test_chat_rejects_invalid_token(monkeypatch):
    client, stub, fake_chat_service = _client(monkeypatch)
    with patch(VERIFY_PATH, side_effect=ChatAuthError("bad token")):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer bad"},
            json={"type": "MESSAGE", "message": {"text": "hi"}},
        )
    assert response.status_code == 401
    assert stub.calls == []
    assert fake_chat_service.spaces_resource.messages_resource.create_calls == []


def test_chat_ignores_non_message_events(monkeypatch):
    client, stub, fake_chat_service = _client(monkeypatch)
    with patch(VERIFY_PATH):
        response = client.post(
            "/chat", headers={"Authorization": "Bearer faketoken"}, json={"type": "ADDED_TO_SPACE"}
        )
    assert response.status_code == 200
    assert response.json() == {}
    assert stub.calls == []
    assert fake_chat_service.spaces_resource.messages_resource.create_calls == []


def test_chat_ignores_blank_message_text(monkeypatch):
    client, stub, fake_chat_service = _client(monkeypatch)
    with patch(VERIFY_PATH):
        response = client.post(
            "/chat",
            headers={"Authorization": "Bearer faketoken"},
            json={"type": "MESSAGE", "message": {"text": "   "}, "space": {"name": "spaces/AAAA"}},
        )
    assert response.status_code == 200
    assert response.json() == {}
    assert stub.calls == []
    assert fake_chat_service.spaces_resource.messages_resource.create_calls == []
