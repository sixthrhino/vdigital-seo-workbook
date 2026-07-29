import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always resolve .env relative to this component's own root (not this
# file's package directory, and not cwd) — agents/seo-testing-agent/src/
# seo_testing_agent/config.py -> agents/seo-testing-agent/.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8")

    # Google API key — used in development (not needed when using Vertex AI ADC in production)
    google_api_key: str = ""

    # GCP / Vertex AI — currently unused (reserved for when session storage
    # moves to a provisioned Vertex AI Reasoning Engine)
    gcp_project_id: str = ""
    gcp_location: str = "us-central1"

    # MCP server (Cloud Run service URL in production)
    mcp_server_url: str = "http://localhost:8080"

    # seo-workbook-mcp's Streamable HTTP endpoint (e.g.
    # "https://seo-workbook-mcp-server-xyz.run.app/mcp") — Mode B's plan
    # data source (see plan_session_source.py). Different transport from
    # mcp_server_url above (Streamable HTTP vs. SSE), so it needs its own
    # client, not just another URL to the same one.
    workbook_mcp_url: str = "http://localhost:8000/mcp"

    # Gemini model used by the agent
    agent_model: str = "gemini-2.0-flash"

    # "production" attaches an ID token to MCP calls (see agent.py); session
    # storage is InMemorySessionService in both modes for now
    environment: str = "development"

    # Webhook URL for rule-failure notifications (optional)
    notification_webhook_url: str = ""

    # Shared-secret header required on /run when set (X-Api-Key). Empty means
    # /run stays open — keeps local dev / scripts/qa.sh frictionless.
    run_api_key: str = ""

    # The HTTP endpoint URL (e.g. "https://<agent-url>/chat") Google Chat's
    # bearer token audience must match — confirmed from a real token's `aud`
    # claim, NOT the numeric project number some Google docs suggest.
    # deploy.sh sets this to the agent service's own URL + "/chat". Empty
    # means /chat skips verification — keeps local dev usable, since Chat
    # can't reach a local webhook anyway.
    chat_audience: str = ""


settings = Settings()

# ADK reads GOOGLE_API_KEY directly from os.environ, so mirror it there
if settings.google_api_key:
    os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)
