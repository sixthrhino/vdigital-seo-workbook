from __future__ import annotations

import re

from pydantic import BaseModel

_KEYWORD_VOLUME_RE = re.compile(r"^(.*?)\s*\(\s*([\d,]*\.?\d+)\s*([kK])?\s*\)\s*$")


class KeywordTarget(BaseModel):
    keyword: str
    search_volume: int | None = None


def parse_keyword_target(raw: str) -> KeywordTarget:
    """Split legacy-workbook keyword shorthand like "IEC rocky mountain
    (100)" or "career fair (8.5K)" into separate keyword/search_volume
    fields — the combined string reads as one value but is really two,
    which was a stated source of confusion in the workbook this replaces.
    """
    raw = raw.strip()
    match = _KEYWORD_VOLUME_RE.match(raw)
    if not match:
        return KeywordTarget(keyword=raw, search_volume=None)

    keyword, number, k_suffix = match.groups()
    try:
        volume = float(number.replace(",", ""))
    except ValueError:
        return KeywordTarget(keyword=raw, search_volume=None)
    if k_suffix:
        volume *= 1000

    return KeywordTarget(keyword=keyword.strip(), search_volume=round(volume))
