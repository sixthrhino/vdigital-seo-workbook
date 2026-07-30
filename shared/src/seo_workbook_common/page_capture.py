"""Parses one page's month-of-changes from a single labeled text block —
an SEO specialist pastes one "label: value" per line instead of going
through a separate tool call per field (add_page, set_page_targeting, one
record_touchpoint per touchpoint). Deterministic, line-based parsing (not
an LLM guessing which field is which), matching the same "keep dispatch
logic in code" approach already used for legacy-workbook imports and
check dispatch elsewhere in this codebase.

Expected shape (all optional except url; see record_page_from_text's own
docstring in mcp-servers/seo-workbook-mcp for the specialist-facing version
of this):

    url: https://example.com/service-a/
    keyword: auto insurance (500)
    geo: Scottsdale, AZ
    title: Old Title Tag -> New Title Tag
    meta: Old meta description -> New meta description
    cta: Get a Quote
    h1: Old H1 -> New H1
    notes: free text, can span multiple lines, must be the last label

"->" (or the unicode arrow) separates old from new — not the bare word
"to", which is common enough inside real title/meta text to false-split on
("Guide to Trailer Maintenance" has its own "to").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_LABEL_LINE_RE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9 ]*?)\s*:\s*(.*)$")

_LABEL_ALIASES: dict[str, str] = {
    "url": "url",
    "page": "url",
    "keyword": "keyword",
    "primary keyword": "keyword",
    "geo": "geo",
    "target geo": "geo",
    "title": "title",
    "title tag": "title",
    "meta": "meta",
    "meta description": "meta",
    "cta": "cta",
    "h1": "h1",
    "h1 tag": "h1",
    "notes": "notes",
    "note": "notes",
}

_OLD_NEW_RE = re.compile(r"^(.*?)\s*(?:->|→)\s*(.+)$")


def _split_old_new(value: str) -> tuple[str | None, str]:
    match = _OLD_NEW_RE.match(value)
    if match:
        old = match.group(1).strip()
        return (old or None), match.group(2).strip()
    return None, value.strip()


@dataclass
class ParsedPageCapture:
    url: str
    keyword: str | None = None
    geo: str | None = None
    title_old: str | None = None
    title_new: str | None = None
    meta_old: str | None = None
    meta_new: str | None = None
    cta: str | None = None
    h1_old: str | None = None
    h1_new: str | None = None
    notes: str | None = None


def parse_page_capture(text: str) -> ParsedPageCapture:
    """Parse one page's labeled capture block into its fields.

    Every label except "notes" is expected on a single line. "notes" runs
    to the end of the text once encountered (so it must come last),
    preserving its own line breaks — they're meaningful paragraph/section
    structure, same reasoning as legacy_import.converter._normalize_note.

    Raises ValueError if no "url:" line is found.
    """
    fields: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _LABEL_LINE_RE.match(lines[i])
        if not match:
            i += 1
            continue
        raw_label, value = match.groups()
        label = _LABEL_ALIASES.get(raw_label.strip().lower())
        if label is None:
            i += 1
            continue
        if label == "notes":
            fields["notes"] = "\n".join([value] + lines[i + 1 :]).strip()
            break
        fields[label] = value.strip()
        i += 1

    url = fields.get("url", "").strip()
    if not url:
        raise ValueError('Missing "url:" line — every page capture must name the page being recorded.')

    title_old, title_new = _split_old_new(fields["title"]) if fields.get("title") else (None, None)
    meta_old, meta_new = _split_old_new(fields["meta"]) if fields.get("meta") else (None, None)
    h1_old, h1_new = _split_old_new(fields["h1"]) if fields.get("h1") else (None, None)

    return ParsedPageCapture(
        url=url,
        keyword=fields.get("keyword") or None,
        geo=fields.get("geo") or None,
        title_old=title_old,
        title_new=title_new or None,
        meta_old=meta_old,
        meta_new=meta_new or None,
        cta=fields.get("cta") or None,
        h1_old=h1_old,
        h1_new=h1_new or None,
        notes=fields.get("notes") or None,
    )
