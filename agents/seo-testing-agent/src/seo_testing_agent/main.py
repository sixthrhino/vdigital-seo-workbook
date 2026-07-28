import logging
import re
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

import google.auth
import google.auth.transport.requests as google_requests
from google.oauth2 import id_token as google_id_token

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from . import check_orchestrator
from . import workbook_upload as wu
from .agent import create_agent, _mcp_auth_headers
from .config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_NAME = "web_content_reviewer"
AGENT_USER_ID = "autonomous-agent"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ReviewRequest(BaseModel):
    url: HttpUrl
    rules: list[str]
    session_id: str | None = None


class ReviewResponse(BaseModel):
    session_id: str
    result: str


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def _build_session_service():
    # VertexAiSessionService needs the agent registered as a Vertex AI
    # Reasoning Engine resource (vertexai.agent_engines.create(...)), which
    # isn't provisioned yet. Using InMemorySessionService everywhere for now
    # means session state doesn't persist across cold starts or when Cloud
    # Run scales past one instance — acceptable for one-shot QA runs.
    logger.info("Using InMemorySessionService")
    return InMemorySessionService()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.session_service = _build_session_service()
    yield


app = FastAPI(title="Web Content Reviewer Agent", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}


@app.post("/run", response_model=ReviewResponse)
async def run_review(request: ReviewRequest, x_api_key: str | None = Header(default=None)):
    if settings.run_api_key and x_api_key != settings.run_api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Api-Key")

    session_service = app.state.session_service
    session_id = request.session_id or str(uuid.uuid4())
    url = str(request.url)

    logger.info("Starting review | session=%s url=%s rules=%d",
                session_id, url, len(request.rules))

    agent = create_agent()
    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
        auto_create_session=True,
    )

    rules_text = "\n".join(f"- {rule}" for rule in request.rules)
    prompt = (
        f"Please review the following URL against the rules below.\n\n"
        f"URL: {url}\n\n"
        f"Rules:\n{rules_text}"
    )

    message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=prompt)],
    )

    final_response = ""
    try:
        async for event in runner.run_async(
            user_id=AGENT_USER_ID,
            session_id=session_id,
            new_message=message,
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        final_response += part.text
    except Exception as exc:
        logger.exception("Agent run failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("Review complete | session=%s result_chars=%d",
                session_id, len(final_response))

    return ReviewResponse(session_id=session_id, result=final_response)


# ---------------------------------------------------------------------------
# Google Chat webhook
# ---------------------------------------------------------------------------

_CHAT_API = "https://chat.googleapis.com/v1"
_CHAT_SCOPES = ["https://www.googleapis.com/auth/chat.bot"]


async def _post_to_chat(space_name: str, thread_name: str, text: str) -> None:
    creds, _ = google.auth.default(scopes=_CHAT_SCOPES)
    creds.refresh(google_requests.Request())

    payload: dict = {"text": text}
    params: dict = {}
    if thread_name and thread_name != space_name:
        payload["thread"] = {"name": thread_name}
        # messageReplyOption is a query param on spaces.messages.create, not a
        # field on the Message body — Chat rejects it with 400 INVALID_ARGUMENT
        # ("Unknown name ... Cannot find field") if it's put in the JSON body.
        params["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{_CHAT_API}/{space_name}/messages",
            json=payload,
            params=params,
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=15,
        )
        if r.status_code >= 400:
            logger.error("Chat API rejected the reply (%s): %s", r.status_code, r.text)
        r.raise_for_status()


async def _download_chat_attachment(resource_name: str) -> bytes:
    """Download an UPLOADED_CONTENT Chat attachment's raw bytes.

    resource_name is the opaque token from
    message.attachment[i].attachmentDataRef.resourceName — it must be used as
    media/{resourceName}, not appended to the API root directly (a bare
    {_CHAT_API}/{resource_name} 404s).
    """
    creds, _ = google.auth.default(scopes=_CHAT_SCOPES)
    creds.refresh(google_requests.Request())

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{_CHAT_API}/media/{resource_name}",
            params={"alt": "media"},
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.content


# Rows parsed from an uploaded workbook, cached per Chat *space* (not
# thread) between the "which month?" question and the user's plain-text
# reply naming one. Confirmed live: a plain top-level reply (not an
# explicit "reply in thread") gets a brand-new thread ID from Chat, unlike
# what its name suggests — Chat's threaded-space model gives every new
# top-level message its own thread. Keying this by thread meant the
# follow-up almost always landed in a different thread than the question,
# so the lookup missed and fell through to a confused generic response.
# Space-scoped assumes at most one pending question per space at a time,
# which matches how this bot is actually used. Bounded LRU (not unbounded)
# since Cloud Run instances are long-lived across many spaces over time —
# same reasoning as tools/fetcher.py's URL cache.
_PENDING_UPLOADS_MAXSIZE = 50
# Value also carries brand_guide (structured dict, parsed from the uploaded
# file's Brand Guide tab if any) and client_name (derived from the
# attachment's filename) alongside raw_rows — the original file bytes
# aren't retained, so this is the only chance to capture them before the
# "which month?" round-trip discards everything but the parsed rows.
_pending_uploads: "OrderedDict[str, tuple[list[dict], dict, str]]" = OrderedDict()

# Same "which month?" round-trip, but for a Google Sheets link shared
# directly in Chat (a Drive file reference) instead of an uploaded .xlsx —
# caches the spreadsheet ID rather than parsed rows, since the rows still
# need to be pulled from the Sheets API once the month is known.
_pending_sheets: "OrderedDict[str, str]" = OrderedDict()


def _cache_pending_upload(space_name: str, raw_rows: list[dict], brand_guide: dict | None = None,
                           client_name: str = "") -> None:
    _pending_uploads[space_name] = (raw_rows, brand_guide or {}, client_name)
    _pending_uploads.move_to_end(space_name)
    if len(_pending_uploads) > _PENDING_UPLOADS_MAXSIZE:
        _pending_uploads.popitem(last=False)


def _cache_pending_sheet(space_name: str, spreadsheet_id: str) -> None:
    _pending_sheets[space_name] = spreadsheet_id
    _pending_sheets.move_to_end(space_name)
    if len(_pending_sheets) > _PENDING_UPLOADS_MAXSIZE:
        _pending_sheets.popitem(last=False)


# Google Chat retries a webhook delivery if it doesn't get an ack quickly
# enough — confirmed live: a slow-to-process message (cold start + two
# sequential Sheets round-trips resolving a pending-sheet month) got
# delivered twice, ~9s apart. The first delivery resolved and popped the
# pending-sheet/upload state correctly; the second arrived after that state
# was already gone, fell through to the generic LLM path with no context,
# and posted a confusing "do you have a workbook?" reply to the same
# thread. message.name (format "spaces/.../messages/...") is stable across
# retried deliveries of the same event, unlike anything derived from the
# message content — dedupe on that instead of trying to make every
# downstream branch idempotent individually.
_SEEN_MESSAGES_MAXSIZE = 200
_seen_message_names: "OrderedDict[str, None]" = OrderedDict()


def _already_processed(message_name: str) -> bool:
    if not message_name:
        return False  # no name to dedupe on — don't block a message over it
    if message_name in _seen_message_names:
        _seen_message_names.move_to_end(message_name)
        return True
    _seen_message_names[message_name] = None
    if len(_seen_message_names) > _SEEN_MESSAGES_MAXSIZE:
        _seen_message_names.popitem(last=False)
    return False


def _client_name_from_title(title: str) -> str:
    """Best-effort client name from a workbook title or uploaded filename,
    e.g. "IEC Rocky Mountain (main) | Organic SEO Workbook" -> "IEC Rocky
    Mountain", "Sonoran Spine  _ [Main] Organic SEO Workbook.xlsx" ->
    "Sonoran Spine". Falls back to the trimmed input unchanged if it
    doesn't match the usual "<client> | ... Workbook" naming pattern —
    good enough for generate_report's client field, which is just a label.
    """
    name = re.sub(r"\.xlsx?$", "", title, flags=re.I).strip()
    name = name.split("|", 1)[0]
    name = re.sub(r"[\[(]main[\])]", "", name, flags=re.I)
    name = re.sub(r"organic seo workbook\s*$", "", name, flags=re.I)
    name = name.rstrip("_ \t").strip()
    return name or title.strip()


async def _resolve_sheet_client_name(spreadsheet_id: str, fallback_title: str) -> str:
    """The workbook's Client Details tab (Client Business Name) is the
    authoritative source for the report's client label — falls back to the
    filename heuristic only if that tab is missing or unlabeled."""
    try:
        details = await check_orchestrator.get_workbook_client_details(
            spreadsheet_id, settings.mcp_server_url, _mcp_auth_headers()
        )
    except Exception:
        logger.exception("Failed to read Client Details tab for spreadsheet %s", spreadsheet_id)
        details = {}
    return details.get("client") or _client_name_from_title(fallback_title)


def _resolve_upload_client_name(file_bytes: bytes, content_name: str) -> str:
    try:
        details = wu.parse_client_details_tab(file_bytes)
    except Exception:
        logger.exception("Failed to parse Client Details tab from uploaded workbook")
        details = {}
    return details.get("client") or _client_name_from_title(content_name)


_REPLY_LIMIT = 4000
_REPORT_URL_RE = re.compile(r"https://storage\.googleapis\.com/\S+")


def _truncate_reply(reply: str, limit: int = _REPLY_LIMIT) -> str:
    """Truncate a reply to fit Chat's message limit without ever cutting a
    signed report URL in half — a blind reply[:N] slice will eventually land
    inside the (very long, query-string-heavy) signed URL generate_report
    returns, corrupting it into something GCS rejects with
    SignatureDoesNotMatch. If a report URL is present, only the narrative
    text is trimmed; the full URL is always preserved at the end.
    """
    if len(reply) <= limit:
        return reply

    note = "\n\n_(message truncated — see report link for full details)_"
    match = _REPORT_URL_RE.search(reply)
    if not match:
        return reply[: limit - len(note)] + note

    url = match.group(0)
    budget = limit - len(url) - len(note) - 2  # 2 for the joining "\n\n"
    if budget < 0:
        return url
    return reply[:budget] + note + "\n\n" + url


_RUN_AND_REPLY_MAX_ATTEMPTS = 3


async def _run_and_reply(
    space_name: str,
    thread_name: str,
    session_id: str,
    user_text: str,
    session_service,
) -> None:
    parts: list[str] = []
    try:
        runner = Runner(
            agent=create_agent(),
            app_name=APP_NAME,
            session_service=session_service,
            auto_create_session=True,
        )
        message = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=user_text)],
        )

        for attempt in range(1, _RUN_AND_REPLY_MAX_ATTEMPTS + 1):
            # MALFORMED_FUNCTION_CALL (Gemini writing pseudocode instead of a
            # real function call) is a probabilistic generation failure, more
            # likely the bigger the final generate_report payload is (a large
            # Mode B batch). Retrying is the standard mitigation for this —
            # prompt wording alone (see agent.py's SYSTEM_INSTRUCTION) reduces
            # but doesn't eliminate it. Each retry gets its own session id so
            # the model doesn't see its own malformed attempt in the history
            # it's retrying from.
            attempt_session_id = session_id if attempt == 1 else f"{session_id}-retry{attempt}"
            parts = []
            async for event in runner.run_async(
                user_id=space_name,
                session_id=attempt_session_id,
                new_message=message,
            ):
                if not event.is_final_response():
                    continue
                event_parts = event.content.parts if event.content else []
                found_text = False
                for p in event_parts:
                    if hasattr(p, "text") and p.text:
                        parts.append(p.text)
                        found_text = True
                if not found_text:
                    # Diagnostic for the "No response generated." fallback below —
                    # captures *why* a final event had no usable text (safety
                    # block, truncation, a bare function-call with no follow-up
                    # turn, etc.) instead of just silently posting a blank reply.
                    logger.warning(
                        "Final event had no text (attempt %d/%d) | finish_reason=%s "
                        "error_code=%s error_message=%s part_types=%s",
                        attempt, _RUN_AND_REPLY_MAX_ATTEMPTS,
                        getattr(event, "finish_reason", None),
                        getattr(event, "error_code", None),
                        getattr(event, "error_message", None),
                        [type(p).__name__ for p in event_parts],
                    )

            if parts:
                break
            if attempt < _RUN_AND_REPLY_MAX_ATTEMPTS:
                logger.warning("Retrying agent run for session %s (attempt %d produced no text)",
                                session_id, attempt)

        reply = "\n".join(parts) or "No response generated."
        reply = _truncate_reply(reply)
    except Exception as exc:
        logger.exception("Agent run failed for session %s", session_id)
        reply = f"❌ Agent error: {exc}"

    try:
        await _post_to_chat(space_name, thread_name, reply)
    except Exception:
        logger.exception("Failed to post reply to Chat for space %s", space_name)


async def _run_workbook_batch(
    space_name: str,
    thread_name: str,
    session_id: str,
    month_year: str,
    rows: list[dict],
    session_service,
    brand_guide: dict | None = None,
    client_name: str = "",
) -> None:
    """Run all Mode B checks directly via check_orchestrator and post the
    result straight to Chat — no LLM step at all, for either the checks or
    the final report.

    Each row's key_issues/recommended_fixes are already computed
    page-by-page inside run_batch (see gemini_checks.py::generate_recommendations),
    so by the time results come back there's nothing left for an LLM to
    contribute here — routing the final generate_report call through the
    agent's Gemini loop just to reproduce data it didn't write is exactly
    what caused MALFORMED_FUNCTION_CALL on large real batches.
    """
    try:
        url_results, brand_guide_notes = await check_orchestrator.run_batch(
            rows, settings.mcp_server_url, _mcp_auth_headers(), brand_guide
        )
    except Exception as exc:
        logger.exception("Workbook check orchestration failed for session %s", session_id)
        try:
            await _post_to_chat(space_name, thread_name, f"❌ Error running checks: {exc}")
        except Exception:
            logger.exception("Failed to post error reply to Chat for space %s", space_name)
        return

    try:
        report_url = await check_orchestrator.submit_report(
            client_name, month_year, url_results, settings.mcp_server_url, _mcp_auth_headers(),
            brand_guide_notes,
        )
    except Exception as exc:
        logger.exception("Report generation failed for session %s", session_id)
        try:
            await _post_to_chat(space_name, thread_name, f"❌ Error generating report: {exc}")
        except Exception:
            logger.exception("Failed to post error reply to Chat for space %s", space_name)
        return

    fail_count = sum(1 for r in url_results if r.get("verdict") == "FAIL")
    pass_count = len(url_results) - fail_count
    summary = (f"All {pass_count} page(s) passed." if not fail_count
               else f"{pass_count} passed, {fail_count} need attention.")
    reply = _truncate_reply(f"✅ QA report for {month_year} ready: {report_url}\n\n{summary}")

    try:
        await _post_to_chat(space_name, thread_name, reply)
    except Exception:
        logger.exception("Failed to post report reply to Chat for space %s", space_name)


_CHAT_ISSUER = "chat@system.gserviceaccount.com"


def _verify_chat_request(request: Request) -> bool:
    """Verify the incoming request's bearer token is really from Google Chat.

    Chat signs requests with a Google-issued ID token whose audience is the
    numeric GCP project number (settings.chat_audience) and whose issuer is
    the fixed Chat system service account. Skipped when chat_audience is
    unset — keeps local dev usable, since Chat can't reach a local webhook
    anyway, and /chat is otherwise indistinguishable from any other public
    POST endpoint without this.
    """
    if not settings.chat_audience:
        return True

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        logger.warning("Chat request rejected: no Bearer token (headers had: %s)",
                        list(request.headers.keys()))
        return False
    token = auth_header[len("Bearer "):]

    try:
        claims = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=settings.chat_audience
        )
    except Exception as exc:
        # Log the actual claims (unverified) alongside the failure so we can
        # tell "wrong audience" from "wrong issuer" from "bad signature"
        # without guessing — verify_oauth2_token gives no partial info itself.
        import json as _json
        unverified = None
        try:
            payload_b64 = token.split(".")[1]
            padding = "=" * (-len(payload_b64) % 4)
            import base64
            unverified = _json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        except Exception:
            pass
        logger.warning(
            "Chat token verification failed: %s | expected audience=%s | unverified claims=%s",
            exc, settings.chat_audience, unverified,
        )
        return False

    if claims.get("email") != _CHAT_ISSUER or claims.get("email_verified") is not True:
        logger.warning("Chat token verified but issuer mismatch: claims=%s", claims)
        return False
    return True


@app.post("/chat")
async def handle_chat(request: Request, background_tasks: BackgroundTasks):
    """Google Chat webhook endpoint.

    Returns an immediate acknowledgement then runs the agent in a background
    task and posts the result back to the originating Chat thread.
    """
    if not _verify_chat_request(request):
        return JSONResponse({}, status_code=401)

    body = await request.json()
    event_type = body.get("type")

    if event_type == "ADDED_TO_SPACE":
        return JSONResponse({"text": "VDS QA Agent is ready. Send me a workbook to review!"})

    if event_type != "MESSAGE":
        return JSONResponse({})

    message = body.get("message", {})

    # Defense-in-depth against genuine Chat webhook retries (a documented
    # platform behavior when a response is slow) — confirmed via live
    # diagnostic logging that this was NOT the cause of the duplicate-reply
    # bug users actually hit (every message.name observed was unique; the
    # real cause was thread-scoped pending-state lookups, fixed below by
    # switching _pending_sheets/_pending_uploads to space-scoped keys).
    # Left in since it's cheap and still correct protection if Chat ever
    # does retry with a stable message.name.
    if _already_processed(message.get("name", "")):
        logger.info("Ignoring duplicate Chat delivery of message %s", message.get("name"))
        return JSONResponse({})

    user_text = message.get("text", "").strip()
    space_name = body.get("space", {}).get("name", "")
    thread_name = message.get("thread", {}).get("name", "") or space_name
    session_id = thread_name.replace("/", "-")
    attachments = message.get("attachment") or []

    if not space_name or (not user_text and not attachments):
        return JSONResponse({})

    session_service = request.app.state.session_service

    if attachments:
        attachment = attachments[0]
        resource_name = attachment.get("attachmentDataRef", {}).get("resourceName")
        drive_file_id = attachment.get("driveDataRef", {}).get("driveFileId")
        content_name = attachment.get("contentName", "that file")

        if drive_file_id:
            # A Google Sheets link shared/pasted in Chat — Chat represents this
            # as a Drive file reference, not an uploaded file, so it's read
            # directly via the Sheets API (same mechanism as workbook_get_rows)
            # instead of downloading bytes.
            try:
                months = await check_orchestrator.list_workbook_months(
                    drive_file_id, settings.mcp_server_url, _mcp_auth_headers()
                )
            except Exception as exc:
                logger.exception("Failed to read shared workbook %s for space %s", content_name, space_name)
                return JSONResponse({"text": f"Couldn't read \"{content_name}\": {exc}"})

            # Drive-link attachments don't reliably carry a contentName the
            # way uploaded files do (content_name falls back to "that file")
            # — the workbook's own title from Sheets metadata is authoritative
            # and always available once list_workbook_months above succeeded.
            try:
                title = await check_orchestrator.get_workbook_title(
                    drive_file_id, settings.mcp_server_url, _mcp_auth_headers()
                )
            except Exception:
                title = ""
            display_name = title or content_name

            month = wu.find_mentioned_month(user_text, months) if user_text else None

            if not month:
                _cache_pending_sheet(space_name, drive_file_id)
                month_list = "\n".join(f"- {m}" for m in months) or "(no months found in the On-Page sheet)"
                return JSONResponse({"text": f"Got \"{display_name}\". Which month should I review?\n{month_list}"})

            rows = await check_orchestrator.get_workbook_month_rows(
                drive_file_id, month, settings.mcp_server_url, _mcp_auth_headers()
            )
            try:
                brand_guide = await check_orchestrator.get_workbook_brand_guide(
                    drive_file_id, settings.mcp_server_url, _mcp_auth_headers()
                )
            except Exception:
                logger.exception("Failed to read Brand Guide tab for space %s", space_name)
                brand_guide = {}
            client_name = await _resolve_sheet_client_name(drive_file_id, display_name)
            _pending_sheets.pop(space_name, None)
            background_tasks.add_task(
                _run_workbook_batch, space_name, thread_name, session_id, month, rows, session_service,
                brand_guide, client_name,
            )
            return JSONResponse({"text": f"Running checks for {month} ({len(rows)} rows)… I'll post results here when done ⏳"})

        if not resource_name:
            logger.warning("Chat attachment had neither attachmentDataRef nor driveDataRef | keys=%s",
                            list(attachment.keys()))
            return JSONResponse({"text": (
                f"I can't read \"{content_name}\" — it doesn't look like a direct "
                "file upload or a shared Google Sheets link. Please attach the "
                ".xlsx workbook directly, or share the Sheets link."
            )})

        try:
            file_bytes = await _download_chat_attachment(resource_name)
            raw_rows = wu.parse_on_page_rows(file_bytes)
        except Exception as exc:
            logger.exception("Failed to parse uploaded workbook for space %s", space_name)
            return JSONResponse({"text": f"Couldn't read that workbook: {exc}"})

        try:
            brand_guide_text = wu.parse_brand_guide_tab(file_bytes)
            brand_guide = (
                await check_orchestrator.parse_brand_guide_text(
                    brand_guide_text, settings.mcp_server_url, _mcp_auth_headers()
                )
                if brand_guide_text.strip() else {}
            )
        except Exception:
            logger.exception("Failed to parse Brand Guide tab for space %s", space_name)
            brand_guide = {}

        client_name = _resolve_upload_client_name(file_bytes, content_name)
        months = wu.distinct_months(raw_rows)
        month = wu.find_mentioned_month(user_text, months) if user_text else None

        if not month:
            _cache_pending_upload(space_name, raw_rows, brand_guide, client_name)
            month_list = "\n".join(f"- {m}" for m in months) or "(no months found in the On-Page sheet)"
            return JSONResponse({"text": f"Got your workbook. Which month should I review?\n{month_list}"})

        rows = wu.month_rows(raw_rows, month)
        _pending_uploads.pop(space_name, None)
        background_tasks.add_task(
            _run_workbook_batch, space_name, thread_name, session_id, month, rows, session_service,
            brand_guide, client_name,
        )
        return JSONResponse({"text": f"Running checks for {month} ({len(rows)} rows)… I'll post results here when done ⏳"})

    if space_name in _pending_sheets:
        spreadsheet_id = _pending_sheets[space_name]
        try:
            months = await check_orchestrator.list_workbook_months(
                spreadsheet_id, settings.mcp_server_url, _mcp_auth_headers()
            )
        except Exception as exc:
            logger.exception("Failed to read pending shared workbook for space %s", space_name)
            _pending_sheets.pop(space_name, None)
            return JSONResponse({"text": f"Couldn't read that workbook: {exc}"})

        month = wu.find_mentioned_month(user_text, months)
        if month:
            rows = await check_orchestrator.get_workbook_month_rows(
                spreadsheet_id, month, settings.mcp_server_url, _mcp_auth_headers()
            )
            try:
                brand_guide = await check_orchestrator.get_workbook_brand_guide(
                    spreadsheet_id, settings.mcp_server_url, _mcp_auth_headers()
                )
            except Exception:
                logger.exception("Failed to read Brand Guide tab for space %s", space_name)
                brand_guide = {}
            try:
                title = await check_orchestrator.get_workbook_title(
                    spreadsheet_id, settings.mcp_server_url, _mcp_auth_headers()
                )
            except Exception:
                title = ""
            client_name = await _resolve_sheet_client_name(spreadsheet_id, title)
            _pending_sheets.pop(space_name, None)
            background_tasks.add_task(
                _run_workbook_batch, space_name, thread_name, session_id, month, rows, session_service,
                brand_guide, client_name,
            )
            return JSONResponse({"text": f"Running checks for {month} ({len(rows)} rows)… I'll post results here when done ⏳"})
        # Didn't match a cached month — fall through and handle as a normal message.
        # The pending sheet stays cached in case they follow up with the month later.

    if space_name in _pending_uploads:
        raw_rows, brand_guide, client_name = _pending_uploads[space_name]
        months = wu.distinct_months(raw_rows)
        month = wu.find_mentioned_month(user_text, months)
        if month:
            rows = wu.month_rows(raw_rows, month)
            _pending_uploads.pop(space_name, None)
            background_tasks.add_task(
                _run_workbook_batch, space_name, thread_name, session_id, month, rows, session_service,
                brand_guide, client_name,
            )
            return JSONResponse({"text": f"Running checks for {month} ({len(rows)} rows)… I'll post results here when done ⏳"})
        # Didn't match a cached month — fall through and handle as a normal message.
        # The pending upload stays cached in case they follow up with the month later.

    background_tasks.add_task(
        _run_and_reply, space_name, thread_name, session_id, user_text, session_service
    )

    return JSONResponse({"text": "Running checks… I'll post results here when done ⏳"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Cloud Run injects PORT and expects the process to bind 0.0.0.0:$PORT.
    import os
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
