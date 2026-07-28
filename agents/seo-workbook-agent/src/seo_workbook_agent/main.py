from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI
from seo_workbook_common.logging import configure_logging
from seo_workbook_common.output.gcs_uploader import build_storage_client, iam_signing_credentials
from seo_workbook_common.storage import build_mongo_collection

from .agent_core import AgentCore
from .chat_client import build_chat_service
from .config import get_agent_settings
from .routers import chat_router, http_router, reports_router


def create_app(
    agent_core: AgentCore | None = None,
    chat_client_factory: Callable[[], Any] = build_chat_service,
    storage_client_factory: Callable[[], Any] = build_storage_client,
    signing_credentials_factory: Callable[[], tuple[str, str]] = iam_signing_credentials,
    report_tokens_collection_factory: Callable[[], Any] | None = None,
) -> FastAPI:
    settings = get_agent_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="seo-workbook-agent")
    app.state.agent_core = agent_core or AgentCore()
    # All stored as factories, not called here: building real clients
    # eagerly would trigger credential resolution on every app construction
    # (including in tests that never touch /chat or /reports) — each is
    # only invoked lazily, inside the handler that actually needs it.
    app.state.chat_client_factory = chat_client_factory
    app.state.storage_client_factory = storage_client_factory
    app.state.signing_credentials_factory = signing_credentials_factory
    app.state.report_tokens_collection_factory = report_tokens_collection_factory or (
        lambda: build_mongo_collection(
            settings.mongo_uri, settings.mongo_database, settings.mongo_report_tokens_collection
        )
    )
    app.include_router(http_router.router)
    app.include_router(chat_router.router)
    app.include_router(reports_router.router)
    return app


app = create_app()


def main() -> None:
    # Cloud Run injects PORT and expects the process to bind 0.0.0.0:$PORT.
    import os

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


if __name__ == "__main__":
    main()
