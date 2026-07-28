from functools import lru_cache

from seo_workbook_common.config import Settings


class AgentSettings(Settings):
    """Adds agent-specific env vars on top of the shared Settings base —
    kept in this package rather than in seo_workbook_common since
    mcp_server has no use for an LLM model, an MCP URL to call itself, or
    Chat/API-key auth config.
    """

    agent_model: str = "gemini-2.5-flash"
    mcp_server_url: str = "http://localhost:8000/mcp"

    # Required in production; left optional here so the app can still start
    # (and fail closed on each request) if one hasn't been configured yet.
    run_api_key: str | None = None
    chat_audience: str | None = None


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
