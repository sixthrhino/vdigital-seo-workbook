from __future__ import annotations

from collections.abc import Callable

from .models.plan_session import ValidationResult

# Returns per-item error messages (empty list = item passes).
ItemValidator = Callable[[dict[str, str]], list[str]]


def _check_title_tag_item(item: dict[str, str]) -> list[str]:
    messages: list[str] = []
    new_value = item.get("new_value", "")
    if not new_value:
        messages.append("new_value is required")
    elif len(new_value) > 60:
        messages.append(f"title tag is {len(new_value)} characters, must be 60 or fewer (brand name excluded)")
    if not item.get("primary_keyword"):
        messages.append("primary_keyword is required")
    return messages


def _check_meta_description_item(item: dict[str, str]) -> list[str]:
    messages: list[str] = []
    new_value = item.get("new_value", "")
    length = len(new_value)
    if not new_value:
        messages.append("new_value is required")
    elif not (120 <= length <= 160):
        messages.append(f"meta description is {length} characters, must be between 120 and 160")
    if not item.get("cta"):
        messages.append("cta is required")
    return messages


def _check_h1_tag_item(item: dict[str, str]) -> list[str]:
    messages: list[str] = []
    new_value = item.get("new_value", "")
    if not new_value:
        messages.append("new_value is required")
    elif len(new_value) > 70:
        messages.append(f"H1 is {len(new_value)} characters, must be 70 or fewer")
    if not item.get("primary_keyword"):
        messages.append("primary_keyword is required")
    return messages


def _check_image_alt_text_item(item: dict[str, str]) -> list[str]:
    messages: list[str] = []
    new_value = item.get("new_value", "")
    if not new_value:
        messages.append("new_value is required")
    elif len(new_value.split()) < 3:
        messages.append("alt text should be a descriptive phrase, not a single generic word")
    return messages


def _check_canonical_tags_item(item: dict[str, str]) -> list[str]:
    messages: list[str] = []
    new_value = item.get("new_value", "")
    if not new_value:
        messages.append("new_value is required")
    elif not new_value.startswith(("http://", "https://")):
        messages.append("canonical target must be a full URL (including scheme)")
    return messages


# h1 is allowed here despite this touchpoint's own name (h2_h3_h4_tags) —
# real historical notes describe a *secondary* heading being demoted from
# H1 to H2 (a duplicate/mistaken H1 elsewhere on the page, distinct from
# the page's single official H1, which the dedicated h1_tag touchpoint
# already tracks). Confirmed live: "Change <H1> ... to an <H2> tag." is a
# real recurring phrasing, not a hypothetical.
_HEADING_TAGS = {"h1", "h2", "h3", "h4"}


def _check_heading_item(item: dict[str, str]) -> list[str]:
    """One heading promotion/demotion, e.g. {old_tag: h4, new_tag: h3,
    heading_text: "Common Career Paths"} — kept separate per heading so a
    batch of unrelated changes never collapses into one ambiguous blob.

    old_tag is optional: the copy itself usually isn't changing, only the
    tag wrapping it, and the source (a free-text note, a specialist who
    only knows what a heading is *becoming*) often doesn't state what
    level it currently is. new_tag and heading_text are still required —
    without heading_text there's nothing to identify or verify the heading
    by at all.
    """
    messages: list[str] = []
    old_tag = item.get("old_tag", "").lower()
    new_tag = item.get("new_tag", "").lower()
    if old_tag and old_tag not in _HEADING_TAGS:
        messages.append(f"old_tag must be one of {sorted(_HEADING_TAGS)}")
    if new_tag not in _HEADING_TAGS:
        messages.append(f"new_tag must be one of {sorted(_HEADING_TAGS)}")
    if not item.get("heading_text"):
        messages.append("heading_text is required")
    return messages


def _check_internal_link_item(item: dict[str, str]) -> list[str]:
    """One internal link, e.g. {anchor_text: "our FAQ page", target_url:
    "https://iecrm.org/faqs/"} — one item per link, not one cell listing
    several links with no explicit anchor/target pairing.
    """
    messages: list[str] = []
    target_url = item.get("target_url", "")
    if not target_url:
        messages.append("target_url is required")
    elif not target_url.startswith(("http://", "https://", "/")):
        messages.append("target_url must be a full URL or a site-relative path")
    if not item.get("anchor_text"):
        messages.append("anchor_text is required")
    return messages


def _default_item_validator(item: dict[str, str]) -> list[str]:
    if not item:
        return ["at least one field must be recorded"]
    return []


# touchpoint_id -> (min_items, max_items, per-item validator). max_items of
# None means unbounded — most touchpoints can legitimately apply more than
# once per page (e.g. several headings or several new internal links).
_TOUCHPOINT_RULES: dict[str, tuple[int, int | None, ItemValidator]] = {
    "title_tag": (1, 1, _check_title_tag_item),
    "meta_description": (1, 1, _check_meta_description_item),
    "h1_tag": (1, 1, _check_h1_tag_item),
    "canonical_tags": (1, 1, _check_canonical_tags_item),
    "image_alt_text": (1, None, _check_image_alt_text_item),
    "h2_h3_h4_tags": (1, None, _check_heading_item),
    "internal_linking_to_other_pages_homepage": (1, None, _check_internal_link_item),
    "internal_linking_to_target_page": (1, None, _check_internal_link_item),
}

_DEFAULT_RULE: tuple[int, int | None, ItemValidator] = (1, None, _default_item_validator)


def validate_touchpoint(touchpoint_id: str, items: list[dict[str, str]]) -> ValidationResult:
    min_items, max_items, item_validator = _TOUCHPOINT_RULES.get(touchpoint_id, _DEFAULT_RULE)

    messages: list[str] = []
    if len(items) < min_items:
        messages.append(f"at least {min_items} item(s) required, got {len(items)}")
    if max_items is not None and len(items) > max_items:
        messages.append(f"at most {max_items} item(s) allowed, got {len(items)}")

    multi = len(items) > 1
    for index, item in enumerate(items):
        item_messages = item_validator(item)
        if multi:
            messages.extend(f"item {index + 1}: {m}" for m in item_messages)
        else:
            messages.extend(item_messages)

    return ValidationResult(passed=not messages, messages=messages)
