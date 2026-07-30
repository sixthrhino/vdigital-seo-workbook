"""Fetches a finalized SEO plan from MongoDB — via seo-workbook-mcp's
find_session tool, the same system of record seo-workbook-agent writes to —
and converts it into the row shape Mode B's check pipeline
(check_orchestrator.checks_for_row/_run_row/run_batch) already expects.

This replaces Mode B's old source of "what was planned": a Google Sheet read
live over the Sheets API, or an uploaded .xlsx parsed locally (see git history
for workbook_upload.py, removed alongside this). Both fed the same flat row
shape; this module produces that shape from a PlanSession instead.

Auto-check dispatch is still resolve_checks_for_opt_note (seo-testing-mcp) —
what changed is the *input* it's given. Instead of parsing a specialist's
free-text "What Is Planned / Has Been Done?" cell, page_optimization_names
builds an opt_note-shaped string entirely from an exact lookup of which
touchpoint_ids are actually recorded on the page (_TOUCHPOINT_TO_OPTIMIZATION_NAMES),
so the eventual substring match against the catalog is deterministic — driven
by structured data, not a guess at what a client's free text means. Reusing
resolve_checks_for_opt_note rather than adding a parallel tool on
seo-testing-mcp means the auto_checks/guided_questions mapping only lives in
one place (rules.json) and can't drift between the two input paths.
"""

from __future__ import annotations

import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# PlanSession touchpoint_id (shared/seo_workbook_common, sourced from the
# organic_qa_checklist.csv best-practices catalog) -> the corresponding
# optimization name(s) in seo-testing-mcp's own catalog
# (mcp-servers/seo-testing-mcp/data/rules.json). The two catalogs describe
# the same real-world touchpoints under different names/granularity — this
# is the one place that correspondence is recorded. Empty list means no
# testing-catalog auto_checks exist for that touchpoint (manual-only there
# too, e.g. Backlink QA-style entries).
_TOUCHPOINT_TO_OPTIMIZATION_NAMES: dict[str, list[str]] = {
    "adding_original_photos_videos": [],
    "ai_crawler_directives_for_robots_txt_files": ["AIO AI Crawler Directives"],
    "canonical_tags": ["Canonical Tags"],
    "cta_call_to_action": ["CTA Structure & Alignment"],
    "embedding_google_maps": ["Google Maps Embedding"],
    "embedding_youtube_videos_on_site": ["YouTube Video Embedding"],
    "external_linking": ["External Linking"],
    "geo_keywords": ["Geo Keywords"],
    "h1_tag": ["H1 Tag"],
    "h2_h3_h4_tags": ["H2 / H3 / H4 Tags"],
    "html_sitemap": ["XML Sitemaps"],
    "image_alt_text": ["Image Alt Text & Optimization"],
    "indexation_audits": ["Indexation Audits"],
    "internal_link_cleanup": ["Internal Links to Redirects"],
    "internal_linking_to_other_pages_homepage": ["Internal Linking & Anchor Text"],
    "internal_linking_to_target_page": ["Internal Linking & Anchor Text"],
    "llms_txt_check": ["LLMs.txt Check"],
    "lsi_keywords": ["LSI Keywords"],
    "meta_description": ["Meta Description"],
    "nap_information": ["NAP Information"],
    "off_site_youtube_optimization": ["YouTube Channel Optimization"],
    "page_speed_analysis": ["Page Speed Analysis"],
    "reformatting_content": ["Content Reformatting"],
    "robots_txt_check": ["Robots.txt Check"],
    "schema_markup_structured_data": ["Schema Markup"],
    "section_design_faq_content": ["Section Design & FAQ Content"],
    "social_metadata_og_and_twitter_tag": ["Social Metadata (OG & Twitter)"],
    "takeaways_tldr": ["TLDR / AI Takeaways"],
    "title_tag": ["Title Tag"],
    "toc_table_of_contents": ["TOC (Table of Contents)"],
    "trust_signals_testimonials_reviews_etc": [],
    "url_changes_redirection": ["New Page / URL Created"],
    "xml_sitemaps": ["XML Sitemaps"],
}


def _split_geo(geo: str) -> tuple[str, str]:
    """"Denver, CO" -> ("Denver", "CO"). No comma (a bare state name/list,
    or "Multiple Locations") passes through as geo_city unchanged — matches
    geo_check_accuracy's own state-list/multi-location handling, and mirrors
    the identical split the old Sheets/xlsx row-parsing paths used."""
    geo = (geo or "").strip()
    if "," not in geo:
        return geo, ""
    city, state = geo.rsplit(",", 1)
    return city.strip(), state.strip()


def _touchpoints_by_id(page: dict) -> dict[str, dict]:
    return {tp["touchpoint_id"]: tp for tp in page.get("touchpoints", [])}


def _first_item(touchpoints: dict[str, dict], touchpoint_id: str) -> dict[str, str]:
    items = touchpoints.get(touchpoint_id, {}).get("items") or []
    return items[0] if items else {}


def _heading_opt_note(items: list[dict[str, str]]) -> str:
    """Reconstruct "<H#> heading text" lines from structured h2_h3_h4_tags
    items (old_tag/new_tag/heading_text — see validators.py) — the exact
    format checks_for_row._extract_inline_headings already knows how to
    parse back out, so that extraction doesn't need duplicating here."""
    lines = []
    for item in items:
        new_tag = (item.get("new_tag") or "").upper()
        text = item.get("heading_text") or ""
        if new_tag and text:
            lines.append(f"<{new_tag}> {text}")
    return "\n".join(lines)


def _internal_link_opt_note(items: list[dict[str, str]]) -> str:
    """Reconstruct "Internal link to <URL>" lines from structured
    internal-linking items (anchor_text/target_url — see validators.py) —
    the format checks_for_row._extract_internal_links already parses."""
    lines = []
    for item in items:
        target = item.get("target_url") or ""
        if target:
            lines.append(f"Internal link to {target}")
    return "\n".join(lines)


def page_optimization_names(page: dict) -> str:
    """Canonical optimization-name text, for feeding to
    resolve_checks_for_opt_note — built from exactly which touchpoints are
    recorded on this page (an exact dict lookup per touchpoint_id), not
    from parsing arbitrary text."""
    names: list[str] = []
    for tp in page.get("touchpoints", []):
        for name in _TOUCHPOINT_TO_OPTIMIZATION_NAMES.get(tp["touchpoint_id"], []):
            if name not in names:
                names.append(name)
    return "; ".join(names)


def page_to_row(page: dict) -> dict[str, Any]:
    """Convert one PlanSession page (as returned by find_session/get_session)
    into the flat row shape check_orchestrator.checks_for_row expects.

    row["opt_note"] combines the canonical optimization-name text (so
    resolve_checks_for_opt_note's substring match resolves auto_checks/
    guided_questions) with reconstructed heading/internal-link mini-blocks
    (so checks_for_row's own inline-heading/internal-link extraction keeps
    working unchanged) — everything downstream of this function is
    unmodified Mode B pipeline.
    """
    touchpoints = _touchpoints_by_id(page)
    keyword_target = page.get("keyword_target") or {}
    geo_city, geo_state = _split_geo(page.get("geo") or "")

    opt_note_parts = [page_optimization_names(page)]
    if "h2_h3_h4_tags" in touchpoints:
        opt_note_parts.append(_heading_opt_note(touchpoints["h2_h3_h4_tags"].get("items") or []))
    for link_touchpoint_id in ("internal_linking_to_other_pages_homepage", "internal_linking_to_target_page"):
        if link_touchpoint_id in touchpoints:
            opt_note_parts.append(_internal_link_opt_note(touchpoints[link_touchpoint_id].get("items") or []))
    if "optimizations" in touchpoints:
        # Free-text note (a legacy import, or record_page_from_text's own
        # notes: field) — unlike the structured touchpoints above, there's
        # no touchpoint_id here to map to a testing-catalog auto_check via
        # page_optimization_names. Folded into opt_note anyway:
        # checks_for_row's own _extract_inline_headings/_extract_internal_links
        # already parse "<H#> text"/"internal link to <url>" mentions out of
        # free text (built for exactly this, from the old Sheets opt_note
        # column) and dispatch the matching check unconditionally when
        # found — so headings/links actually named in the note still get
        # verified against the live site, even though anything else
        # mentioned there (e.g. "added schema markup") isn't checkable
        # without a structured touchpoint to hang a check off of.
        for item in touchpoints["optimizations"].get("items") or []:
            note = item.get("note", "")
            if note:
                opt_note_parts.append(note)

    redirect_item = _first_item(touchpoints, "url_changes_redirection")

    return {
        "url": page["url"],
        "keyword": keyword_target.get("keyword", ""),
        "geo_city": geo_city,
        "geo_state": geo_state,
        "new_title": _first_item(touchpoints, "title_tag").get("new_value", ""),
        "new_meta": _first_item(touchpoints, "meta_description").get("new_value", ""),
        "new_h1": _first_item(touchpoints, "h1_tag").get("new_value", ""),
        "redirection": redirect_item.get("old_value") or redirect_item.get("old_url", ""),
        "opt_note": "\n\n".join(part for part in opt_note_parts if part),
    }


def session_to_rows(session: dict) -> list[dict[str, Any]]:
    return [page_to_row(page) for page in session.get("pages", [])]


async def fetch_plan_session(client: str, month: str, workbook_mcp_url: str,
                              auth_headers: dict | None) -> dict:
    """Call seo-workbook-mcp's find_session(client, month) over Streamable
    HTTP (its transport — different from seo-testing-mcp's SSE, hence the
    separate client here rather than reusing check_orchestrator's sse_client
    helpers). Raises RuntimeError if no session exists for that client/month,
    or on any other tool error."""
    async with streamablehttp_client(workbook_mcp_url, headers=auth_headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("find_session", {"client": client, "month": month})
            if result.isError:
                detail = result.content[0].text if result.content and hasattr(result.content[0], "text") else str(result.content)
                raise RuntimeError(detail)
            if result.structuredContent is not None:
                return result.structuredContent
            texts = [c.text for c in result.content if hasattr(c, "text")]
            if len(texts) == 1:
                return json.loads(texts[0])
            raise RuntimeError(f"Unexpected find_session response shape: {result!r}")
