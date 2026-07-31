from contextlib import asynccontextmanager

import pytest

from seo_workbook_agent import dialog_submission as ds


def test_build_capture_text_includes_only_filled_fields():
    text = ds._build_capture_text({"url": "https://example.com/a/", "title_new": "New Title"})
    assert text == "url: https://example.com/a/\ntitle: New Title"


def test_build_capture_text_joins_old_and_new_with_arrow():
    text = ds._build_capture_text({
        "url": "https://example.com/a/", "title_old": "Old Title", "title_new": "New Title",
    })
    assert "title: Old Title -> New Title" in text


def test_build_capture_text_omits_old_new_pair_when_new_is_blank():
    text = ds._build_capture_text({"url": "https://example.com/a/", "title_old": "Old Title"})
    assert "title" not in text


def test_build_capture_text_puts_notes_last_after_headings():
    text = ds._build_capture_text({
        "url": "https://example.com/a/",
        "headings": "H2 -> H3: Checking Over Your Trailer",
        "notes": "Added internal link to homepage.",
    })
    lines = text.splitlines()
    assert lines[-2] == "headings: H2 -> H3: Checking Over Your Trailer"
    assert lines[-1] == "notes: Added internal link to homepage."


def test_build_capture_text_preserves_multiline_headings_block():
    text = ds._build_capture_text({
        "url": "https://example.com/a/",
        "headings": "H2 -> H3: Checking Over Your Trailer\nH3: Emergency Equipment",
    })
    assert text == (
        "url: https://example.com/a/\n"
        "headings: H2 -> H3: Checking Over Your Trailer\nH3: Emergency Equipment"
    )


def test_build_capture_text_keeps_all_optional_fields_in_order():
    text = ds._build_capture_text({
        "url": "https://example.com/a/",
        "keyword": "auto insurance (500)",
        "geo": "Scottsdale, AZ",
        "cta": "Get a Quote",
    })
    assert text == (
        "url: https://example.com/a/\n"
        "keyword: auto insurance (500)\n"
        "geo: Scottsdale, AZ\n"
        "cta: Get a Quote"
    )


class _FakeToolResult:
    def __init__(self, *, is_error=False, structured=None, text=None):
        self.isError = is_error
        self.structuredContent = structured
        if text is not None:
            self.content = [type("C", (), {"text": text})()]
        else:
            self.content = []


class _FakeSession:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def initialize(self):
        pass

    async def call_tool(self, name, kwargs):
        self.calls.append((name, kwargs))
        return self.responses[name]


@pytest.fixture
def patch_mcp(monkeypatch):
    """Stubs out streamablehttp_client + ClientSession so
    handle_dialog_submission can be tested without a real MCP connection —
    handle_dialog_submission's tool calls are asserted against the fake
    session's `.calls` list."""
    def _patch(responses: dict):
        session = _FakeSession(responses)

        @asynccontextmanager
        async def fake_streamablehttp_client(url, headers=None):
            yield (None, None, None)

        class _FakeClientSession:
            def __init__(self, read, write):
                pass

            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        async def fake_fetch_auth_headers(url):
            return None

        monkeypatch.setattr(ds, "streamablehttp_client", fake_streamablehttp_client)
        monkeypatch.setattr(ds, "ClientSession", _FakeClientSession)
        monkeypatch.setattr(ds, "_fetch_mcp_auth_headers", fake_fetch_auth_headers)
        return session

    return _patch


async def test_handle_dialog_submission_requires_client_month_and_url():
    result = await ds.handle_dialog_submission({}, mcp_server_url="http://mcp.example.com/mcp")
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "INVALID_ARGUMENT"
    assert "client" in status["userFacingMessage"]
    assert "month" in status["userFacingMessage"]
    assert "url" in status["userFacingMessage"]


async def test_handle_dialog_submission_uses_existing_session_when_found(patch_mcp):
    session = patch_mcp({
        "find_session": _FakeToolResult(structured={"session_id": "ntt-2026-07", "pages": []}),
        "record_page_from_text": _FakeToolResult(structured={"touchpoints": []}),
    })

    result = await ds.handle_dialog_submission(
        {"client": "North Texas Trailers", "month": "2026-07", "url": "https://example.com/a/"},
        mcp_server_url="http://mcp.example.com/mcp",
    )

    tool_names = [name for name, _ in session.calls]
    assert tool_names == ["find_session", "record_page_from_text"]
    assert session.calls[1][1]["session_id"] == "ntt-2026-07"
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "OK"
    assert "Recorded" in status["userFacingMessage"]


async def test_handle_dialog_submission_starts_a_session_when_none_found(patch_mcp):
    session = patch_mcp({
        "find_session": _FakeToolResult(is_error=True, text="No session found"),
        "start_session": _FakeToolResult(structured={"session_id": "ntt-2026-07", "pages": []}),
        "record_page_from_text": _FakeToolResult(structured={"touchpoints": []}),
    })

    result = await ds.handle_dialog_submission(
        {"client": "North Texas Trailers", "month": "2026-07", "url": "https://example.com/a/"},
        mcp_server_url="http://mcp.example.com/mcp",
    )

    tool_names = [name for name, _ in session.calls]
    assert tool_names == ["find_session", "start_session", "record_page_from_text"]
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "OK"


async def test_handle_dialog_submission_surfaces_failed_validation(patch_mcp):
    patch_mcp({
        "find_session": _FakeToolResult(structured={"session_id": "ntt-2026-07", "pages": []}),
        "record_page_from_text": _FakeToolResult(structured={
            "touchpoints": [
                {"touchpoint_id": "meta_description", "validation": {"passed": False, "messages": ["needs a cta"]}},
            ]
        }),
    })

    result = await ds.handle_dialog_submission(
        {"client": "North Texas Trailers", "month": "2026-07", "url": "https://example.com/a/"},
        mcp_server_url="http://mcp.example.com/mcp",
    )

    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "OK"
    assert "meta_description" in status["userFacingMessage"]
    assert "needs attention" in status["userFacingMessage"]


async def test_handle_dialog_submission_reports_record_failure(patch_mcp):
    patch_mcp({
        "find_session": _FakeToolResult(structured={"session_id": "ntt-2026-07", "pages": []}),
        "record_page_from_text": _FakeToolResult(is_error=True, text="Page not found"),
    })

    result = await ds.handle_dialog_submission(
        {"client": "North Texas Trailers", "month": "2026-07", "url": "https://example.com/a/"},
        mcp_server_url="http://mcp.example.com/mcp",
    )

    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "INVALID_ARGUMENT"
    assert "Page not found" in status["userFacingMessage"]
