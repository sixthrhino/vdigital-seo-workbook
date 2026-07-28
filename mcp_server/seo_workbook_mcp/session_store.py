from __future__ import annotations

import threading
from typing import Any, Callable

from seo_workbook_common.models.plan_session import PlanSession
from seo_workbook_common.storage import load_session


class SessionNotFoundError(KeyError):
    pass


class SessionStore:
    """In-memory session store, one process per Cloud Run instance.

    Exposes an explicit load/save interface rather than relying on in-place
    mutation of the stored object — an in-memory dict doesn't strictly need
    save() to persist a mutation, but tool code written against that
    assumption would silently break the day this becomes a Firestore-backed
    store. Cloud Run session-affinity keeps a given conversation pinned to
    one instance, so this doesn't need to be shared across instances (see
    the deploy notes on the vds-mcp-server precedent) *while that instance
    stays warm* — but an instance restart, redeploy, or affinity failover
    still loses this dict entirely. `mongo_collection_factory`, if given,
    lets get() fall back to MongoDB (the durable system of record — see
    session_tools._persist) on a cache miss instead of forcing a session
    that's already been persisted to be started over from scratch.
    """

    def __init__(self, mongo_collection_factory: Callable[[], Any] | None = None) -> None:
        self._sessions: dict[str, PlanSession] = {}
        self._lock = threading.Lock()
        self._mongo_collection_factory = mongo_collection_factory

    def create(self, session: PlanSession) -> None:
        with self._lock:
            if session.session_id in self._sessions:
                raise ValueError(f"session_id already exists: {session.session_id}")
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> PlanSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is not None:
            return session

        if self._mongo_collection_factory is not None:
            session = load_session(self._mongo_collection_factory(), session_id)
            if session is not None:
                with self._lock:
                    self._sessions.setdefault(session_id, session)
                return session

        raise SessionNotFoundError(f"Unknown session_id: {session_id!r}")

    def save(self, session: PlanSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
