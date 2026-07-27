from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI
from seo_workbook_common.logging import configure_logging

from .agent_core import AgentCore
from .chat_client import build_chat_service
from .config import get_agent_settings
from .routers import chat_router, http_router


def create_app(
    agent_core: AgentCore | None = None,
    chat_client_factory: Callable[[], Any] = build_chat_service,
) -> FastAPI:
    configure_logging(get_agent_settings().log_level)

    app = FastAPI(title="seo-workbook-agent")
    app.state.agent_core = agent_core or AgentCore()
    # Stored as a factory, not called here: building a real chat client
    # eagerly would trigger credential resolution on every app construction
    # (including in tests that never touch /chat) — it's only invoked
    # lazily, inside the background task that actually needs it.
    app.state.chat_client_factory = chat_client_factory
    app.include_router(http_router.router)
    app.include_router(chat_router.router)
    return app


app = create_app()


def main() -> None:
    # Cloud Run injects PORT and expects the process to bind 0.0.0.0:$PORT.
    import os

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


if __name__ == "__main__":
    main()
