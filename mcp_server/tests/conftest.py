from pathlib import Path

import pytest
from seo_workbook_common.config import Settings

from seo_workbook_mcp.app import create_app

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "organic_qa_checklist.csv"


@pytest.fixture
def mcp_app():
    settings = Settings(best_practices_csv_path=str(CSV_PATH))
    return create_app(settings)
