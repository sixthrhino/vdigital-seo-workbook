from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from ..chat_auth import ChatAuthError, verify_chat_bearer_token
from ..chat_formatting import build_reply, extract_message
from ..config import get_agent_settings

router = APIRouter()


@router.post("/chat")
async def chat(request: Request, authorization: str | None = Header(default=None)) -> dict:
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
        return build_reply("")

    session_key, user_id, text = parsed
    if not text.strip():
        return build_reply("")

    agent_core = request.app.state.agent_core
    reply = await agent_core.handle_turn(user_id=user_id, session_id=session_key, message=text)
    return build_reply(reply)
