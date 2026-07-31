from __future__ import annotations

from typing import Any

# (field name, label, hint text, multiline) shown in the page-update dialog,
# in display order — mirrors record_page_from_text's whole field set (see
# seo_workbook_common.page_capture.parse_page_capture) so the dialog and the
# conversational labeled-text workflow are two front ends over the exact
# same parsing/validation, not two competing ones. old/new pairs are split
# into two separate inputs here (clearer in a form than one "->"-joined
# field), then rejoined into that same "old -> new" syntax when the
# labeled-text block gets assembled (see dialog_submission.py).
_FIELDS: list[tuple[str, str, str, bool]] = [
    ("client", "Client", "", False),
    ("month", "Month", "YYYY-MM", False),
    ("url", "Page URL", "", False),
    ("keyword", "Primary keyword", 'e.g. "auto insurance (500)"', False),
    ("geo", "Target geo", "", False),
    ("title_old", "Old title tag", "", False),
    ("title_new", "New title tag", "", False),
    ("meta_old", "Old meta description", "", False),
    ("meta_new", "New meta description", "", False),
    ("cta", "CTA", "", False),
    ("h1_old", "Old H1", "", False),
    ("h1_new", "New H1", "", False),
    ("headings", "Headings changed", 'One per line: "H2 -> H3: text" or just "H3: text"', True),
    ("notes", "Other notes", "Links added, schema, alt text, etc.", True),
]

REQUIRED_FIELDS = ("client", "month", "url")


def build_page_update_dialog() -> dict[str, Any]:
    """Cards v2 dialog body for the page-update slash command — one page's
    month of changes, matching record_page_from_text's whole field set.
    Returned synchronously as the /chat webhook's HTTP response (Chat
    expects the dialog in the immediate response, not via a follow-up API
    call the way normal message replies are posted — see chat_router.py).
    """
    widgets: list[dict[str, Any]] = []
    for name, label, hint, multiline in _FIELDS:
        text_input: dict[str, Any] = {"name": name, "label": label}
        if hint:
            text_input["hintText"] = hint
        if multiline:
            text_input["type"] = "MULTIPLE_LINE"
        widgets.append({"textInput": text_input})

    widgets.append({
        "buttonList": {
            "buttons": [{
                "text": "Record update",
                "onClick": {"action": {"function": "submitPageUpdate"}},
            }]
        }
    })

    return {
        "actionResponse": {
            "type": "DIALOG",
            "dialogAction": {
                "dialog": {
                    "body": {
                        "sections": [{
                            "header": "Record a page update",
                            "widgets": widgets,
                        }]
                    }
                }
            },
        }
    }


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
    success and for a completed submission that still has something to
    flag (e.g. a touchpoint that failed validation), since either way the
    interaction itself succeeded."""
    return _status_response("OK", message)


def dialog_error(message: str) -> dict[str, Any]:
    """Closes the dialog with an error status — used when the submission
    itself couldn't be processed (missing required field, MCP call
    failed). The consultant re-invokes the slash command to try again;
    this first version doesn't re-render the same form pre-filled."""
    return _status_response("INVALID_ARGUMENT", message)


def extract_form_inputs(event: dict[str, Any]) -> dict[str, str]:
    """Pull {field_name: value} out of a CARD_CLICKED/SUBMIT_DIALOG event's
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


def is_dialog_submission(event: dict[str, Any]) -> bool:
    return event.get("type") == "CARD_CLICKED" and event.get("dialogEventType") == "SUBMIT_DIALOG"


def is_slash_command(event: dict[str, Any]) -> bool:
    return event.get("type") == "MESSAGE" and bool((event.get("message") or {}).get("slashCommand"))
