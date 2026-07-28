from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastmcp import FastMCP
from seo_workbook_common.best_practices import BestPracticeCatalog
from seo_workbook_common.best_practices.loader import slugify
from seo_workbook_common.keywords import parse_keyword_target
from seo_workbook_common.models.plan_session import PlanSession, SessionStatus, TouchpointAnswer
from seo_workbook_common.storage import build_mongo_collection, save_session
from seo_workbook_common.validators import validate_touchpoint

from ..config import McpSettings
from ..session_store import SessionNotFoundError, SessionStore


def _session_id(client: str, month: str) -> str:
    return f"{slugify(client)}-{month}"


def register(
    mcp: FastMCP,
    catalog: BestPracticeCatalog,
    store: SessionStore,
    settings: McpSettings,
    mongo_collection_factory: Callable[[], Any] | None = None,
) -> None:
    def _default_mongo_collection_factory() -> Any:
        return build_mongo_collection(settings.mongo_uri, settings.mongo_database, settings.mongo_collection)

    get_mongo_collection = mongo_collection_factory or _default_mongo_collection_factory

    def _persist(session: PlanSession) -> None:
        """Persist to both the in-memory store and MongoDB on every
        mutation, not just at finalize — MongoDB holds a continuously
        current snapshot (status "draft" until finalize_session flips it),
        so a session survives an mcp-server restart/redeploy instead of
        only the final one being recoverable. Raises the same clear error
        as finalize_session if MongoDB isn't configured, so a
        misconfigured deployment fails on the very first tool call rather
        than 20 minutes into a conversation.
        """
        store.save(session)
        if not settings.mongo_uri:
            raise ValueError("mongo_uri is not configured (SEO_WORKBOOK_MONGO_URI)")
        save_session(get_mongo_collection(), session)

    @mcp.tool()
    def start_session(client: str, month: str, requested_by: str | None = None) -> dict:
        """Start a new monthly SEO plan session for one client.

        `month` must be "YYYY-MM" (e.g. "2026-06"). Returns the new session
        with a generated session_id — use that id in every subsequent call.
        Raises if a session for this client/month already exists (whether
        still in memory or only in MongoDB, e.g. after an mcp-server
        restart) — call get_session with the same id to resume it instead
        of starting over, since starting over would overwrite the existing
        record's pages/touchpoints.
        """
        session_id = _session_id(client, month)

        # store.create() alone only catches an in-memory collision — after
        # a restart this instance's memory is empty even though MongoDB
        # already has the record, and create()+_persist() would silently
        # replace_one() over it, discarding everything recorded so far.
        # Skipped when mongo_uri isn't configured at all so that case still
        # fails with _persist()'s clearer "not configured" error below,
        # rather than a confusing one from an unconfigured Mongo client.
        if settings.mongo_uri:
            try:
                store.get(session_id)
            except SessionNotFoundError:
                pass
            else:
                raise ValueError(
                    f"A session already exists for client={client!r}, month={month!r} "
                    f"(session_id={session_id!r}). Call get_session({session_id!r}) to resume "
                    "it instead of starting over."
                )

        session = PlanSession(session_id=session_id, client=client, month=month, requested_by=requested_by)
        store.create(session)
        _persist(session)
        return session.model_dump(mode="json")

    @mcp.tool()
    def add_page(session_id: str, url: str) -> dict:
        """Add a page/URL to a session's scope of work for the month.

        Call this once per page before recording any touchpoints for it.
        """
        session = store.get(session_id)
        page = session.add_page(url)
        _persist(session)
        return page.model_dump(mode="json")

    @mcp.tool()
    def set_page_targeting(session_id: str, url: str, keyword: str | None = None, geo: str | None = None) -> dict:
        """Set the primary keyword/volume target and geo for a page.

        `keyword` accepts legacy shorthand like "electrician apprenticeship
        (25k)" and splits it into separate keyword text and search_volume
        fields automatically — pass the two only if you already have them
        split.
        """
        session = store.get(session_id)
        page = session.get_page(url)
        if page is None:
            raise ValueError(f"Page not found in session {session_id!r}: {url!r} — call add_page first")
        if keyword is not None:
            page.keyword_target = parse_keyword_target(keyword)
        if geo is not None:
            page.geo = geo
        _persist(session)
        return page.model_dump(mode="json")

    @mcp.tool()
    def record_touchpoint(session_id: str, url: str, touchpoint_id: str, items: list[dict[str, str]]) -> dict:
        """Record (or replace) one touchpoint's answers for a page.

        `items` is a list of field dicts. Most touchpoints (title tag, meta
        description, H1) take exactly one item; others (heading changes,
        internal linking, image alt text) commonly take several — one item
        per instance (one per heading promoted, one per link added) rather
        than bundling everything into one free-text blob. Call
        get_touchpoint_detail(touchpoint_id) first to see what's expected.
        Re-calling with the same touchpoint_id replaces the previous answer
        for that touchpoint on this page.
        """
        session = store.get(session_id)
        page = session.get_page(url)
        if page is None:
            raise ValueError(f"Page not found in session {session_id!r}: {url!r} — call add_page first")

        try:
            category = catalog.get(touchpoint_id).category
        except KeyError as exc:
            raise ValueError(str(exc)) from exc

        validation = validate_touchpoint(touchpoint_id, items)
        answer = TouchpointAnswer(touchpoint_id=touchpoint_id, category=category, items=items, validation=validation)

        existing = page.get_touchpoint(touchpoint_id)
        if existing is not None:
            page.touchpoints.remove(existing)
        page.touchpoints.append(answer)

        _persist(session)
        return answer.model_dump(mode="json")

    @mcp.tool()
    def list_open_questions(session_id: str) -> list[str]:
        """List what's still unresolved in a session: pages with no
        recorded touchpoints yet, and touchpoints that failed validation.
        An empty list means the session is ready to finalize.
        """
        session = store.get(session_id)
        return session.open_questions()

    @mcp.tool()
    def get_session(session_id: str) -> dict:
        """Get the full current state of a session — all pages and their
        recorded touchpoints — e.g. to summarize progress back to the user.
        """
        session = store.get(session_id)
        return session.model_dump(mode="json")

    @mcp.tool()
    def finalize_session(session_id: str) -> dict:
        """Mark a session finalized once every page has at least one
        touchpoint and every touchpoint has passed validation, and persist
        it to MongoDB as the system of record. Raises with the remaining
        open questions if the session isn't ready yet, or a clear error if
        MongoDB isn't configured.
        """
        session = store.get(session_id)
        if not session.is_complete():
            open_questions = session.open_questions()
            raise ValueError("Session is not complete yet — resolve these first: " + "; ".join(open_questions))

        session.status = SessionStatus.FINALIZED
        session.finalized_at = datetime.now(timezone.utc)
        _persist(session)

        return session.model_dump(mode="json")
