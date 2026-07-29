from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams

from . import check_orchestrator
from .config import settings
from .plan_session_source import fetch_plan_session, session_to_rows

SYSTEM_INSTRUCTION = """\
You are an autonomous web content QA agent for VDS. You run checks based on the
VDS optimization catalog and produce structured QA reports.

## Mode A — Single URL
1. Call list_rule_categories to show the full optimization list. Ask the user
   which optimizations were completed this period.
2. Call get_rules() for the selected categories to get auto_checks and guided_questions.
3. Run each tool in auto_checks against the URL.
4. Use guided_questions as manual_checklist in the report.
5. Call generate_report.

## Mode B — Plan review against the live site (primary workflow)
The "what was planned" side of this comes from the same structured plan
seo-workbook-agent records — not a Google Sheet or an uploaded file. If the
client and month aren't both already clear from the conversation, ask for
them.

Once you have both, call review_plan_against_live_site(client, month) — a
single, direct function call. It fetches the recorded plan, runs every check
against the live site, and generates the report itself; there is nothing
left for you to compute or reproduce. Just relay what it returns (it already
includes the report link and a pass/fail summary) — don't restate the
checks yourself, and don't call generate_report separately for this path.

If it reports no plan found for that client/month, say so plainly rather
than guessing at one.

## No client/month given
Ask: "Which client and month should I review — checking their recorded plan
against what's actually live?"

## generate_report (Mode A only)
Call it directly as a real function call — its "results" argument is a large
nested object, but it must still be one direct function call, exactly like
any other tool call. Never wrap it (or any tool) in print(...), never write
it as default_api.generate_report(...) Python syntax, and never describe it
in a code block — that is not a real call and will be rejected as malformed.
Call with:
{
  "month": "...", "client": "...",
  "urls": [{
    "url": "...", "verdict": "PASS|FAIL|PARTIAL", "opt_note": "...",
    "checks": [{"label": "...", "status": "pass|fail|warn", "detail": "..."}],
    "manual_checklist": ["guided question 1", "..."],
    "key_issues": "...", "recommended_fixes": "..."
  }]
}
Then tell the user the returned file path.

## Rules
- Call tools ONE AT A TIME using real function calls — never write Python,
  pseudocode, or any code block (e.g. a for-loop over rows, or wrapping a
  call in print(...)) to describe what you would do instead of actually
  calling the function. Each tool call must be its own real, direct function
  call — waiting for its actual result before making the next one. Writing
  code instead of calling a function fails with a malformed-function-call
  error and produces no result.
- Narrate progress ("Running checks for <url>…").
- Only fail counts toward FAIL verdict — warn/info are advisory.
- Do not pause mid-batch to ask questions.
"""


def _fetch_id_token_headers(audience: str) -> dict | None:
    """In production every MCP server here is deployed with
    --no-allow-unauthenticated, so calls need a Google-signed ID token scoped
    to that service as audience. Cloud Run provides this via the metadata
    server for the agent's attached service account — no key material
    needed. Locally these servers are unauthenticated, so no headers are
    needed there.
    """
    if settings.environment != "production":
        return None

    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token

    token = fetch_id_token(Request(), audience)
    return {"Authorization": f"Bearer {token}"}


def _mcp_auth_headers() -> dict | None:
    return _fetch_id_token_headers(settings.mcp_server_url)


def _workbook_mcp_audience() -> str:
    """Cloud Run's IAM check validates an ID token's audience against the
    *service's* base URL, not the sub-path — strip the /mcp suffix
    FastMCP's streamable-http transport adds so the audience actually
    matches what seo-workbook-mcp's ingress expects (mirrors
    seo_workbook_agent.adk_agent.mcp_audience)."""
    url = settings.workbook_mcp_url
    return url[: -len("/mcp")] if url.endswith("/mcp") else url


def _workbook_mcp_auth_headers() -> dict | None:
    return _fetch_id_token_headers(_workbook_mcp_audience())


async def review_plan_against_live_site(client: str, month: str) -> str:
    """Review a client's recorded SEO plan for one month against what's
    actually live on the site, and produce a QA report.

    This is Mode B's entire batch pipeline as a single tool call: fetches
    the plan seo-workbook-agent recorded for this client/month (not a Google
    Sheet or uploaded file), runs every check directly (no further LLM
    involvement — the same MALFORMED_FUNCTION_CALL-avoidance reasoning as
    generate_report elsewhere in this module applies here too, just earlier
    in the pipeline), and generates the report.

    Args:
        client: Client name, exactly as it was recorded (e.g. "Sixth Rhino").
        month: "YYYY-MM", e.g. "2026-06".

    Returns:
        A short plain-text summary plus the report link, ready to relay
        as-is — or a plain-text explanation if no plan was found.
    """
    try:
        session = await fetch_plan_session(client, month, settings.workbook_mcp_url, _workbook_mcp_auth_headers())
    except Exception as exc:
        return f"Couldn't find a recorded plan for {client}, {month}: {exc}"

    rows = session_to_rows(session)
    if not rows:
        return f"Found a plan for {client}, {month}, but it has no pages recorded yet — nothing to check."

    try:
        url_results, brand_guide_notes = await check_orchestrator.run_batch(
            rows, settings.mcp_server_url, _mcp_auth_headers()
        )
    except Exception as exc:
        return f"Error running checks for {client}, {month}: {exc}"

    try:
        report_url = await check_orchestrator.submit_report(
            client, month, url_results, settings.mcp_server_url, _mcp_auth_headers(), brand_guide_notes,
        )
    except Exception as exc:
        return f"Checks ran, but report generation failed: {exc}"

    fail_count = sum(1 for r in url_results if r.get("verdict") == "FAIL")
    pass_count = len(url_results) - fail_count
    summary = (f"All {pass_count} page(s) passed." if not fail_count
               else f"{pass_count} passed, {fail_count} need attention.")
    return f"QA report for {client}, {month} ready: {report_url}\n\n{summary}"


def create_agent() -> LlmAgent:
    return LlmAgent(
        name="web_content_reviewer",
        model=settings.agent_model,
        instruction=SYSTEM_INSTRUCTION,
        tools=[
            McpToolset(
                connection_params=SseConnectionParams(
                    url=f"{settings.mcp_server_url}/sse",
                    headers=_mcp_auth_headers(),
                )
            ),
            review_plan_against_live_site,
        ],
    )
