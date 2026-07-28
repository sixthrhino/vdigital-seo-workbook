import pytest

from seo_workbook_common.models.plan_session import PlanSession, TouchpointAnswer, ValidationResult
from seo_workbook_common.storage.mongo_store import load_session, save_session, session_to_document


@pytest.fixture
def sample_session() -> PlanSession:
    session = PlanSession(session_id="kyz-2026-06", client="KYZ", month="2026-06")
    page = session.add_page("https://kyz.com/service-a/")
    page.touchpoints.append(
        TouchpointAnswer(
            touchpoint_id="title_tag",
            category="Core",
            items=[{"new_value": "Auto Insurance in Scottsdale", "primary_keyword": "auto insurance"}],
            validation=ValidationResult(passed=True, messages=[]),
        )
    )
    return session


def test_session_to_document_sets_mongo_id_from_session_id(sample_session):
    document = session_to_document(sample_session)
    assert document["_id"] == "kyz-2026-06"
    assert document["session_id"] == "kyz-2026-06"


def test_session_to_document_includes_full_session_shape(sample_session):
    document = session_to_document(sample_session)
    assert document["client"] == "KYZ"
    assert document["month"] == "2026-06"
    assert len(document["pages"]) == 1
    assert document["pages"][0]["touchpoints"][0]["touchpoint_id"] == "title_tag"


class _FakeCollection:
    def __init__(self):
        self.replace_one_calls = []
        self.documents: dict[str, dict] = {}

    def replace_one(self, filter, replacement, upsert=False):
        self.replace_one_calls.append({"filter": filter, "replacement": replacement, "upsert": upsert})
        self.documents[filter["_id"]] = replacement

    def find_one(self, filter):
        return self.documents.get(filter["_id"])


def test_save_session_upserts_by_id(sample_session):
    collection = _FakeCollection()

    save_session(collection, sample_session)

    assert len(collection.replace_one_calls) == 1
    call = collection.replace_one_calls[0]
    assert call["filter"] == {"_id": "kyz-2026-06"}
    assert call["upsert"] is True
    assert call["replacement"]["client"] == "KYZ"


def test_save_session_called_twice_upserts_same_id_both_times(sample_session):
    collection = _FakeCollection()

    save_session(collection, sample_session)
    sample_session.pages[0].geo = "Scottsdale, AZ"
    save_session(collection, sample_session)

    assert len(collection.replace_one_calls) == 2
    assert collection.replace_one_calls[0]["filter"] == collection.replace_one_calls[1]["filter"]
    assert collection.replace_one_calls[1]["replacement"]["pages"][0]["geo"] == "Scottsdale, AZ"


def test_load_session_returns_none_for_unknown_id():
    collection = _FakeCollection()
    assert load_session(collection, "does-not-exist") is None


def test_load_session_round_trips_a_saved_session(sample_session):
    collection = _FakeCollection()
    save_session(collection, sample_session)

    loaded = load_session(collection, "kyz-2026-06")

    assert loaded is not None
    assert loaded.session_id == "kyz-2026-06"
    assert loaded.client == "KYZ"
    assert loaded.month == "2026-06"
    assert len(loaded.pages) == 1
    assert loaded.pages[0].touchpoints[0].touchpoint_id == "title_tag"
