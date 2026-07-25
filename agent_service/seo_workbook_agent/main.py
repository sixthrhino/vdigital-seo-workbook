from __future__ import annotations

from fastapi import FastAPI
from seo_workbook_common.logging import configure_logging

from .agent_core import AgentCore
from .config import get_agent_settings
from .routers import chat_router, http_router


def create_app(agent_core: AgentCore | None = None) -> FastAPI:
    configure_logging(get_agent_settings().log_level)

    app = FastAPI(title="seo-workbook-agent")
    app.state.agent_core = agent_core or AgentCore()
    app.include_router(http_router.router)
    app.include_router(chat_router.router)
    return app


app = create_app()
