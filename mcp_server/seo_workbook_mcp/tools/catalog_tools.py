from __future__ import annotations

from fastmcp import FastMCP
from seo_workbook_common.best_practices import BestPracticeCatalog


def register(mcp: FastMCP, catalog: BestPracticeCatalog) -> None:
    @mcp.tool()
    def list_touchpoints(category: str | None = None) -> list[dict]:
        """List SEO optimization touchpoints from the best-practices catalog.

        Pass `category` (e.g. "Core", "Deep", "Tech", "Off-site", "Technical
        Optimization") to filter, or omit to list all. Returns a short
        summary per touchpoint — call get_touchpoint_detail(touchpoint_id)
        for the full QA guidelines before asking the specialist about it.
        """
        touchpoints = catalog.by_category(category) if category else catalog.touchpoints
        return [
            {
                "touchpoint_id": tp.touchpoint_id,
                "name": tp.name,
                "category": tp.category,
                "search_tactic": tp.search_tactic,
                "description": tp.description,
            }
            for tp in touchpoints
        ]

    @mcp.tool()
    def get_touchpoint_detail(touchpoint_id: str) -> dict:
        """Get full detail for one touchpoint: description, the QA
        guidelines checklist to walk the specialist through, and
        implementation notes. Call list_touchpoints() first to find valid
        touchpoint_id values.
        """
        try:
            tp = catalog.get(touchpoint_id)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "touchpoint_id": tp.touchpoint_id,
            "name": tp.name,
            "category": tp.category,
            "search_tactic": tp.search_tactic,
            "description": tp.description,
            "qa_guidelines": list(tp.qa_guidelines),
            "implementation_notes": tp.implementation_notes,
        }
