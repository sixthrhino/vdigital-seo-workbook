"""Tests for mcp-server/tools/geo.py"""

import httpx
import pytest
import respx

import seo_testing_mcp.tools.geo as geo_mod
from seo_testing_mcp.tools.geo import check_geo_accuracy, _extract_target_states, add_city
from conftest import make_html

# Saved before the autouse patch_cities fixture below ever runs, so
# TestLoadCitiesTTLCache can restore the real implementation for its own
# tests instead of the SMALL_DB stand-in every other test in this file uses.
_REAL_LOAD_CITIES = geo_mod._load_cities

URL = "https://example.com/page"

# Minimal cities database for unit tests.
# Keys are lowercase city names; values are lists of candidate dicts.
SMALL_DB = {
    "phoenix": [{"city": "Phoenix", "state": "AZ", "lat": 33.4484, "lng": -112.074, "county": "maricopa", "population": 1600000}],
    "scottsdale": [{"city": "Scottsdale", "state": "AZ", "lat": 33.4942, "lng": -111.9261, "county": "maricopa", "population": 240000}],
    "mesa": [{"city": "Mesa", "state": "AZ", "lat": 33.4152, "lng": -111.8315, "county": "maricopa", "population": 500000}],
    "dallas": [{"city": "Dallas", "state": "TX", "lat": 32.7767, "lng": -96.797, "county": "dallas", "population": 1300000}],
    "chicago": [{"city": "Chicago", "state": "IL", "lat": 41.8781, "lng": -87.6298, "county": "cook", "population": 2700000}],
    "tucson": [{"city": "Tucson", "state": "AZ", "lat": 32.2226, "lng": -110.9747, "county": "pima", "population": 540000}],
    "austin": [{"city": "Austin", "state": "TX", "lat": 30.2672, "lng": -97.7431, "county": "travis", "population": 960000}],
}


@pytest.fixture(autouse=True)
def patch_cities(monkeypatch):
    """Replace _load_cities with our small test database for all geo tests."""
    try:
        geo_mod._load_cities.cache_clear()
    except AttributeError:
        pass
    monkeypatch.setattr(geo_mod, "_load_cities", lambda: SMALL_DB)
    yield
    try:
        geo_mod._load_cities.cache_clear()
    except AttributeError:
        pass


def _mock(html, url=URL, status=200):
    respx.get(url).mock(return_value=httpx.Response(status, text=html))


# ---------------------------------------------------------------------------
# City-distance mode (geo_city is a city name)
# ---------------------------------------------------------------------------

class TestCityMode:
    @respx.mock
    async def test_pass_only_target_city_mentioned(self):
        body = "<p>Our clinic is located in Phoenix, AZ. We serve the Phoenix metro area.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="Phoenix", geo_state="AZ")
        assert not any(r["status"] == "fail" for r in results)
        assert any(r["status"] == "pass" for r in results)

    @respx.mock
    async def test_warn_far_city_mentioned(self):
        # Dallas is ~900 miles from Phoenix — should be flagged
        body = (
            "<p>Our clinic in Phoenix also treats patients in Dallas, TX "
            "who travel for specialized spine care.</p>"
        )
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="Phoenix", geo_state="AZ", threshold_miles=100)
        assert any(r["status"] == "warn" and "Dallas" in r.get("detail", "") for r in results)

    @respx.mock
    async def test_pass_nearby_city_allowlisted(self):
        body = "<p>We serve Phoenix and Scottsdale, Arizona.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(
            URL,
            geo_city="Phoenix",
            geo_state="AZ",
            threshold_miles=50,
            allowlist_cities=["Scottsdale, AZ"],
        )
        # Scottsdale is ~9 miles from Phoenix, within threshold but also allowlisted
        assert not any("Scottsdale" in r["label"] and r["status"] == "fail" for r in results)

    @respx.mock
    async def test_fail_excluded_city_always_flagged(self):
        # excluded_cities matches the literal string, so include state in body and in list
        body = "<p>Our Phoenix spine clinic — patients come from Austin, TX to see us.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(
            URL,
            geo_city="Phoenix",
            geo_state="AZ",
            threshold_miles=2000,
            excluded_cities=["Austin, TX"],
        )
        assert any("Austin" in r["label"] and r["status"] == "fail" for r in results)

    @respx.mock
    async def test_out_of_range_city_without_context_is_still_flagged(self):
        # No nearby "in/near/serving/clinic/..." wording around "Dallas" — the
        # old suppress-on-no-context behavior would have silently dropped
        # this mention entirely. It should still be flagged, just annotated
        # as lower-confidence rather than treated as a confirmed hit.
        body = (
            "<p>Testimonial: Sarah said Dallas has wonderful weather year round "
            "for outdoor activities and hiking trails everywhere.</p>"
        )
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="Phoenix", geo_state="AZ", threshold_miles=100)
        out_of_range = next(r for r in results if r["label"] == "Out-of-Range Cities")
        assert out_of_range["status"] == "warn"
        assert "Dallas" in out_of_range["detail"]
        assert "unconfirmed context" in out_of_range["detail"]

    @respx.mock
    async def test_out_of_range_city_with_context_has_no_uncertainty_marker(self):
        body = (
            "<p>Our clinic in Phoenix also treats patients in Dallas, TX "
            "who travel for specialized spine care.</p>"
        )
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="Phoenix", geo_state="AZ", threshold_miles=100)
        out_of_range = next(r for r in results if r["label"] == "Out-of-Range Cities")
        assert "Dallas" in out_of_range["detail"]
        assert "unconfirmed context" not in out_of_range["detail"]


# ---------------------------------------------------------------------------
# State-wide mode (geo_city is a state abbr or full state name)
# ---------------------------------------------------------------------------

class TestStateMode:
    @respx.mock
    async def test_pass_in_state_cities_only(self):
        # body must mention "AZ" (abbreviation) for the Geo Target check to pass
        body = "<p>We serve the entire state of AZ, including Phoenix and Tucson.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="AZ")
        assert not any(r["label"] == "Out-of-State Cities" and r["status"] == "fail" for r in results)

    @respx.mock
    async def test_warn_out_of_state_city(self):
        body = "<p>Serving AZ clients and Dallas patients from our Phoenix location.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="AZ")
        assert any(r["label"] == "Out-of-State Cities" and r["status"] == "warn" for r in results)


# ---------------------------------------------------------------------------
# Multi-state mode (geo_city is a comma/and/&-separated list of states)
# ---------------------------------------------------------------------------

class TestExtractTargetStates:
    def test_single_full_name(self):
        assert _extract_target_states("Arizona") == ["AZ"]

    def test_single_abbreviation(self):
        assert _extract_target_states("AZ") == ["AZ"]

    def test_two_states_with_and(self):
        assert _extract_target_states("Indiana and Kentucky") == ["IN", "KY"]

    def test_two_states_with_comma(self):
        assert _extract_target_states("IN, KY") == ["IN", "KY"]

    def test_two_states_with_ampersand(self):
        assert _extract_target_states("IN & KY") == ["IN", "KY"]

    def test_dedupes_repeated_state(self):
        assert _extract_target_states("Indiana and Indiana") == ["IN"]

    def test_city_and_state_returns_none(self):
        # "evansville" isn't a state — must fall through to city-distance
        # mode rather than being misread as a two-item state list.
        assert _extract_target_states("Evansville, IN") is None

    def test_empty_string_returns_none(self):
        assert _extract_target_states("") is None

    def test_plain_city_returns_none(self):
        assert _extract_target_states("Phoenix") is None


class TestMultiStateMode:
    @respx.mock
    async def test_pass_cities_in_either_target_state(self):
        body = "<p>Serving clients across Arizona and Texas, including Phoenix and Dallas.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="Arizona and Texas")
        assert not any(r["label"] == "Out-of-State Cities" and r["status"] == "warn" for r in results)

    @respx.mock
    async def test_warn_city_outside_both_target_states(self):
        body = "<p>Serving Arizona and Texas clients, plus a satellite office near Chicago.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="Arizona and Texas")
        assert any(
            r["label"] == "Out-of-State Cities" and r["status"] == "warn" and "Chicago" in r["detail"]
            for r in results
        )

    @respx.mock
    async def test_geo_target_passes_when_only_one_state_mentioned(self):
        body = "<p>We proudly serve Arizona residents from our Phoenix office.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="Arizona and Texas")
        assert any(r["label"] == "Geo Target" and r["status"] == "pass" for r in results)

    @respx.mock
    async def test_geo_target_fails_when_neither_state_mentioned(self):
        body = "<p>A generic page with no geographic mention at all.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="Arizona and Texas")
        assert any(r["label"] == "Geo Target" and r["status"] == "fail" for r in results)


# ---------------------------------------------------------------------------
# Target-market mode (geo_city is empty, allowlist_cities provided)
# ---------------------------------------------------------------------------

class TestTargetMarketMode:
    @respx.mock
    async def test_pass_only_allowlisted_cities(self):
        body = "<p>We serve clients in Phoenix, Scottsdale, and Mesa, Arizona.</p>"
        _mock(make_html(body=body))
        results = await check_geo_accuracy(
            URL,
            geo_city="",
            allowlist_cities=["Phoenix, AZ", "Scottsdale, AZ", "Mesa, AZ"],
        )
        assert not any("Geo Flag" in r["label"] for r in results)
        assert any(r["status"] == "pass" for r in results)

    @respx.mock
    async def test_warn_unlisted_city_mentioned(self):
        body = (
            "<p>We operate nationwide from our base in Phoenix. "
            "Customers in Chicago also choose our services.</p>"
        )
        _mock(make_html(body=body))
        results = await check_geo_accuracy(
            URL,
            geo_city="",
            allowlist_cities=["Phoenix, AZ"],
        )
        assert any("Chicago" in r["label"] and r["status"] == "warn" for r in results)

    @respx.mock
    async def test_warn_no_city_or_allowlist(self):
        _mock(make_html(body="<p>Some page content.</p>"))
        results = await check_geo_accuracy(URL, geo_city="")
        assert any(r["status"] == "warn" for r in results)

    @respx.mock
    async def test_unlisted_city_without_context_still_flagged(self):
        # Same no-suppress-on-missing-context behavior as city-distance mode.
        # Enough distance from "Phoenix" that its own context words ("in",
        # "base") don't bleed into Chicago's own context window.
        body = (
            "<p>We operate nationwide. Testimonial number four hundred and "
            "twelve reads as follows: quote, Sarah said Chicago has wonderful "
            "weather year round for outdoor activities and hiking trails "
            "everywhere, end quote.</p>"
        )
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="", allowlist_cities=["Phoenix, AZ"])
        flag = next(r for r in results if "Chicago" in r["label"])
        assert flag["status"] == "warn"
        assert "verify — may be incidental" in flag["detail"]

    @respx.mock
    async def test_unlisted_city_with_context_marked_as_detected(self):
        body = (
            "<p>We operate nationwide from our base in Phoenix. "
            "Customers in Chicago also choose our services.</p>"
        )
        _mock(make_html(body=body))
        results = await check_geo_accuracy(URL, geo_city="", allowlist_cities=["Phoenix, AZ"])
        flag = next(r for r in results if "Chicago" in r["label"])
        assert "geographic context detected" in flag["detail"]


# ---------------------------------------------------------------------------
# add_city
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cities_cache():
    """add_city and the TTL-cache tests bypass the patch_cities fixture
    above (they call the real _load_cities / gcs_json, not the monkeypatched
    stand-in) — make sure no state leaks between tests."""
    geo_mod._invalidate_cities_cache()
    yield
    geo_mod._invalidate_cities_cache()


class TestAddCity:
    async def test_errors_without_gcs_uri_configured(self, monkeypatch):
        monkeypatch.delenv("CITIES_GCS_URI", raising=False)
        result = await add_city("Springfield", "OH", 39.9242, -83.8088)
        assert "error" in result
        assert "CITIES_GCS_URI" in result["error"]

    async def test_appends_new_city_under_new_key(self, monkeypatch):
        monkeypatch.setenv("CITIES_GCS_URI", "gs://bucket/uscities.json")
        db = {}
        monkeypatch.setattr(geo_mod.gcs_json, "read_json", lambda uri, path: db)
        written = {}
        monkeypatch.setattr(geo_mod.gcs_json, "write_json", lambda uri, data: written.update(data))

        result = await add_city("Springfield", "OH", 39.9242, -83.8088, county="clark", population=58000)

        assert result["status"] == "ok"
        assert written["springfield"] == [{
            "city": "Springfield", "state": "OH", "lat": 39.9242, "lng": -83.8088,
            "county": "clark", "population": 58000,
        }]

    async def test_appends_additional_candidate_for_existing_city_different_state(self, monkeypatch):
        monkeypatch.setenv("CITIES_GCS_URI", "gs://bucket/uscities.json")
        db = {"springfield": [{"city": "Springfield", "state": "IL", "lat": 1, "lng": 2,
                                "county": "sangamon", "population": 114000}]}
        monkeypatch.setattr(geo_mod.gcs_json, "read_json", lambda uri, path: db)
        written = {}
        monkeypatch.setattr(geo_mod.gcs_json, "write_json", lambda uri, data: written.update(data))

        await add_city("Springfield", "OH", 39.9242, -83.8088)

        assert len(written["springfield"]) == 2
        states = {c["state"] for c in written["springfield"]}
        assert states == {"IL", "OH"}

    async def test_replaces_existing_candidate_for_same_city_and_state(self, monkeypatch):
        monkeypatch.setenv("CITIES_GCS_URI", "gs://bucket/uscities.json")
        db = {"springfield": [{"city": "Springfield", "state": "OH", "lat": 0, "lng": 0,
                                "county": "old-county", "population": 1}]}
        monkeypatch.setattr(geo_mod.gcs_json, "read_json", lambda uri, path: db)
        written = {}
        monkeypatch.setattr(geo_mod.gcs_json, "write_json", lambda uri, data: written.update(data))

        await add_city("Springfield", "OH", 39.9242, -83.8088, county="clark", population=58000)

        assert len(written["springfield"]) == 1
        assert written["springfield"][0]["population"] == 58000
        assert written["springfield"][0]["county"] == "clark"

    async def test_invalidates_cache_after_write(self, monkeypatch):
        monkeypatch.setenv("CITIES_GCS_URI", "gs://bucket/uscities.json")
        monkeypatch.setattr(geo_mod.gcs_json, "read_json", lambda uri, path: {})
        monkeypatch.setattr(geo_mod.gcs_json, "write_json", lambda uri, data: None)

        geo_mod._cities_cache = {"stale": "data"}
        geo_mod._cities_cache_loaded_at = __import__("time").monotonic()

        await add_city("Springfield", "OH", 39.9242, -83.8088)

        assert geo_mod._cities_cache is None


class TestLoadCitiesTTLCache:
    @pytest.fixture(autouse=True)
    def use_real_load_cities(self, monkeypatch):
        # The file-level patch_cities fixture replaces _load_cities with a
        # SMALL_DB stand-in for every test here too (it's autouse) — restore
        # the genuine TTL-caching implementation just for this class.
        monkeypatch.setattr(geo_mod, "_load_cities", _REAL_LOAD_CITIES)

    def test_second_call_within_ttl_does_not_refetch(self, monkeypatch):
        monkeypatch.setenv("CITIES_GCS_URI", "gs://bucket/uscities.json")
        calls = []
        monkeypatch.setattr(geo_mod.gcs_json, "read_json",
                             lambda uri, path: calls.append(1) or {"a": []})

        geo_mod._load_cities()
        geo_mod._load_cities()

        assert len(calls) == 1

    def test_refetches_after_ttl_expires(self, monkeypatch):
        monkeypatch.setenv("CITIES_GCS_URI", "gs://bucket/uscities.json")
        monkeypatch.setattr(geo_mod, "_CITIES_CACHE_TTL_SECONDS", 0)
        calls = []
        monkeypatch.setattr(geo_mod.gcs_json, "read_json",
                             lambda uri, path: calls.append(1) or {"a": []})

        geo_mod._load_cities()
        geo_mod._load_cities()

        assert len(calls) == 2

    def test_refetches_after_explicit_invalidation(self, monkeypatch):
        monkeypatch.setenv("CITIES_GCS_URI", "gs://bucket/uscities.json")
        calls = []
        monkeypatch.setattr(geo_mod.gcs_json, "read_json",
                             lambda uri, path: calls.append(1) or {"a": []})

        geo_mod._load_cities()
        geo_mod._invalidate_cities_cache()
        geo_mod._load_cities()

        assert len(calls) == 2
