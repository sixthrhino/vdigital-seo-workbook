from __future__ import annotations

import re

from ..best_practices.loader import slugify
from ..keywords import parse_keyword_target
from ..models.plan_session import Page, PlanSession, SessionStatus, TouchpointAnswer, ValidationResult
from ..output.formatting import reduce_core_optimizations_mentions

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


# Two reliably parseable heading shapes seen in real historical notes —
# other prose forms ("Change H1: ... to an H2: tag." with no bracket
# markers at all) vary too much to parse without risking fabricated
# structure, so only these two are promoted to a real touchpoint at import
# time; everything else stays free text.
#
# 1. A line that itself starts with a literal <H#> marker states its own
#    target level unambiguously — e.g. "<H3> Checking Over Your Trailer".
#    Gives new_tag + heading_text only; old_tag is never stated (fine,
#    it's optional — see validators.py). Duplicated from (not shared with)
#    seo_testing_agent.check_orchestrator._extract_inline_headings's
#    literal-marker branch, which recognizes the same shape for live-site
#    QA — small and stable enough that duplicating it beats a
#    cross-component import between otherwise-unrelated packages.
_INLINE_HEADING_LINE_RE = re.compile(r"^<h([1-6])>\s*(\S.*)$", re.I)

# 2. "Change <H#> heading text to an <H#> tag." — bracket markers *and* the
#    word "tag" are both optional in the wild ("to an <H2>" alone, or a
#    missing "s" on "tags"), but the "change ... to a(n) ..." shape itself
#    is consistent enough to trust. Gives old_tag, new_tag, *and*
#    heading_text — better than shape 1, since the old level is stated.
_CHANGE_HEADING_RE = re.compile(
    r"change\s+<h([1-6])>\s*(.+?)\s+to\s+an?\s+<h([1-6])>\s*(?:tags?)?\.?",
    re.I,
)


def _extract_heading_items(normalized_note: str) -> tuple[list[dict[str, str]], str]:
    """Pull recognized heading-change phrasing (see the two shapes above)
    out of an already-normalized note into real h2_h3_h4_tags items, and
    reduce/remove its "Core Optimizations: Title Tag, Meta Description, H1"
    summary sentence (see reduce_core_optimizations_mentions) — so what's
    actually *stored* as the fallback "optimizations" note is already
    clean, not just what a report renders from it later.

    Returns (heading_items, remaining_text) — matched text is removed from
    what's returned, so none of it ends up duplicated in that fallback.
    """
    heading_items: list[dict[str, str]] = []

    def _collect_change(match: re.Match) -> str:
        heading_items.append({
            "old_tag": f"h{match.group(1)}",
            "new_tag": f"h{match.group(3)}",
            "heading_text": match.group(2).strip().rstrip(":").strip(),
        })
        return ""

    text = reduce_core_optimizations_mentions(normalized_note)
    text = _CHANGE_HEADING_RE.sub(_collect_change, text)

    remaining_lines: list[str] = []
    for line in text.splitlines():
        match = _INLINE_HEADING_LINE_RE.match(line.strip())
        if match:
            heading_text = match.group(2).strip().rstrip(":").strip()
            heading_items.append({"new_tag": f"h{match.group(1)}", "heading_text": heading_text})
        else:
            remaining_lines.append(line)

    remaining = "\n".join(remaining_lines)
    # Removing a Core Optimizations sentence or a "Change ... tag."
    # sentence can leave blank lines behind — collapse those the same way
    # _normalize_note already collapses repeated blank lines.
    remaining = re.sub(r"\n{3,}", "\n\n", remaining).strip()
    return heading_items, remaining


def build_session_from_rows(client: str, month: str, rows: list[dict]) -> PlanSession:
    """Convert one month's worth of legacy-workbook rows (as returned by
    workbook_sheets.get_month_rows) into a PlanSession.

    Best-effort only, same rationale as the CSV importer this was adapted
    from: only the explicit Old/New Title Tag, Meta Description, and H1
    columns become real title_tag/meta_description/h1_tag touchpoints
    (skipped when the "new" value is blank/"N/A"/"No changes"/unchanged
    from "old"). The free-text "opt_note" column mixes several kinds of
    changes in unstructured prose per row — the one reliably parseable
    shape within it ("<H#> heading text" lines) is promoted to a real
    h2_h3_h4_tags touchpoint (see _extract_heading_items); everything else
    is preserved verbatim as an "optimizations" touchpoint per page, rather
    than risk mis-parsing prose whose phrasing varies too much across real
    historical notes into fabricated structure. Every imported touchpoint
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
            heading_items, remaining_note = _extract_heading_items(_normalize_note(notes_raw))
            if heading_items:
                page.touchpoints.append(
                    TouchpointAnswer(
                        touchpoint_id="h2_h3_h4_tags",
                        category="Deep",
                        items=heading_items,
                        validation=LEGACY_VALIDATION,
                    )
                )
            if remaining_note:
                page.touchpoints.append(
                    TouchpointAnswer(
                        touchpoint_id="optimizations",
                        category="Optimizations",
                        items=[{"note": remaining_note}],
                        validation=LEGACY_VALIDATION,
                    )
                )

    return session
