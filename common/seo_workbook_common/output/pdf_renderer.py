from __future__ import annotations

from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from ..best_practices import BestPracticeCatalog
from ..models.plan_session import PlanSession
from .formatting import format_item

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "jinja"]),
)
_env.filters["format_item"] = format_item


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


def render_summary_pdf(session: PlanSession, catalog: BestPracticeCatalog | None = None) -> bytes:
    html = render_summary_html(session, catalog)
    return HTML(string=html).write_pdf()
