"""Tests for agent/main.py — the /run endpoint, the Google Chat /chat webhook,
and the reply-truncation logic in _run_and_reply.

The ADK Runner is faked out everywhere (no real Gemini calls); only the
FastAPI routing, session wiring, and text-handling logic under test are real.
"""

import httpx
import pytest
import respx
from starlette.testclient import TestClient

import seo_testing_agent.main as agent_main


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakePart:
    def __init__(self, text):
        self.text = text


class FakeContent:
    def __init__(self, parts):
        self.parts = parts


class FakeEvent:
    def __init__(self, text, final=True):
        self._final = final
        self.content = FakeContent([FakePart(text)])

    def is_final_response(self):
        return self._final


def make_fake_runner(events=None, raise_exc=None, events_sequence=None):
    """events_sequence, if given, is a list of event-lists — one per
    run_async call on the same Runner instance (for testing _run_and_reply's
    retry loop). The last entry repeats for any calls beyond its length.
    Otherwise every call yields the fixed `events` list."""
    class FakeRunner:
        def __init__(self, *, agent, app_name, session_service, auto_create_session):
            self.agent = agent
            self.calls = 0

        async def run_async(self, *, user_id, session_id, new_message):
            if raise_exc:
                raise raise_exc
            if events_sequence is not None:
                idx = min(self.calls, len(events_sequence) - 1)
                self.calls += 1
                seq = events_sequence[idx]
            else:
                seq = events or []
            for e in seq:
                yield e

    return FakeRunner


class FakeCreds:
    token = "tok123"

    def refresh(self, request):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    # Force InMemorySessionService regardless of what agent/.env sets locally.
    monkeypatch.setattr(agent_main.settings, "environment", "development")
    monkeypatch.setattr(agent_main, "create_agent", lambda: object())
    with TestClient(agent_main.app) as c:
        yield c


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_reports_configured_environment(client, monkeypatch):
    monkeypatch.setattr(agent_main.settings, "environment", "development")
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "environment": "development"}


# ---------------------------------------------------------------------------
# POST /run
# ---------------------------------------------------------------------------

class TestRunEndpoint:
    def test_returns_final_agent_text(self, client, monkeypatch):
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([FakeEvent("REPORT DONE")]))
        resp = client.post("/run", json={"url": "https://example.com", "rules": ["Title Tag"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"] == "REPORT DONE"
        assert body["session_id"]

    def test_echoes_provided_session_id(self, client, monkeypatch):
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([FakeEvent("ok")]))
        resp = client.post("/run", json={
            "url": "https://example.com", "rules": [], "session_id": "my-session",
        })
        assert resp.json()["session_id"] == "my-session"

    def test_concatenates_multiple_final_parts(self, client, monkeypatch):
        monkeypatch.setattr(
            agent_main, "Runner",
            make_fake_runner([FakeEvent("part1"), FakeEvent("part2")]),
        )
        resp = client.post("/run", json={"url": "https://example.com", "rules": []})
        assert resp.json()["result"] == "part1part2"

    def test_ignores_non_final_events(self, client, monkeypatch):
        monkeypatch.setattr(
            agent_main, "Runner",
            make_fake_runner([FakeEvent("thinking...", final=False), FakeEvent("done")]),
        )
        resp = client.post("/run", json={"url": "https://example.com", "rules": []})
        assert resp.json()["result"] == "done"

    def test_agent_exception_returns_500(self, client, monkeypatch):
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner(raise_exc=RuntimeError("boom")))
        resp = client.post("/run", json={"url": "https://example.com", "rules": []})
        assert resp.status_code == 500
        assert "boom" in resp.json()["detail"]

    def test_invalid_url_is_rejected(self, client):
        resp = client.post("/run", json={"url": "not-a-url", "rules": []})
        assert resp.status_code == 422


class TestRunEndpointApiKey:
    def test_rejects_missing_key_when_configured(self, client, monkeypatch):
        monkeypatch.setattr(agent_main.settings, "run_api_key", "secret123")
        resp = client.post("/run", json={"url": "https://example.com", "rules": []})
        assert resp.status_code == 401

    def test_rejects_wrong_key_when_configured(self, client, monkeypatch):
        monkeypatch.setattr(agent_main.settings, "run_api_key", "secret123")
        resp = client.post(
            "/run", json={"url": "https://example.com", "rules": []},
            headers={"X-Api-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_accepts_correct_key_when_configured(self, client, monkeypatch):
        monkeypatch.setattr(agent_main.settings, "run_api_key", "secret123")
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([FakeEvent("ok")]))
        resp = client.post(
            "/run", json={"url": "https://example.com", "rules": []},
            headers={"X-Api-Key": "secret123"},
        )
        assert resp.status_code == 200

    def test_open_when_key_unconfigured(self, client, monkeypatch):
        monkeypatch.setattr(agent_main.settings, "run_api_key", "")
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([FakeEvent("ok")]))
        resp = client.post("/run", json={"url": "https://example.com", "rules": []})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

class TestChatWebhook:
    def test_added_to_space_returns_greeting(self, client):
        resp = client.post("/chat", json={"type": "ADDED_TO_SPACE"})
        assert resp.status_code == 200
        assert "ready" in resp.json()["text"].lower()

    def test_unrecognized_event_type_is_ignored(self, client):
        resp = client.post("/chat", json={"type": "REMOVED_FROM_SPACE"})
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_message_without_text_is_ignored(self, client):
        resp = client.post("/chat", json={
            "type": "MESSAGE",
            "message": {"text": "  "},
            "space": {"name": "spaces/AAA"},
        })
        assert resp.json() == {}

    def test_message_without_space_is_ignored(self, client):
        resp = client.post("/chat", json={
            "type": "MESSAGE",
            "message": {"text": "hello"},
            "space": {"name": ""},
        })
        assert resp.json() == {}

    def test_message_acks_immediately_and_schedules_background_run(self, client, monkeypatch):
        calls = []

        async def fake_run_and_reply(space_name, thread_name, session_id, user_text, session_service):
            calls.append((space_name, thread_name, session_id, user_text))

        monkeypatch.setattr(agent_main, "_run_and_reply", fake_run_and_reply)

        resp = client.post("/chat", json={
            "type": "MESSAGE",
            "message": {"text": "review this site", "thread": {"name": "spaces/AAA/threads/T1"}},
            "space": {"name": "spaces/AAA"},
        })

        assert resp.status_code == 200
        assert "Running checks" in resp.json()["text"]
        assert calls == [("spaces/AAA", "spaces/AAA/threads/T1", "spaces-AAA-threads-T1", "review this site")]

    def test_message_without_thread_falls_back_to_space_as_session(self, client, monkeypatch):
        calls = []

        async def fake_run_and_reply(space_name, thread_name, session_id, user_text, session_service):
            calls.append((space_name, thread_name, session_id))

        monkeypatch.setattr(agent_main, "_run_and_reply", fake_run_and_reply)

        client.post("/chat", json={
            "type": "MESSAGE",
            "message": {"text": "hi"},
            "space": {"name": "spaces/AAA"},
        })

        assert calls == [("spaces/AAA", "spaces/AAA", "spaces-AAA")]


class TestChatWebhookDeduplication:
    """Google Chat retries a webhook delivery if it doesn't get an ack fast
    enough — confirmed live: a slow-to-process message got delivered twice
    and each delivery took a different code path, producing two conflicting
    replies in the same thread (see message.name-based dedup in handle_chat)."""

    @pytest.fixture(autouse=True)
    def clear_seen_messages(self):
        agent_main._seen_message_names.clear()
        yield
        agent_main._seen_message_names.clear()

    def test_second_delivery_of_same_message_name_is_ignored(self, client, monkeypatch):
        calls = []

        async def fake_run_and_reply(space_name, thread_name, session_id, user_text, session_service):
            calls.append(user_text)

        monkeypatch.setattr(agent_main, "_run_and_reply", fake_run_and_reply)

        payload = {
            "type": "MESSAGE",
            "message": {"name": "spaces/AAA/messages/M1", "text": "review this site"},
            "space": {"name": "spaces/AAA"},
        }

        resp1 = client.post("/chat", json=payload)
        resp2 = client.post("/chat", json=payload)

        assert resp1.json() != {}
        assert resp2.json() == {}
        assert calls == ["review this site"]

    def test_different_message_names_both_processed(self, client, monkeypatch):
        calls = []

        async def fake_run_and_reply(space_name, thread_name, session_id, user_text, session_service):
            calls.append(user_text)

        monkeypatch.setattr(agent_main, "_run_and_reply", fake_run_and_reply)

        client.post("/chat", json={
            "type": "MESSAGE",
            "message": {"name": "spaces/AAA/messages/M1", "text": "first"},
            "space": {"name": "spaces/AAA"},
        })
        client.post("/chat", json={
            "type": "MESSAGE",
            "message": {"name": "spaces/AAA/messages/M2", "text": "second"},
            "space": {"name": "spaces/AAA"},
        })

        assert calls == ["first", "second"]

    def test_message_without_a_name_is_never_deduped(self, client, monkeypatch):
        calls = []

        async def fake_run_and_reply(space_name, thread_name, session_id, user_text, session_service):
            calls.append(user_text)

        monkeypatch.setattr(agent_main, "_run_and_reply", fake_run_and_reply)

        payload = {
            "type": "MESSAGE",
            "message": {"text": "no name field"},
            "space": {"name": "spaces/AAA"},
        }

        client.post("/chat", json=payload)
        client.post("/chat", json=payload)

        assert calls == ["no name field", "no name field"]


class TestAlreadyProcessed:
    @pytest.fixture(autouse=True)
    def clear_seen_messages(self):
        agent_main._seen_message_names.clear()
        yield
        agent_main._seen_message_names.clear()

    def test_first_call_returns_false(self):
        assert agent_main._already_processed("spaces/A/messages/M1") is False

    def test_second_call_with_same_name_returns_true(self):
        agent_main._already_processed("spaces/A/messages/M1")
        assert agent_main._already_processed("spaces/A/messages/M1") is True

    def test_empty_name_never_counts_as_processed(self):
        assert agent_main._already_processed("") is False
        assert agent_main._already_processed("") is False

    def test_bounded_and_evicts_oldest(self, monkeypatch):
        monkeypatch.setattr(agent_main, "_SEEN_MESSAGES_MAXSIZE", 3)
        for i in range(5):
            agent_main._already_processed(f"m{i}")
        assert len(agent_main._seen_message_names) == 3
        assert "m0" not in agent_main._seen_message_names
        assert "m4" in agent_main._seen_message_names


class TestChatAuthVerification:
    def test_open_when_audience_unconfigured(self, client, monkeypatch):
        monkeypatch.setattr(agent_main.settings, "chat_audience", "")
        resp = client.post("/chat", json={"type": "ADDED_TO_SPACE"})
        assert resp.status_code == 200

    def test_rejects_missing_bearer_token_when_configured(self, client, monkeypatch):
        monkeypatch.setattr(agent_main.settings, "chat_audience", "123456789")
        resp = client.post("/chat", json={"type": "ADDED_TO_SPACE"})
        assert resp.status_code == 401

    def test_rejects_token_that_fails_verification(self, client, monkeypatch):
        monkeypatch.setattr(agent_main.settings, "chat_audience", "123456789")

        def fake_verify(token, request, audience):
            raise ValueError("invalid token")

        monkeypatch.setattr(agent_main.google_id_token, "verify_oauth2_token", fake_verify)
        resp = client.post(
            "/chat", json={"type": "ADDED_TO_SPACE"},
            headers={"Authorization": "Bearer bogus"},
        )
        assert resp.status_code == 401

    def test_rejects_token_from_wrong_issuer(self, client, monkeypatch):
        monkeypatch.setattr(agent_main.settings, "chat_audience", "123456789")

        def fake_verify(token, request, audience):
            assert audience == "123456789"
            return {"email": "someone-else@example.com", "email_verified": True}

        monkeypatch.setattr(agent_main.google_id_token, "verify_oauth2_token", fake_verify)
        resp = client.post(
            "/chat", json={"type": "ADDED_TO_SPACE"},
            headers={"Authorization": "Bearer sometoken"},
        )
        assert resp.status_code == 401

    def test_accepts_valid_chat_token(self, client, monkeypatch):
        monkeypatch.setattr(agent_main.settings, "chat_audience", "123456789")

        def fake_verify(token, request, audience):
            return {"email": "chat@system.gserviceaccount.com", "email_verified": True}

        monkeypatch.setattr(agent_main.google_id_token, "verify_oauth2_token", fake_verify)
        resp = client.post(
            "/chat", json={"type": "ADDED_TO_SPACE"},
            headers={"Authorization": "Bearer realtoken"},
        )
        assert resp.status_code == 200
        assert "ready" in resp.json()["text"].lower()


# ---------------------------------------------------------------------------
# _post_to_chat
# ---------------------------------------------------------------------------

class TestPostToChat:
    @respx.mock
    async def test_sends_bearer_token_and_text_payload(self, monkeypatch):
        monkeypatch.setattr(agent_main.google.auth, "default", lambda scopes: (FakeCreds(), None))
        route = respx.post("https://chat.googleapis.com/v1/spaces/AAA/messages").mock(
            return_value=httpx.Response(200, json={})
        )

        await agent_main._post_to_chat("spaces/AAA", "spaces/AAA/threads/T1", "hello world")

        assert route.called
        req = route.calls[0].request
        assert req.headers["authorization"] == "Bearer tok123"
        import json
        payload = json.loads(req.content)
        assert payload["text"] == "hello world"
        assert payload["thread"]["name"] == "spaces/AAA/threads/T1"
        assert "messageReplyOption" not in payload
        assert req.url.params["messageReplyOption"] == "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"

    @respx.mock
    async def test_omits_thread_when_thread_equals_space(self, monkeypatch):
        monkeypatch.setattr(agent_main.google.auth, "default", lambda scopes: (FakeCreds(), None))
        route = respx.post("https://chat.googleapis.com/v1/spaces/AAA/messages").mock(
            return_value=httpx.Response(200, json={})
        )

        await agent_main._post_to_chat("spaces/AAA", "spaces/AAA", "hello")

        import json
        req = route.calls[0].request
        payload = json.loads(req.content)
        assert "thread" not in payload
        assert "messageReplyOption" not in req.url.params

    @respx.mock
    async def test_raises_on_non_2xx_response(self, monkeypatch):
        monkeypatch.setattr(agent_main.google.auth, "default", lambda scopes: (FakeCreds(), None))
        respx.post("https://chat.googleapis.com/v1/spaces/AAA/messages").mock(
            return_value=httpx.Response(403, text="forbidden")
        )

        with pytest.raises(httpx.HTTPStatusError):
            await agent_main._post_to_chat("spaces/AAA", "spaces/AAA", "hello")


# ---------------------------------------------------------------------------
# _run_and_reply
# ---------------------------------------------------------------------------

class TestTruncateReply:
    def test_short_reply_is_unchanged(self):
        assert agent_main._truncate_reply("short") == "short"

    def test_no_url_falls_back_to_blind_slice(self):
        long_text = "x" * 5000
        result = agent_main._truncate_reply(long_text)
        assert len(result) <= 4000
        assert result.endswith("_(message truncated — see report link for full details)_")

    def test_preserves_full_report_url_when_truncating(self):
        # The real bug this guards against: a blind reply[:3900] slice landing
        # inside the (very long, query-string-heavy) signed URL corrupts it —
        # GCS then rejects it with SignatureDoesNotMatch.
        url = "https://storage.googleapis.com/bucket/report.html?" + "X-Goog-Signature=" + ("a" * 512)
        reply = ("Here is a very long narrative. " * 200) + f"\n\nReport: {url}"
        assert len(reply) > 4000

        result = agent_main._truncate_reply(reply)

        assert url in result
        assert len(result) <= 4000 + len(url)  # url itself is never cut, even if that alone exceeds 4000
        assert "message truncated" in result

    def test_url_alone_exceeding_limit_is_still_returned_whole(self):
        url = "https://storage.googleapis.com/bucket/report.html?" + ("a" * 5000)
        result = agent_main._truncate_reply(f"Report: {url}")
        assert result == url

    def test_reply_exactly_at_limit_is_unchanged(self):
        reply = "x" * 4000
        assert agent_main._truncate_reply(reply) == reply


class TestRunAndReply:
    async def test_joins_multiple_parts_with_newline(self, monkeypatch):
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(
            agent_main, "Runner",
            make_fake_runner([FakeEvent("part1"), FakeEvent("part2")]),
        )
        posted = {}

        async def fake_post(space, thread, text):
            posted["text"] = text

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert posted["text"] == "part1\npart2"

    async def test_no_final_response_posts_placeholder(self, monkeypatch):
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([]))
        posted = {}

        async def fake_post(space, thread, text):
            posted["text"] = text

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert posted["text"] == "No response generated."

    async def test_logs_diagnostics_when_final_event_has_no_text(self, monkeypatch, caplog):
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([FakeEvent(None)]))

        async def fake_post(space, thread, text):
            pass

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        with caplog.at_level("WARNING"):
            await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert any("Final event had no text" in r.message for r in caplog.records)

    async def test_retries_after_malformed_function_call_and_succeeds(self, monkeypatch):
        # Real production failure: MALFORMED_FUNCTION_CALL on a large Mode B
        # batch's generate_report call produces no text on the first attempt
        # but a retry (fresh session) succeeds.
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(
            agent_main, "Runner",
            make_fake_runner(events_sequence=[[FakeEvent(None)], [FakeEvent("good reply")]]),
        )
        posted = {}

        async def fake_post(space, thread, text):
            posted["text"] = text

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert posted["text"] == "good reply"

    async def test_gives_up_after_max_attempts(self, monkeypatch, caplog):
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        fake_runner_cls = make_fake_runner([FakeEvent(None)])
        monkeypatch.setattr(agent_main, "Runner", fake_runner_cls)
        posted = {}

        async def fake_post(space, thread, text):
            posted["text"] = text

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        with caplog.at_level("WARNING"):
            await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert posted["text"] == "No response generated."
        no_text_warnings = [r for r in caplog.records if "Final event had no text" in r.message]
        assert len(no_text_warnings) == agent_main._RUN_AND_REPLY_MAX_ATTEMPTS

    async def test_first_attempt_success_does_not_retry(self, monkeypatch):
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(
            agent_main, "Runner",
            make_fake_runner(events_sequence=[[FakeEvent("first try")], [FakeEvent("should not be used")]]),
        )
        posted = {}

        async def fake_post(space, thread, text):
            posted["text"] = text

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert posted["text"] == "first try"

    async def test_truncates_long_replies_to_4000_chars(self, monkeypatch):
        long_text = "x" * 5000
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([FakeEvent(long_text)]))
        posted = {}

        async def fake_post(space, thread, text):
            posted["text"] = text

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert len(posted["text"]) <= 4000
        assert posted["text"].startswith("x" * 3900)
        assert posted["text"].endswith("_(message truncated — see report link for full details)_")

    async def test_long_reply_with_report_url_keeps_url_intact(self, monkeypatch):
        url = "https://storage.googleapis.com/bucket/report.html?X-Goog-Signature=" + ("a" * 512)
        long_reply = ("Detailed narrative text. " * 200) + f"\n\nFull report: {url}"
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([FakeEvent(long_reply)]))
        posted = {}

        async def fake_post(space, thread, text):
            posted["text"] = text

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert url in posted["text"]

    async def test_short_replies_are_not_truncated(self, monkeypatch):
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([FakeEvent("short reply")]))
        posted = {}

        async def fake_post(space, thread, text):
            posted["text"] = text

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert posted["text"] == "short reply"

    async def test_agent_exception_is_reported_as_chat_reply(self, monkeypatch):
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner(raise_exc=RuntimeError("kaboom")))
        posted = {}

        async def fake_post(space, thread, text):
            posted["text"] = text

        monkeypatch.setattr(agent_main, "_post_to_chat", fake_post)

        await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())

        assert posted["text"].startswith("❌ Agent error:")
        assert "kaboom" in posted["text"]

    async def test_post_to_chat_failure_does_not_propagate(self, monkeypatch):
        monkeypatch.setattr(agent_main, "create_agent", lambda: object())
        monkeypatch.setattr(agent_main, "Runner", make_fake_runner([FakeEvent("hello")]))

        async def failing_post(space, thread, text):
            raise RuntimeError("chat is down")

        monkeypatch.setattr(agent_main, "_post_to_chat", failing_post)

        # Should not raise despite the Chat API call failing.
        await agent_main._run_and_reply("spaces/AAA", "spaces/AAA", "sess1", "hi", object())
