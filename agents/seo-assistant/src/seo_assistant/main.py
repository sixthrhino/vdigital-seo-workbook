from __future__ import annotations

from seo_workbook_agent.agent_core import AgentCore
from seo_workbook_agent.main import create_app

from .orchestrator_agent import build_orchestrator

# Deliberately reuses seo_workbook_agent's entire FastAPI/chat-webhook/
# /reports layer as-is (AgentCore already accepts an injected agent;
# create_app already accepts an injected agent_core) rather than
# duplicating it — the HTTP/Chat/report-link plumbing isn't specific to
# the workbook agent's content, just to "one ADK agent behind a chat
# webhook", which is exactly what the orchestrator is too.
app = create_app(agent_core=AgentCore(agent=build_orchestrator()))


def main() -> None:
    # Cloud Run injects PORT and expects the process to bind 0.0.0.0:$PORT.
    import os

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


if __name__ == "__main__":
    main()
