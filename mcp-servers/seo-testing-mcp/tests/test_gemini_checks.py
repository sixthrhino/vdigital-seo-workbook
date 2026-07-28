"""Tests for mcp-server/tools/gemini_checks.py"""

import json

import httpx
import pytest
import respx
from google.genai import errors as genai_errors

from seo_testing_mcp.tools.gemini_checks import check_grammar, add_site_dictionary_term, generate_recommendations
import seo_testing_mcp.tools.gemini_checks as gemini_checks_module
from conftest import make_html

URL = "https://example.com/article"


def _mock(html, url=URL, status=200):
    respx.get(url).mock(return_value=httpx.Response(status, text=html))


class _FakeGenaiResponse:
    def __init__(self, text):
        self.text = text


class _FakeGenaiModels:
    def __init__(self, response=None, exc=None, side_effects=None):
        self._response = response
        self._exc = exc
        # Optional list of exceptions/responses consumed in order across
        # successive calls — for exercising retry-then-succeed or
        # retry-until-exhausted behavior. Falls back to response/exc when
        # not given, or once the list runs out.
        self._side_effects = list(side_effects) if side_effects else None
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._side_effects:
            outcome = self._side_effects.pop(0) if len(self._side_effects) > 1 else self._side_effects[0]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        if self._exc:
            raise self._exc
        return self._response


class _FakeGenaiClient:
    def __init__(self, models):
        self.aio = type("Aio", (), {"models": models})()


def _install_fake_genai(monkeypatch, response=None, exc=None, side_effects=None):
    models = _FakeGenaiModels(response=response, exc=exc, side_effects=side_effects)
    monkeypatch.setattr(gemini_checks_module, "_get_genai_client", lambda: _FakeGenaiClient(models))
    return models


# ---------------------------------------------------------------------------
# generate_recommendations
# ---------------------------------------------------------------------------

class TestGenerateRecommendations:
    async def test_skips_llm_call_when_no_problem_checks(self, monkeypatch):
        models = _install_fake_genai(monkeypatch)
        checks = [{"label": "Title", "status": "pass", "detail": "Good"}]

        result = await generate_recommendations(URL, checks)

        assert result == {"key_issues": "", "recommended_fixes": ""}
        assert models.calls == []

    async def test_passes_only_fail_and_warn_checks_to_the_prompt(self, monkeypatch):
        raw = json.dumps({"key_issues": "1. Title too short", "recommended_fixes": "1. Lengthen it"})
        models = _install_fake_genai(monkeypatch, response=_FakeGenaiResponse(raw))
        checks = [
            {"label": "Title", "status": "fail", "detail": "Too short"},
            {"label": "Meta", "status": "pass", "detail": "Fine"},
            {"label": "H1", "status": "warn", "detail": "Missing keyword"},
        ]

        result = await generate_recommendations(URL, checks)

        assert result == {"key_issues": "1. Title too short", "recommended_fixes": "1. Lengthen it"}
        prompt = models.calls[0]["contents"]
        assert "Too short" in prompt
        assert "Missing keyword" in prompt
        assert "Fine" not in prompt  # passing checks are dropped to keep the request small

    async def test_llm_exception_returns_fallback_without_raising(self, monkeypatch):
        _install_fake_genai(monkeypatch, exc=RuntimeError("quota exceeded"))
        checks = [{"label": "Title", "status": "fail", "detail": "Too short"}]

        result = await generate_recommendations(URL, checks)

        assert "quota exceeded" in result["key_issues"]
        assert result["recommended_fixes"] == ""

    async def test_malformed_json_response_returns_fallback(self, monkeypatch):
        _install_fake_genai(monkeypatch, response=_FakeGenaiResponse("not json"))
        checks = [{"label": "Title", "status": "fail", "detail": "Too short"}]

        result = await generate_recommendations(URL, checks)

        assert "unavailable" in result["key_issues"].lower()


# ---------------------------------------------------------------------------
# _generate_content_with_retry
# ---------------------------------------------------------------------------

class TestGenerateContentWithRetry:
    async def test_succeeds_on_first_try_with_no_retry(self, monkeypatch):
        models = _install_fake_genai(monkeypatch, response=_FakeGenaiResponse("ok"))
        result = await gemini_checks_module._generate_content_with_retry("gemini-2.0-flash", "prompt")
        assert result.text == "ok"
        assert len(models.calls) == 1

    async def test_retries_on_429_then_succeeds(self, monkeypatch):
        rate_limited = genai_errors.ClientError(429, {"error": {"message": "rate limited", "status": "RESOURCE_EXHAUSTED"}})
        models = _install_fake_genai(
            monkeypatch, side_effects=[rate_limited, rate_limited, _FakeGenaiResponse("ok")],
        )
        result = await gemini_checks_module._generate_content_with_retry("gemini-2.0-flash", "prompt")
        assert result.text == "ok"
        assert len(models.calls) == 3

    async def test_gives_up_after_max_attempts_on_429(self, monkeypatch):
        rate_limited = genai_errors.ClientError(429, {"error": {"message": "rate limited", "status": "RESOURCE_EXHAUSTED"}})
        models = _install_fake_genai(monkeypatch, exc=rate_limited)
        with pytest.raises(genai_errors.ClientError):
            await gemini_checks_module._generate_content_with_retry("gemini-2.0-flash", "prompt")
        assert len(models.calls) == gemini_checks_module._GEMINI_MAX_ATTEMPTS

    async def test_retries_on_server_error_then_succeeds(self, monkeypatch):
        server_error = genai_errors.ServerError(503, {"error": {"message": "unavailable", "status": "UNAVAILABLE"}})
        models = _install_fake_genai(monkeypatch, side_effects=[server_error, _FakeGenaiResponse("ok")])
        result = await gemini_checks_module._generate_content_with_retry("gemini-2.0-flash", "prompt")
        assert result.text == "ok"
        assert len(models.calls) == 2

    async def test_does_not_retry_non_429_client_error(self, monkeypatch):
        bad_request = genai_errors.ClientError(400, {"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}})
        models = _install_fake_genai(monkeypatch, exc=bad_request)
        with pytest.raises(genai_errors.ClientError):
            await gemini_checks_module._generate_content_with_retry("gemini-2.0-flash", "prompt")
        assert len(models.calls) == 1  # fails fast — retrying a non-transient error wastes time for nothing

    async def test_does_not_retry_unrelated_exception(self, monkeypatch):
        models = _install_fake_genai(monkeypatch, exc=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await gemini_checks_module._generate_content_with_retry("gemini-2.0-flash", "prompt")
        assert len(models.calls) == 1


# ---------------------------------------------------------------------------
# check_grammar
# ---------------------------------------------------------------------------

class TestCheckGrammar:
    @respx.mock
    async def test_pass_response(self, monkeypatch):
        raw = json.dumps({"status": "pass", "issues": []})
        _install_fake_genai(monkeypatch, response=_FakeGenaiResponse(raw))
        _mock(make_html(body="<article><p>Clean, well-written copy.</p></article>"))

        results = await check_grammar(URL)

        assert results == [{"label": "Grammar & Syntax", "status": "pass",
                             "detail": "No spelling/grammar issues found"}]

    @respx.mock
    async def test_fail_response_lists_issues(self, monkeypatch):
        raw = json.dumps({"status": "fail", "issues": ["Misspelled 'recieve'"]})
        _install_fake_genai(monkeypatch, response=_FakeGenaiResponse(raw))
        _mock(make_html(body="<article><p>Text with an error.</p></article>"))

        results = await check_grammar(URL)

        assert results[0]["status"] == "fail"
        assert "recieve" in results[0]["detail"]

    @respx.mock
    async def test_page_load_error(self, monkeypatch):
        _install_fake_genai(monkeypatch)
        respx.get(URL).mock(side_effect=httpx.ConnectError("Connection refused"))

        results = await check_grammar(URL)

        assert results[0]["status"] == "fail"
        assert results[0]["label"] == "Page Load"

    @respx.mock
    async def test_no_content_text_returns_warn(self, monkeypatch):
        _install_fake_genai(monkeypatch)
        _mock(make_html(body="<article></article>"))

        results = await check_grammar(URL)

        assert results == [{"label": "Grammar & Syntax", "status": "warn",
                             "detail": "No page text available to review"}]

    @respx.mock
    async def test_llm_exception_returns_warn(self, monkeypatch):
        _install_fake_genai(monkeypatch, exc=RuntimeError("quota exceeded"))
        _mock(make_html(body="<article><p>Some text.</p></article>"))

        results = await check_grammar(URL)

        assert results[0]["status"] == "warn"
        assert "quota exceeded" in results[0]["detail"]

    @respx.mock
    async def test_survives_a_transient_429_via_retry(self, monkeypatch):
        rate_limited = genai_errors.ClientError(429, {"error": {"message": "rate limited", "status": "RESOURCE_EXHAUSTED"}})
        raw = json.dumps({"status": "pass", "issues": []})
        _install_fake_genai(monkeypatch, side_effects=[rate_limited, _FakeGenaiResponse(raw)])
        _mock(make_html(body="<article><p>Clean, well-written copy.</p></article>"))

        results = await check_grammar(URL)

        assert results[0]["status"] == "pass"

    @respx.mock
    async def test_malformed_json_response_returns_warn(self, monkeypatch):
        _install_fake_genai(monkeypatch, response=_FakeGenaiResponse("not json at all"))
        _mock(make_html(body="<article><p>Some text.</p></article>"))

        results = await check_grammar(URL)

        assert results[0]["status"] == "warn"

    @respx.mock
    async def test_known_site_terms_are_included_in_the_prompt(self, monkeypatch):
        # URL's domain is example.com — see the URL constant at top of file.
        monkeypatch.setattr(gemini_checks_module, "_load_site_dictionaries",
                             lambda: {"example.com": ["Tacolandia", "Huahua's"]})
        raw = json.dumps({"status": "pass", "issues": []})
        models = _install_fake_genai(monkeypatch, response=_FakeGenaiResponse(raw))
        _mock(make_html(body="<article><p>Come to Tacolandia this weekend.</p></article>"))

        await check_grammar(URL)

        prompt = models.calls[0]["contents"]
        assert "Tacolandia" in prompt
        assert "Huahua's" in prompt
        assert "not typos" in prompt

    @respx.mock
    async def test_no_dictionary_entry_for_domain_omits_the_section(self, monkeypatch):
        monkeypatch.setattr(gemini_checks_module, "_load_site_dictionaries",
                             lambda: {"someotherdomain.com": ["Foo"]})
        raw = json.dumps({"status": "pass", "issues": []})
        models = _install_fake_genai(monkeypatch, response=_FakeGenaiResponse(raw))
        _mock(make_html(body="<article><p>Plain copy.</p></article>"))

        await check_grammar(URL)

        prompt = models.calls[0]["contents"]
        assert "not typos" not in prompt
        assert "Foo" not in prompt


class TestSiteDictionaryTerms:
    def test_exact_domain_match(self, monkeypatch):
        monkeypatch.setattr(gemini_checks_module, "_load_site_dictionaries",
                             lambda: {"example.com": ["Tacolandia"]})
        assert gemini_checks_module._site_dictionary_terms("https://example.com/page") == ["Tacolandia"]

    def test_www_prefix_is_stripped(self, monkeypatch):
        monkeypatch.setattr(gemini_checks_module, "_load_site_dictionaries",
                             lambda: {"example.com": ["Tacolandia"]})
        assert gemini_checks_module._site_dictionary_terms("https://www.example.com/page") == ["Tacolandia"]

    def test_unknown_domain_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(gemini_checks_module, "_load_site_dictionaries", lambda: {})
        assert gemini_checks_module._site_dictionary_terms("https://example.com/page") == []


class TestAddSiteDictionaryTerm:
    async def test_errors_without_gcs_uri_configured(self, monkeypatch):
        monkeypatch.delenv("SITE_DICTIONARIES_GCS_URI", raising=False)
        result = await add_site_dictionary_term("example.com", "Tacolandia")
        assert "error" in result
        assert "SITE_DICTIONARIES_GCS_URI" in result["error"]

    async def test_adds_term_to_new_domain(self, monkeypatch):
        monkeypatch.setenv("SITE_DICTIONARIES_GCS_URI", "gs://bucket/site_dictionaries.json")
        monkeypatch.setattr(gemini_checks_module.gcs_json, "read_json", lambda uri, path: {})
        written = {}
        monkeypatch.setattr(gemini_checks_module.gcs_json, "write_json", lambda uri, data: written.update(data))

        result = await add_site_dictionary_term("example.com", "Tacolandia")

        assert result == {"status": "ok", "domain": "example.com", "terms": ["Tacolandia"]}
        assert written == {"example.com": ["Tacolandia"]}

    async def test_appends_term_to_existing_domain(self, monkeypatch):
        monkeypatch.setenv("SITE_DICTIONARIES_GCS_URI", "gs://bucket/site_dictionaries.json")
        monkeypatch.setattr(gemini_checks_module.gcs_json, "read_json",
                             lambda uri, path: {"example.com": ["Tacolandia"]})
        written = {}
        monkeypatch.setattr(gemini_checks_module.gcs_json, "write_json", lambda uri, data: written.update(data))

        result = await add_site_dictionary_term("example.com", "Huahua's")

        assert result["terms"] == ["Tacolandia", "Huahua's"]
        assert written["example.com"] == ["Tacolandia", "Huahua's"]

    async def test_does_not_duplicate_existing_term(self, monkeypatch):
        monkeypatch.setenv("SITE_DICTIONARIES_GCS_URI", "gs://bucket/site_dictionaries.json")
        monkeypatch.setattr(gemini_checks_module.gcs_json, "read_json",
                             lambda uri, path: {"example.com": ["Tacolandia"]})
        written = {}
        monkeypatch.setattr(gemini_checks_module.gcs_json, "write_json", lambda uri, data: written.update(data))

        result = await add_site_dictionary_term("example.com", "Tacolandia")

        assert result["terms"] == ["Tacolandia"]

    async def test_domain_is_normalized(self, monkeypatch):
        monkeypatch.setenv("SITE_DICTIONARIES_GCS_URI", "gs://bucket/site_dictionaries.json")
        monkeypatch.setattr(gemini_checks_module.gcs_json, "read_json", lambda uri, path: {})
        written = {}
        monkeypatch.setattr(gemini_checks_module.gcs_json, "write_json", lambda uri, data: written.update(data))

        result = await add_site_dictionary_term("WWW.Example.com", "Tacolandia")

        assert result["domain"] == "example.com"
        assert "example.com" in written
