from __future__ import annotations

from .url_safety import UnsafeURLError, assert_safe_url

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT = 10

_EMPTY_CURRENT_VALUES = {"title": "", "meta_description": "", "h1": ""}


async def fetch_current_page_values(url: str) -> dict[str, str]:
    """Best-effort fetch of a live page's current title/meta description/H1
    — shown as hint text on the corresponding new-value fields in the
    page-fields dialog step (see dialog_cards.build_url_entry_dialog), so a
    specialist doesn't have to separately look up and paste in what's
    already there.

    Deliberately simpler than seo-testing-mcp's fetch_parsed (no bot-
    mitigation header-profile retries, no caching): this is a UI
    convenience, not a pass/fail QA check, so an occasional blank hint on a
    WAF-protected site is an acceptable trade for staying simple — the
    specialist can still type the fields in by hand either way.

    Never raises: any failure (unsafe/unresolvable URL, timeout, non-2xx,
    no matching tag) just leaves that field's value as "".
    """
    try:
        assert_safe_url(url)
    except UnsafeURLError:
        return dict(_EMPTY_CURRENT_VALUES)

    import httpx
    from bs4 import BeautifulSoup

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT) as client:
            response = await client.get(url, headers={"User-Agent": _USER_AGENT})
        if response.status_code >= 400:
            return dict(_EMPTY_CURRENT_VALUES)
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception:
        return dict(_EMPTY_CURRENT_VALUES)

    title = soup.title.get_text(strip=True) if soup.title else ""

    meta_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = (meta_tag.get("content") or "").strip() if meta_tag else ""

    h1_tag = soup.find("h1")
    h1 = h1_tag.get_text(strip=True) if h1_tag else ""

    return {"title": title, "meta_description": meta_description, "h1": h1}
