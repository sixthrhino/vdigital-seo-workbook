from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_QA_ITEM_RE = re.compile(r"\d+\.\)\s*")


def slugify(text: str) -> str:
    """Deterministic touchpoint_id derivation from an "Optimization Touchpoint"
    cell, e.g. "H2 / H3 / H4 tags" -> "h2_h3_h4_tags". Kept as a standalone
    function (rather than inlined) so mcp_server/agent_service can derive the
    same id from user-facing touchpoint names if ever needed.
    """
    slug = _SLUG_STRIP_RE.sub("_", text.strip().lower())
    return slug.strip("_")


def _split_qa_guidelines(raw: str) -> list[str]:
    """The CSV packs numbered guidelines ("1.) ... 2.) ...") into a single
    cell with blank-line separators; split them into a clean list.
    """
    if not raw or not raw.strip():
        return []
    parts = _QA_ITEM_RE.split(raw)
    return [" ".join(p.split()) for p in parts if p.strip()]


@dataclass(frozen=True)
class Touchpoint:
    touchpoint_id: str
    category: str
    search_tactic: str
    name: str
    description: str
    qa_guidelines: tuple[str, ...]
    implementation_notes: str


@dataclass(frozen=True)
class BestPracticeCatalog:
    touchpoints: tuple[Touchpoint, ...]

    def get(self, touchpoint_id: str) -> Touchpoint:
        for tp in self.touchpoints:
            if tp.touchpoint_id == touchpoint_id:
                return tp
        raise KeyError(f"Unknown touchpoint_id: {touchpoint_id!r}")

    def by_category(self, category: str) -> tuple[Touchpoint, ...]:
        return tuple(tp for tp in self.touchpoints if tp.category == category)

    def ids(self) -> tuple[str, ...]:
        return tuple(tp.touchpoint_id for tp in self.touchpoints)


def load_catalog(csv_path: str | Path) -> BestPracticeCatalog:
    """Parse the best-practices CSV into a BestPracticeCatalog.

    The source file has a title row before the real header row (a merged
    "Best Practices Doc:" cell), so we scan for the row starting with
    "Category" rather than assuming row 0 is the header.

    Known upstream issue (confirmed against the source PDF, not a parsing
    bug here): cta_call_to_action, off_site_youtube_optimization,
    section_design_faq_content, and url_changes_redirection all carry
    qa_guidelines copy-pasted from a video-optimization touchpoint. The SEO
    team owns correcting this in the source doc — parsed verbatim for now.
    """
    path = Path(csv_path)
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    header_idx = next(
        (i for i, row in enumerate(rows) if row and row[0].strip() == "Category"),
        None,
    )
    if header_idx is None:
        raise ValueError(f"Could not find a 'Category' header row in {path}")

    header = [cell.strip() for cell in rows[header_idx]]
    data_rows = rows[header_idx + 1 :]

    touchpoints: list[Touchpoint] = []
    seen_ids: set[str] = set()

    for raw_row in data_rows:
        if not raw_row or not any(cell.strip() for cell in raw_row):
            continue

        row = dict(zip(header, raw_row))
        name = (row.get("Optimization Touchpoint") or "").strip()
        if not name:
            continue

        touchpoint_id = slugify(name)
        if touchpoint_id in seen_ids:
            raise ValueError(
                f"Duplicate touchpoint_id {touchpoint_id!r} generated for {name!r} — "
                "the CSV needs a disambiguating name or slugify() needs a manual override."
            )
        seen_ids.add(touchpoint_id)

        touchpoints.append(
            Touchpoint(
                touchpoint_id=touchpoint_id,
                category=(row.get("Category") or "").strip(),
                search_tactic=(row.get("Search Tactic") or "").strip(),
                name=name,
                description=(row.get("What it is?") or "").strip(),
                qa_guidelines=tuple(
                    _split_qa_guidelines(row.get("Expected Output (QA Guidelines)") or "")
                ),
                implementation_notes=(row.get("Implementation / Best Practice Notes") or "").strip(),
            )
        )

    return BestPracticeCatalog(touchpoints=tuple(touchpoints))
