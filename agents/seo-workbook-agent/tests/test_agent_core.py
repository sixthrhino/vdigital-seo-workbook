import pytest
from google.adk.agents import Agent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.sessions import InMemorySessionService
from google.genai import types

from seo_workbook_agent.agent_core import AgentCore


class FakeLlm(BaseLlm):
    """Minimal stand-in for a real Gemini model — returns a fixed reply so
    AgentCore's session bookkeeping and response extraction can be tested
    deterministically without real model credentials or a live MCP server.
    """

    model: str = "fake-model"
    reply_text: str = "hello from fake"

    async def generate_content_async(self, llm_request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=self.reply_text)]))


def _core(reply_text: str = "hello from fake") -> AgentCore:
    agent = Agent(name="test_agent", model=FakeLlm(reply_text=reply_text), instruction="test agent", tools=[])
    return AgentCore(agent=agent, session_service=InMemorySessionService())


async def test_handle_turn_returns_model_reply():
    core = _core()
    reply = await core.handle_turn(user_id="u1", session_id="s1", message="hi")
    assert reply == "hello from fake"


async def test_handle_turn_creates_session_on_first_call():
    core = _core()
    session = await core._session_service.get_session(app_name=core.app_name, user_id="u1", session_id="s1")
    assert session is None

    await core.handle_turn(user_id="u1", session_id="s1", message="hi")
    session = await core._session_service.get_session(app_name=core.app_name, user_id="u1", session_id="s1")
    assert session is not None


async def test_handle_turn_reuses_existing_session_across_calls():
    core = _core()
    await core.handle_turn(user_id="u1", session_id="s1", message="first")
    await core.handle_turn(user_id="u1", session_id="s1", message="second")

    session = await core._session_service.get_session(app_name=core.app_name, user_id="u1", session_id="s1")
    # two user turns + two model replies = 4 events accumulated in one session
    assert len(session.events) == 4


async def test_handle_turn_keeps_separate_sessions_isolated():
    core = _core()
    await core.handle_turn(user_id="u1", session_id="s1", message="hi")
    session_2 = await core._session_service.get_session(app_name=core.app_name, user_id="u1", session_id="s2")
    assert session_2 is None
