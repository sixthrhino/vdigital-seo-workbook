import logging
import re
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, HttpUrl

import google.auth
import google.auth.transport.requests as google_requests
from google.oauth2 import id_token as google_id_token

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from .agent import create_agent
from .config import settings
from .gcs_signing import build_mongo_collection, build_storage_client, generate_report_url, iam_signing_credentials
from .report_tokens import lookup_report_token

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


# ---------------------------------------------------------------------------
# Report share-link redirect
# ---------------------------------------------------------------------------

# Module-level, monkeypatchable factories (matching this file's existing
# style — see FakeRunner/_post_to_chat patching in tests) rather than a
# create_app(...)-injected app.state, since this file doesn't use that
# pattern anywhere else. Each is only called lazily inside get_report, not
# at import time, so importing this module never triggers credential
# resolution.
_storage_client_factory = build_storage_client
_signing_credentials_factory = iam_signing_credentials
_report_tokens_collection_factory = lambda: build_mongo_collection(
    settings.mongo_uri, settings.mongo_database, settings.mongo_report_tokens_collection
)


@app.get("/reports/{token}")
def get_report(token: str):
    """Resolve a short report share-token (minted by seo-testing-mcp's
    generate_report) into a freshly-signed GCS URL and redirect there.

    Exists because the alternative — an LLM reproducing a ~700-char signed
    URL verbatim in a chat reply — is a real source of corruption
    (confirmed live: a relayed signed URL came back 15 hex characters
    short, breaking its signature). The short link is what actually goes
    in front of the model instead; this route does the signing, fresh,
    at click time.

    A plain (non-async) route — FastAPI runs it in a worker thread, so the
    blocking Mongo/GCS calls here don't block the event loop.
    """
    if not settings.mongo_uri:
        raise HTTPException(status_code=503, detail="Report storage is not configured")

    tokens_collection = _report_tokens_collection_factory()
    record = lookup_report_token(tokens_collection, token)
    if record is None:
        raise HTTPException(status_code=404, detail="Report link not found or expired")

    storage_client = _storage_client_factory()
    service_account_email, access_token = _signing_credentials_factory()
    url = generate_report_url(
        storage_client,
        record["bucket_name"],
        record["blob_name"],
        service_account_email=service_account_email,
        access_token=access_token,
    )
    return RedirectResponse(url=url, status_code=302)


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


# Google Chat retries a webhook delivery if it doesn't get an ack quickly
# enough — confirmed live: a slow-to-process message got delivered twice,
# ~9s apart. message.name (format "spaces/.../messages/...") is stable
# across retried deliveries of the same event, unlike anything derived from
# the message content — dedupe on that.
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
            # real function call) is a probabilistic generation failure.
            # Retrying is the standard mitigation for this — prompt wording
            # alone (see agent.py's SYSTEM_INSTRUCTION) reduces but doesn't
            # eliminate it. Each retry gets its own session id so the model
            # doesn't see its own malformed attempt in the history it's
            # retrying from.
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
    task and posts the result back to the originating Chat thread. Every
    message goes through the agent's own tool-calling loop (see agent.py) —
    Mode B (reviewing a recorded plan against the live site) is triggered by
    the agent calling review_plan_against_live_site once it knows the client
    and month, not by anything detected here.
    """
    if not _verify_chat_request(request):
        return JSONResponse({}, status_code=401)

    body = await request.json()
    event_type = body.get("type")

    if event_type == "ADDED_TO_SPACE":
        return JSONResponse({"text": "VDS QA Agent is ready. Tell me which client and month to review!"})

    if event_type != "MESSAGE":
        return JSONResponse({})

    message = body.get("message", {})

    # Defense-in-depth against genuine Chat webhook retries (a documented
    # platform behavior when a response is slow).
    if _already_processed(message.get("name", "")):
        logger.info("Ignoring duplicate Chat delivery of message %s", message.get("name"))
        return JSONResponse({})

    user_text = message.get("text", "").strip()
    space_name = body.get("space", {}).get("name", "")
    thread_name = message.get("thread", {}).get("name", "") or space_name
    session_id = thread_name.replace("/", "-")

    if not space_name or not user_text:
        return JSONResponse({})

    session_service = request.app.state.session_service
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
