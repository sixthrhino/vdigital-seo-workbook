from __future__ import annotations

import re
from datetime import datetime

_SINGLE_VALUE_TOUCHPOINTS = {"title_tag", "meta_description", "h1_tag"}
_INTERNAL_LINK_TOUCHPOINTS = {"internal_linking_to_other_pages_homepage", "internal_linking_to_target_page"}

# Real-world legacy-workbook "optimizations" notes (see legacy_import/
# converter.py) tend to follow a loose, recurring shape: a "Core
# Optimizations: X, Y, Z." summary sentence, then one or more "Make Headers
# below <H#> tag" instruction phrases each followed by the actual "<H#>
# heading text" lines. The instruction phrase is redundant with each
# heading's own explicit <H#> marker (and wording varies — "an <H2> tag",
# bare "<H2>", no "tag" at all), so it's dropped rather than parsed for its
# own sake; the marker itself is what's authoritative.
_CORE_OPTIMIZATIONS_RE = re.compile(r"core optimizations:\s*([^.]+)\.?", re.I)
_HEADER_INSTRUCTION_RE = re.compile(r"make headers?\s+below\s+(?:an?\s+)?<h[1-6]>\s*(?:tags?)?\.?", re.I)
_HEADING_SEGMENT_RE = re.compile(r"<h([1-6])>\s*(.*?)(?=<h[1-6]>|$)", re.I | re.S)

# Title Tag/Meta Description/H1 already get their own dedicated old/new
# display everywhere this note is shown (the table report's Title/Meta/H1
# columns, format_old_new's pairing in the narrative report) — naming them
# again in the Core Optimizations summary is pure repetition, not new
# information. Typo-tolerant ("TItle Tag" seen live) via lowercasing.
_REDUNDANT_CORE_OPTIMIZATIONS = {"title tag", "meta description", "h1", "h1 tag"}


def format_optimizations_note(note: str) -> list[str]:
    """Best-effort reformat of a legacy-imported "optimizations" touchpoint's
    free-text note into standardized, readable lines instead of one run-on
    blob — real examples are consistent enough (see module docstring) to
    parse deterministically, without guessing at meaning the way an LLM
    might. Any text that doesn't match a recognized pattern is preserved
    verbatim as its own line rather than silently dropped, since this is a
    display transform only — nothing here feeds back into what was
    actually recorded.
    """
    if not note or not note.strip():
        return []

    text = note
    lines: list[str] = []

    core_match = _CORE_OPTIMIZATIONS_RE.search(text)
    if core_match:
        touchpoints = [
            p.strip() for p in core_match.group(1).split(",")
            if p.strip() and p.strip().lower() not in _REDUNDANT_CORE_OPTIMIZATIONS
        ]
        if touchpoints:
            lines.append(f"Core Optimizations: {', '.join(touchpoints)}")
        text = text[: core_match.start()] + text[core_match.end() :]

    text = _HEADER_INSTRUCTION_RE.sub(" ", text)

    heading_lines: list[str] = []

    def _collect_heading(match: re.Match) -> str:
        level, heading_text = match.group(1), match.group(2).strip().rstrip(":").strip()
        if heading_text:
            heading_lines.append(f"H{level}: {heading_text}")
        return " "

    text = _HEADING_SEGMENT_RE.sub(_collect_heading, text)
    lines.extend(heading_lines)

    leftover = " ".join(text.split())
    if leftover:
        lines.insert(0, leftover)

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
