from __future__ import annotations

from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..best_practices import BestPracticeCatalog
from ..models.plan_session import Page, PlanSession
from .formatting import format_item, format_month, format_old_new

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "jinja"]),
)
_env.filters["format_item"] = format_item
_env.filters["format_month"] = format_month
_env.filters["format_old_new"] = format_old_new

# Title/meta/H1 get their own dedicated columns in the row-per-URL table
# view (render_page_table_html) — everything else recorded on a page is
# summarized in that same row's "Optimizations" column instead.
_TABLE_DEDICATED_TOUCHPOINTS = {"title_tag", "meta_description", "h1_tag"}


def _touchpoint_name_resolver(catalog: BestPracticeCatalog | None) -> Callable[[str], str]:
    def resolve(touchpoint_id: str) -> str:
        if catalog is None:
            return touchpoint_id
        try:
            return catalog.get(touchpoint_id).name
        except KeyError:
            return touchpoint_id

    return resolve


def render_summary_html(session: PlanSession, catalog: BestPracticeCatalog | None = None) -> str:
    """Render a PlanSession into the client-facing HTML summary.

    `catalog` is optional and only used to show human-readable touchpoint
    names (e.g. "Title Tag" instead of "title_tag") — the summary still
    renders correctly without it, just with the raw touchpoint_id.
    """
    template = _env.get_template("summary.html.jinja")
    return template.render(session=session, touchpoint_name=_touchpoint_name_resolver(catalog))


def _page_table_row(page: Page, resolve_name: Callable[[str], str]) -> dict:
    keyword = page.keyword_target.keyword if page.keyword_target else ""
    volume = page.keyword_target.search_volume if page.keyword_target else None
    keyword_display = f"{keyword} ({volume})" if keyword and volume is not None else keyword

    optimizations = [
        resolve_name(tp.touchpoint_id) for tp in page.touchpoints
        if tp.touchpoint_id not in _TABLE_DEDICATED_TOUCHPOINTS
    ]

    def pair(touchpoint_id: str) -> tuple[str, str]:
        tp = page.get_touchpoint(touchpoint_id)
        if tp is None or not tp.items:
            return "—", "—"
        return format_old_new(tp.items[0], touchpoint_id) or ("—", tp.items[0].get("new_value", ""))

    title_old, title_new = pair("title_tag")
    meta_old, meta_new = pair("meta_description")
    h1_old, h1_new = pair("h1_tag")

    return {
        "url": page.url,
        "keyword": keyword_display,
        "geo": page.geo or "",
        "optimizations": ", ".join(optimizations) or "—",
        "title_old": title_old, "title_new": title_new,
        "meta_old": meta_old, "meta_new": meta_new,
        "h1_old": h1_old, "h1_new": h1_new,
    }


def render_page_table_html(session: PlanSession, catalog: BestPracticeCatalog | None = None) -> str:
    """Render a PlanSession as a compact, one-row-per-URL table — closer to
    the legacy workbook's row layout than render_summary_html's per-
    touchpoint breakdown, for quickly scanning a whole month's planned
    changes across every page at once. Title/meta/H1 get dedicated
    old/new columns (see format_old_new); every other recorded touchpoint
    is summarized by name in a single "Optimizations" column.
    """
    template = _env.get_template("page_table.html.jinja")
    rows = [_page_table_row(page, _touchpoint_name_resolver(catalog)) for page in session.pages]
    return template.render(session=session, rows=rows)
