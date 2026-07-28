from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env-driven settings shared by mcp-server and agent-service.

    Both Cloud Run services read the same env var names so a single
    --set-env-vars block can be reused across deploy commands.
    """

    model_config = SettingsConfigDict(
        env_prefix="SEO_WORKBOOK_",
        env_file=".env",
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"

    gcp_project_id: str = "your-project-id"
    gcp_region: str = "us-central1"

    best_practices_csv_path: str = "data/organic_qa_checklist.csv"

    # Shared between mcp-server (which writes reports/finalized sessions)
    # and agent-service (which resolves short report-share links back to a
    # signed URL) — kept here rather than duplicated in each service's own
    # settings subclass since both need the exact same values.
    reports_bucket: str = ""
    mongo_uri: str = ""
    mongo_database: str = "seo"
    mongo_report_tokens_collection: str = "report_tokens"


@lru_cache
def get_settings() -> Settings:
    return Settings()
