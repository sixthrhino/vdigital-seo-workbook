"""
Geo accuracy checks — haversine distance scanning against the US cities database.

Requires uscities.json (SimpleMaps format) placed in this component's data/
directory, pointed to by the CITIES_DB_PATH env var, or loaded from GCS via
CITIES_GCS_URI (preferred in production — see add_city, which can only
write to GCS, not the local file, so a GCS-backed database is what makes
"add a city" actually useful there).

uscities.json format: dict keyed by lowercase city name, values are lists of:
  {city, state, lat, lng, county, population}
"""

from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path

from . import gcs_json
from .fetcher import body_text_excluding_chrome, fetch_parsed

# mcp-servers/seo-testing-mcp/src/seo_testing_mcp/tools/geo.py -> this
# component's own root (mcp-servers/seo-testing-mcp/), where data/ lives.
_COMPONENT_ROOT = Path(__file__).resolve().parents[3]
_CITIES_DB_PATH = Path(os.environ.get("CITIES_DB_PATH",
                        _COMPONENT_ROOT / "data" / "uscities.json"))

_US_STATE_ABBR = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
    "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah",
    "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}
_STATE_NAME_TO_ABBR = {name: abbr.upper() for abbr, name in _US_STATE_ABBR.items()}

_MIN_CITY_LEN = 5
_MIN_POPULATION = 5_000

_CITY_STOPWORDS = {
    "aurora", "mesa", "haven", "beach", "grove", "summit", "highland",
    "valley", "ridge", "park", "garden", "hills", "springs", "lake",
    "forest", "manor", "heights", "view", "point", "falls", "creek",
    "landing", "crossing", "junction", "center", "central", "mission",
    "talent", "worth", "grants", "progress", "spring", "mobile", "delta", "media",
}

_GEO_CONTEXT_RE = re.compile(
    r"(in|near|from|around|serving|located|clinic|office|spa|center|area|"
    r"residents|patients|clients|visit|come\s+to|come\s+in|based\s+in|"
    r"drive|commute|neighborhood|community|city|town|suburb)",
    re.IGNORECASE,
)


# uscities.json is ~4MB — unlike site_dictionaries.json (tiny, re-read every
# call) this is worth caching, but a forever-cache would mean add_city's
# writes never show up in a running instance without a restart. A short TTL
# is the middle ground: an edit propagates within one window, and the common
# case (every check in a batch) still hits the in-memory copy, not GCS.
_CITIES_CACHE_TTL_SECONDS = 600
_cities_cache: dict | None = None
_cities_cache_loaded_at: float = 0.0


def _load_cities() -> dict:
    global _cities_cache, _cities_cache_loaded_at
    now = time.monotonic()
    if _cities_cache is not None and (now - _cities_cache_loaded_at) < _CITIES_CACHE_TTL_SECONDS:
        return _cities_cache
    _cities_cache = gcs_json.read_json(os.environ.get("CITIES_GCS_URI"), _CITIES_DB_PATH)
    _cities_cache_loaded_at = now
    return _cities_cache


def _invalidate_cities_cache() -> None:
    global _cities_cache
    _cities_cache = None


async def add_city(city: str, state: str, lat: float, lng: float,
                    county: str = "", population: int = 0) -> dict:
    """Add or update a city in the cities database (uscities.json).

    If a candidate with this city name already exists for the given state,
    it's replaced in place; otherwise a new candidate is appended (a city
    name can have multiple candidates across different states — e.g.
    "Springfield" exists in a dozen states).

    Only writes to GCS — there's no local-file write path, since a local
    edit on one Cloud Run instance wouldn't be visible to any other running
    instance anyway. Requires CITIES_GCS_URI to be configured.
    """
    gcs_uri = os.environ.get("CITIES_GCS_URI")
    if not gcs_uri:
        return {"error": "CITIES_GCS_URI is not configured — cannot add a city without a shared GCS location."}

    cities_db = gcs_json.read_json(gcs_uri, _CITIES_DB_PATH)
    key = city.strip().lower()
    state_abbr = state.strip().upper()
    entry = {"city": city.strip(), "state": state_abbr, "lat": lat, "lng": lng,
              "county": county.strip().lower(), "population": population}

    candidates = cities_db.setdefault(key, [])
    for i, existing in enumerate(candidates):
        if str(existing.get("state", "")).upper() == state_abbr:
            candidates[i] = entry
            break
    else:
        candidates.append(entry)

    gcs_json.write_json(gcs_uri, cities_db)
    _invalidate_cities_cache()
    return {"status": "ok", "city": entry}


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 3958.8
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _extract_target_states(text: str) -> list[str] | None:
    """Parse one or more US states from text like "Indiana and Kentucky",
    "IN, KY", "IN & KY", or a single "Indiana"/"IN". Returns a list of
    state abbreviations (uppercase, de-duplicated, order preserved) or None
    if the text isn't a pure state (or state-list) target.

    Every comma/and/&/slash-separated segment must itself resolve to a
    state name or 2-letter code — if even one segment doesn't, this returns
    None so a normal "Evansville, IN" city+state target doesn't get
    misread as a two-item state list ("evansville" fails the state check,
    so the whole thing correctly falls through to city-distance parsing
    instead).
    """
    if not text:
        return None
    parts = [p.strip().lower() for p in re.split(r"\s*(?:,|&|/|\band\b)\s*", text.strip(), flags=re.I) if p.strip()]
    if not parts:
        return None
    found: list[str] = []
    for p in parts:
        if p in _STATE_NAME_TO_ABBR:
            abbr = _STATE_NAME_TO_ABBR[p]
        elif len(p) == 2 and p in _US_STATE_ABBR:
            abbr = p.upper()
        else:
            return None
        if abbr not in found:
            found.append(abbr)
    return found or None


def _parse_county_entries(entries: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for raw in (entries or []):
        item = raw.strip()
        if not item:
            continue
        county_part, state_part = (item.split(",", 1) if "," in item else (item, ""))
        county_part = re.sub(r"\s+county$", "", county_part.strip(), flags=re.I).strip()
        pairs.append((county_part.lower(), state_part.strip().upper()))
    return pairs


def _plausible_city_mention(city_name: str, body_lower: str, match, city_data: dict,
                             title_words: set[str]) -> bool:
    """Hard filters only — a mention failing any of these is almost never a
    genuine geographic reference (part of the client's own name, a tiny
    same-named hamlet, a county/park name). Nearby geo-context wording is
    NOT required here — see _has_geo_context, which instead annotates
    confidence rather than suppressing the mention outright (mirrors the
    reference script: it flags all plausible mentions and only *notes*
    missing context, rather than silently dropping mentions that lack it —
    a real out-of-area mention with no nearby "serving"/"near"/etc. wording
    is still worth a human's attention, just with lower confidence)."""
    if len(city_name) < _MIN_CITY_LEN:
        return False
    if city_name.lower() in _CITY_STOPWORDS:
        return False
    if (city_data.get("population") or 0) < _MIN_POPULATION:
        return False
    if city_name.lower() in title_words:
        return False
    tail = body_lower[match.end():match.end() + 60]
    if re.match(r"^(?:[\s,&]+(?:and\s+)?\w+){0,3}[\s,]*count(?:y|ies)\b", tail):
        return False
    if re.search(r"\bpark\b", body_lower[match.end():match.end() + 30]):
        return False
    return True


def _has_geo_context(body_lower: str, match) -> bool:
    """Whether wording near the match (in/near/serving/located/etc.)
    supports reading it as a genuine geographic reference. A window on both
    sides of the match, roughly sentence-sized — wider than just checking
    what precedes it, since context often follows too ("Dayton area
    residents" vs. "serving customers in Dayton")."""
    window = body_lower[max(0, match.start() - 60):match.end() + 60]
    return bool(_GEO_CONTEXT_RE.search(window))


def _context_note(has_context: bool) -> str:
    return " (geographic context detected)" if has_context else " (verify — may be incidental)"


def _r(label: str, status: str, detail: str) -> dict:
    return {"label": label, "status": status, "detail": detail}


async def check_geo_accuracy(
    url: str,
    geo_city: str = "",
    geo_state: str = "",
    threshold_miles: int = 100,
    allowed_counties: list[str] | None = None,
    excluded_cities: list[str] | None = None,
    allowlist_cities: list[str] | None = None,
) -> list[dict]:
    """Scan page body for city mentions outside the geo target area.

    Three modes (selected automatically based on inputs):
    - City-distance: provide geo_city + optional geo_state. Flags cities beyond
      threshold_miles from the target city.
    - State-wide: provide a full state name or 2-letter abbreviation as geo_city.
      Flags cities in other states.
    - Target-market (nationwide clients): leave geo_city empty and provide
      allowlist_cities. Flags any city mention NOT in the allowlist.

    Args:
        url: Live page URL.
        geo_city: Primary city, full state name, 2-letter state abbr, or empty
                  string for target-market mode.
        geo_state: 2-letter state code (disambiguates same-name cities).
        threshold_miles: Distance threshold in miles (default 100, city-distance mode only).
        allowed_counties: County names to exempt (e.g. ["Butler County, OH"]).
        excluded_cities: Cities the client explicitly CANNOT service — always flagged.
        allowlist_cities: In city/state modes, extra in-area cities to whitelist.
                          In target-market mode, this IS the full valid market list.

    Returns:
        List of check result dicts.
    """
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    cities_db = _load_cities()
    if not cities_db:
        return [_r("Geo Check", "warn",
                   "Cities database not found. Place uscities.json in mcp-server/ or set CITIES_DB_PATH.")]

    results: list[dict] = []
    soup = p.soup

    body_text = body_text_excluding_chrome(p)
    body_lower = body_text.lower()

    title_tag = soup.find("title") if soup else None
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    title_words = set(re.findall(r"\w+", title_text.lower()))

    county_pairs = _parse_county_entries(allowed_counties or [])
    allowlist_set = set()
    for item in (allowlist_cities or []):
        city_part = item.split(",")[0].strip().lower()
        if city_part:
            allowlist_set.add(city_part)

    def in_allowed_county(city_data: dict) -> bool:
        if not county_pairs:
            return False
        county = (city_data.get("county") or "").strip().lower()
        state = (city_data.get("state") or "").strip().upper()
        return any(c == county and (not s or s == state) for c, s in county_pairs)

    # Excluded cities — always flag regardless of mode
    for city in (excluded_cities or []):
        city_clean = city.strip()
        if city_clean and re.search(r"\b" + re.escape(city_clean) + r"\b", body_text, re.I):
            results.append(_r(f'Excluded City: "{city_clean}"', "fail",
                               f'"{city_clean}" mentioned — client CANNOT SERVICE this area'))

    # Target-market mode: no anchor city, just validate against allowlist
    if not geo_city.strip():
        if not allowlist_cities:
            return results + [_r("Geo Check", "warn",
                                  "No geo_city or allowlist_cities provided — skipping geo check")]

        if allowlist_set:
            results.append(_r("Target Markets", "pass",
                               f"Treating as valid markets: {', '.join(sorted(allowlist_set))}"))

        flagged: list[tuple[str, bool]] = []
        for city_key, cands in cities_db.items():
            city_name = cands[0]["city"]
            if city_name.lower() in allowlist_set:
                continue
            match = re.search(r"\b" + re.escape(city_name.lower()) + r"\b", body_lower)
            if not match or not _plausible_city_mention(city_name, body_lower, match,
                                                         cands[0], title_words):
                continue
            flagged.append((f"{city_name}, {cands[0]['state']}", _has_geo_context(body_lower, match)))

        if flagged:
            for city_label, has_context in flagged[:10]:
                results.append(_r(f'Geo Flag: "{city_label}"', "warn",
                                   f'"{city_label}" mentioned but not in target market allowlist'
                                   f" — verify this is intentional{_context_note(has_context)}"))
            if len(flagged) > 10:
                results.append(_r("Geo Flag", "warn",
                                   f"{len(flagged) - 10} additional cities flagged — review page content"))
        else:
            results.append(_r("Geo Check", "pass",
                               "No city mentions found outside the target market allowlist"))
        return results

    # State-mode: geo_city is a state name/abbreviation, or a comma/and/&
    # separated list of states (e.g. "Indiana and Kentucky" — some clients,
    # like a bank branch near a state line, are explicitly targeted at more
    # than one).
    target_states = _extract_target_states(geo_city)

    if target_states:
        primary_found = any(
            re.search(r"\b" + re.escape(s.lower()) + r"\b", body_lower) or
            re.search(r"\b" + re.escape(_US_STATE_ABBR[s.lower()]) + r"\b", body_lower)
            for s in target_states
        )
        results.append(_r("Geo Target", "pass" if primary_found else "fail",
                           f"'{geo_city}' {'found' if primary_found else 'not found'} in body copy"))

        out_of_state = []
        for city_key, candidates in cities_db.items():
            city_name = candidates[0]["city"]
            match = re.search(r"\b" + re.escape(city_name.lower()) + r"\b", body_lower)
            if not match or not _plausible_city_mention(city_name, body_lower, match,
                                                         candidates[0], title_words):
                continue
            if any(c["state"].upper() in target_states for c in candidates):
                continue
            if any(in_allowed_county(c) for c in candidates):
                continue
            context_mark = "" if _has_geo_context(body_lower, match) else " (unconfirmed context)"
            out_of_state.append(f"{city_name}, {candidates[0]['state']}{context_mark}")

        if out_of_state:
            shown = "; ".join(out_of_state[:5])
            results.append(_r("Out-of-State Cities", "warn",
                               f"{len(out_of_state)} city/cities outside {geo_city}: {shown}"
                               + ("..." if len(out_of_state) > 5 else "")))
        else:
            results.append(_r("Out-of-State Cities", "pass",
                               f"No cities outside {geo_city} found in body copy"))
        return results

    # City-distance mode
    primary_key = geo_city.strip().lower()
    candidates = cities_db.get(primary_key)
    if not candidates:
        return results + [_r("Geo Check", "warn",
                              f"'{geo_city}' not found in cities database — verify spelling")]

    primary_data = (next((c for c in candidates if c["state"].upper() == geo_state.strip().upper()), None)
                    if geo_state else None) or candidates[0]

    primary_found = geo_city.lower() in body_lower
    results.append(_r("Geo Target", "pass" if primary_found else "fail",
                       f"'{geo_city}' {'found' if primary_found else 'not found'} in body copy"))

    out_of_range = []
    for city_key, cands in cities_db.items():
        if city_key == primary_key:
            continue
        city_name = cands[0]["city"]
        if city_name.lower() in allowlist_set:
            continue
        match = re.search(r"\b" + re.escape(city_name.lower()) + r"\b", body_lower)
        if not match or not _plausible_city_mention(city_name, body_lower, match,
                                                     cands[0], title_words):
            continue
        dists = [_haversine(primary_data["lat"], primary_data["lng"],
                             c["lat"], c["lng"]) for c in cands]
        best_dist = min(dists)
        best_cand = cands[dists.index(best_dist)]
        if best_dist <= threshold_miles or any(in_allowed_county(c) for c in cands):
            continue
        out_of_range.append({"city": city_name, "state": best_cand["state"],
                              "distance": round(best_dist),
                              "context_mark": "" if _has_geo_context(body_lower, match) else " (unconfirmed context)"})

    out_of_range.sort(key=lambda x: -x["distance"])

    if out_of_range:
        shown = "; ".join(f"{c['city']}, {c['state']} ({c['distance']} mi){c['context_mark']}"
                          for c in out_of_range[:5])
        results.append(_r("Out-of-Range Cities", "warn",
                           f"{len(out_of_range)} city/cities >{threshold_miles} mi from {geo_city}: "
                           + shown + ("..." if len(out_of_range) > 5 else "")))
    else:
        results.append(_r("Out-of-Range Cities", "pass",
                           f"No cities >{threshold_miles} miles from {geo_city} found in body copy"))

    return results
