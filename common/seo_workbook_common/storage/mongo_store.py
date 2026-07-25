from __future__ import annotations

from typing import Any

from ..models.plan_session import PlanSession


def session_to_document(session: PlanSession) -> dict:
    """Deterministically convert a PlanSession into its MongoDB document
    shape — session_id becomes _id so re-finalizing the same session
    upserts in place rather than creating a duplicate record.
    """
    document = session.model_dump(mode="json")
    document["_id"] = document["session_id"]
    return document


def save_session(collection: Any, session: PlanSession) -> None:
    """Upsert a session into MongoDB as the system of record for finalized
    plans.

    `collection` is a pymongo Collection (or any test double implementing
    `.replace_one(filter, replacement, upsert=True)`) — injected rather than
    constructed here so this stays unit-testable without a real MongoDB
    connection. Use build_mongo_collection() to get a real one.
    """
    document = session_to_document(session)
    collection.replace_one({"_id": document["_id"]}, document, upsert=True)


def build_mongo_collection(uri: str, database: str, collection_name: str) -> Any:
    """Construct a real MongoDB collection handle.

    Not exercised in unit tests — see save_session's injectable `collection`
    param for the testable seam.
    """
    from pymongo import MongoClient

    client = MongoClient(uri)
    return client[database][collection_name]
