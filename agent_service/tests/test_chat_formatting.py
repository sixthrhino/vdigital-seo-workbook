from seo_workbook_agent.chat_formatting import build_reply, extract_message


def test_extract_message_parses_message_event():
    event = {
        "type": "MESSAGE",
        "message": {"text": "create the plan"},
        "space": {"name": "spaces/AAAA"},
        "user": {"name": "users/123", "email": "seo@vdigital.com"},
    }
    assert extract_message(event) == ("spaces/AAAA", "users/123", "create the plan")


def test_extract_message_ignores_non_message_events():
    assert extract_message({"type": "ADDED_TO_SPACE"}) is None


def test_extract_message_defaults_missing_fields():
    result = extract_message({"type": "MESSAGE", "message": {}})
    assert result == ("unknown-space", "unknown-user", "")


def test_build_reply_shape():
    assert build_reply("hello") == {"text": "hello"}
