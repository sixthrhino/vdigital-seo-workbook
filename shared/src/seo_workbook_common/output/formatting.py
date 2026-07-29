from __future__ import annotations

from datetime import datetime

_SINGLE_VALUE_TOUCHPOINTS = {"title_tag", "meta_description", "h1_tag"}
_INTERNAL_LINK_TOUCHPOINTS = {"internal_linking_to_other_pages_homepage", "internal_linking_to_target_page"}


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
        # A legacy-workbook import's free-text "what was done" cell,
        # preserved verbatim (see legacy_import/converter.py) rather than
        # parsed into fabricated structure — there's no old/new pairing or
        # other structured fields to show alongside it, just the note.
        return item.get("note", "")

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
