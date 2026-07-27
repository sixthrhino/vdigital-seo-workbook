from __future__ import annotations

from typing import Any


def extract_message(event: dict[str, Any]) -> tuple[str, str, str, str | None] | None:
    """Pull (session_key, user_id, message_text, thread_name) out of a
    Google Chat MESSAGE event. Returns None for event types we don't act on
    yet (space added/removed, card clicks, etc.) so the router can just ack
    quietly.

    `thread_name` (e.g. "spaces/AAAA/threads/BBBB") is included, when
    present, so the async reply can be posted into the same thread instead
    of starting a new one.
    """
    if event.get("type") != "MESSAGE":
        return None

    message = event.get("message") or {}
    text = message.get("text", "")
    session_key = (event.get("space") or {}).get("name", "unknown-space")
    user_id = (event.get("user") or {}).get("name", "unknown-user")
    thread_name = (message.get("thread") or {}).get("name")
    return session_key, user_id, text, thread_name
