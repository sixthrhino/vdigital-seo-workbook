"""Runs Mode B checks directly against mcp-server via the MCP client protocol,
bypassing Gemini's tool-calling loop entirely for check execution.

Previously every single check (~40-50 per a 7-row batch) went through its own
sequential LLM round-trip (each costing 1-30+ seconds — see
[[feature-workbook-upload]] in memory for the MALFORMED_FUNCTION_CALL history).
This does the same work as a bounded-concurrency set of direct MCP calls,
typically finishing in seconds instead of minutes.

The LLM isn't in the loop at all anymore, not even at the end: each row's
key_issues/recommended_fixes are generated page-by-page during _run_row (see
content.py::generate_recommendations — a small, page-specific, non-function-
calling Gemini call, skipped entirely for pages with nothing wrong), and
submit_report() calls generate_report directly via MCP once results are
assembled. Routing that final call through Gemini's function-calling used to
require the model to faithfully reproduce the whole batch's results as
call arguments — the root cause of a real MALFORMED_FUNCTION_CALL failure on
a large client batch. Structured JSON instead of an LLM-authored blob removes
that failure mode entirely rather than just shrinking it.

This mirrors the existing "keep dispatch logic in code, not LLM reasoning"
principle already used for resolve_checks_for_opt_note itself — it just
extends that to the checks each opt_note resolves to.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

# check tool name -> (kwarg name, row field) for tools that take an
# "expected" value from the workbook row, beyond the bare url=... every
# check gets. Mirrors agent.py's SYSTEM_INSTRUCTION Mode B steps c-g.
_EXPECTED_KWARG: dict[str, tuple[str, str]] = {
    "seo_check_title": ("expected", "new_title"),
    "seo_check_meta_description": ("expected", "new_meta"),
    "seo_check_h1": ("expected", "new_h1"),
}

# A line only counts as a literal heading spec if it STARTS with the <H#>
# marker — a line that merely mentions a level in passing ("Make headers
# below <H3> tags") isn't itself a heading to verify.
_INLINE_HEADING_LINE_RE = re.compile(r"^<h[1-6]>\s*\S", re.I)

# Real-world phrasing: one instruction line naming the *target* level,
# followed by the plain (unmarked) heading text itself, e.g.
#   Make the <H4> headers below into <H3> headers
#   Common Career Paths
#   Starting out, you should seek employment as an electrician's apprentice...
# "to"/"into" both accepted; the "headers" word after either <H#> is optional
# so slight rewording ("into <H3>") still matches.
_HEADING_CHANGE_RE = re.compile(
    r"<h[1-6]>(?:\s*headers?\b)?.*?\b(?:in)?to\s*<h([1-6])>(?:\s*headers?\b)?",
    re.I,
)
_INTERNAL_LINK_RE = re.compile(r"internal\s+link.*?(https?://\S+)", re.I)
_INTERNAL_LINK_LINE_RE = re.compile(r"^internal\s+link", re.I)

# Real-world workbooks sometimes put the literal phrase "Multiple Locations"
# (or "Multi-location(s)") in the Target Geo column instead of a real city —
# for a page that legitimately covers many service areas, not one specific
# city/radius. Treated as "no single geo target for this row" rather than
# handed to geo_check_accuracy as a city name, which would otherwise fail to
# find it in the cities database and surface a bogus "verify spelling" warn.
_MULTI_LOCATION_RE = re.compile(r"\bmulti(?:ple)?[\s\-]*location", re.I)


def _extract_inline_headings(opt_note: str) -> str:
    """Pull the heading text that needs verifying out of opt_note free text
    — workbooks spell out new/changed headings inline in the opt_note
    itself (as opposed to a separate New H1/New Headers column), two ways:

    1. Literal per-line markers, level given explicitly on each line:
        <H4> Common Career Paths
        <H4> Starting out, you should seek employment...

    2. One instruction line naming the target level, followed by the
       plain heading text (no markup) itself, one per line, ending at a
       blank line or the next "Internal link" mention — the actual
       phrasing real client workbooks use:
        Make the <H4> headers below into <H3> headers
        Common Career Paths
        Starting out, you should seek employment...

    Both yield the same "<H#> text" format seo_check_headings already
    accepts as expected_headings.
    """
    lines = opt_note.splitlines()

    literal = [line.strip() for line in lines if _INLINE_HEADING_LINE_RE.match(line.strip())]
    if literal:
        return "\n".join(literal)

    target_level: str | None = None
    out: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if target_level is None:
            m = _HEADING_CHANGE_RE.search(line)
            if m:
                target_level = m.group(1).upper()
            continue
        if not line or _INTERNAL_LINK_LINE_RE.match(line):
            break
        out.append(f"<H{target_level}> {line}")
    return "\n".join(out)


def _extract_internal_links(opt_note: str) -> list[str]:
    """Pull explicit "Internal link (here) to <URL>" mentions out of
    opt_note free text — the real-world phrasing client workbooks actually
    use, as opposed to a structured Anchor Text:/Link: column pair."""
    return [m.group(1).rstrip(".,;:)") for m in _INTERNAL_LINK_RE.finditer(opt_note)]

# Cap concurrent in-flight MCP tool calls so a big batch doesn't fire 50+
# simultaneous outbound fetches at the client's website (or overload
# mcp-server's single 512Mi instance) all at once.
_MAX_CONCURRENT_CALLS = 8


def _extract_result(result: Any) -> Any:
    """Extract a successful tool call's return value from a CallToolResult.

    Prefers structuredContent. Otherwise falls back to the content blocks —
    usually one block holding the whole return value JSON-encoded, but some
    list-returning tools (seen: workbook_list_months, a bare list[str]) come
    back as one raw-text block per item instead of a single JSON blob. Each
    block is parsed as JSON individually, falling back to its raw text if it
    isn't valid JSON, so both shapes work.
    """
    if result.structuredContent is not None:
        return result.structuredContent

    texts = [c.text for c in result.content if hasattr(c, "text")]
    if not texts:
        return []

    def _parse(t: str) -> Any:
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            return t

    if len(texts) == 1:
        return _parse(texts[0])
    return [_parse(t) for t in texts]


async def _call_tool(session: ClientSession, semaphore: asyncio.Semaphore, name: str, **kwargs) -> Any:
    async with semaphore:
        result = await session.call_tool(name, kwargs)

    if result.isError:
        detail = result.content[0].text if result.content and hasattr(result.content[0], "text") else str(result.content)
        return [{"label": name, "status": "fail", "detail": f"Tool call failed: {detail}"}]

    return _extract_result(result)


def checks_for_row(row: dict, auto_checks: list[str], brand_guide: dict | None = None) -> list[tuple[str, dict]]:
    """Return [(tool_name, kwargs), ...] to call for one workbook row.

    Deterministic port of the conditional-extras logic previously described
    only in prose in agent.py's Mode B system instruction (steps c-g).
    """
    calls: list[tuple[str, dict]] = []
    seen: set[str] = set(auto_checks)

    for name in auto_checks:
        kwargs: dict[str, Any] = {"url": row["url"]}
        if name in _EXPECTED_KWARG:
            field, row_key = _EXPECTED_KWARG[name]
            if row.get(row_key):
                kwargs[field] = row[row_key]
        calls.append((name, kwargs))

    opt_note = str(row.get("opt_note", ""))

    # Heading text spelled out inline in the opt_note (rather than a
    # separate New H1/New Headers column) is more specific than a generic
    # hierarchy check — replaces any auto_checks-driven seo_check_headings
    # call rather than duplicating it, so the report doesn't show the same
    # check twice (once generic, once with the real expected text).
    inline_headings = _extract_inline_headings(opt_note)
    if inline_headings:
        calls = [(n, kw) for n, kw in calls if n != "seo_check_headings"]
        headings_kwargs = {"url": row["url"], "expected_headings": inline_headings}
        # Only meaningful when index-aligned with inline_headings — see
        # plan_session_source._heading_old_opt_note, the one thing that
        # currently populates this row field. A length mismatch (e.g. some
        # of inline_headings came from a legacy free-text note this field
        # doesn't cover) makes check_heading_hierarchy ignore old_headings
        # entirely rather than mispair them, so passing it through even
        # then is safe.
        old_headings = str(row.get("old_headings", "")).strip()
        if old_headings:
            headings_kwargs["old_headings"] = old_headings
        calls.append(("seo_check_headings", headings_kwargs))

    # "Internal link (here) to <URL>" mentions in the opt_note are a
    # concrete, checkable claim — verify each one is actually linked from
    # this page rather than trusting the note that it was done.
    internal_links = _extract_internal_links(opt_note)
    if internal_links:
        calls.append(("elements_check_expected_links", {
            "url": row["url"], "expected_links": internal_links,
        }))

    keyword = str(row.get("keyword", "")).strip()
    if keyword and keyword.lower() != "n/a":
        calls.append(("seo_check_keywords", {"url": row["url"], "primary_keyword": keyword}))

    # Excluded cities ("CLIENT CANNOT SERVICE" areas, parsed from the brand
    # guide) are worth checking even on rows with no geo_city of their own —
    # geo_check_accuracy's excluded-cities scan runs unconditionally
    # regardless of mode, so this only needs *a* reason to call the tool at
    # all, not specifically a row-level geo target.
    excluded_cities = (brand_guide or {}).get("excluded_cities") or []
    # A Geo Targeting list with more than one city is a genuine multi-market
    # allowlist (a single-city guide is redundant with the row's own
    # geo_city/geo_state, so isn't treated as one). Used as allowlist_cities
    # two ways: extra in-area cities to whitelist in city-distance mode, and
    # as the sole target-market allowlist for rows with no geo_city of their
    # own — nationwide/multi-market clients, which otherwise got no geo
    # check dispatched at all.
    geo_targets = (brand_guide or {}).get("geo") or []
    allowlist_cities = geo_targets if len(geo_targets) > 1 else []
    is_multi_location_row = bool(_MULTI_LOCATION_RE.search(str(row.get("geo_city", ""))))
    if not is_multi_location_row and (row.get("geo_city") or excluded_cities or allowlist_cities):
        geo_kwargs: dict[str, Any] = {
            "url": row["url"],
            "geo_city": row.get("geo_city", ""),
            "geo_state": row.get("geo_state", ""),
        }
        if excluded_cities:
            geo_kwargs["excluded_cities"] = excluded_cities
        if allowlist_cities:
            geo_kwargs["allowlist_cities"] = allowlist_cities
        calls.append(("geo_check_accuracy", geo_kwargs))

    if row.get("redirection"):
        # tech_check_redirect verifies the OLD url (the Redirection? column
        # value) redirects cleanly — not the row's current url.
        calls.append(("tech_check_redirect", {"url": row["redirection"]}))

    if "/blog/" in row.get("url", "") and "seo_check_schema" not in seen:
        calls.append(("seo_check_schema", {"url": row["url"]}))

    # Every row's fetch is worth checking for bot-mitigation gating — a
    # stripped-head response silently poisons Title/Meta/H1/Schema/keyword
    # results for this same URL (fetch_parsed's result is cached and reused
    # by all of them), so this isn't conditional on opt_note the way the
    # checks above are.
    calls.append(("tech_check_fetch_reliability", {"url": row["url"]}))

    # Same rationale as tech_check_fetch_reliability above — branding tokens,
    # CTA URLs/phone, and negative words are worth checking on every page,
    # not just rows whose opt_note happens to mention brand work. Only added
    # when a Brand Guide was actually found (see get_workbook_brand_guide),
    # so workbooks without one don't pay for a pointless empty-guide check.
    # brand_guide is already parsed (parse_brand_guide's dict shape always
    # has every key present) — "raw" is blank only when nothing was found.
    if brand_guide and brand_guide.get("raw", "").strip():
        calls.append(("content_check_brand_guide", {
            "url": row["url"], "brand_guide": brand_guide,
        }))

    return calls


async def _run_row(session: ClientSession, semaphore: asyncio.Semaphore, row: dict,
                    brand_guide: dict | None = None) -> dict:
    resolved = await _call_tool(session, semaphore, "resolve_checks_for_opt_note", opt_note=row.get("opt_note", ""))
    auto_checks = resolved.get("auto_checks", []) if isinstance(resolved, dict) else []
    guided_questions = resolved.get("guided_questions", []) if isinstance(resolved, dict) else []

    calls = checks_for_row(row, auto_checks, brand_guide)
    call_results = await asyncio.gather(
        *[_call_tool(session, semaphore, name, **kwargs) for name, kwargs in calls]
    )

    checks: list[dict] = []
    for result in call_results:
        if isinstance(result, list):
            checks.extend(result)
        elif isinstance(result, dict):
            checks.append(result)

    verdict = "FAIL" if any(c.get("status") == "fail" for c in checks) else "PASS"

    # Page-specific and small by design (only this row's checks, not the
    # batch) — see content.py::generate_recommendations. Computing this here
    # means the batch's results are already generate_report-ready by the
    # time run_batch returns, with no LLM step left for submit_report to
    # depend on.
    recommendations = await _call_tool(session, semaphore, "content_generate_recommendations",
                                        url=row["url"], checks=checks)
    if not isinstance(recommendations, dict):
        recommendations = {}

    return {
        "url": row["url"],
        "opt_note": row.get("opt_note", ""),
        "verdict": verdict,
        "checks": checks,
        "manual_checklist": guided_questions,
        "key_issues": recommendations.get("key_issues", ""),
        "recommended_fixes": recommendations.get("recommended_fixes", ""),
    }


# ---------------------------------------------------------------------------
# Batch-level (cross-page) checks — only make sense once every row's result
# is known, so unlike everything above these run once after all rows finish
# rather than per-row.
# ---------------------------------------------------------------------------

_DUPLICATE_TITLE_LABEL = "Duplicate Title Tag"
_DUPLICATE_META_LABEL = "Duplicate Meta Description"
_KEYWORD_CANNIBALIZATION_LABEL = "Keyword Cannibalization"


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _row_keyword(row: dict) -> str:
    keyword = str(row.get("keyword", "")).strip()
    return keyword if keyword and keyword.lower() != "n/a" else ""


def _group_duplicates(items: list[tuple[int, str]]) -> dict[str, list[int]]:
    """items: [(row_index, normalized_value), ...]. Returns {value: [row_index,
    ...]} for every non-empty value that appears against 2+ row indices."""
    groups: dict[str, list[int]] = {}
    for idx, value in items:
        if not value:
            continue
        groups.setdefault(value, []).append(idx)
    return {value: idxs for value, idxs in groups.items() if len(idxs) > 1}


def _flag_duplicates(rows: list[dict], results: list[dict], groups: dict[str, list[int]],
                      label: str, detail_fn) -> None:
    for indices in groups.values():
        for i in indices:
            others = ", ".join(rows[j]["url"] for j in indices if j != i)
            results[i]["checks"].append({"label": label, "status": "fail", "detail": detail_fn(rows[i], others)})
            results[i]["verdict"] = "FAIL"


async def _apply_batch_checks(session: ClientSession, semaphore: asyncio.Semaphore,
                               rows: list[dict], results: list[dict]) -> None:
    """Cross-page checks: duplicate title/meta tags and keyword
    cannibalization across the whole batch. Mutates results in place —
    appends a fail check (and flips verdict to FAIL) on every row involved
    in a duplicate/collision.
    """
    if len(rows) < 2:
        return

    title_meta = await asyncio.gather(
        *[_call_tool(session, semaphore, "seo_get_title_meta", url=row["url"]) for row in rows]
    )

    title_groups = _group_duplicates([
        (i, _normalize_text(tm.get("title"))) for i, tm in enumerate(title_meta) if isinstance(tm, dict)
    ])
    meta_groups = _group_duplicates([
        (i, _normalize_text(tm.get("meta_description"))) for i, tm in enumerate(title_meta) if isinstance(tm, dict)
    ])
    keyword_groups = _group_duplicates([
        (i, _normalize_text(_row_keyword(row))) for i, row in enumerate(rows)
    ])

    _flag_duplicates(rows, results, title_groups, _DUPLICATE_TITLE_LABEL,
                      lambda row, others: f"Same title tag as: {others}")
    _flag_duplicates(rows, results, meta_groups, _DUPLICATE_META_LABEL,
                      lambda row, others: f"Same meta description as: {others}")
    _flag_duplicates(rows, results, keyword_groups, _KEYWORD_CANNIBALIZATION_LABEL,
                      lambda row, others: f'Same primary keyword ("{_row_keyword(row)}") targeted by: {others}')


async def _get_brand_guide_notes(session: ClientSession, semaphore: asyncio.Semaphore,
                                  brand_guide: dict | None) -> list[str]:
    """Guide-level reminders (voice/tone, writing rules, imaging) don't vary
    by page — fetched once here rather than checks_for_row/
    content_check_brand_guide repeating them on every single row (which,
    for a guide with a long Imaging/Voice&Tone section, was enough
    duplicate text across a batch to blow up generate_report's
    function-call payload and trigger Gemini's MALFORMED_FUNCTION_CALL on
    real client data). Returned separately rather than attached to any one
    row so the report can render them as their own top-level section
    instead of tucked inside whichever URL happens to be first.
    """
    if not brand_guide or not brand_guide.get("raw", "").strip():
        return []
    notes = await _call_tool(session, semaphore, "content_get_brand_guide_notes", brand_guide=brand_guide)
    return notes if isinstance(notes, list) else []


async def run_batch(rows: list[dict], mcp_url: str, auth_headers: dict | None,
                     brand_guide: dict | None = None) -> tuple[list[dict], list[str]]:
    """Run Mode B checks for every row concurrently (bounded), returning
    (per-URL result dicts, brand guide notes) — the former is the same
    shape generate_report expects for its urls list, minus key_issues/
    recommended_fixes, which the LLM fills in afterward; the latter is the
    guide-level reminders for generate_report's top-level brand_guide_notes."""
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CALLS)
    try:
        async with sse_client(f"{mcp_url}/sse", headers=auth_headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                results = list(await asyncio.gather(
                    *[_run_row(session, semaphore, row, brand_guide) for row in rows]
                ))
                await _apply_batch_checks(session, semaphore, rows, results)
                brand_guide_notes = await _get_brand_guide_notes(session, semaphore, brand_guide)
                return results, brand_guide_notes
    except BaseExceptionGroup as eg:
        raise RuntimeError(_flatten_exception_message(eg)) from eg


# ---------------------------------------------------------------------------
# submit_report — single-call, short-lived MCP session, not part of the
# batch run's own session (main.py's /chat handler and
# agent.py's review_plan_against_live_site call this directly, after
# run_batch's session has already closed).
#
# Deliberately don't go through _call_tool: it converts a tool error into a
# fabricated fail "check" entry (right for the checks list elsewhere in this
# module), which here would silently swallow a real report-generation
# failure instead of surfacing it. This raises instead, so callers' existing
# try/except can show a real error message.
# ---------------------------------------------------------------------------

async def _call_tool_or_raise(session: ClientSession, name: str, **kwargs) -> Any:
    result = await session.call_tool(name, kwargs)
    if result.isError:
        detail = result.content[0].text if result.content and hasattr(result.content[0], "text") else str(result.content)
        raise RuntimeError(detail)
    return _extract_result(result)


def _flatten_exception_message(exc: BaseException) -> str:
    """anyio's TaskGroup (used internally by sse_client/ClientSession) wraps
    any exception raised inside the `async with` blocks below in a
    BaseExceptionGroup, so str(exc) at the call site becomes the useless
    "unhandled errors in a TaskGroup (N sub-exceptions)" instead of the real
    message — unwrap down to the first leaf exception's message instead."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return str(exc)


async def submit_report(client: str, month_year: str, url_results: list[dict], mcp_url: str,
                         auth_headers: dict | None, brand_guide_notes: list[str] | None = None) -> str:
    """Assemble the batch's already-computed results (checks, verdicts, and
    each row's key_issues/recommended_fixes from _run_row) into
    generate_report's expected shape and call it directly via MCP.

    brand_guide_notes (guide-level reminders — voice/tone, writing rules,
    imaging) render as their own top-level report section, not tucked into
    any one URL's card.

    No LLM involved — by the time run_batch returns, there's nothing left
    for one to contribute for the batch path, and going through the agent's
    function-calling loop for this exact call is what caused
    MALFORMED_FUNCTION_CALL on large batches in the first place.
    """
    results = {
        "month": month_year, "client": client, "urls": url_results,
        "brand_guide_notes": brand_guide_notes or [],
    }
    try:
        async with sse_client(f"{mcp_url}/sse", headers=auth_headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await _call_tool_or_raise(session, "generate_report", results=results)
                return result if isinstance(result, str) else str(result)
    except BaseExceptionGroup as eg:
        raise RuntimeError(_flatten_exception_message(eg)) from eg
