"""
The only two checks in this whole tools/ directory that call an LLM instead
of just parsing HTML deterministically: grammar/spelling review, and the
short per-page key_issues/recommended_fixes summary used by Mode B batches.
Both share the same Gemini client and retry-with-backoff plumbing, which is
the main reason they live together rather than inside content.py alongside
the deterministic checks.

Also home to the site dictionaries (per-domain known-terms lists) — GCS-
backed self-serve data that exists solely to keep check_grammar from
false-flagging a site's own brand/event names as spelling errors.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from google import genai
from google.genai import errors as genai_errors

from . import gcs_json
from .fetcher import fetch_parsed, main_content_text


def _r(label: str, status: str, detail: str) -> dict:
    return {"label": label, "status": status, "detail": detail}


# ---------------------------------------------------------------------------
# Gemini client + retry
# ---------------------------------------------------------------------------

_genai_client: genai.Client | None = None


def _get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client()
    return _genai_client


_GEMINI_MAX_ATTEMPTS = 3
_GEMINI_BACKOFF_BASE_SECONDS = 2.0


async def _generate_content_with_retry(model: str, contents: str):
    """Retry a Gemini call on rate-limit (429) or transient server (5xx)
    errors, with exponential backoff.

    A batch fires several content_generate_recommendations calls concurrently
    (one per row with issues, up to check_orchestrator's 8-in-flight bound),
    which can trip Vertex AI's per-project rate limit under a big/bursty
    batch — without this, a single 429 would permanently downgrade that one
    page's result to "unavailable" when a short wait would have succeeded.
    Non-429 client errors (bad request, auth, etc.) are NOT retried — retrying
    an error that isn't transient just wastes the same failure three times.
    """
    for attempt in range(_GEMINI_MAX_ATTEMPTS):
        try:
            return await _get_genai_client().aio.models.generate_content(model=model, contents=contents)
        except genai_errors.ClientError as exc:
            if exc.code != 429 or attempt == _GEMINI_MAX_ATTEMPTS - 1:
                raise
        except genai_errors.ServerError:
            if attempt == _GEMINI_MAX_ATTEMPTS - 1:
                raise
        await asyncio.sleep(_GEMINI_BACKOFF_BASE_SECONDS * (2 ** attempt))


# ---------------------------------------------------------------------------
# Site dictionaries — per-domain known terms, so check_grammar doesn't
# false-flag a site's own brand/event names as spelling errors
# ---------------------------------------------------------------------------

# Small file (unlike uscities.json) — read fresh every call, same as
# app.py's rules.json loading, so add_site_dictionary_term's writes are
# visible immediately with no cache-invalidation logic needed.
#
# mcp-servers/seo-testing-mcp/src/seo_testing_mcp/tools/gemini_checks.py ->
# this component's own root (mcp-servers/seo-testing-mcp/), where data/ lives.
_COMPONENT_ROOT = Path(__file__).resolve().parents[3]
_SITE_DICT_PATH = Path(os.environ.get("SITE_DICTIONARIES_PATH",
                        _COMPONENT_ROOT / "data" / "site_dictionaries.json"))


def _load_site_dictionaries() -> dict:
    return gcs_json.read_json(os.environ.get("SITE_DICTIONARIES_GCS_URI"), _SITE_DICT_PATH)


def _site_dictionary_terms(url: str) -> list[str]:
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    return _load_site_dictionaries().get(domain, [])


async def add_site_dictionary_term(domain: str, term: str) -> dict:
    """Add a term to a domain's grammar-check dictionary so
    content_check_grammar won't flag it as a spelling error — for a
    site's own brand names, event names, or other proper nouns.

    Only writes to GCS — there's no local-file write path, since a local
    edit on one Cloud Run instance wouldn't be visible to any other
    running instance anyway. Requires SITE_DICTIONARIES_GCS_URI to be
    configured.
    """
    gcs_uri = os.environ.get("SITE_DICTIONARIES_GCS_URI")
    if not gcs_uri:
        return {"error": "SITE_DICTIONARIES_GCS_URI is not configured — "
                          "cannot add a term without a shared GCS location."}

    domain = domain.strip().lower().removeprefix("www.")
    term = term.strip()
    site_dicts = gcs_json.read_json(gcs_uri, _SITE_DICT_PATH)
    terms = site_dicts.setdefault(domain, [])
    if term not in terms:
        terms.append(term)
    gcs_json.write_json(gcs_uri, site_dicts)
    return {"status": "ok", "domain": domain, "terms": terms}


# ---------------------------------------------------------------------------
# Grammar / syntax / polish; rules.json's "Grammar, Syntax & Polish"
# optimization has no auto_checks of its own because writing-quality issues
# aren't something a rule-based check can catch.
# ---------------------------------------------------------------------------

_GRAMMAR_LABEL = "Grammar & Syntax"

_GRAMMAR_PROMPT = """You are a strict copy editor reviewing web page content for a client QA report.

Review the text below for:
- Spelling errors
- Grammatical errors
- Repetitive sentence starters (e.g. 3+ consecutive sentences or paragraphs starting with the same word, like "The...")

Ignore formatting artifacts left over from HTML text extraction (extra whitespace, stray navigation/menu text) — focus only on genuine writing-quality issues in the actual prose.
{known_terms_section}
Respond with ONLY a JSON object, no markdown code fences, in exactly this shape:
{{"status": "pass", "issues": []}}
or
{{"status": "fail", "issues": ["short description of issue", ...]}}

Use "pass" only if you find zero genuine spelling/grammar errors and no repetitive sentence-starter pattern. List at most 5 issues, most significant first.

PAGE TEXT:
{text}
"""


def _parse_grammar_response(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned.strip())


async def check_grammar(url: str) -> list[dict]:
    """Fetch a page and run a Gemini spelling/grammar/polish review over its
    main content text."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    text = main_content_text(p)
    if not text or not text.strip():
        return [_r(_GRAMMAR_LABEL, "warn", "No page text available to review")]

    known_terms = _site_dictionary_terms(url)
    known_terms_section = ""
    if known_terms:
        known_terms_section = (
            "\nDo NOT flag any of the following as spelling errors — they are "
            "this site's own brand names, event names, or other proper nouns, "
            "not typos: " + ", ".join(known_terms) + "\n"
        )

    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    try:
        response = await _generate_content_with_retry(
            model, _GRAMMAR_PROMPT.format(text=text[:4000], known_terms_section=known_terms_section),
        )
        parsed = _parse_grammar_response(response.text or "")
    except Exception as exc:
        return [_r(_GRAMMAR_LABEL, "warn", f"Grammar check unavailable: {exc}")]

    status = "pass" if parsed.get("status") == "pass" else "fail"
    issues = [str(i) for i in (parsed.get("issues") or [])][:5]
    detail = "No spelling/grammar issues found" if status == "pass" else ("; ".join(issues) or "Issues found")
    return [_r(_GRAMMAR_LABEL, status, detail)]


# ---------------------------------------------------------------------------
# Per-page recommendations (key_issues / recommended_fixes)
# ---------------------------------------------------------------------------

_RECOMMENDATIONS_PROMPT = """You are writing a short QA summary for one page in an SEO audit report.

Page: {url}

Automated checks found these issues on this page (passing checks are omitted — nothing to say about those):
{issues_text}

Respond with ONLY a JSON object, no markdown code fences, in exactly this shape:
{{"key_issues": "1. ...\\n2. ...", "recommended_fixes": "1. ...\\n2. ..."}}

key_issues: a numbered list (one string, newline-separated) summarizing the issues above in plain language, most important first.
recommended_fixes: a numbered list (one string, newline-separated) of concrete fixes, one per issue, same order as key_issues.

One concise sentence per item. Do not invent issues beyond what's listed above.
"""


def _parse_recommendations_response(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned.strip())


async def generate_recommendations(url: str, checks: list[dict]) -> dict:
    """Ask Gemini for a short key_issues/recommended_fixes summary for one
    page, given only its own failing/warning checks.

    Deliberately page-specific (one page per call) and as small as possible
    (passing checks are dropped — they add nothing to summarize) rather than
    handing an LLM the whole batch's results to reproduce, which is what
    made generate_report's function-call arguments large and fragile
    enough to trip Gemini's MALFORMED_FUNCTION_CALL on real client batches.

    Returns {"key_issues": "", "recommended_fixes": ""} without calling
    Gemini at all when nothing failed or warned — most pages in a batch
    pass everything, so this skips the LLM call entirely for those.
    """
    problem_checks = [c for c in checks if c.get("status") in ("fail", "warn")]
    if not problem_checks:
        return {"key_issues": "", "recommended_fixes": ""}

    issues_text = "\n".join(
        f'- [{c.get("status", "").upper()}] {c.get("label", "")}: {c.get("detail", "")}'
        for c in problem_checks
    )

    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    try:
        response = await _generate_content_with_retry(
            model, _RECOMMENDATIONS_PROMPT.format(url=url, issues_text=issues_text),
        )
        parsed = _parse_recommendations_response(response.text or "")
    except Exception as exc:
        return {"key_issues": f"Recommendations unavailable: {exc}", "recommended_fixes": ""}

    return {
        "key_issues": str(parsed.get("key_issues", "")).strip(),
        "recommended_fixes": str(parsed.get("recommended_fixes", "")).strip(),
    }
