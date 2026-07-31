from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .adk_agent import mcp_audience
from .dialog_cards import REQUIRED_FIELDS, dialog_error, dialog_ok


def _build_capture_text(values: dict[str, str]) -> str:
    """Assemble a record_page_from_text-compatible labeled-text block from
    the dialog's form values (see page_capture.parse_page_capture) — reuses
    that tool's own parsing/validation rather than duplicating it here, so
    the dialog and the conversational labeled-text workflow stay two front
    ends over the exact same logic. Only url is guaranteed present; every
    other line is included only if the consultant filled that field in.
    """
    lines = [f"url: {values.get('url', '').strip()}"]

    def _optional(label: str, key: str) -> None:
        value = values.get(key, "").strip()
        if value:
            lines.append(f"{label}: {value}")

    def _old_new(label: str, old_key: str, new_key: str) -> None:
        new_value = values.get(new_key, "").strip()
        if not new_value:
            return
        old_value = values.get(old_key, "").strip()
        lines.append(f"{label}: {old_value} -> {new_value}" if old_value else f"{label}: {new_value}")

    _optional("keyword", "keyword")
    _optional("geo", "geo")
    _old_new("title", "title_old", "title_new")
    _old_new("meta", "meta_old", "meta_new")
    _optional("cta", "cta")
    _old_new("h1", "h1_old", "h1_new")

    # headings/notes preserve internal line breaks (only the block's own
    # leading/trailing whitespace is trimmed) — parse_page_capture reads
    # each field's first line off the "label: " line itself and the rest
    # as subsequent raw lines, same as a specialist typing this by hand.
    headings = values.get("headings", "").strip()
    if headings:
        lines.append(f"headings: {headings}")

    notes = values.get("notes", "").strip()
    if notes:
        # Must stay last: parse_page_capture treats everything from
        # "notes:" to the end of the text as its value.
        lines.append(f"notes: {notes}")

    return "\n".join(lines)


def _extract_result(result: Any) -> Any:
    if result.structuredContent is not None:
        return result.structuredContent
    texts = [c.text for c in result.content if hasattr(c, "text")]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return None


def _error_text(result: Any) -> str:
    if result.content and hasattr(result.content[0], "text"):
        return result.content[0].text
    return str(result.content)


async def _fetch_mcp_auth_headers(mcp_server_url: str) -> dict[str, str] | None:
    """Same ID-token-per-request mechanism as the ADK toolset's
    header_provider (see adk_agent.py) — mcp-server runs
    --no-allow-unauthenticated in production, and this call bypasses the
    ADK toolset entirely (see module docstring), so it needs its own
    token."""
    import google.auth.transport.requests
    import google.oauth2.id_token

    audience = mcp_audience(mcp_server_url)

    def _fetch_token() -> str:
        request = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(request, audience)

    try:
        token = await asyncio.to_thread(_fetch_token)
    except Exception:
        return None
    return {"Authorization": f"Bearer {token}"}


async def handle_dialog_submission(values: dict[str, str], *, mcp_server_url: str) -> dict[str, Any]:
    """Process a submitted page-update dialog: find or start the
    client/month session, then record the page through
    record_page_from_text — deterministically, with no LLM in this path at
    all (mirrors seo-testing-agent's review_plan_against_live_site calling
    its MCP server directly rather than through the agent's tool-calling
    loop). Returns a Cards v2 dialog action-status response, ready to
    return as-is from the /chat webhook.
    """
    missing = [name for name in REQUIRED_FIELDS if not values.get(name, "").strip()]
    if missing:
        return dialog_error(f"Missing required field(s): {', '.join(missing)}")

    client = values["client"].strip()
    month = values["month"].strip()
    url = values["url"].strip()
    text = _build_capture_text(values)
    headers = await _fetch_mcp_auth_headers(mcp_server_url)

    try:
        async with streamablehttp_client(mcp_server_url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                find_result = await session.call_tool("find_session", {"client": client, "month": month})
                if find_result.isError:
                    start_result = await session.call_tool("start_session", {"client": client, "month": month})
                    if start_result.isError:
                        return dialog_error(f"Couldn't start a session: {_error_text(start_result)}")
                    session_id = _extract_result(start_result)["session_id"]
                else:
                    session_id = _extract_result(find_result)["session_id"]

                record_result = await session.call_tool(
                    "record_page_from_text", {"session_id": session_id, "text": text}
                )
                if record_result.isError:
                    return dialog_error(f"Couldn't record the update: {_error_text(record_result)}")

                page = _extract_result(record_result)
    except Exception as exc:
        return dialog_error(f"Couldn't reach the workbook service: {exc}")

    failed = [
        tp.get("touchpoint_id") for tp in page.get("touchpoints", [])
        if not (tp.get("validation") or {}).get("passed", True)
    ]
    if failed:
        return dialog_ok(f"Recorded {url} for {client} ({month}) — but needs attention: {', '.join(failed)}")
    return dialog_ok(f"Recorded {url} for {client} ({month}).")
