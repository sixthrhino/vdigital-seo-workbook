from __future__ import annotations

from ..best_practices.loader import slugify
from ..keywords import parse_keyword_target
from ..models.plan_session import Page, PlanSession, SessionStatus, TouchpointAnswer, ValidationResult

_EMPTY_MARKERS = {"", "n/a", "na", "no changes", "no change", "description is missing!", "missing"}

LEGACY_VALIDATION = ValidationResult(
    passed=True,
    messages=["Imported from legacy workbook — not validated against current QA rules"],
)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return None if v.lower() in _EMPTY_MARKERS else v


def _normalize_note(raw: str) -> str:
    """Clean up a free-text opt_note cell's whitespace without destroying
    its line breaks — they're meaningful structure (the cell's own
    paragraph/section breaks), needed to correctly read its content back,
    not incidental formatting to collapse away. Only collapses repeated
    whitespace *within* a line and repeated blank lines (down to one),
    trimming each line's leading/trailing whitespace.
    """
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    normalized: list[str] = []
    for line in lines:
        if line == "" and normalized and normalized[-1] == "":
            continue
        normalized.append(line)
    return "\n".join(normalized).strip()


def build_session_from_rows(client: str, month: str, rows: list[dict]) -> PlanSession:
    """Convert one month's worth of legacy-workbook rows (as returned by
    workbook_sheets.get_month_rows) into a PlanSession.

    Best-effort only, same rationale as the CSV importer this was adapted
    from: only the explicit Old/New Title Tag, Meta Description, and H1
    columns become real title_tag/meta_description/h1_tag touchpoints
    (skipped when the "new" value is blank/"N/A"/"No changes"/unchanged
    from "old"). The free-text "opt_note" column mixes several kinds of
    changes in unstructured prose per row — rather than risk
    mis-parsing that into fabricated structure, it's preserved verbatim as
    a single "optimizations" touchpoint per page. Every imported touchpoint
    is marked validation.passed=True with an explanatory message rather
    than run through today's validate_touchpoint rules, since this sheet
    didn't capture per-touchpoint primary_keyword/cta metadata and grading
    it against current rules would show most historical work as "failed"
    for a data-capture gap, not a quality issue.
    """
    session_id = f"{slugify(client)}-{month}"
    session = PlanSession(session_id=session_id, client=client, month=month, status=SessionStatus.FINALIZED)

    for row in rows:
        url = row["url"].strip()
        page = next((p for p in session.pages if p.url == url), None)
        if page is None:
            page = Page(url=url)
            session.pages.append(page)

        keyword_raw = _clean(row.get("keyword_raw"))
        keyword_text: str | None = None
        if keyword_raw:
            keyword_text = parse_keyword_target(keyword_raw).keyword
            if page.keyword_target is None:
                page.keyword_target = parse_keyword_target(keyword_raw)

        geo_raw = _clean(row.get("geo"))
        if geo_raw and page.geo is None:
            page.geo = geo_raw

        def _add_single_value(touchpoint_id: str, old_raw: str, new_raw: str, keyword: str | None) -> None:
            old, new = _clean(old_raw), _clean(new_raw)
            if new is None or (old is not None and old == new):
                return
            item = {"new_value": new}
            if old is not None:
                item["old_value"] = old
            if keyword:
                item["primary_keyword"] = keyword
            page.touchpoints.append(
                TouchpointAnswer(touchpoint_id=touchpoint_id, category="Core", items=[item], validation=LEGACY_VALIDATION)
            )

        _add_single_value("title_tag", row.get("old_title", ""), row.get("new_title", ""), keyword_text)
        _add_single_value("meta_description", row.get("old_meta", ""), row.get("new_meta", ""), None)
        _add_single_value("h1_tag", row.get("old_h1", ""), row.get("new_h1", ""), keyword_text)

        notes_raw = (row.get("opt_note") or "").strip()
        if notes_raw:
            page.touchpoints.append(
                TouchpointAnswer(
                    touchpoint_id="optimizations",
                    category="Optimizations",
                    items=[{"note": _normalize_note(notes_raw)}],
                    validation=LEGACY_VALIDATION,
                )
            )

    return session
