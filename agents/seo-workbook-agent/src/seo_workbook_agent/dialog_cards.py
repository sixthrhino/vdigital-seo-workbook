from __future__ import annotations

from typing import Any

# The page-fields step's set mirrors record_page_from_text's whole field
# set (see seo_workbook_common.page_capture.parse_page_capture) minus
# client/month (collected once in step 1) and minus url and the old_*
# fields (collected/fetched in the url-only step — see
# build_url_only_dialog and page_fetch.fetch_current_page_values). A
# specialist typically enters 5-7 pages per client/month in one sitting,
# so nothing already known gets re-typed per page.
_URL_FIELDS: list[tuple[str, str, str, bool]] = [
    ("keyword", "Primary keyword", 'e.g. "auto insurance (500)"', False),
    ("geo", "Target geo", "", False),
    ("title_new", "New title tag", "", False),
    ("meta_new", "New meta description", "", False),
    ("cta", "CTA", "", False),
    ("h1_new", "New H1", "", False),
    ("headings", "Headings changed", 'One per line: "H2 -> H3: text" or just "H3: text"', True),
    ("notes", "Other notes", "Links added, schema, alt text, etc.", True),
]

# Which fetched current-page value (see page_fetch.fetch_current_page_values)
# backs each new-value field's hint text, when one was found.
_CURRENT_VALUE_HINT_SOURCE = {"title_new": "title", "meta_new": "meta_description", "h1_new": "h1"}
_HINT_TRUNCATE = 150

REQUIRED_CLIENT_MONTH_FIELDS = ("client", "month")
REQUIRED_URL_FIELDS = ("url",)


def _dialog_response(header: str, widgets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "actionResponse": {
            "type": "DIALOG",
            "dialogAction": {
                "dialog": {
                    "body": {
                        "sections": [{"header": header, "widgets": widgets}]
                    }
                }
            },
        }
    }


def build_client_month_dialog() -> dict[str, Any]:
    """Step 1 of the page-update dialog flow, opened by the slash command —
    collected once per batch, then carried forward through every
    url-entry step's button parameters (see build_url_entry_dialog).
    """
    widgets: list[dict[str, Any]] = [
        {"textInput": {"name": "client", "label": "Client"}},
        {"textInput": {"name": "month", "label": "Month", "hintText": "YYYY-MM"}},
        {
            "buttonList": {
                "buttons": [{
                    "text": "Next",
                    "onClick": {"action": {"function": "startPageEntry"}},
                }]
            }
        },
    ]
    return _dialog_response("Record page updates", widgets)


def build_url_only_dialog(client: str, month: str, count: int, last_saved_url: str | None = None) -> dict[str, Any]:
    """Middle step: just the page URL, submitted before any of its fields
    are shown — so the live page can be fetched (see
    page_fetch.fetch_current_page_values) and its current title/meta
    description/H1 shown as hint text on the next step's corresponding
    fields, instead of the specialist having to look those up and paste
    them in by hand.
    """
    parameters = [
        {"key": "client", "value": client},
        {"key": "month", "value": month},
        {"key": "count", "value": str(count)},
    ]
    widgets: list[dict[str, Any]] = [
        {"textInput": {"name": "url", "label": "Page URL"}},
        {
            "buttonList": {
                "buttons": [{
                    "text": "Next",
                    "onClick": {"action": {"function": "fetchPageAndContinue", "parameters": parameters}},
                }]
            }
        },
    ]
    header = f"Page {count} for {client} ({month})"
    if last_saved_url:
        header = f"✓ Saved {last_saved_url} — {header}"
    return _dialog_response(header, widgets)


def _current_value_hint(name: str, current_values: dict[str, str]) -> str | None:
    source_key = _CURRENT_VALUE_HINT_SOURCE.get(name)
    if not source_key:
        return None
    value = (current_values or {}).get(source_key, "").strip()
    if not value:
        return None
    if len(value) > _HINT_TRUNCATE:
        value = value[: _HINT_TRUNCATE - 1] + "…"
    return f"Current: {value}"


def build_url_entry_dialog(
    client: str,
    month: str,
    url: str,
    count: int,
    current_values: dict[str, str] | None = None,
    prefill: dict[str, str] | None = None,
    error_text: str | None = None,
) -> dict[str, Any]:
    """One page's fields (minus url, minus the old_* values — see
    build_url_only_dialog and page_fetch.fetch_current_page_values),
    repeatable — "Next URL" saves this page and returns to a fresh
    build_url_only_dialog for the next one; "Done" saves this page and
    closes the dialog. Both buttons operate on whatever is currently
    filled in, so "Done" is only ever clicked on a page that actually has
    data, never a blank one.

    client/month/count/url and the fetched current_values all ride along
    on both buttons' action.parameters rather than being shown as fields
    here — they're locked in for this page, not re-editable in this first
    version.

    `error_text`/`prefill`: when a save comes back with a failed
    touchpoint (see dialog_submission._record_one_page), the caller
    re-renders *this exact page* — same url, same count, fields pre-filled
    with what was just typed (not blanked out) — with the real validation
    message shown up top, so the specialist can fix and resubmit in place
    instead of the failure being silently recorded and only surfacing (if
    at all) in the closing summary. current_values (the fetched hints)
    are carried through unchanged on a retry — no need to re-fetch the
    same page.
    """
    widgets: list[dict[str, Any]] = []
    if error_text:
        widgets.append({"textParagraph": {"text": error_text}})
    widgets.append({"textParagraph": {"text": f"Editing: {url}"}})

    current_values = current_values or {}
    prefill = prefill or {}
    for name, label, hint, multiline in _URL_FIELDS:
        text_input: dict[str, Any] = {"name": name, "label": label}
        dynamic_hint = _current_value_hint(name, current_values)
        if dynamic_hint:
            text_input["hintText"] = dynamic_hint
        elif hint:
            text_input["hintText"] = hint
        if multiline:
            text_input["type"] = "MULTIPLE_LINE"
        if prefill.get(name):
            text_input["value"] = prefill[name]
        widgets.append({"textInput": text_input})

    parameters = [
        {"key": "client", "value": client},
        {"key": "month", "value": month},
        {"key": "url", "value": url},
        {"key": "count", "value": str(count)},
        {"key": "current_title", "value": current_values.get("title", "")},
        {"key": "current_meta_description", "value": current_values.get("meta_description", "")},
        {"key": "current_h1", "value": current_values.get("h1", "")},
    ]
    widgets.append({
        "buttonList": {
            "buttons": [
                {"text": "Next URL", "onClick": {"action": {"function": "saveAndContinue", "parameters": parameters}}},
                {"text": "Done", "onClick": {"action": {"function": "saveAndFinish", "parameters": parameters}}},
            ]
        }
    })

    header = f"Page {count} for {client} ({month})"
    if error_text:
        header = f"⚠️ Please fix and resubmit — {header}"
    return _dialog_response(header, widgets)


def _status_response(status_code: str, message: str) -> dict[str, Any]:
    return {
        "actionResponse": {
            "type": "DIALOG",
            "dialogAction": {
                "actionStatus": {
                    "statusCode": status_code,
                    "userFacingMessage": message,
                }
            },
        }
    }


def dialog_ok(message: str) -> dict[str, Any]:
    """Closes the dialog and shows `message` — used both for a clean
    finish and for a completed submission that still has something to
    flag (e.g. a touchpoint that failed validation), since either way the
    interaction itself succeeded."""
    return _status_response("OK", message)


def dialog_error(message: str) -> dict[str, Any]:
    """Closes the dialog with an error status — used when a step couldn't
    be processed (missing required field, MCP call failed). This first
    version doesn't re-render the same step pre-filled on error; the
    specialist re-invokes the slash command to start over."""
    return _status_response("INVALID_ARGUMENT", message)


def extract_form_inputs(event: dict[str, Any]) -> dict[str, str]:
    """Pull {field_name: value} out of a CARD_CLICKED event's
    common.formInputs — every text input's value arrives nested under
    stringInputs.value (a list; a plain text input only ever populates the
    first entry). Fields the consultant left blank are simply absent from
    formInputs, not present with an empty value — callers should use
    values.get(name, "") rather than assuming every field key exists.
    """
    form_inputs = (event.get("common") or {}).get("formInputs") or {}
    values: dict[str, str] = {}
    for name, entry in form_inputs.items():
        string_values = (entry.get("stringInputs") or {}).get("value") or []
        values[name] = string_values[0] if string_values else ""
    return values


def invoked_function(event: dict[str, Any]) -> str | None:
    """Which button was clicked — dispatches between the multi-step
    dialog's steps (startPageEntry / saveAndContinue / saveAndFinish).
    Checked in both places Chat has been observed to carry this, since
    the exact event shape here hasn't been exercised against every
    possible Chat client/version."""
    common = event.get("common") or {}
    if common.get("invokedFunction"):
        return common["invokedFunction"]
    action = event.get("action") or {}
    return action.get("actionMethodName")


def extract_button_parameters(event: dict[str, Any]) -> dict[str, str]:
    """Pull the clicked button's action.parameters back out of a
    CARD_CLICKED event — this is how client/month/count are carried
    forward across the multi-step dialog without re-collecting them on
    every page (see build_url_entry_dialog). Handles both the map shape
    (common.parameters) and the list-of-{key,value} shape
    (action.parameters), since which one a given Chat client actually
    sends hasn't been confirmed live."""
    common = event.get("common") or {}
    if isinstance(common.get("parameters"), dict):
        return common["parameters"]

    action = event.get("action") or {}
    params = action.get("parameters") or []
    return {p["key"]: p["value"] for p in params if "key" in p and "value" in p}


def is_dialog_submission(event: dict[str, Any]) -> bool:
    return event.get("type") == "CARD_CLICKED" and event.get("dialogEventType") == "SUBMIT_DIALOG"


def is_slash_command(event: dict[str, Any]) -> bool:
    return event.get("type") == "MESSAGE" and bool((event.get("message") or {}).get("slashCommand"))
