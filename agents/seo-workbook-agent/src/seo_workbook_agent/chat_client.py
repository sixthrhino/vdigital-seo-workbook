from __future__ import annotations

from typing import Any

_CHAT_BOT_SCOPE = "https://www.googleapis.com/auth/chat.bot"


def build_chat_service() -> Any:
    """Construct a real Google Chat API client using this app's own
    service-account credentials, scoped to post messages as the bot.

    Not exercised in unit tests — callers inject a fake via the
    chat_client_factory seam instead (see chat_router.py).
    """
    import google.auth
    from googleapiclient.discovery import build

    credentials, _ = google.auth.default(scopes=[_CHAT_BOT_SCOPE])
    return build("chat", "v1", credentials=credentials, cache_discovery=False)


def post_chat_message(chat_service: Any, space_name: str, text: str, thread_name: str | None = None) -> dict:
    """Post a message into a Chat space, optionally within an existing
    thread, via the Chat REST API.

    Used for the async-ack pattern: /chat acks the webhook immediately and
    this posts the real reply once the agent turn actually finishes,
    rather than trying to return it synchronously (which risks exceeding
    Chat's response-time budget once a turn involves several tool calls).

    `chat_service` is a googleapiclient Chat API resource (or any test
    double implementing `.spaces().messages().create(parent=, body=)
    .execute()`) — injected rather than constructed here so this stays
    unit-testable without real Google credentials.
    """
    body: dict[str, Any] = {"text": text}
    if thread_name:
        body["thread"] = {"name": thread_name}
    return chat_service.spaces().messages().create(parent=space_name, body=body).execute()
