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
# verbatim, one display line per the note's own line breaks (see
# legacy_import.converter._normalize_note — those breaks are preserved at
# import time specifically so they survive to here, since they're the
# cell's own paragraph/section structure, needed to read it back correctly,
# not incidental formatting).
_CORE_OPTIMIZATIONS_RE = re.compile(r"core optimizations:\s*([^.]+)\.?", re.I)

# Title Tag/Meta Description/H1 already get their own dedicated old/new
# display everywhere this note is shown (the table report's Title/Meta/H1
# columns, format_old_new's pairing in the narrative report) — naming them
# again in the Core Optimizations summary is pure repetition, not new
# information. Typo-tolerant ("TItle Tag" seen live) via lowercasing.
_REDUNDANT_CORE_OPTIMIZATIONS = {"title tag", "meta description", "h1", "h1 tag"}


def reduce_core_optimizations_mentions(text: str) -> str:
    """Reduce "Core Optimizations: X, Y, Z" sentences down to just their
    non-redundant names (dropping Title Tag/Meta Description/H1 — see
    _REDUNDANT_CORE_OPTIMIZATIONS), or remove the whole sentence if every
    name mentioned is redundant. Used both at display time
    (format_optimizations_note, below) and at import time
    (legacy_import.converter, so the *stored* note is already clean, not
    just what's rendered) — kept in one place so the two can't drift.
    """

    def _collect(match: re.Match) -> str:
        names = [
            p.strip() for p in match.group(1).split(",")
            if p.strip() and p.strip().lower() not in _REDUNDANT_CORE_OPTIMIZATIONS
        ]
        return f"Core Optimizations: {', '.join(names)}" if names else ""

    return _CORE_OPTIMIZATIONS_RE.sub(_collect, text)


def format_optimizations_note(note: str) -> list[str]:
    """Reformat a legacy-imported "optimizations" touchpoint's free-text
    note for display: reduce/remove its "Core Optimizations:" summary
    sentence (see reduce_core_optimizations_mentions — redundant with
    Title/Meta/H1's own dedicated display elsewhere). Everything else is
    returned verbatim, one entry per the note's own line breaks —
    deliberately not parsed further (see module docstring for why); blank
    lines are dropped rather than kept as empty entries.
    """
    if not note or not note.strip():
        return []

    text = reduce_core_optimizations_mentions(note)
    lines: list[str] = []
    for raw_line in text.splitlines():
        cleaned = " ".join(raw_line.split())
        if cleaned:
            lines.append(cleaned)

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
        # old_tag is optional (see validators.py) — the copy usually isn't
        # changing, only the tag wrapping it, and the source often doesn't
        # state what level a heading currently is. Shown as just the
        # target level when there's no old one to compare against.
        new_tag = item.get("new_tag", "?").upper()
        heading_text = item.get("heading_text", "")
        old_tag = item.get("old_tag", "").upper()
        if old_tag:
            return f"{old_tag} → {new_tag}: {heading_text}"
        return f"{new_tag}: {heading_text}"

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
        new_tag = item.get("new_tag", "?").upper()
        old_tag = item.get("old_tag", "").upper()
        old_display = f"<{old_tag}> {heading_text}" if old_tag else "—"
        return old_display, f"<{new_tag}> {heading_text}"

    return None
