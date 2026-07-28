from functools import lru_cache

from seo_workbook_common.config import Settings


class McpSettings(Settings):
    """Adds mcp_server-specific env vars on top of the shared Settings base.

    reports_bucket / mongo_uri / mongo_database / mongo_report_tokens_collection
    live in the shared base (seo_workbook_common.config.Settings) since
    agent_service needs the exact same values to resolve short report
    share-links back to a signed URL — only the finalized-session
    collection name is mcp_server-specific.
    """

    mongo_collection: str = "workbook"

    # Base URL of agent-service's public /reports/{token} redirect route —
    # render_session_report returns a short link pointing here instead of a
    # ~400-character signed GCS URL directly, since that length is a real
    # source of transcription errors when the model reproduces it in text.
    agent_public_url: str = ""


@lru_cache
def get_mcp_settings() -> McpSettings:
    return McpSettings()
