from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastmcp import FastMCP
from seo_workbook_common.best_practices import load_catalog
from seo_workbook_common.config import Settings, get_settings
from seo_workbook_common.logging import configure_logging
from seo_workbook_common.output import build_sheets_service

from .session_store import SessionStore
from .tools import catalog_tools, output_tools, session_tools

# mcp_server/seo_workbook_mcp/app.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_csv_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_file():
        return path
    # Falls back to a path anchored on this file's location (rather than
    # cwd) so the default app works the same whether it's imported from the
    # repo root, from mcp_server/, or from inside a container.
    fallback = _REPO_ROOT / raw_path
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(f"best_practices_csv_path not found: tried {path} and {fallback}")


def create_app(
    settings: Settings | None = None,
    sheets_client_factory: Callable[[], Any] = build_sheets_service,
) -> FastMCP:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    mcp = FastMCP("seo-workbook-mcp")
    catalog = load_catalog(_resolve_csv_path(settings.best_practices_csv_path))
    store = SessionStore()

    catalog_tools.register(mcp, catalog)
    session_tools.register(mcp, catalog, store)
    output_tools.register(mcp, catalog, store, sheets_client_factory=sheets_client_factory)

    return mcp


mcp = create_app()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
