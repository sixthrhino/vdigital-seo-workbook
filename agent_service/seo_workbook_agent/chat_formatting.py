from __future__ import annotations

import re
from typing import Any

# Google Chat's text-message markup is a small, specific subset of
# Markdown — *bold* (single asterisk), _italic_, no headers, no numbered
# lists, and links as <url|text> rather than [text](url). See
# https://developers.google.com/workspace/chat/format-messages
#
# The agent's system prompt already asks Gemini to write in this style
# directly, but instruction-following on formatting isn't 100% reliable
# for longer structured replies — Gemini reverts to standard
# CommonMark-style **bold**/headers/links often enough that this
# deterministic backstop is worth having. Not DOTALL: bold spans are kept
# to a single line so one stray/unpaired ** can't swallow the rest of the
# message looking for its match.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_HEADER_RE = re.compile(r"^#{1,6}[ \t]+(.+)$", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")


def to_chat_markup(text: str) -> str:
    """Convert common standard-Markdown patterns to Google Chat's markup
    before posting a reply. See module docstring for why this exists.
    """
    text = _MD_LINK_RE.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", text)
    text = _MD_BOLD_RE.sub(lambda m: f"*{m.group(1) or m.group(2)}*", text)
    text = _MD_HEADER_RE.sub(lambda m: f"*{m.group(1).strip()}*", text)
    return text


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
