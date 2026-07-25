from __future__ import annotations

from typing import Any


def extract_message(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Pull (session_key, user_id, message_text) out of a Google Chat
    MESSAGE event. Returns None for event types we don't act on yet (space
    added/removed, card clicks, etc.) so the router can just ack quietly.
    """
    if event.get("type") != "MESSAGE":
        return None

    message = event.get("message") or {}
    text = message.get("text", "")
    session_key = (event.get("space") or {}).get("name", "unknown-space")
    user_id = (event.get("user") or {}).get("name", "unknown-user")
    return session_key, user_id, text


def build_reply(text: str) -> dict[str, Any]:
    return {"text": text}
