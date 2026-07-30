from seo_workbook_agent.chat_formatting import ADDED_TO_SPACE_PROMPT, extract_message, to_chat_markup


def test_extract_message_parses_message_event():
    event = {
        "type": "MESSAGE",
        "message": {"text": "create the plan"},
        "space": {"name": "spaces/AAAA"},
        "user": {"name": "users/123", "email": "seo@vdigital.com"},
    }
    assert extract_message(event) == ("spaces/AAAA", "users/123", "create the plan", None)


def test_extract_message_includes_thread_name_when_present():
    event = {
        "type": "MESSAGE",
        "message": {"text": "yes", "thread": {"name": "spaces/AAAA/threads/BBBB"}},
        "space": {"name": "spaces/AAAA"},
        "user": {"name": "users/123"},
    }
    assert extract_message(event) == ("spaces/AAAA", "users/123", "yes", "spaces/AAAA/threads/BBBB")


def test_extract_message_ignores_non_message_events():
    assert extract_message({"type": "REMOVED_FROM_SPACE"}) is None
    assert extract_message({"type": "CARD_CLICKED"}) is None


def test_extract_message_turns_added_to_space_into_a_synthetic_greeting_turn():
    # No hardcoded greeting string here — this feeds into the same agent
    # turn as a real message, so whichever agent is actually configured
    # (orchestrator or a standalone specialist) introduces itself using
    # its own instructions.
    event = {
        "type": "ADDED_TO_SPACE",
        "space": {"name": "spaces/AAAA"},
        "user": {"name": "users/123"},
    }
    assert extract_message(event) == ("spaces/AAAA", "users/123", ADDED_TO_SPACE_PROMPT, None)


def test_extract_message_added_to_space_defaults_missing_fields():
    assert extract_message({"type": "ADDED_TO_SPACE"}) == (
        "unknown-space", "unknown-user", ADDED_TO_SPACE_PROMPT, None,
    )


def test_extract_message_defaults_missing_fields():
    result = extract_message({"type": "MESSAGE", "message": {}})
    assert result == ("unknown-space", "unknown-user", "", None)


def test_to_chat_markup_converts_double_asterisk_bold_to_single():
    assert to_chat_markup("**Primary Keyword:** Custom Clothing") == "*Primary Keyword:* Custom Clothing"


def test_to_chat_markup_converts_dunder_bold_to_single_asterisk():
    assert to_chat_markup("__Primary Keyword:__ Custom Clothing") == "*Primary Keyword:* Custom Clothing"


def test_to_chat_markup_leaves_existing_single_asterisk_bullets_alone():
    text = "* First item\n* Second item"
    assert to_chat_markup(text) == text


def test_to_chat_markup_converts_markdown_link_to_chat_hyperlink():
    text = "See the [report](https://example.com/report.html) for details."
    assert to_chat_markup(text) == "See the <https://example.com/report.html|report> for details."


def test_to_chat_markup_demotes_headers_to_bold():
    assert to_chat_markup("## Page Summary") == "*Page Summary*"


def test_to_chat_markup_does_not_let_an_unpaired_double_asterisk_swallow_the_rest_of_the_message():
    text = "A 2** increase this month.\n\n**Real bold** stays intact."
    result = to_chat_markup(text)
    assert "A 2** increase this month." in result
    assert "*Real bold*" in result


def test_to_chat_markup_handles_the_reported_sixth_rhino_summary():
    text = (
        "**Page: `https://sixthrhino.com/`**\n"
        "*   **Headers Updated:**\n"
        '    *   "Features" (changed from H3 to H4)\n'
        "*   **Primary Keyword:** \"Custom Clothin\"\n"
    )
    result = to_chat_markup(text)
    assert "**" not in result
    assert "*Page: `https://sixthrhino.com/`*" in result
    assert "*Headers Updated:*" in result
    assert "*Primary Keyword:*" in result
