from __future__ import annotations

from google.adk.agents import BaseAgent
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService
from google.genai import types

from .adk_agent import build_agent
from .config import AgentSettings, get_agent_settings

APP_NAME = "seo-workbook-agent"


class AgentCore:
    """Wraps an ADK Runner + session service behind one turn-taking method,
    shared by both entry points (the plain HTTP endpoint and the Google Chat
    webhook) so neither duplicates agent wiring or session bookkeeping.

    Session state is in-memory per process for phase 1 — fine given Cloud
    Run session affinity pins a given conversation (session_id) to one
    instance, same reasoning as mcp_server's SessionStore.
    """

    def __init__(
        self,
        *,
        settings: AgentSettings | None = None,
        agent: BaseAgent | None = None,
        session_service: BaseSessionService | None = None,
    ) -> None:
        self.app_name = APP_NAME
        self._session_service = session_service or InMemorySessionService()
        if agent is None:
            agent = build_agent(settings or get_agent_settings())
        self._runner = Runner(app_name=self.app_name, agent=agent, session_service=self._session_service)

    async def _ensure_session(self, user_id: str, session_id: str) -> None:
        existing = await self._session_service.get_session(
            app_name=self.app_name, user_id=user_id, session_id=session_id
        )
        if existing is None:
            await self._session_service.create_session(
                app_name=self.app_name, user_id=user_id, session_id=session_id
            )

    async def handle_turn(self, *, user_id: str, session_id: str, message: str) -> str:
        await self._ensure_session(user_id, session_id)

        content = types.Content(role="user", parts=[types.Part(text=message)])
        reply_parts: list[str] = []
        async for event in self._runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
            if event.is_final_response() and event.content and event.content.parts:
                reply_parts.extend(part.text for part in event.content.parts if part.text)

        return "\n".join(reply_parts)
