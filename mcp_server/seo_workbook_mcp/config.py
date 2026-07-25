from functools import lru_cache

from seo_workbook_common.config import Settings


class McpSettings(Settings):
    """Adds mcp_server-specific env vars on top of the shared Settings base
    — agent_service has no use for a reports bucket or MongoDB, so these
    stay local rather than in seo_workbook_common.
    """

    # Empty by default so render_session_report / finalize_session can fail
    # with a clear error rather than a confusing one if these haven't been
    # configured yet.
    reports_bucket: str = ""
    mongo_uri: str = ""
    mongo_database: str = "seo_workbook"
    mongo_collection: str = "plan_sessions"


@lru_cache
def get_mcp_settings() -> McpSettings:
    return McpSettings()
