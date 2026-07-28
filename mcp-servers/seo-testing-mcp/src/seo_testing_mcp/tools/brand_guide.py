"""
Brand guide parsing and checks: turning raw pasted/workbook brand guide
text into structured JSON, checking a live page against it (branding
tokens, CTA URLs/phone, negative words), and the guide-level reminders
(voice/tone, writing rules, imaging) that apply once per batch rather
than once per page.
"""

from __future__ import annotations

import re

from .fetcher import fetch_parsed


def _r(label: str, status: str, detail: str) -> dict:
    return {"label": label, "status": status, "detail": detail}


# ---------------------------------------------------------------------------
# Brand guide parser
# ---------------------------------------------------------------------------

def parse_brand_guide(raw: str) -> dict:
    """Parse a pasted brand guide (copied from the workbook) into a structured dict.

    Handles the tab-separated two-column format used in the workbook as well as
    plain label: value lines.

    Returns a dict with keys:
      branding, cta_urls, cta_phones, voice_tone, negative_words,
      writing_rules, imaging, geo, excluded_cities, raw

    geo is a list of every Geo Targeting line (e.g. ["Tempe, AZ", "Queen
    Creek, AZ", ...]) — every line, not just the first, so it doubles as a
    usable multi-city allowlist for nationwide/multi-market clients, not
    just a single display string.
    """
    guide: dict = {
        "branding": [], "cta_urls": [], "cta_phones": [],
        "voice_tone": [], "negative_words": [], "writing_rules": [], "imaging": [],
        "geo": [], "excluded_cities": [], "raw": raw,
    }
    if not raw.strip():
        return guide

    key_map = {
        "client branding": "branding", "branding": "branding",
        "cta": "cta_urls",
        "voice & tone": "voice_tone", "voice and tone": "voice_tone", "tone": "voice_tone",
        "negative words": "negative_words", "words to avoid": "negative_words",
        "avoid words": "negative_words",
        "writing rules": "writing_rules", "writing": "writing_rules",
        "imaging": "imaging",
        "geo targeting": "geo", "geo-targeting": "geo", "geo": "geo",
    }
    skip_labels = {"website", "kick off form", "kickoff form", "audience info",
                   "about the client", "demographics"}

    current_key: str | None = None

    for line in raw.splitlines():
        line = line.strip().strip('"')
        if not line:
            current_key = None
            continue

        # CLIENT CANNOT SERVICE exclusion lines
        if re.search(r"client cannot service", line, re.I):
            before = re.split(r"[→>]", line)[0]
            for part in re.split(r"[&,]|\band\b", before, flags=re.I):
                city = re.sub(r"\b(proper|city|area|metro)\b", "", part, flags=re.I).strip()
                if city:
                    guide["excluded_cities"].append(city)
            continue

        # Tab-separated label\tvalue format
        if "\t" in line:
            parts = line.split("\t", 1)
            label_raw = parts[0].strip().lower().rstrip(":")
            value_raw = (parts[1].strip().strip('"') if len(parts) > 1 else "")

            if not label_raw:
                if current_key:
                    line = value_raw
                else:
                    _harvest(value_raw, guide)
                    continue
            else:
                matched = next((key for kw, key in key_map.items()
                                if label_raw.startswith(kw)), None)
                if matched is None:
                    if any(label_raw.startswith(s) for s in skip_labels):
                        current_key = None
                        continue
                    if not current_key:
                        continue
                    # Not a recognized label, but still inside a section —
                    # likely a continuation of a multi-line cell value, not
                    # a genuine new label\tvalue pair (Sheets/Excel copy
                    # embeds a cell's internal newlines as extra "lines"
                    # here, each still carrying that cell's trailing-tab
                    # column padding — e.g. a Geo Targeting cell listing
                    # several cities, one per line). Treat the whole line
                    # (tabs stripped) as more content for current_key
                    # instead of silently dropping it, matching the no-tab
                    # branch below, which already falls through this way.
                    line = line.replace("\t", " ").strip()
                else:
                    current_key = matched
                    if not value_raw:
                        continue
                    line = value_raw
        else:
            matched = None
            for kw, key in key_map.items():
                if line.lower().startswith(kw):
                    colon = line.find(":")
                    if 0 < colon < 35:
                        matched = key
                        current_key = key
                        line = line[colon + 1:].strip()
                        break
            if matched is None:
                if any(line.lower().startswith(s) for s in skip_labels):
                    current_key = None
                if not line:
                    continue

        if not line:
            continue

        urls = re.findall(r"https?://[^\s)]+", line)
        phones = re.findall(r"\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}", line)

        if current_key == "geo":
            if line not in guide["geo"]:
                guide["geo"].append(line)
        elif current_key == "cta_urls":
            for u in urls:
                if u not in guide["cta_urls"]:
                    guide["cta_urls"].append(u)
            for ph in phones:
                if ph not in guide["cta_phones"]:
                    guide["cta_phones"].append(ph)
            if not urls and not phones:
                guide["cta_urls"].append(line)
        elif current_key == "negative_words":
            # Split on commas so "Toxic products, low toxicity" becomes two
            # separate words/phrases to scan for individually.
            for word in line.split(","):
                word = word.strip()
                if not word:
                    continue
                # A pasted brand guide sometimes tacks a general instructional
                # note onto the end of the Negative Words block with no blank
                # line or new label in between. Real negative-word entries are
                # short phrases, so anything long enough to read like prose is
                # routed to writing_rules instead, where it surfaces as a
                # manual review reminder rather than a nonsense literal check.
                if len(word.split()) > 10:
                    if word not in guide["writing_rules"]:
                        guide["writing_rules"].append(word)
                elif word not in guide["negative_words"]:
                    guide["negative_words"].append(word)
        elif current_key and current_key != "geo":
            guide[current_key].append(line)
        else:
            _harvest(line, guide)

    return guide


def _harvest(line: str, guide: dict) -> None:
    for u in re.findall(r"https?://[^\s)]+", line):
        if u not in guide["cta_urls"]:
            guide["cta_urls"].append(u)
    for ph in re.findall(r"\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}", line):
        if ph not in guide["cta_phones"]:
            guide["cta_phones"].append(ph)


# ---------------------------------------------------------------------------
# Brand guide checks against a live URL
# ---------------------------------------------------------------------------

_BRAND_BLOCKLIST = {
    "DEFAULT", "TRUE", "FALSE", "NULL", "NONE", "NA", "TBD", "TBC",
    "URL", "CTA", "SEO", "FAQ", "HTML", "CSS", "API", "ID", "OK",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December",
}


def _hyphen_space_normalize(s: str) -> str:
    return re.sub(r"[-\s]+", " ", s.lower()).strip()


def _normalize_url_for_match(u: str) -> str:
    """Normalize a URL for comparison only (not for fetching/display).

    Strips scheme, leading "www.", trailing slash, and query/fragment, so
    equivalent URLs that differ only in http vs https, www vs non-www, or a
    trailing slash aren't treated as different links — avoids false "CTA
    link not found" results where the link is genuinely present but differs
    from the expected URL in one of these cosmetic ways.
    """
    if not u:
        return ""
    u = u.strip()
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    u = u.split("#")[0].split("?")[0]
    u = u.rstrip("/")
    return u.lower()


def _normalize_url_for_match_haystack(text: str) -> str:
    """Same normalization as _normalize_url_for_match, applied to a text blob."""
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"https?://", "", t)
    t = re.sub(r"(?:^|(?<=\s))www\.", "", t)
    return t


def _find_url_zones(url: str, soup) -> list[str]:
    zones = []
    target = _normalize_url_for_match(url)

    def href_matches(href):
        norm_href = _normalize_url_for_match(href)
        return norm_href == target or norm_href.startswith(target + "/")

    zone_map = [
        ("header", soup.find("header")),
        ("nav", soup.find("nav")),
        ("footer", soup.find("footer")),
        ("sidebar", soup.find(class_=re.compile(r"sidebar|widget-area", re.I))),
        ("content", (
            soup.find("article") or soup.find("main") or
            soup.find(class_=re.compile(
                r"entry-content|post-content|page-content|aiq-blog-content|"
                r"blog-content|article-content|post-body|entry-body|content-area|ct-text-block",
                re.I))
        )),
    ]
    for zone_name, zone_el in zone_map:
        if zone_el:
            for a in zone_el.find_all("a", href=True):
                if href_matches(a["href"]):
                    zones.append(zone_name)
                    break
    if not zones:
        for a in soup.find_all("a", href=True):
            if href_matches(a["href"]):
                zones.append("page")
                break
    return zones


async def check_brand_guide(url: str, guide: dict) -> list[dict]:
    """Check a live page against an already-parsed brand guide (see
    parse_brand_guide) — call that first on raw pasted text if you don't
    already have the structured dict.

    Checks: client branding tokens, CTA URLs, phone numbers, and negative
    words. Guide-level (non-page-specific) reminders like voice/tone and
    writing rules are NOT included here — see brand_guide_manual_notes,
    which only needs calling once per batch rather than once per URL.
    """
    p = await fetch_parsed(url)
    if p.error:
        return [_r("Page Load", "fail", p.error)]

    results = []
    visible = p.body_text
    soup = p.soup

    is_blog = "/blog/" in url.lower()

    # Branding tokens
    for rule in guide["branding"]:
        tokens = re.findall(r"[A-Z][a-zA-Z]*[A-Z][a-zA-Z]*", rule)
        tokens = [t for t in tokens if t not in _BRAND_BLOCKLIST and len(t) >= 4]
        if tokens:
            for token in tokens:
                if token in visible:
                    results.append(_r(f'Branding: "{token}"', "pass",
                                       f'Correct branding found in live content'))
                else:
                    wrong = [m for m in re.findall(re.escape(token.lower()), visible, re.I)
                             if m != token]
                    if wrong:
                        results.append(_r(f'Branding: "{token}"', "fail",
                                           f'Possible misspelling(s): {", ".join(set(wrong[:3]))}'))
                    else:
                        results.append(_r(f'Branding: "{token}"', "warn",
                                           f'"{token}" not found — verify manually'))
        else:
            results.append(_r("Branding Rule", "warn", f"Manual check: {rule}"))

    # CTA URLs
    if not is_blog:
        for cta in guide["cta_urls"]:
            if cta.startswith("http") and soup:
                zones = _find_url_zones(cta, soup)
                if zones:
                    status = "pass" if "content" in zones else "warn"
                    results.append(_r("CTA URL", status,
                                       f'"{cta}" found in: {", ".join(zones)}'))
                else:
                    results.append(_r("CTA URL", "warn", f'"{cta}" not found on page'))
            elif cta.startswith("http"):
                found = _normalize_url_for_match(cta) in _normalize_url_for_match_haystack(visible)
                results.append(_r("CTA URL", "pass" if found else "warn",
                                   f'"{cta}" {"found" if found else "not found"}'))
            else:
                results.append(_r("CTA Instruction", "warn", f"Manual check: {cta}"))

    # Phone numbers
    if not is_blog:
        for phone in guide["cta_phones"]:
            digits = re.sub(r"\D", "", phone)
            vis_digits = re.sub(r"\D", "", visible)
            results.append(_r("CTA Phone", "pass" if digits in vis_digits else "warn",
                               f'"{phone}" {"found" if digits in vis_digits else "not found"}'))

    # Negative words — automated scan. A hit isn't an automatic fail (e.g.
    # "non-toxic" contains "toxic" but may be exactly the safe phrasing the
    # client wants), so it's flagged as a warn for manual review rather than
    # a hard fail. Hyphens and spaces are treated as equivalent on both
    # sides, so a configured phrase like "low toxicity" also catches
    # "low-toxicity" in the content.
    text_norm_hs = _hyphen_space_normalize(visible)
    for word in guide["negative_words"]:
        if _hyphen_space_normalize(word) in text_norm_hs:
            results.append(_r(f'Negative Word: "{word}"', "warn",
                               f'"{word}" appears in content — verify the surrounding context '
                               f'isn\'t exactly the safer phrasing the brand guide wants (e.g. '
                               f'"non-" or "low-" prefixed usage), rather than the flagged claim '
                               f'on its own'))
        else:
            results.append(_r(f'Negative Word: "{word}"', "pass",
                               f'"{word}" not found in content'))

    return results


def brand_guide_manual_notes(guide: dict) -> list[str]:
    """Guide-level manual-review reminders (voice/tone, writing rules,
    imaging) — these don't vary by page, so unlike check_brand_guide's
    output they're meant to be surfaced once per batch, not once per URL.
    """
    notes = []
    if guide.get("voice_tone"):
        notes.append(f"Voice & Tone — should be: {', '.join(guide['voice_tone'])}")
    for rule in guide.get("writing_rules", []):
        notes.append(f"Writing Rule: {rule}")
    for note in guide.get("imaging", []):
        notes.append(f"Imaging: {note}")
    return notes
