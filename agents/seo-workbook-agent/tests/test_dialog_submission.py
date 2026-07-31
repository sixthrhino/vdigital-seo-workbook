from contextlib import asynccontextmanager

import pytest

from seo_workbook_agent import dialog_submission as ds


def test_build_capture_text_includes_only_filled_fields():
    text = ds._build_capture_text("https://example.com/a/", {"title_new": "New Title"}, {})
    assert text == "url: https://example.com/a/\ntitle: New Title"


def test_build_capture_text_joins_fetched_old_with_typed_new():
    text = ds._build_capture_text(
        "https://example.com/a/", {"title_new": "New Title"}, {"title": "Old Title"}
    )
    assert "title: Old Title -> New Title" in text


def test_build_capture_text_omits_title_line_when_new_is_blank():
    text = ds._build_capture_text("https://example.com/a/", {}, {"title": "Old Title"})
    assert "title" not in text


def test_build_capture_text_puts_notes_last_after_headings():
    text = ds._build_capture_text(
        "https://example.com/a/",
        {
            "headings": "H2 -> H3: Checking Over Your Trailer",
            "notes": "Added internal link to homepage.",
        },
        {},
    )
    lines = text.splitlines()
    assert lines[-2] == "headings: H2 -> H3: Checking Over Your Trailer"
    assert lines[-1] == "notes: Added internal link to homepage."


def test_build_capture_text_preserves_multiline_headings_block():
    text = ds._build_capture_text(
        "https://example.com/a/",
        {"headings": "H2 -> H3: Checking Over Your Trailer\nH3: Emergency Equipment"},
        {},
    )
    assert text == (
        "url: https://example.com/a/\n"
        "headings: H2 -> H3: Checking Over Your Trailer\nH3: Emergency Equipment"
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
    dispatch_dialog_submission's MCP-calling steps can be tested without a
    real connection — asserted against the fake session's `.calls` list."""
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


@pytest.fixture
def patch_page_fetch(monkeypatch):
    def _patch(current_values: dict[str, str]):
        async def fake_fetch(url):
            fake_fetch.received_url = url
            return current_values

        monkeypatch.setattr(ds, "fetch_current_page_values", fake_fetch)
        return fake_fetch

    return _patch


def _form_inputs(**fields):
    return {"formInputs": {name: {"stringInputs": {"value": [value]}} for name, value in fields.items()}}


def _button_click(function, parameters=None, **fields):
    event = {
        "type": "CARD_CLICKED",
        "dialogEventType": "SUBMIT_DIALOG",
        "common": {"invokedFunction": function, **_form_inputs(**fields)},
    }
    if parameters is not None:
        event["common"]["parameters"] = parameters
    return event


# ---------------------------------------------------------------------------
# startPageEntry
# ---------------------------------------------------------------------------

async def test_start_page_entry_requires_client_and_month():
    event = _button_click("startPageEntry")
    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "INVALID_ARGUMENT"
    assert "client" in status["userFacingMessage"]
    assert "month" in status["userFacingMessage"]


async def test_start_page_entry_returns_the_url_only_dialog():
    event = _button_click("startPageEntry", client="North Texas Trailers", month="2026-07")
    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")
    dialog = result["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]
    assert dialog["header"] == "Page 1 for North Texas Trailers (2026-07)"
    text_input_names = [w["textInput"]["name"] for w in dialog["widgets"] if "textInput" in w]
    assert text_input_names == ["url"]


# ---------------------------------------------------------------------------
# fetchPageAndContinue
# ---------------------------------------------------------------------------

async def test_fetch_page_and_continue_requires_url():
    event = _button_click("fetchPageAndContinue", parameters={"client": "KYZ", "month": "2026-06", "count": "1"})
    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "INVALID_ARGUMENT"
    assert "url" in status["userFacingMessage"]


async def test_fetch_page_and_continue_errors_when_client_month_parameters_missing():
    event = _button_click("fetchPageAndContinue", url="https://kyz.com/a/")
    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "INVALID_ARGUMENT"


async def test_fetch_page_and_continue_fetches_the_typed_url_and_shows_the_fields_step(patch_page_fetch):
    fake_fetch = patch_page_fetch({"title": "Old Title", "meta_description": "Old meta", "h1": "Old H1"})
    event = _button_click(
        "fetchPageAndContinue",
        parameters={"client": "KYZ", "month": "2026-06", "count": "1"},
        url="https://kyz.com/a/",
    )

    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")

    assert fake_fetch.received_url == "https://kyz.com/a/"
    dialog = result["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]
    assert dialog["widgets"][0]["textParagraph"]["text"] == "Editing: https://kyz.com/a/"
    by_name = {w["textInput"]["name"]: w["textInput"] for w in dialog["widgets"] if "textInput" in w}
    assert by_name["title_new"]["hintText"] == "Current: Old Title"
    assert by_name["meta_new"]["hintText"] == "Current: Old meta"
    assert by_name["h1_new"]["hintText"] == "Current: Old H1"


# ---------------------------------------------------------------------------
# saveAndContinue / saveAndFinish
# ---------------------------------------------------------------------------

async def test_save_and_continue_requires_client_month_and_url_parameters():
    event = _button_click("saveAndContinue")
    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "INVALID_ARGUMENT"


async def test_save_and_continue_records_the_page_using_fetched_old_values(patch_mcp):
    session = patch_mcp({
        "find_session": _FakeToolResult(structured={"session_id": "kyz-2026-06", "pages": []}),
        "record_page_from_text": _FakeToolResult(structured={"touchpoints": []}),
    })
    event = _button_click(
        "saveAndContinue",
        parameters={
            "client": "KYZ", "month": "2026-06", "url": "https://kyz.com/a/", "count": "1",
            "current_title": "Old Title", "current_meta_description": "", "current_h1": "",
        },
        title_new="New Title",
    )

    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")

    tool_names = [name for name, _ in session.calls]
    assert tool_names == ["find_session", "record_page_from_text"]
    assert session.calls[1][1] == {
        "session_id": "kyz-2026-06",
        "text": "url: https://kyz.com/a/\ntitle: Old Title -> New Title",
    }

    # "Next URL" success returns to a fresh url-only step for the next page.
    dialog = result["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]
    assert dialog["header"] == "✓ Saved https://kyz.com/a/ — Page 2 for KYZ (2026-06)"
    text_input_names = [w["textInput"]["name"] for w in dialog["widgets"] if "textInput" in w]
    assert text_input_names == ["url"]


async def test_save_and_continue_starts_a_session_when_none_found(patch_mcp):
    session = patch_mcp({
        "find_session": _FakeToolResult(is_error=True, text="No session found"),
        "start_session": _FakeToolResult(structured={"session_id": "kyz-2026-06", "pages": []}),
        "record_page_from_text": _FakeToolResult(structured={"touchpoints": []}),
    })
    event = _button_click(
        "saveAndContinue",
        parameters={"client": "KYZ", "month": "2026-06", "url": "https://kyz.com/a/", "count": "1"},
    )

    await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")

    tool_names = [name for name, _ in session.calls]
    assert tool_names == ["find_session", "start_session", "record_page_from_text"]


async def test_save_and_finish_closes_the_dialog(patch_mcp):
    patch_mcp({
        "find_session": _FakeToolResult(structured={"session_id": "kyz-2026-06", "pages": []}),
        "record_page_from_text": _FakeToolResult(structured={"touchpoints": []}),
    })
    event = _button_click(
        "saveAndFinish",
        parameters={"client": "KYZ", "month": "2026-06", "url": "https://kyz.com/c/", "count": "3"},
    )

    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")

    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "OK"
    assert "Recorded https://kyz.com/c/" in status["userFacingMessage"]
    assert "All done for KYZ (2026-06)" in status["userFacingMessage"]


async def test_save_and_finish_surfaces_failed_validation(patch_mcp):
    patch_mcp({
        "find_session": _FakeToolResult(structured={"session_id": "kyz-2026-06", "pages": []}),
        "record_page_from_text": _FakeToolResult(structured={
            "touchpoints": [
                {"touchpoint_id": "meta_description", "validation": {"passed": False, "messages": ["needs a cta"]}},
            ]
        }),
    })
    event = _button_click(
        "saveAndFinish",
        parameters={"client": "KYZ", "month": "2026-06", "url": "https://kyz.com/a/", "count": "1"},
        meta_new="Great meta with no cta",
    )

    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")

    # A validation failure blocks finishing too — re-renders the *same*
    # page (same url, same count, fields pre-filled) with the real message
    # shown, rather than closing with the failure buried in a footnote.
    dialog = result["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]
    assert "meta_description: needs a cta" in dialog["widgets"][0]["textParagraph"]["text"]
    assert dialog["header"] == "⚠️ Please fix and resubmit — Page 1 for KYZ (2026-06)"
    assert dialog["widgets"][1]["textParagraph"]["text"] == "Editing: https://kyz.com/a/"
    prefilled = {w["textInput"]["name"]: w["textInput"].get("value") for w in dialog["widgets"] if "textInput" in w}
    assert prefilled["meta_new"] == "Great meta with no cta"


async def test_save_and_continue_re_renders_the_same_page_on_validation_failure(patch_mcp):
    patch_mcp({
        "find_session": _FakeToolResult(structured={"session_id": "kyz-2026-06", "pages": []}),
        "record_page_from_text": _FakeToolResult(structured={
            "touchpoints": [
                {
                    "touchpoint_id": "title_tag",
                    "validation": {
                        "passed": False,
                        "messages": ["title tag is 91 characters, must be 60 or fewer (brand name excluded)"],
                    },
                },
            ]
        }),
    })
    event = _button_click(
        "saveAndContinue",
        parameters={"client": "KYZ", "month": "2026-06", "url": "https://kyz.com/a/", "count": "2"},
        title_new="x" * 91,
    )

    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")

    dialog = result["actionResponse"]["dialogAction"]["dialog"]["body"]["sections"][0]
    assert "title tag is 91 characters" in dialog["widgets"][0]["textParagraph"]["text"]
    # Same page number as the attempt that failed — not advanced to page 3.
    assert dialog["header"] == "⚠️ Please fix and resubmit — Page 2 for KYZ (2026-06)"


async def test_save_and_finish_reports_record_failure(patch_mcp):
    patch_mcp({
        "find_session": _FakeToolResult(structured={"session_id": "kyz-2026-06", "pages": []}),
        "record_page_from_text": _FakeToolResult(is_error=True, text="Page not found"),
    })
    event = _button_click(
        "saveAndFinish",
        parameters={"client": "KYZ", "month": "2026-06", "url": "https://kyz.com/a/", "count": "1"},
    )

    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")

    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "INVALID_ARGUMENT"
    assert "Page not found" in status["userFacingMessage"]


async def test_unknown_invoked_function_returns_an_error():
    event = _button_click("somethingElse")
    result = await ds.dispatch_dialog_submission(event, mcp_server_url="http://mcp.example.com/mcp")
    status = result["actionResponse"]["dialogAction"]["actionStatus"]
    assert status["statusCode"] == "INVALID_ARGUMENT"
