import pytest
from seo_workbook_common.models.plan_session import PlanSession
from seo_workbook_common.storage.mongo_store import save_session

from conftest import FakeMongoCollection
from seo_workbook_mcp.session_store import SessionNotFoundError, SessionStore


def test_get_unknown_session_with_no_mongo_factory_raises():
    store = SessionStore()
    with pytest.raises(SessionNotFoundError):
        store.get("does-not-exist")


def test_get_unknown_session_with_mongo_factory_but_no_document_raises():
    store = SessionStore(mongo_collection_factory=lambda: FakeMongoCollection())
    with pytest.raises(SessionNotFoundError):
        store.get("does-not-exist")


def test_get_falls_back_to_mongo_when_not_in_memory():
    # Simulates a Cloud Run instance restart: the session was persisted by
    # some earlier process (or a different instance) and never went
    # through this SessionStore's create()/save(), so it isn't in memory —
    # but it is in Mongo.
    collection = FakeMongoCollection()
    session = PlanSession(session_id="kyz-2026-06", client="KYZ", month="2026-06")
    session.add_page("https://kyz.com/a/")
    save_session(collection, session)

    store = SessionStore(mongo_collection_factory=lambda: collection)
    loaded = store.get("kyz-2026-06")

    assert loaded.client == "KYZ"
    assert len(loaded.pages) == 1


def test_get_caches_the_mongo_fallback_result_in_memory():
    collection = FakeMongoCollection()
    session = PlanSession(session_id="kyz-2026-06", client="KYZ", month="2026-06")
    save_session(collection, session)

    calls = []
    original_find_one = collection.find_one

    def _counting_find_one(filter):
        calls.append(filter)
        return original_find_one(filter)

    collection.find_one = _counting_find_one

    store = SessionStore(mongo_collection_factory=lambda: collection)
    store.get("kyz-2026-06")
    store.get("kyz-2026-06")

    assert len(calls) == 1
