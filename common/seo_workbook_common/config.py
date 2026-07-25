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


@lru_cache
def get_settings() -> Settings:
    return Settings()
