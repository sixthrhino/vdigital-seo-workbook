from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from ..agent_core import AgentCore
from ..chat_auth import ChatAuthError, verify_chat_bearer_token
from ..chat_client import post_chat_message
from ..chat_formatting import extract_message
from ..config import get_agent_settings

router = APIRouter()
logger = logging.getLogger(__name__)


async def _process_and_reply(
    agent_core: AgentCore,
    chat_client_factory: Callable[[], Any],
    space_name: str,
    user_id: str,
    text: str,
    thread_name: str | None,
) -> None:
    """Runs after /chat has already acked the webhook. A turn with several
    tool calls can take longer than Chat's synchronous response budget, so
    the real reply is posted here via the Chat API instead of being
    returned directly from the request handler.
    """
    try:
        reply = await agent_core.handle_turn(user_id=user_id, session_id=space_name, message=text)
    except Exception:
        logger.exception("Agent turn failed while handling a Chat message")
        reply = "Sorry, something went wrong processing that — please try again."

    if not reply.strip():
        return

    try:
        chat_client = chat_client_factory()
        post_chat_message(chat_client, space_name, reply, thread_name)
    except Exception:
        logger.exception("Failed to post the reply back to Google Chat")


@router.post("/chat")
async def chat(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
) -> dict:
    settings = get_agent_settings()
    if not settings.chat_audience:
        raise HTTPException(status_code=503, detail="CHAT_AUDIENCE is not configured")

    try:
        verify_chat_bearer_token(authorization, settings.chat_audience)
    except ChatAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    event = await request.json()
    parsed = extract_message(event)
    if parsed is None:
        return {}

    space_name, user_id, text, thread_name = parsed
    if not text.strip():
        return {}

    # Ack immediately with no message content — Chat interprets this as
    # "no synchronous reply, one may arrive asynchronously" — then keep
    # working in the background (needs --no-cpu-throttling on this Cloud
    # Run service, or CPU gets frozen the moment this response is sent).
    background_tasks.add_task(
        _process_and_reply,
        request.app.state.agent_core,
        request.app.state.chat_client_factory,
        space_name,
        user_id,
        text,
        thread_name,
    )
    return {}
