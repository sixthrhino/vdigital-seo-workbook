from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from ..keywords import KeywordTarget

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class SessionStatus(str, Enum):
    DRAFT = "draft"
    FINALIZED = "finalized"


class ValidationResult(BaseModel):
    passed: bool = False
    messages: list[str] = Field(default_factory=list)


class TouchpointAnswer(BaseModel):
    """One optimization applied to a page.

    `items` is a list rather than a single dict because touchpoints like
    H2/H3/H4 changes or internal linking commonly apply multiple times per
    page (e.g. four headings promoted, two links added) — the legacy
    workbook crammed all of that into one free-text cell per page, which is
    exactly the ambiguity this structure is meant to avoid. Single-instance
    touchpoints (title tag, meta description) just carry a list of length 1.
    """

    touchpoint_id: str
    category: str
    items: list[dict[str, str]] = Field(default_factory=list)
    validation: ValidationResult = Field(default_factory=ValidationResult)


class Page(BaseModel):
    url: str
    keyword_target: KeywordTarget | None = None
    geo: str | None = None
    touchpoints: list[TouchpointAnswer] = Field(default_factory=list)

    def get_touchpoint(self, touchpoint_id: str) -> TouchpointAnswer | None:
        return next((tp for tp in self.touchpoints if tp.touchpoint_id == touchpoint_id), None)

    def is_complete(self) -> bool:
        return bool(self.touchpoints) and all(tp.validation.passed for tp in self.touchpoints)


class PlanSession(BaseModel):
    session_id: str
    client: str
    month: str  # "YYYY-MM"
    requested_by: str | None = None
    status: SessionStatus = SessionStatus.DRAFT
    pages: list[Page] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finalized_at: datetime | None = None

    @field_validator("month")
    @classmethod
    def _validate_month(cls, value: str) -> str:
        if not _MONTH_RE.match(value):
            raise ValueError("month must be in YYYY-MM format, e.g. '2026-06'")
        return value

    def get_page(self, url: str) -> Page | None:
        return next((page for page in self.pages if page.url == url), None)

    def add_page(self, url: str) -> Page:
        if self.get_page(url) is not None:
            raise ValueError(f"Page already exists in session: {url}")
        page = Page(url=url)
        self.pages.append(page)
        return page

    def open_questions(self) -> list[str]:
        """Human-readable list of unresolved items, driven by validation
        state rather than the LLM's judgment — this is what the agent polls
        each turn to know whether the session is actually done.
        """
        issues: list[str] = []
        for page in self.pages:
            if not page.touchpoints:
                issues.append(f"{page.url} — no optimizations recorded yet")
                continue
            for tp in page.touchpoints:
                if not tp.validation.passed:
                    reason = "; ".join(tp.validation.messages) or "not yet validated"
                    issues.append(f"{page.url} — {tp.touchpoint_id}: {reason}")
        return issues

    def is_complete(self) -> bool:
        return bool(self.pages) and all(page.is_complete() for page in self.pages)
