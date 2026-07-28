"""Integration smoke tests — require a live network connection.

Run with: pytest -m integration
Skip with: pytest -m "not integration"
"""

import pytest

from seo_testing_mcp.tools.seo_core import check_title, check_canonical
from seo_testing_mcp.tools.technical import check_url_hygiene, check_noindex, check_redirect
from seo_testing_mcp.tools.technical import check_robots_txt

pytestmark = pytest.mark.integration

# example.com is a stable, minimally dynamic test target maintained by IANA.
EXAMPLE_URL = "https://example.com"


class TestExampleCom:
    async def test_title_returns_results(self):
        results = await check_title(EXAMPLE_URL)
        assert isinstance(results, list)
        assert len(results) > 0
        assert all("label" in r and "status" in r and "detail" in r for r in results)

    async def test_canonical_returns_results(self):
        results = await check_canonical(EXAMPLE_URL)
        assert isinstance(results, list)
        assert len(results) > 0

    async def test_noindex_not_set(self):
        results = await check_noindex(EXAMPLE_URL)
        # example.com should not be noindexed
        assert not any(r["status"] == "fail" for r in results)

    async def test_redirect_direct_200(self):
        results = await check_redirect("https://example.com/")
        assert isinstance(results, list)
        assert results[0]["status"] in ("pass", "warn")

    def test_url_hygiene_example_clean(self):
        results = check_url_hygiene("https://example.com/clean-path")
        assert isinstance(results, list)
        assert all(r["status"] == "pass" for r in results)

    async def test_robots_txt_reachable(self):
        results = await check_robots_txt(EXAMPLE_URL)
        assert isinstance(results, list)
        assert len(results) > 0
