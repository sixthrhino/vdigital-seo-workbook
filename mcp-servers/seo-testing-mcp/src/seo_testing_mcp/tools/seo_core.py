"""
Core on-page SEO checks: title, meta description, H1, headings, keywords,
canonical, schema, and Open Graph / Twitter tags.

All check functions return list[dict] where each dict has:
  label  : str   — check name
  status : str   — "pass" | "fail" | "warn" | "info"
  detail : str   — explanation / evidence
"""

from __future__ import annotations

import json
import re

from .fetcher import FetchResult, body_text_excluding_chrome, fetch_parsed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# CMSes, Google Docs, and copy tools routinely convert straight quotes/
# apostrophes into "smart" curly ones (or vice versa) somewhere between where
# an expected title/meta/H1 value was typed and what actually renders on the
# live page — e.g. "Women's" vs "Women's". These look byte-for-byte identical
# to a human reading a screenshot but fail a raw comparison, producing a
# false mismatch on copy that's actually correct.
_SMART_PUNCT_MAP = str.maketrans({
    "‘": "'", "’": "'",   # left/right single quotation mark (curly apostrophes)
    "‚": "'", "′": "'",   # single low-9 quote, prime
    "“": '"', "”": '"',   # left/right double quotation mark
    "„": '"', "″": '"',   # double low-9 quote, double prime
    "–": "-", "—": "-",   # en dash, em dash
    " ": " ",                  # non-breaking space
})


def _normalize_for_exact_match(text: str) -> str:
    """Normalize smart punctuation and whitespace before exact-match
    comparisons. Only for comparison — callers should keep displaying the
    original text, not this normalized form."""
    if not text:
        return text
    return re.sub(r"\s+", " ", text.translate(_SMART_PUNCT_MAP)).strip()


def _normalize_heading_text(text: str) -> str:
    """Case- and punctuation-insensitive normalization for heading-text
    comparisons — strips everything but letters/digits/whitespace after
    lowercasing and smart-punctuation normalization, so a trailing colon,
    curly apostrophe, or a symbol like "®" doesn't cause a false mismatch
    between opt_note copy and what's actually live on the page."""
    text = _normalize_for_exact_match(text).lower()
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _fuzzy(a: str, b: str, threshold: float = 0.85) -> bool:
    import difflib
    a = _normalize_for_exact_match(a).lower()
    b = _normalize_for_exact_match(b).lower()
    if not a or not b:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def _strip_volume(kw: str) -> str:
    return re.sub(r"\s*\(\d+\.?\d*[kK]?\)\s*$", "", kw).strip()


def _normalize_kw(text: str) -> str:
    # "&"/"and" and hyphen-vs-space are used interchangeably in titles,
    # headings, and keywords (e.g. "Butler & Montgomery" vs "Butler and
    # Montgomery", "Roll-Up Door" vs "Roll Up Door") — normalizing both
    # lets phrase/keyword comparisons match across either spelling instead
    # of falling through to a bag-of-words proximity match (or missing
    # entirely) on a real match that's just spelled differently. Covers the
    # ASCII hyphen plus common unicode variants (non-breaking hyphen, en
    # dash, em dash, minus sign) CMSes and copy tools like to substitute in.
    text = re.sub(r"\s*&\s*", " and ", text.lower())
    text = re.sub(r"[-‐‑‒–—−]", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _word_variants(word: str) -> set[str]:
    variants = {word}
    if word.endswith("s") and len(word) > 3:
        variants.add(word[:-1])
    else:
        variants.add(word + "s")
    return variants


def _proximity_match(kw: str, text: str) -> bool:
    words = [w for w in _normalize_kw(kw).split() if len(w) >= 3]
    if not words:
        return False
    text_words = set(_normalize_kw(text).split())
    return all(_word_variants(w).intersection(text_words) for w in words)


# ---------------------------------------------------------------------------
# Tiered phrase matching for check_keywords: exact (plural-tolerant, word
# order preserved) -> fuzzy (same, but tolerates up to 2 filler words
# inserted between keyword words) -> loose bag-of-words (order-independent,
# always a warn — real evidence but the weakest tier). Real English plural
# morphology, not just a trailing "s" — a keyword like "company" needs to
# match "companies" on the page (and vice versa) just as reliably as
# "award" matches "awards".
# ---------------------------------------------------------------------------

def _plural_tolerant_word(word: str) -> str:
    lw = word.lower()
    if lw.endswith("ies") and len(lw) > 3:
        # "companies" -> "compan" + (?:y|ies)
        return re.escape(word[:-3]) + r"(?:y|ies)"
    if lw.endswith("y") and len(lw) > 1 and lw[-2] not in "aeiou":
        # "company" -> "compan" + (?:y|ies)
        return re.escape(word[:-1]) + r"(?:y|ies)"
    if lw.endswith("es") and lw[:-2].endswith(("s", "x", "z", "ch", "sh")):
        # "boxes" -> "box" + (?:es)?
        return re.escape(word[:-2]) + r"(?:es)?"
    if lw.endswith("s") and not lw.endswith("ss"):
        # "awards" <-> "award"
        return re.escape(word[:-1]) + "s?"
    if lw.endswith(("s", "x", "z", "ch", "sh")):
        # singular that would take "es" in its plural form, e.g. "box" -> "boxes"
        return re.escape(word) + "(?:es)?"
    return re.escape(word) + "s?"


def _exact_phrase_pattern(phrase: str) -> str | None:
    words = re.findall(r"\w+", phrase)
    if not words:
        return None
    return r"\b" + r"\s+".join(_plural_tolerant_word(w) for w in words) + r"\b"


def _fuzzy_phrase_pattern(phrase: str, max_filler_words: int = 2) -> str | None:
    words = re.findall(r"\w+", phrase)
    if len(words) < 2:
        return None
    parts = [_plural_tolerant_word(words[0])]
    for w in words[1:]:
        parts.append(rf"(?:\s+\S+){{0,{max_filler_words}}}\s+{_plural_tolerant_word(w)}")
    return r"\b" + "".join(parts) + r"\b"


_KEYWORD_STOPWORDS = {"a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "with", "at", "by"}


def _significant_words(phrase: str) -> list[str]:
    words = [w for w in re.findall(r"\w+", phrase) if w.lower() not in _KEYWORD_STOPWORDS]
    return words or re.findall(r"\w+", phrase)


def _r(label: str, status: str, detail: str) -> dict:
    return {"label": label, "status": status, "detail": detail}


# ---------------------------------------------------------------------------
# Title tag
# ---------------------------------------------------------------------------

async def check_title(url: str, expected: str | None = None) -> list[dict]:
    """Check the <title> tag — existence, length, and optional fuzzy comparison."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    results = []
    if p.status_code != 200:
        results.append(_r("Page Load", "fail", f"HTTP {p.status_code}"))
    else:
        results.append(_r("Page Load", "pass", "HTTP 200"))

    tag = p.soup.find("title") if p.soup else None
    live = tag.get_text(strip=True) if tag else ""

    if not live:
        return results + [_r("Title Tag", "fail", "No <title> tag found")]

    # Length check (strip brand suffix for fair character count)
    brand_stripped = re.split(r"\s*[\|–—\-]\s*[^|\-–—]+$", live)[0].strip()
    length = len(brand_stripped)
    if length > 60:
        results.append(_r("Title Length", "warn", f"{length} chars (excl. brand) — aim for under 60"))
    else:
        results.append(_r("Title Length", "pass", f"{length} chars (excl. brand)"))

    if expected:
        expected = expected.strip()
        if _fuzzy(live, expected):
            results.append(_r("Title Tag", "pass", f'"{live}"'))
        elif _normalize_for_exact_match(expected).lower() in _normalize_for_exact_match(live).lower():
            results.append(_r("Title Tag", "pass", f'"{live}" — expected text present (extra content added)'))
        else:
            results.append(_r("Title Tag", "fail",
                               f'Expected: "{expected}"\n     Live: "{live}"'))
    else:
        results.append(_r("Title Tag", "info", f'No new title planned — live: "{live}"'))

    return results


# ---------------------------------------------------------------------------
# Meta description
# ---------------------------------------------------------------------------

async def check_meta_description(url: str, expected: str | None = None) -> list[dict]:
    """Check meta description — existence, length, CTA presence, comparison."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    results = []
    tag = p.soup.find("meta", {"name": re.compile(r"^description$", re.I)}) if p.soup else None
    live = tag.get("content", "").strip() if tag else ""

    if not live:
        return results + [_r("Meta Description", "fail", "Missing meta description")]

    length = len(live)
    if length < 120:
        results.append(_r("Meta Length", "warn", f"{length} chars — aim for 120–160"))
    elif length > 160:
        results.append(_r("Meta Length", "warn", f"{length} chars — may truncate in SERPs"))
    else:
        results.append(_r("Meta Length", "pass", f"{length} chars"))

    cta_words = ["call", "contact", "get", "request", "schedule", "learn",
                 "discover", "find", "start", "try"]
    if any(w in live.lower() for w in cta_words):
        results.append(_r("Meta CTA", "pass", "Action-oriented language detected"))
    else:
        results.append(_r("Meta CTA", "warn", "No clear CTA detected"))

    if expected:
        expected = expected.strip()
        live_lc, exp_lc = _normalize_for_exact_match(live).lower(), _normalize_for_exact_match(expected).lower()
        if _fuzzy(live, expected):
            results.append(_r("Meta Description", "pass", f'"{live}"'))
        elif exp_lc in live_lc:
            extra = live[len(expected):].strip()
            results.append(_r("Meta Description", "pass",
                               f'"{live}" — expected text present' + (f'; extra: "{extra}"' if extra else "")))
        elif expected:
            results.append(_r("Meta Description", "fail",
                               f'Expected: "{expected}"\n     Live: "{live}"'))
    else:
        results.append(_r("Meta Description", "info",
                           f'No new meta description planned — live: "{live[:120]}{"..." if len(live) > 120 else ""}"'))

    return results


async def get_title_meta(url: str) -> dict:
    """Fetch a page's raw <title> text and meta description content.

    Unlike check_title/check_meta_description this isn't a pass/fail check
    against an expected value — it's the two raw live strings, for a caller
    doing its own cross-page comparison (e.g. duplicate-title detection
    across a batch of pages, where the comparison only makes sense once
    every page's actual live value is known).
    """
    p = await fetch_parsed(url)
    if p.error:
        return {"url": url, "title": "", "meta_description": "", "error": p.error}

    title_tag = p.soup.find("title") if p.soup else None
    title = title_tag.get_text(strip=True) if title_tag else ""

    meta_tag = p.soup.find("meta", {"name": re.compile(r"^description$", re.I)}) if p.soup else None
    meta_description = meta_tag.get("content", "").strip() if meta_tag else ""

    return {"url": url, "title": title, "meta_description": meta_description, "error": None}


# ---------------------------------------------------------------------------
# H1
# ---------------------------------------------------------------------------

async def check_h1(url: str, expected: str | None = None) -> list[dict]:
    """Check H1 — count, length, and optional comparison."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    results = []
    h1s = p.soup.find_all("h1") if p.soup else []

    if not h1s:
        return results + [_r("H1 Tag", "fail", "No H1 found on page")]

    if len(h1s) > 1:
        results.append(_r("H1 Count", "warn", f"{len(h1s)} H1 tags found — should be exactly 1"))
    else:
        results.append(_r("H1 Count", "pass", "Exactly 1 H1"))

    live = h1s[0].get_text(strip=True)
    length = len(live)
    results.append(_r("H1 Length", "pass" if length <= 70 else "warn",
                       f"{length} chars" + (" — aim for under 70" if length > 70 else "")))

    if expected:
        expected = expected.strip()
        if _fuzzy(live, expected) or _normalize_for_exact_match(expected).lower() in _normalize_for_exact_match(live).lower():
            results.append(_r("H1 Tag", "pass", f'"{live}"'))
        else:
            results.append(_r("H1 Tag", "fail",
                               f'Expected: "{expected}"\n     Live: "{live}"'))
    else:
        results.append(_r("H1 Tag", "info", f'No new H1 planned — live: "{live}"'))

    return results


# ---------------------------------------------------------------------------
# Heading hierarchy
# ---------------------------------------------------------------------------

async def check_heading_hierarchy(
    url: str,
    expected_headings: str | None = None,
    old_headings: str | None = None,
) -> list[dict]:
    """Validate H1-H4 hierarchy.

    expected_headings / old_headings: multi-line strings in the format
      <H1> Some title
      <H2> Subtitle
    Used for opts_qa style old-vs-new heading comparison.
    """
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    results = []
    headings = [(t.name, t.get_text(strip=True))
                for t in p.soup.find_all(["h1", "h2", "h3", "h4"])] if p.soup else []

    def parse_spec(spec: str | None) -> list[tuple[str, str]]:
        out = []
        if not spec:
            return out
        for line in spec.strip().splitlines():
            m = re.match(r"<(H[1-4])>\s*(.+)", line.strip(), re.I)
            if m:
                out.append((m.group(1).lower(), m.group(2).strip()))
        return out

    if expected_headings:
        expected = parse_spec(expected_headings)
        old = parse_spec(old_headings)
        # Case- and punctuation-insensitive: opt_note copy and the live page
        # routinely differ on trailing colons/periods, curly vs. straight
        # apostrophes, and stray symbols (e.g. "GI Bill®") that don't change
        # whether the heading is actually the one that was asked for.
        live_lookup: dict[str, list[tuple[str, str]]] = {}
        for lvl, txt in headings:
            live_lookup.setdefault(lvl, []).append((_normalize_heading_text(txt), txt))

        h1_texts = [t for lvl, t in headings if lvl == "h1"]
        if len(h1_texts) == 1:
            results.append(_r("H1", "pass", f'"{h1_texts[0][:80]}"'))
        elif not h1_texts:
            results.append(_r("H1", "fail", "No H1 found"))
        else:
            results.append(_r("H1", "warn", f"{len(h1_texts)} H1s found"))

        safely_paired = len(old) == len(expected)
        for idx, (lvl, txt) in enumerate(expected):
            candidates = live_lookup.get(lvl, [])
            norm_expected = _normalize_heading_text(txt)
            if any(norm_expected == norm for norm, _ in candidates):
                results.append(_r(f"<{lvl.upper()}> Match", "pass", f'"{txt}" confirmed'))
            else:
                live_at = "; ".join(orig for _, orig in candidates) or "(none found)"
                old_pair = old[idx] if safely_paired and idx < len(old) else None
                old_still = (old_pair and old_pair[0] == lvl
                             and any(_normalize_heading_text(old_pair[1]) == norm for norm, _ in candidates))
                if old_still:
                    results.append(_r(f"<{lvl.upper()}> Match", "fail",
                                       f'Old heading still live: "{old_pair[1]}" — new: "{txt}"'))
                else:
                    results.append(_r(f"<{lvl.upper()}> Match", "fail",
                                       f'"{txt}" not found. Live {lvl.upper()}s: {live_at}'))
    else:
        h1s = [t for l, t in headings if l == "h1"]
        h2s = [t for l, t in headings if l == "h2"]
        if len(h1s) == 1:
            results.append(_r("H1", "pass", f'"{h1s[0][:80]}"'))
        elif not h1s:
            results.append(_r("H1", "fail", "No H1 found"))
        else:
            results.append(_r("H1", "warn", f"{len(h1s)} H1s found"))

        results.append(_r("H2s", "pass" if h2s else "warn",
                           "; ".join(t[:50] for t in h2s) if h2s else "No H2 tags found"))

    # Hierarchy violations
    level_map = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
    violations = []
    prev = 0
    for lvl, txt in headings:
        n = level_map.get(lvl, 0)
        if prev > 0 and n > prev + 1:
            violations.append(f"H{prev}→H{n}: \"{txt[:40]}\"")
        prev = n
    results.append(_r("Hierarchy", "warn" if violations else "pass",
                       ("Skipped levels: " + "; ".join(violations)) if violations
                       else "No skipped heading levels"))
    return results


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

async def check_keywords(
    url: str,
    primary_keyword: str,
    secondary_keywords: str | None = None,
    doc_text: str | None = None,
) -> list[dict]:
    """Check focus + secondary keywords in title, H1, meta, body, and optional doc text."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    results = []
    soup = p.soup

    title_text = soup.find("title").get_text(strip=True).lower() if soup and soup.find("title") else ""
    h1_tags = soup.find_all("h1") if soup else []
    h1_text = " ".join(h.get_text(strip=True) for h in h1_tags).lower()
    meta_tag = soup.find("meta", {"name": re.compile(r"^description$", re.I)}) if soup else None
    meta_text = (meta_tag.get("content", "") if meta_tag else "").lower()
    # Excludes nav/header/footer — a keyword sitting only in the site's nav
    # menu or footer boilerplate isn't genuine on-page optimization, and
    # counting it there would produce a false "found in body" pass.
    body_text = body_text_excluding_chrome(p).lower()

    def _check_kw(kw: str, label: str) -> list[dict]:
        kw = _strip_volume(kw).strip()
        if not kw:
            return []

        locations = {"Title": title_text, "H1": h1_text, "Meta": meta_text}
        norm_locations = {name: _normalize_kw(txt) for name, txt in locations.items()}
        norm_body = _normalize_kw(body_text)

        # "/" is "this OR that" shorthand (e.g. a staff-announcement keyword
        # like "Stephen Roller / Primary Health Solutions" — the person's
        # name is what should appear, not both halves glued into one
        # literal phrase). Each alternative is evaluated independently and
        # the best result wins.
        alternatives = [a.strip() for a in kw.split("/") if a.strip()] if "/" in kw else [kw]

        exact_loc = fuzzy_loc = fuzzy_snippet = loose_loc = None
        loose_missing: list[str] | None = None
        body_found = False
        matched_alt = kw

        for alt in alternatives:
            alt_lc = _normalize_kw(alt)
            exact_re = _exact_phrase_pattern(alt_lc)
            if exact_re and re.search(exact_re, norm_body):
                body_found = True

            if not exact_loc:
                a_loc = next((name for name, txt in norm_locations.items()
                             if exact_re and re.search(exact_re, txt)), None)
                if a_loc:
                    exact_loc, matched_alt = a_loc, alt

            if not exact_loc and not fuzzy_loc:
                fuzzy_re = _fuzzy_phrase_pattern(alt_lc)
                if fuzzy_re:
                    for name, txt in norm_locations.items():
                        m = re.search(fuzzy_re, txt)
                        if m:
                            fuzzy_loc, fuzzy_snippet, matched_alt = name, m.group(0), alt
                            break

            if not exact_loc and not fuzzy_loc and not loose_loc:
                sig_words = _significant_words(alt_lc)
                if len(sig_words) >= 2:
                    combined = " ".join(norm_locations.values()) + " " + norm_body
                    missing = [w for w in sig_words
                              if not re.search(r"\b" + _plural_tolerant_word(w) + r"\b", combined)]
                    if not missing:
                        loose_loc, matched_alt = "Title/H1/Meta/Body (combined)", alt
                    elif len(missing) <= max(1, len(sig_words) // 3):
                        loose_loc, loose_missing, matched_alt = "Title/H1/Meta/Body (combined)", missing, alt

            if exact_loc:
                break

        found_any = exact_loc or fuzzy_loc or loose_loc
        body_note = (" Also in body." if body_found else " Not in body.") if found_any else (
            " Found in body." if body_found else " Not found anywhere.")
        alt_note = f' (matched via "{matched_alt}")' if matched_alt != kw and len(alternatives) > 1 else ""

        out = []
        if exact_loc:
            out.append(_r(label, "pass", f'"{kw}" found as the exact phrase in {exact_loc}{alt_note}.{body_note}'))
        elif fuzzy_loc:
            out.append(_r(label, "warn",
                           f'"{kw}" not found as an exact phrase, but a close match was found in '
                           f'{fuzzy_loc}{alt_note}: "{fuzzy_snippet}". Words are all present and in order, just '
                           f'split apart by extra wording — confirm it still reads naturally.{body_note}'))
        elif loose_loc:
            if loose_missing:
                out.append(_r(label, "warn",
                               f'"{kw}" not found as a phrase, but most of its key words appear on the page '
                               f'(missing: {", ".join(loose_missing)}) — likely present in different '
                               f'wording/order. Verify manually.{body_note}'))
            else:
                out.append(_r(label, "warn",
                               f'"{kw}" not found as a phrase, but all of its key words appear on the page in '
                               f'some form — likely present in reordered/reworded copy rather than a true miss. '
                               f'Verify manually.{body_note}'))
        else:
            out.append(_r(label, "fail",
                           f'"{kw}" not found as the exact phrase (or a close variant) in Title, H1, or Meta '
                           f'Description.{body_note}'))

        if doc_text:
            kw_lc = _normalize_kw(kw)
            doc_lc = _normalize_kw(doc_text)
            doc_found = kw_lc in doc_lc or _proximity_match(kw, doc_text)
            out.append(_r(f"{label} (doc)", "pass" if doc_found else "fail",
                           f'"{kw}" {"found" if doc_found else "not found"} in doc'))
        return out

    results.extend(_check_kw(primary_keyword, "Primary Keyword"))

    if secondary_keywords:
        for kw in re.split(r"[,\n]", secondary_keywords):
            kw = _strip_volume(kw.strip())
            if kw:
                results.extend(_check_kw(kw, f'Secondary KW: "{kw}"'))

    return results


# ---------------------------------------------------------------------------
# Canonical
# ---------------------------------------------------------------------------

async def check_canonical(url: str) -> list[dict]:
    """Check canonical tag — existence, self-canonical, staging leak."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    tag = p.soup.find("link", rel="canonical") if p.soup else None
    if not tag:
        return [_r("Canonical", "fail", "No canonical tag found")]

    href = tag.get("href", "").strip()
    if not href:
        return [_r("Canonical", "fail", "Canonical tag present but href is empty")]

    results = []
    if href.rstrip("/") == url.rstrip("/"):
        results.append(_r("Canonical", "pass", f"Self-canonicalized: {href}"))
    else:
        results.append(_r("Canonical", "warn", f"Points to: {href} — verify this is intentional"))

    staging = ["staging.", "stage.", "dev.", "test.", "localhost"]
    if any(s in href.lower() for s in staging):
        results.append(_r("Canonical Staging", "fail", "Canonical references a staging URL!"))

    return results


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _get_schema_nodes(soup) -> list[dict]:
    """Return every JSON-LD node that has an @type, flattening @graph wrappers."""
    nodes: list[dict] = []

    def extract(node):
        if isinstance(node, dict):
            if node.get("@type"):
                nodes.append(node)
            for item in node.get("@graph", []):
                extract(item)
        elif isinstance(node, list):
            for item in node:
                extract(item)

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if raw and raw.strip():
            try:
                extract(json.loads(raw))
            except Exception:
                pass
    return nodes


def _node_type_str(node: dict) -> str:
    t = node.get("@type")
    return t if isinstance(t, str) else ", ".join(t)


def _node_types(node: dict) -> list[str]:
    t = node.get("@type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    return []


def _get_schema_types(soup) -> list[str]:
    return [_node_type_str(n) for n in _get_schema_nodes(soup)]


# Properties Google's structured-data guidelines treat as required (or
# effectively required for eligibility) per schema type — presence-only
# check, not full schema.org/Rich-Results validation. A page can pass
# "Schema Types" (the type exists) and still fail here (the type is missing
# the fields that actually make it useful).
_REQUIRED_PROPERTIES: dict[str, list[str]] = {
    "LocalBusiness": ["name", "address", "telephone"],
    "ProfessionalService": ["name", "address", "telephone"],
    "HomeAndConstructionBusiness": ["name", "address", "telephone"],
    "Plumber": ["name", "address", "telephone"],
    "HVACBusiness": ["name", "address", "telephone"],
    "Electrician": ["name", "address", "telephone"],
    "Service": ["name"],
    "Article": ["headline", "datePublished", "author"],
    "BlogPosting": ["headline", "datePublished", "author"],
    "NewsArticle": ["headline", "datePublished", "author"],
    "TechArticle": ["headline", "datePublished", "author"],
    "FAQPage": ["mainEntity"],
    "VideoObject": ["name", "description", "thumbnailUrl", "uploadDate"],
    "HowTo": ["name", "step"],
}


def _missing_required_properties(node: dict) -> dict[str, list[str]]:
    """Return {type_name: [missing properties]} for each recognized type on this node."""
    missing: dict[str, list[str]] = {}
    for type_name in _node_types(node):
        required = _REQUIRED_PROPERTIES.get(type_name)
        if not required:
            continue
        gaps = [prop for prop in required if not node.get(prop)]
        if gaps:
            missing[type_name] = gaps
    return missing


async def check_schema(url: str) -> list[dict]:
    """Detect JSON-LD schema types, flag missing blog/service page schemas,
    and flag recognized types that are missing required properties (a type
    can be present and still be incomplete/ineligible for rich results)."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    nodes = _get_schema_nodes(p.soup) if p.soup else []
    types = [_node_type_str(n) for n in nodes]

    if not types:
        return [_r("Schema", "warn", "No JSON-LD schema found")]

    results = [_r("Schema Types", "pass", ", ".join(types))]

    is_blog = "/blog/" in url.lower()
    if is_blog:
        blog_types = {"BlogPosting", "Article", "NewsArticle", "TechArticle"}
        matched = [t for t in types if t in blog_types]
        results.append(_r("Blog Schema", "pass" if matched else "warn",
                           f"Found: {', '.join(matched)}" if matched
                           else "No BlogPosting/Article schema — recommended for blog posts"))
    else:
        service_types = {"Service", "LocalBusiness", "FAQPage", "ProfessionalService",
                         "HomeAndConstructionBusiness", "Plumber", "HVACBusiness", "Electrician"}
        matched = [t for t in types if t in service_types]
        if not matched:
            results.append(_r("Service Schema", "warn",
                               f"No service-page schema found — types present: {', '.join(types)}"))

    if "FAQPage" in types:
        results.append(_r("FAQPage Schema", "pass", "FAQPage schema detected"))
    if "VideoObject" in types:
        results.append(_r("VideoObject Schema", "pass", "VideoObject schema detected"))

    for node in nodes:
        for type_name, missing_props in _missing_required_properties(node).items():
            plural = "y" if len(missing_props) == 1 else "ies"
            results.append(_r(
                f"{type_name} Schema Completeness", "fail",
                f"Missing required propert{plural}: {', '.join(missing_props)}",
            ))

    return results


# ---------------------------------------------------------------------------
# Open Graph / Twitter
# ---------------------------------------------------------------------------

async def check_og_twitter(url: str) -> list[dict]:
    """Check og:title, og:description, og:image and twitter:card."""
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    soup = p.soup
    og_keys = ["og:title", "og:description", "og:image"]
    og = {k: soup.find("meta", property=k) for k in og_keys} if soup else {}
    tw_keys = ["twitter:card", "twitter:title"]
    tw = {k: soup.find("meta", attrs={"name": k}) for k in tw_keys} if soup else {}

    missing_og = [k for k, v in og.items() if not v]
    missing_tw = [k for k, v in tw.items() if not v]

    return [
        _r("Open Graph", "warn" if missing_og else "pass",
           f"Missing: {', '.join(missing_og)}" if missing_og
           else "og:title, og:description, og:image all present"),
        _r("Twitter/X Tags", "warn" if missing_tw else "pass",
           f"Missing: {', '.join(missing_tw)}" if missing_tw
           else "twitter:card present"),
    ]
