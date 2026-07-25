from __future__ import annotations

import threading

from seo_workbook_common.models.plan_session import PlanSession


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
    the deploy notes on the vds-mcp-server precedent).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, PlanSession] = {}
        self._lock = threading.Lock()

    def create(self, session: PlanSession) -> None:
        with self._lock:
            if session.session_id in self._sessions:
                raise ValueError(f"session_id already exists: {session.session_id}")
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> PlanSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Unknown session_id: {session_id!r}")
        return session

    def save(self, session: PlanSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
