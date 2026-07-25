from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from ..config import get_agent_settings

router = APIRouter()


class RunRequest(BaseModel):
    session_id: str
    message: str
    user_id: str = "default-user"


class RunResponse(BaseModel):
    reply: str


def _check_api_key(x_api_key: str | None) -> None:
    settings = get_agent_settings()
    if not settings.run_api_key:
        raise HTTPException(status_code=503, detail="RUN_API_KEY is not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.run_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Api-Key")


@router.post("/run", response_model=RunResponse)
async def run(
    request: Request, body: RunRequest, x_api_key: str | None = Header(default=None, alias="X-Api-Key")
) -> RunResponse:
    _check_api_key(x_api_key)
    agent_core = request.app.state.agent_core
    reply = await agent_core.handle_turn(user_id=body.user_id, session_id=body.session_id, message=body.message)
    return RunResponse(reply=reply)
