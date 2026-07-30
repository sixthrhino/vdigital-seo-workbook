from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastmcp import FastMCP
from seo_workbook_common.best_practices import BestPracticeCatalog
from seo_workbook_common.best_practices.loader import slugify
from seo_workbook_common.keywords import parse_keyword_target
from seo_workbook_common.models.plan_session import PlanSession, SessionStatus, TouchpointAnswer
from seo_workbook_common.page_capture import parse_page_capture
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
    def find_session(client: str, month: str) -> dict:
        """Look up an existing session by client and month (YYYY-MM) —
        use this when asked to summarize, review, or resume a plan from a
        specific month without already knowing its session_id (e.g. "what
        did we do for KYZ in June?" or "summarize Sixth Rhino's plan for
        2026-06"). Works even if the session was created in a different
        conversation or before an mcp-server restart, since it falls back
        to MongoDB the same way get_session does.

        Returns the same shape as get_session — touchpoint_id values are
        internal ids, not display names; call get_touchpoint_detail to
        resolve one if you need to describe it back to the specialist. For
        a polished, shareable write-up instead of a conversational
        recap, follow up with render_session_report(session_id).

        Raises if no session exists yet for that client/month — call
        start_session instead in that case.
        """
        session_id = _session_id(client, month)
        try:
            session = store.get(session_id)
        except SessionNotFoundError as exc:
            raise ValueError(
                f"No session found for client={client!r}, month={month!r} "
                f"(session_id={session_id!r}). Call start_session to begin one."
            ) from exc
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
    def record_page_from_text(session_id: str, text: str) -> dict:
        """Record (or update) one page's month of changes from a single
        labeled text block, instead of one add_page/set_page_targeting/
        record_touchpoint call per field — for when the specialist already
        has their notes organized and wants to hand a whole page over at
        once. One page per call; for several pages, call this once per URL.

        Expected format — one "label: value" per line, all optional except
        url:

            url: https://example.com/service-a/
            keyword: auto insurance (500)
            geo: Scottsdale, AZ
            title: Old Title Tag -> New Title Tag
            meta: Old meta description -> New meta description
            cta: Get a Quote
            h1: Old H1 -> New H1
            headings: H2 -> H3: Checking Over Your Trailer
              H3: Emergency Equipment
            notes: anything else — links added, schema, alt text, etc.
              Free text, can span multiple lines, but must be the LAST
              label in the block (everything from "notes:" to the end of
              the text becomes its value, line breaks and all).

        title/meta/h1 use "->" (or the unicode arrow) to separate old from
        new — never the bare word "to", which shows up too often inside
        real title/meta text on its own. Omit the old side (e.g. just
        "title: New Title Tag") for a brand-new page with nothing to
        compare against. keyword auto-fills each of title/h1's required
        primary_keyword, so it only needs to be given once, not per field.

        headings is the one multi-line label that doesn't have to be last
        — one heading change per line, each either "H# -> H#: heading
        text" (old and new level both stated) or "H#: heading text" (new
        level only, when the old level isn't known — that's fine, it's
        optional). The block runs until the next recognized label (or the
        end of the text). Becomes a real h2_h3_h4_tags touchpoint, not
        free text.

        notes becomes the same free-text "optimizations" touchpoint
        category import_legacy_workbook uses for a legacy workbook's own
        notes column.

        Adds the page first if it isn't already in the session — no need
        to call add_page separately first. Calling this again for the same
        URL only touches the fields present in the new text; touchpoints
        not mentioned are left exactly as they were, same as record_touchpoint.

        Returns the page's current state, including each touched
        touchpoint's validation result (e.g. a meta given with no cta) —
        relay any failures back conversationally, the same as
        record_touchpoint.
        """
        session = store.get(session_id)
        parsed = parse_page_capture(text)

        page = session.get_page(parsed.url)
        if page is None:
            page = session.add_page(parsed.url)

        if parsed.keyword is not None:
            page.keyword_target = parse_keyword_target(parsed.keyword)
        if parsed.geo is not None:
            page.geo = parsed.geo

        keyword_text = page.keyword_target.keyword if page.keyword_target else None

        def _set_touchpoint(touchpoint_id: str, category: str, items: list[dict[str, str]]) -> None:
            validation = validate_touchpoint(touchpoint_id, items)
            answer = TouchpointAnswer(touchpoint_id=touchpoint_id, category=category, items=items, validation=validation)
            existing = page.get_touchpoint(touchpoint_id)
            if existing is not None:
                page.touchpoints.remove(existing)
            page.touchpoints.append(answer)

        if parsed.title_new:
            item = {"new_value": parsed.title_new}
            if parsed.title_old:
                item["old_value"] = parsed.title_old
            if keyword_text:
                item["primary_keyword"] = keyword_text
            _set_touchpoint("title_tag", catalog.get("title_tag").category, [item])

        if parsed.meta_new:
            item = {"new_value": parsed.meta_new}
            if parsed.meta_old:
                item["old_value"] = parsed.meta_old
            if parsed.cta:
                item["cta"] = parsed.cta
            _set_touchpoint("meta_description", catalog.get("meta_description").category, [item])

        if parsed.h1_new:
            item = {"new_value": parsed.h1_new}
            if parsed.h1_old:
                item["old_value"] = parsed.h1_old
            if keyword_text:
                item["primary_keyword"] = keyword_text
            _set_touchpoint("h1_tag", catalog.get("h1_tag").category, [item])

        if parsed.heading_items:
            _set_touchpoint("h2_h3_h4_tags", catalog.get("h2_h3_h4_tags").category, parsed.heading_items)

        if parsed.notes:
            _set_touchpoint("optimizations", "Optimizations", [{"note": parsed.notes}])

        _persist(session)
        return page.model_dump(mode="json")

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
