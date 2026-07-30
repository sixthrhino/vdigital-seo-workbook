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

    @mcp.tool()
    def get_page_capture_format() -> dict:
        """Return record_page_from_text's expected labeled-text format,
        with a worked example — call this whenever the specialist asks for
        the format, an example, or a hint on how to write one of these
        blocks, and show them exactly what this returns rather than
        reciting the format from memory (it's a real reference, the same
        way list_touchpoints/get_touchpoint_detail are for touchpoints).
        """
        return {
            "format": (
                "url: <page URL>\n"
                "keyword: <primary keyword, optionally \"keyword (volume)\">\n"
                "geo: <target geo>\n"
                "title: <old title tag> -> <new title tag>\n"
                "meta: <old meta description> -> <new meta description>\n"
                "cta: <call to action text>\n"
                "h1: <old H1> -> <new H1>\n"
                "headings: <H# -> H#: heading text, one per line — old level "
                "optional, e.g. just \"H3: heading text\">\n"
                "notes: <anything else — links added, schema, alt text, etc. "
                "Can span multiple lines, but must be the LAST line.>"
            ),
            "example": (
                "url: https://example.com/service-a/\n"
                "keyword: auto insurance (500)\n"
                "geo: Scottsdale, AZ\n"
                "title: Auto Insurance - Acme -> Auto Insurance in Scottsdale, AZ - Acme\n"
                "meta: Get affordable coverage. -> Get a free quote on affordable auto "
                "insurance in Scottsdale.\n"
                "cta: Get a Quote\n"
                "h1: Auto Insurance -> Auto Insurance in Scottsdale\n"
                "headings: H2 -> H3: Checking Over Your Trailer\n"
                "H3: Emergency Equipment\n"
                "notes: Added internal link to homepage"
            ),
            "notes": (
                "Every field except url is optional. \"->\" (or the unicode arrow) "
                "separates old from new — not the word \"to\", which shows up too "
                "often inside real title/meta text on its own. Omit the old side "
                "for a brand-new page with nothing to compare against. keyword "
                "auto-fills title/h1's required primary_keyword, so it only needs "
                "to be given once. headings is the one multi-line field that "
                "doesn't have to be last — one heading change per line, each "
                "\"H# -> H#: text\" or just \"H#: text\" when the old level isn't "
                "known; the block runs until the next recognized label. notes "
                "must be the last line — everything after it becomes its value, "
                "including further line breaks. One block per page — call "
                "record_page_from_text once per URL."
            ),
        }
