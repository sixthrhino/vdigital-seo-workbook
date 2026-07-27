from seo_workbook_agent.chat_formatting import extract_message


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
    assert extract_message({"type": "ADDED_TO_SPACE"}) is None


def test_extract_message_defaults_missing_fields():
    result = extract_message({"type": "MESSAGE", "message": {}})
    assert result == ("unknown-space", "unknown-user", "", None)
