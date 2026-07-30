from __future__ import annotations

import re
from datetime import datetime

_SINGLE_VALUE_TOUCHPOINTS = {"title_tag", "meta_description", "h1_tag"}
_INTERNAL_LINK_TOUCHPOINTS = {"internal_linking_to_other_pages_homepage", "internal_linking_to_target_page"}

# Real-world legacy-workbook "optimizations" notes (see legacy_import/
# converter.py) almost always open with a "Core Optimizations: X, Y, Z."
# summary sentence. What follows it varies too much in real historical data
# to parse reliably (bracket markers like "<H3> heading text", prose like
# "Change H1: ... to an H2: tag.", and surely others not seen yet) — rather
# than chase every phrasing variant (and risk garbling ones we get wrong),
# only the summary sentence is reformatted; everything else is shown
# verbatim as one cleaned-up paragraph and left to the browser's normal
# word-wrap for readability.
_CORE_OPTIMIZATIONS_RE = re.compile(r"core optimizations:\s*([^.]+)\.?", re.I)

# Title Tag/Meta Description/H1 already get their own dedicated old/new
# display everywhere this note is shown (the table report's Title/Meta/H1
# columns, format_old_new's pairing in the narrative report) — naming them
# again in the Core Optimizations summary is pure repetition, not new
# information. Typo-tolerant ("TItle Tag" seen live) via lowercasing.
_REDUNDANT_CORE_OPTIMIZATIONS = {"title tag", "meta description", "h1", "h1 tag"}


def format_optimizations_note(note: str) -> list[str]:
    """Reformat a legacy-imported "optimizations" touchpoint's free-text
    note for display: drop mentions of Title Tag/Meta Description/H1 from
    its "Core Optimizations:" summary sentence (redundant with those
    touchpoints' own dedicated display elsewhere), dropping the whole
    sentence if nothing else was named alongside them. Everything else is
    returned verbatim as a single cleaned-up (whitespace-collapsed)
    paragraph — deliberately not parsed further; see module docstring for
    why.
    """
    if not note or not note.strip():
        return []

    text = note
    core_lines: list[str] = []

    def _collect_core(match: re.Match) -> str:
        touchpoints = [
            p.strip() for p in match.group(1).split(",")
            if p.strip() and p.strip().lower() not in _REDUNDANT_CORE_OPTIMIZATIONS
        ]
        if touchpoints:
            core_lines.append(f"Core Optimizations: {', '.join(touchpoints)}")
        return " "

    text = _CORE_OPTIMIZATIONS_RE.sub(_collect_core, text)

    lines = list(core_lines)
    leftover = " ".join(text.split())
    if leftover:
        lines.append(leftover)

    return lines


def format_month(month: str) -> str:
    """Render a session's "YYYY-MM" month string as "June 2026" for
    display. Falls back to the raw value on anything that doesn't match —
    PlanSession already validates this shape going in, so that should only
    happen for legacy/malformed data.
    """
    try:
        return datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month


def format_item(item: dict[str, str], touchpoint_id: str) -> str:
    """Render one touchpoint item as a single human-readable line, shared by
    the PDF and Sheets output so both stay consistent. Falls back to a
    generic key: value join for touchpoints without a dedicated format —
    every touchpoint still renders as *something* readable even before a
    format is written for it specifically.
    """
    if touchpoint_id in _SINGLE_VALUE_TOUCHPOINTS:
        parts = [f"New: {item.get('new_value', '')}"]
        if item.get("old_value"):
            parts.append(f"(was: {item['old_value']})")
        if item.get("primary_keyword"):
            parts.append(f"Keyword: {item['primary_keyword']}")
        if item.get("cta"):
            parts.append(f"CTA: {item['cta']}")
        return " | ".join(parts)

    if touchpoint_id == "h2_h3_h4_tags":
        old_tag = item.get("old_tag", "?").upper()
        new_tag = item.get("new_tag", "?").upper()
        return f"{old_tag} → {new_tag}: {item.get('heading_text', '')}"

    if touchpoint_id in _INTERNAL_LINK_TOUCHPOINTS:
        return f"\"{item.get('anchor_text', '')}\" → {item.get('target_url', '')}"

    if touchpoint_id == "canonical_tags":
        return f"Canonical → {item.get('new_value', '')}"

    if touchpoint_id == "image_alt_text":
        alt = f"Alt text: {item.get('new_value', '')}"
        if item.get("old_value"):
            alt += f" (was: {item['old_value']})"
        return alt

    if touchpoint_id == "optimizations":
        # A legacy-workbook import's free-text "what was done" cell — see
        # format_optimizations_note for the standardized-line breakdown;
        # joined into one line here since this function's contract is a
        # single string (shared by Sheets/plain-text output).
        return "; ".join(format_optimizations_note(item.get("note", "")))

    return "; ".join(f"{k}: {v}" for k, v in sorted(item.items()))


def format_old_new(item: dict[str, str], touchpoint_id: str) -> tuple[str, str] | None:
    """Split one touchpoint item into (old, new) display strings for the
    HTML report's two-row old/new layout — cramming both into a single
    line (see format_item, still used by Sheets/plain-text output) reads
    poorly for a side-by-side "what changed" comparison. Returns None for
    touchpoints with no genuine old/new pairing (a brand-new internal link,
    a canonical target with no prior value) — the template falls back to
    format_item's single line for those.
    """
    if touchpoint_id in _SINGLE_VALUE_TOUCHPOINTS or touchpoint_id == "image_alt_text":
        return item.get("old_value") or "—", item.get("new_value", "")

    if touchpoint_id == "h2_h3_h4_tags":
        heading_text = item.get("heading_text", "")
        old_tag = item.get("old_tag", "?").upper()
        new_tag = item.get("new_tag", "?").upper()
        return f"<{old_tag}> {heading_text}", f"<{new_tag}> {heading_text}"

    return None
